# tiktok_template.py — MOV/MP4 SAFE, LOW-MEMORY, NO CIRCULAR IMPORTS

import os
import logging
import subprocess
import tempfile
from typing import Dict, Any

import yaml
import numpy as np
from PIL import Image, ImageFilter
import imageio_ffmpeg

from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX

# Pillow compatibility shim
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

logger = logging.getLogger(__name__)

# -----------------------------------------
# Paths / Globals
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

MUSIC_DIR = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_DIR, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

TARGET_W = 1080
TARGET_H = 1920


# -----------------------------------------
# Simple Gaussian blur via Pillow
# -----------------------------------------
def blur_frame(frame, radius: int = 18):
    """Blur a single RGB frame using Pillow (kept for future use)."""
    try:
        img = Image.fromarray(frame)
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        return np.array(img)
    except Exception as e:
        logger.warning(f"[BLUR] Frame blur failed: {e}")
        return frame


# -----------------------------------------
# Config helpers
# -----------------------------------------
def _load_config() -> Dict[str, Any]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg):
    """
    Build a single TTS narration track using low memory.
    Returns a .m4a file path or None.
    """
    import tempfile
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        log_step("[TTS] No API key, skipping TTS.")
        return None

    render = cfg.get("render", {}) or {}
    if not render.get("tts_enabled"):
        return None

    voice = render.get("tts_voice", "alloy")

    # Build narration from all captions
    texts = []
    if cfg.get("first_clip", {}).get("text"):
        texts.append(cfg["first_clip"]["text"])

    for m in cfg.get("middle_clips", []):
        if m.get("text"):
            texts.append(m["text"])

    if cfg.get("last_clip", {}).get("text"):
        texts.append(cfg["last_clip"]["text"])

    cta_cfg = cfg.get("cta", {}) or {}
    if cta_cfg.get("voiceover") and cta_cfg.get("text"):
        texts.append(cta_cfg["text"])

    full_text = "\n".join(texts).strip()
    if not full_text:
        return None

    log_step("[TTS] Generating full narration…")

    temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

    try:
        client = OpenAI(api_key=key)
        resp = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=full_text
        )
        with open(temp_mp3, "wb") as f:
            f.write(resp.read())
    except Exception as e:
        log_step(f"[TTS ERROR] {e}")
        return None

    out_path = temp_mp3.replace(".mp3", ".m4a")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", temp_mp3,
            "-c:a", "aac",
            "-b:a", "192k",
            out_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    log_step(f"[TTS] OK: {out_path}")
    return out_path


# -----------------------------------------
# Background music (YAML: music: {enabled, file, volume})
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    """
    Memory-safe background music loader.
    Returns a temp .m4a file path or None.
    """
    import tempfile

    music_cfg = cfg.get("music", {}) or {}
    if not music_cfg.get("enabled"):
        return None

    music_file = (music_cfg.get("file") or "").strip()
    if not music_file:
        return None

    volume = float(music_cfg.get("volume", 0.25))

    music_path = os.path.join(MUSIC_DIR, music_file)
    if not os.path.exists(music_path):
        log_step(f"[MUSIC] NOT FOUND: {music_path}")
        return None

    log_step(f"[MUSIC] Using file: {music_path}")

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    cmd = [
        "ffmpeg", "-y",
        "-i", music_path,
        "-filter_complex",
        f"apad,atrim=0:{total_duration},volume={volume}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path,
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_path


def _build_base_audio(video_path, total_duration):
    """
    Extract original audio from the stitched video, memory-safe.
    Returns a .m4a file path or None.
    """
    import tempfile

    if not os.path.exists(video_path):
        log_step(f"[AUDIO] Base video missing: {video_path}")
        return None

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-af", f"apad,atrim=0:{total_duration}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path,
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_path


# -----------------------------------------
# Ensure local video exists (S3 → local sync)
# -----------------------------------------
def ensure_local_video(filename: str) -> str:
    """
    Makes sure the video exists locally in tik_tok_downloads/.
    If missing, download from S3 RAW_PREFIX folder.
    Returns absolute local path.
    """
    local_path = os.path.join(video_folder, filename)
    if os.path.exists(local_path):
        return local_path

    # Normalize RAW_PREFIX to avoid double slashes
    prefix = RAW_PREFIX.rstrip("/")
    s3_key = f"{prefix}/{filename}"

    log_step(f"[SYNC] Downloading missing clip: s3://{S3_BUCKET_NAME}/{s3_key}")

    try:
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        s3.download_file(S3_BUCKET_NAME, s3_key, local_path)
        log_step(f"[SYNC] Restored local clip → {local_path}")
    except Exception as e:
        raise RuntimeError(f"[SYNC ERROR] Cannot restore {filename} from S3: {e}")

    return local_path


# -----------------------------------------
# Core export function: edit_video
# -----------------------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Build final TikTok-style video using FFmpeg-only pipeline:

    1. Load config.yml (first_clip, middle_clips, last_clip)
    2. For each clip:
       - Ensure local file (downloads from S3 if missing)
       - Trim with text overlay
       - Validate output with size + ffprobe
    3. Concat all trimmed clips via demuxer
    4. Optional CTA blur/text pass
    5. Build audio (base + TTS + music) and mix
    6. Mux final video + audio
    7. Validate final MP4

    Returns absolute path to final_output.
    """
    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("[EXPORT] Building low-memory FFmpeg timeline…")

    # CLEAN UP legacy wrong music keys from older UI
    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)


    # --------------------------------------------------------
    # Helper: Safe escape for FFmpeg drawtext
    # --------------------------------------------------------
    def esc(text: str) -> str:
        text = text.replace("%", "\\%")
        if not text:
            return ""
        return (
            text.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
        )

    # --------------------------------------------------------
    # Build clip list from config
    # --------------------------------------------------------
    def collect(c: Dict[str, Any], is_last: bool = False) -> Dict[str, Any]:
        raw_file = c["file"]
        filename = os.path.basename(raw_file)
        local_file = ensure_local_video(filename)

        return {
            "file": local_file,
            "start": float(c.get("start_time", 0)),
            "duration": float(c.get("duration", 3)),
            "text": (c.get("text") or "").strip(),
            "is_last": is_last,
        }

    if "first_clip" not in cfg or "last_clip" not in cfg:
        raise RuntimeError("config.yml must contain first_clip and last_clip")

    clips = [collect(cfg["first_clip"])]
    for m in cfg.get("middle_clips", []):
        clips.append(collect(m))
    clips.append(collect(cfg["last_clip"], is_last=True))

    if not clips:
        raise RuntimeError("No clips defined in config.yml")

    # --------------------------------------------------------
    # 1. TRIM EACH CLIP
    # --------------------------------------------------------
    trimmed_files = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    with open(trimlist, "w") as lf:
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            vf = "scale=1080:-2,setsar=1"

            if clip["text"]:
                text_safe = esc(clip["text"])
                vf += (
                    f",drawtext=text='{text_safe}':"
                    f"fontcolor=white:fontsize=48:"
                    f"x=(w-text_w)/2:y=h-200"
                )

            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", clip["file"],
                "-t", str(clip["duration"]),
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-an",
                trimmed_path,
            ]

            log_step(f"[TRIM] {clip['file']} -> {trimmed_path}")

            trim_proc = subprocess.run(
                trim_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            if trim_proc.stderr:
                log_step(f"[TRIM-FFMPEG] stderr for {clip['file']}:\n{trim_proc.stderr}")

            if not os.path.exists(trimmed_path):
                raise RuntimeError(f"[TRIM ERROR] Output not created for {clip['file']}")

            if os.path.getsize(trimmed_path) < 50 * 1024:  # 50KB
                raise RuntimeError(
                    f"[TRIM ERROR] Output too small (<50KB) for {clip['file']}. "
                    f"Likely corrupt input or failed trim."
                )

            try:
                _ = subprocess.check_output(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        trimmed_path,
                    ]
                )
            except Exception as e:
                raise RuntimeError(
                    f"[TRIM ERROR] Invalid MP4 produced for {clip['file']} — ffprobe error: {e}"
                )

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")

    # --------------------------------------------------------
    # 2. CONCAT USING DEMUXER
    # --------------------------------------------------------
    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", trimlist,
        "-c:v", "libx264",
        "-preset", "superfast" if optimized else "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        concat_output,
    ]

    log_step("[CONCAT] Merging all clips…")
    concat_proc = subprocess.run(
        concat_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if concat_proc.stderr:
        log_step(f"[CONCAT-FFMPEG] stderr:\n{concat_proc.stderr}")

    if not os.path.exists(concat_output):
        raise RuntimeError("Concat failed: output file not created.")

    if os.path.getsize(concat_output) < 150 * 1024:  # <150KB
        raise RuntimeError("Concat failed: output file too small (corrupt).")

    try:
        concat_duration = float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    concat_output,
                ]
            ).decode().strip()
        )
        if concat_duration <= 0:
            raise RuntimeError("Concat failed: zero duration.")
    except Exception as e:
        raise RuntimeError(f"Concat failed: invalid MP4. ffprobe error: {e}")

    final_video_source = concat_output

    # --------------------------------------------------------
    # 3. CTA OUTRO BLUR (SAFE-PATCHED VERSION)
    # --------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = cta_cfg.get("enabled", False)
    cta_text = esc(cta_cfg.get("text", ""))
    cta_dur = float(cta_cfg.get("duration", 3.0))

    if cta_enabled:
        try:
            total_dur = float(
                subprocess.check_output(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        concat_output,
                    ]
                ).decode().strip()
            )
        except Exception as e:
            log_step(f"[CTA] Probe failed, skipping CTA: {e}")
            total_dur = 0

        # 🔥 SAFE FIX: clamp CTA duration
        if total_dur <= 0.3:
            log_step("[CTA] Video too short for CTA → skipping CTA step.")
            final_video_source = concat_output
        else:
            # CTA duration cannot exceed available video
            safe_cta = min(cta_dur, total_dur - 0.1)
            if safe_cta < 0.2:
                log_step("[CTA] CTA duration too small → skipping CTA step.")
                final_video_source = concat_output
            else:
                start_cta = total_dur - safe_cta

                blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

                vf = (
                    f"[0:v]split=2[pre_raw][cta_raw];"
                    f"[pre_raw]trim=start=0:end={start_cta},setpts=PTS-STARTPTS[pre];"
                    f"[cta_raw]trim=start={start_cta}:end={total_dur},setpts=PTS-STARTPTS,"
                    f"boxblur=10,"
                    f"drawtext=text='{cta_text}':fontcolor=white:fontsize=60:"
                    f"x=(w-text_w)/2:y=h-200[cta];"
                    f"[pre][cta]concat=n=2:v=1:a=0[out]"
                )

                blur_cmd = [
                    "ffmpeg", "-y",
                    "-i", concat_output,
                    "-vf", vf,
                    "-map", "[out]",
                    blurred,
                ]

                log_step("[CTA] Applying SAFE CTA blur/text…")

                proc = subprocess.run(
                    blur_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                if proc.stderr:
                    log_step(f"[CTA-FFMPEG] stderr:\n{proc.stderr}")

                # 🔥 SAFE OUTPUT CHECK
                if not os.path.exists(blurred) or os.path.getsize(blurred) < 150 * 1024:
                    log_step("[CTA] CTA failed → using unmodified concat output.")
                    final_video_source = concat_output
                else:
                    final_video_source = blurred


    # --------------------------------------------------------
    # 4. AUDIO PIPELINE
    # --------------------------------------------------------
    total_duration = float(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                final_video_source,
            ]
        ).decode().strip()
    )

    base_audio = _build_base_audio(final_video_source, total_duration)
    tts_audio = _build_tts_audio(cfg)
    music_audio = _build_music_audio(cfg, total_duration)

    mix_inputs = []
    mix_filters = []
    idx = 0

    def add(path, vol):
        nonlocal idx
        if not path:
            return
        mix_inputs.extend(["-i", path])
        mix_filters.append(f"[{idx}:a]volume={vol}[a{idx}]")
        idx += 1

    add(base_audio, 0.8)
    add(tts_audio, 1.0)
    add(music_audio, 0.25)

    audio_out = None

    if idx == 0:
        audio_out = None
    elif idx == 1:
        # Single track optimization
        audio_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        log_step("[AUDIO] 1 track → copying directly…")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", mix_inputs[1],
                "-c:a", "aac",
                audio_out,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    else:
        audio_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

        filter_complex = (
            "; ".join(mix_filters)
            + "; "
            + "".join(f"[a{i}]" for i in range(idx))
            + f"amix=inputs={idx}:normalize=0[outa]"
        )

        audio_cmd = [
            "ffmpeg", "-y",
            *mix_inputs,
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            "-c:a", "aac",
            audio_out,
        ]

        log_step("[AUDIO] Mixing…")
        mix_proc = subprocess.run(
            audio_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if mix_proc.stderr:
            log_step(f"[AUDIO-FFMPEG] stderr:\n{mix_proc.stderr}")

    if audio_out and os.path.exists(audio_out) and os.path.getsize(audio_out) == 0:
        log_step("[AUDIO] Empty audio file → disabling audio.")
        audio_out = None

    # --------------------------------------------------------
    # 5. FINAL MUX
    # --------------------------------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))

    mux_cmd = ["ffmpeg", "-y", "-i", final_video_source]

    if audio_out:
        mux_cmd.extend(
            [
                "-i", audio_out,
                "-c:v", "copy",
                "-c:a", "aac",
                final_output,
            ]
        )
    else:
        mux_cmd.extend(
            [
                "-c:v", "copy",
                final_output,
            ]
        )

    log_step("[MUX] Writing final video…")
    mux_proc = subprocess.run(
        mux_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if mux_proc.stderr:
        log_step(f"[MUX-FFMPEG] stderr:\n{mux_proc.stderr}")

    # --------------------------------------------------------
    # VERIFY OUTPUT
    # --------------------------------------------------------
    if not os.path.exists(final_output):
        raise RuntimeError(f"Final output missing! {final_output}")

    if os.path.getsize(final_output) < 1024 * 100:  # <100 KB → invalid MP4
        raise RuntimeError("Output file is suspiciously small (likely a mux failure).")

    log_step(f"[EXPORT] Video rendered: {final_output}")
    return final_output
