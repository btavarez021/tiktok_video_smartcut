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


def _get_layout_mode(cfg: Dict[str, Any]) -> str:
    """
    Decide how to style captions / overlay:
      - "tiktok"  → smaller font, multi-line, TikTok friendly
      - "classic" → closer to your original single-line style
    """
    render = cfg.get("render") or {}
    mode = (render.get("layout_mode") or render.get("video_mode") or "tiktok").lower()
    if mode not in ("tiktok", "classic"):
        mode = "tiktok"
    return mode


# -----------------------------------------
# Caption wrapping helper
# -----------------------------------------
def _wrap_caption(text: str, max_chars_per_line: int = 28) -> str:
    """
    Word-wrap caption into multiple lines so it stays inside frame width.
    Returns a string with literal newlines, which ffmpeg drawtext will render
    as multi-line text.
    """
    text = (text or "").strip()
    if not text:
        return ""

    words = text.split()
    lines = []
    current = ""

    for w in words:
        # +1 for space if current is not empty
        extra = 1 if current else 0
        if len(current) + len(w) + extra > max_chars_per_line:
            if current:
                lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()

    if current:
        lines.append(current)

    return "\n".join(lines)


# -----------------------------------------
# TTS generation
# -----------------------------------------
# -----------------------------------------
# NEW: Per-clip TTS builder (A1 + C1)
# -----------------------------------------
def _build_per_clip_tts(cfg, clips, cta_cfg):
    """
    Build TTS for each clip individually.
    Returns list of (path, duration) tuples, and CTA narration tuple.
    """

    from openai import OpenAI
    import tempfile

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        log_step("[TTS] No API key available — skipping all TTS.")
        return [], None

    render = cfg.get("render", {}) or {}
    tts_cfg = cfg.get("tts", {}) or {}

    tts_enabled = (
        render.get("tts_enabled")
        or tts_cfg.get("enabled")
    )

    if not tts_enabled:
        log_step("[TTS] TTS disabled → skipping narration.")
        return [], None

    voice = (
        render.get("tts_voice")
        or tts_cfg.get("voice")
        or "alloy"
    )

    client = OpenAI(api_key=key)

    tts_files = []

    # -----------------------------------------
    # Generate narration for each clip (A1)
    # -----------------------------------------
    for idx, clip in enumerate(clips):
        text = clip.get("text", "").strip()
        if not text:
            tts_files.append(None)
            continue

        log_step(f"[TTS] Generating narration for clip {idx+1}: '{text}'")

        tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

        try:
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )
            with open(tmp_mp3, "wb") as f:
                f.write(resp.read())
        except Exception as e:
            log_step(f"[TTS ERROR] clip {idx+1}: {e}")
            tts_files.append(None)
            continue

        # Convert → AAC (FFmpeg)
        tmp_m4a = tmp_mp3.replace(".mp3", ".m4a")
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_mp3, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Measure duration
        try:
            dur = float(subprocess.check_output(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 tmp_m4a]
            ).decode().strip())
        except:
            dur = None

        if os.path.exists(tmp_m4a):
            tts_files.append((tmp_m4a, dur))
        else:
            tts_files.append(None)

    # -----------------------------------------
    # CTA Narration (C1)
    # -----------------------------------------
    cta_tuple = None

    if cta_cfg.get("enabled") and cta_cfg.get("voiceover") and cta_cfg.get("text"):
        text = cta_cfg["text"]
        log_step(f"[TTS] Generating CTA narration: '{text}'")

        tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
        try:
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )
            with open(tmp_mp3, "wb") as f:
                f.write(resp.read())
        except Exception as e:
            log_step(f"[TTS ERROR CTA] {e}")
            cta_tuple = None
        else:
            tmp_m4a = tmp_mp3.replace(".mp3", ".m4a")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_mp3, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                dur = float(subprocess.check_output(
                    ["ffprobe", "-v", "error",
                     "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1",
                     tmp_m4a]
                ).decode().strip())
            except:
                dur = None

            if os.path.exists(tmp_m4a):
                cta_tuple = (tmp_m4a, dur)

    return tts_files, cta_tuple

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
        log_step("[MUSIC] Disabled in config.")
        return None

    music_file = (music_cfg.get("file") or "").strip()
    if not music_file:
        log_step("[MUSIC] No music file specified.")
        return None

    volume = float(music_cfg.get("volume", 0.25))

    music_path = os.path.join(MUSIC_DIR, music_file)
    if not os.path.exists(music_path):
        log_step(f"[MUSIC] NOT FOUND in MUSIC_DIR: {music_path}")
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

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[MUSIC-FFMPEG] stderr:\n{proc.stderr}")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log_step("[MUSIC] Output audio invalid, disabling music.")
        return None

    return out_path


def _build_base_audio(video_path, total_duration):
    """
    Extract original audio from the stitched video, memory-safe.
    Returns a .m4a file path or None.

    NOTE: Currently NOT used in the final mix to keep the chain simple:
    we mix only TTS + music to avoid corrupt/empty sources.
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

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[AUDIO-BASE-FFMPEG] stderr:\n{proc.stderr}")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log_step("[AUDIO] Base audio invalid, skipping.")
        return None

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
    4. Optional CTA blur/text pass (safe, time-based)
    5. Build audio (per-clip TTS + CTA + Music) and mix
    6. Mux final video + audio
    7. Validate final MP4
    """
    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    layout_mode = _get_layout_mode(cfg)
    log_step(f"[EXPORT] Building low-memory FFmpeg timeline… (layout_mode={layout_mode})")

    # CLEAN UP legacy wrong music keys from older UI
    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)

    # --------------------------------------------------------
    # Helper: Safe escape for FFmpeg drawtext
    # --------------------------------------------------------
    def esc(text: str) -> str:
        # escape % first (FFmpeg uses it in format strings)
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

    # Layout presets (TikTok / Classic)
    if layout_mode == "tiktok":
        # TikTok caption style
        max_chars = 22
        fontsize = 68
        line_spacing = 14
        boxborderw = 55
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        y_expr = "(h * 0.55)"
    else:  # classic mode
        max_chars = 38
        fontsize = 54
        line_spacing = 8
        boxborderw = 35
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        y_expr = "h-(text_h*1.8)-150"

    with open(trimlist, "w") as lf:
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            vf = "scale=1080:-2,setsar=1"

            if clip["text"]:
                wrapped = _wrap_caption(clip["text"], max_chars_per_line=max_chars)
                text_safe = esc(wrapped)

                vf += (
                    f",drawtext=text='{text_safe}':"
                    f"fontfile={fontfile}:"
                    f"fontcolor=white:fontsize={fontsize}:"
                    f"line_spacing={line_spacing}:"
                    f"shadowcolor=0x000000:shadowx=3:shadowy=3:"
                    f"text_shaping=1:"
                    f"box=1:boxcolor=0x000000AA:boxborderw={boxborderw}:"
                    f"x=(w-text_w)/2:"
                    f"y={y_expr}:"
                    f"fix_bounds=1:"
                    f"borderw=2:bordercolor=0x000000"
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
    # 3. CTA OUTRO BLUR (SAFE, TIME-BASED)
    # --------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = cta_cfg.get("enabled", False)
    cta_text = esc(cta_cfg.get("text", ""))
    cta_dur = float(cta_cfg.get("duration", 3.0))

    if cta_enabled and cta_text:
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

        if total_dur <= 0.3:
            log_step("[CTA] Video too short for CTA → skipping CTA step.")
            final_video_source = concat_output
        else:
            # Clamp CTA duration to reasonable range
            safe_cta = min(cta_dur, max(total_dur - 0.1, 0.5))
            if safe_cta < 0.2:
                log_step("[CTA] CTA duration too small → skipping CTA step.")
                final_video_source = concat_output
            else:
                start_cta = max(total_dur - safe_cta, 0.0)

                blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

                vf = (
                    f"boxblur=10:enable='gte(t,{start_cta})',"
                    f"drawtext=text='{cta_text}':"
                    f"fontcolor=white:fontsize=60:"
                    f"x=(w-text_w)/2:y=h-220:"
                    f"enable='gte(t,{start_cta})'"
                )

                blur_cmd = [
                    "ffmpeg", "-y",
                    "-i", concat_output,
                    "-vf", vf,
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "22",
                    "-pix_fmt", "yuv420p",
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

                if not os.path.exists(blurred) or os.path.getsize(blurred) < 150 * 1024:
                    log_step("[CTA] CTA failed → using unmodified concat output.")
                    final_video_source = concat_output
                else:
                    final_video_source = blurred

    # --------------------------------------------------------
    # NEW — PER-CLIP TTS (A1)
    # --------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    tts_tracks, cta_tts_track = _build_per_clip_tts(cfg, clips, cta_cfg)

    # --------------------------------------------------------
    # A1a — Auto-extend clip duration to fit narration length
    # --------------------------------------------------------
    for i, clip in enumerate(clips):
        tts_entry = tts_tracks[i] if i < len(tts_tracks) else None

        if not tts_entry or not isinstance(tts_entry, tuple):
            continue

        tts_path, tts_dur = tts_entry

        if not tts_path or not tts_dur:
            continue

        needed = tts_dur + 0.35  # safety padding

        if needed > clip["duration"]:
            log_step(
                f"[A1a] Extending clip {i+1} "
                f"duration from {clip['duration']:.2f}s → {needed:.2f}s"
            )
            clip["duration"] = needed



    # --------------------------------------------------------
    # 4. AUDIO PIPELINE — per-clip TTS + CTA + music
    # --------------------------------------------------------
    log_step("[AUDIO] Building audio timeline…")

    # Build narration timeline using adelay offsets so narration
    # lines up with each clip in time.
    audio_inputs = []
    current_time = 0.0  # seconds

    # Per-clip TTS: align by clip durations
    for idx, clip in enumerate(clips):
        tts_entry = tts_tracks[idx] if idx < len(tts_tracks) else None

        if tts_entry and isinstance(tts_entry, tuple):
            tts_path, tts_dur = tts_entry

            audio_inputs.append({
                "path": tts_path,
                "start": current_time,
                "volume": 1.0,
            })

        current_time += clip["duration"]


    # CTA narration: ALWAYS play after the last clip + its narration
    if cta_tts_track and cta_enabled and cta_text:
        # Extract CTA path from tuple
        if isinstance(cta_tts_track, tuple):
            cta_path, cta_dur = cta_tts_track
        else:
            cta_path = cta_tts_track
            cta_dur = None

        # CTA should start AFTER all clips (including extended ones)
        total_clip_time = sum([clip["duration"] for clip in clips])

        cta_start = total_clip_time  # CTA starts exactly when last clip ends

        audio_inputs.append({
            "path": cta_path,
            "start": cta_start,
            "volume": 1.0,
        })



    final_audio = None

    if audio_inputs:
        # Build ffmpeg command to mix all TTS with timing offsets
        narration_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        cmd = ["ffmpeg", "-y"]

        for inp in audio_inputs:
            cmd += ["-i", inp["path"]]

        filter_parts = []
        labels = []

        for idx, inp in enumerate(audio_inputs):
            delay_ms = int(round(inp["start"] * 1000))
            vol = inp["volume"]
            part = f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[a{idx}]"

            filter_parts.append(part)
            labels.append(f"[a{idx}]")

        filter_complex = "; ".join(filter_parts) + "; " + "".join(labels) + f"amix=inputs={len(audio_inputs)}:normalize=0[outa]"

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            "-c:a", "aac",
            narration_out,
        ]

        log_step("[AUDIO] Mixing per-clip TTS (and CTA)…")
        mix_proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if mix_proc.stderr:
            log_step(f"[AUDIO-FFMPEG] stderr:\n{mix_proc.stderr}")

        if os.path.exists(narration_out) and os.path.getsize(narration_out) > 1024:
            final_audio = narration_out
        else:
            log_step("[AUDIO] Narration mix invalid → disabling narration.")
            final_audio = None

    # Load background music and mix with narration (if any)
    try:
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
    except Exception:
        total_duration = current_time

    music_audio = _build_music_audio(cfg, total_duration)

    if final_audio and music_audio:
        mixed = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", final_audio,
                "-i", music_audio,
                "-filter_complex", "[0:a]volume=1.0[a0]; [1:a]volume=0.25[a1]; [a0][a1]amix=inputs=2:normalize=0[out]",
                "-map", "[out]",
                "-c:a", "aac",
                mixed,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if os.path.exists(mixed) and os.path.getsize(mixed) > 1024:
            final_audio = mixed
        else:
            log_step("[AUDIO] Music+TTS mix invalid → falling back to narration only.")

    # --------------------------------------------------------
    # 5. FINAL MUX (NO -shortest, explicit mapping)
    # --------------------------------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))

    mux_cmd = ["ffmpeg", "-y", "-i", final_video_source]

    if final_audio:
        mux_cmd.extend(
            [
                "-i", final_audio,
                "-map", "0:v:0",   # video from first input
                "-map", "1:a:0",   # audio from second input
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