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
# Core export function: edit_video   (WITH S1 SMOOTH CTA FADE)
# -----------------------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Build final TikTok-style video using FFmpeg-only pipeline.
    Includes OPTION S1 smooth fade-to-blur CTA transition.
    """
    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    layout_mode = _get_layout_mode(cfg)
    log_step(f"[EXPORT] Building low-memory FFmpeg timeline… (layout_mode={layout_mode})")

    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)

    # Escape helper
    def esc(text: str) -> str:
        if not text:
            return ""
        return (
            text.replace("%", "\\%")
                .replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
        )

    # --------------------------------------------------------
    # Build clip list
    # --------------------------------------------------------
    def collect(c: Dict[str, Any], is_last=False):
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

    # --------------------------------------------------------
    # 0. TTS + auto-extension upstream (A1 + A1a)
    # --------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    tts_tracks, cta_tts_track = _build_per_clip_tts(cfg, clips, cta_cfg)

    for i, clip in enumerate(clips):
        t = tts_tracks[i] if i < len(tts_tracks) else None
        if not t or not isinstance(t, tuple):
            continue
        t_path, t_dur = t
        if not t_path or not t_dur:
            continue

        needed = t_dur + 0.35
        if needed > clip["duration"]:
            log_step(f"[A1a] Extending clip {i+1} from {clip['duration']:.2f}s → {needed:.2f}s")
            clip["duration"] = needed

    # --------------------------------------------------------
    # 1. TRIM (no freeze; slow-mo handled separately if needed)
    # --------------------------------------------------------
    trimmed_files = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    if layout_mode == "tiktok":
        max_chars = 22
        fontsize = 68
        line_spacing = 14
        boxborderw = 55
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        y_expr = "(h * 0.55)"
    else:
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
                wrapped = _wrap_caption(clip["text"], max_chars)
                vf += (
                    f",drawtext=text='{esc(wrapped)}':"
                    f"fontfile={fontfile}:fontcolor=white:fontsize={fontsize}:"
                    f"line_spacing={line_spacing}:shadowcolor=0x000000:shadowx=3:shadowy=3:"
                    f"text_shaping=1:box=1:boxcolor=0x000000AA:boxborderw={boxborderw}:"
                    f"x=(w-text_w)/2:y={y_expr}:borderw=2:bordercolor=0x000000:fix_bounds=1"
                )

            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", clip["file"],
                "-t", str(clip["duration"]),
                "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20",
                "-an",
                trimmed_path,
            ]
            subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")

    # --------------------------------------------------------
    # 2. CONCAT
    # --------------------------------------------------------
    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", trimlist,
            "-c:v", "libx264",
            "-preset", "superfast" if optimized else "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            concat_output,
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    final_video_source = concat_output

    # --------------------------------------------------------
    # 3. CTA VIDEO EXTENSION + S1 SMOOTH FADE-TO-BLUR
    # --------------------------------------------------------
    cta_enabled = cta_cfg.get("enabled", False)
    cta_text = esc(cta_cfg.get("text", ""))
    cta_config_dur = float(cta_cfg.get("duration", 3.0))

    # CTA narration length
    if cta_tts_track and isinstance(cta_tts_track, tuple):
        _, cta_voice_dur = cta_tts_track
        cta_voice_dur = cta_voice_dur or 0.0
    else:
        cta_voice_dur = 0.0

    cta_segment_len = max(cta_config_dur, cta_voice_dur) if (cta_enabled and cta_text) else 0
    expected_total = sum([clip["duration"] for clip in clips])
    timeline_total = expected_total + cta_segment_len

    try:
        actual_total = float(subprocess.check_output(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             final_video_source]
        ).decode().strip())
    except:
        actual_total = expected_total

    # Pad full timeline if needed
    if timeline_total > actual_total + 0.05:
        pad_len = timeline_total - actual_total
        extended = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", final_video_source,
                "-vf", f"tpad=stop_mode=clone:stop_duration={pad_len}",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                extended,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        final_video_source = extended

    # --------------------------------------------------------
    # S1 — Smooth Fade-to-Blur CTA
    # --------------------------------------------------------
    if cta_enabled and cta_text and cta_segment_len > 0:

        fade_len = 0.30  # smooth fade duration
        cta_start = expected_total        # OPTION A (exact when narration ends)
        fade_start = cta_start
        fade_end = cta_start + fade_len
        cta_end = cta_start + cta_segment_len

        blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        vf = (
            # Blur intensity ramps in 0 → full over fade_len
            f"boxblur=0:enable='lt(t,{fade_start})',"
            f"boxblur=10:enable='gte(t,{fade_end})',"
            f"boxblur=if(between(t,{fade_start},{fade_end}), "
            f"10*((t-{fade_start})/{fade_len}), 0),"

            # CTA text fade-in
            f"drawtext=text='{cta_text}':fontcolor=white@0:"
            f"fontsize=60:x=(w-text_w)/2:y=h-220:"
            f"enable='between(t,{fade_start},{fade_end})',"
            f"drawtext=text='{cta_text}':fontcolor=white@1:"
            f"fontsize=60:x=(w-text_w)/2:y=h-220:"
            f"enable='gte(t,{fade_end})'"
        )

        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", final_video_source,
                "-vf", vf,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                blurred,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        final_video_source = blurred

    # --------------------------------------------------------
    # 4. AUDIO PIPELINE — unchanged
    # --------------------------------------------------------
    log_step("[AUDIO] Building audio timeline…")
    audio_inputs = []
    current_time = 0.0

    for idx, clip in enumerate(clips):
        t = tts_tracks[idx] if idx < len(tts_tracks) else None
        if t and isinstance(t, tuple):
            t_path, _ = t
            if t_path:
                audio_inputs.append({
                    "path": t_path,
                    "start": current_time,
                    "volume": 1.0,
                })
        current_time += clip["duration"]

    if cta_tts_track and cta_enabled and cta_text:
        cta_path, _ = cta_tts_track
        audio_inputs.append({
            "path": cta_path,
            "start": expected_total,
            "volume": 1.0,
        })

    final_audio = None
    if audio_inputs:
        narration_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        cmd = ["ffmpeg", "-y"]

        for inp in audio_inputs:
            cmd += ["-i", inp["path"]]

        parts = []
        labels = []
        for idx, inp in enumerate(audio_inputs):
            delay_ms = int(round(inp["start"] * 1000))
            parts.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={inp['volume']}[a{idx}]"
            )
            labels.append(f"[a{idx}]")

        cmd += [
            "-filter_complex",
            "; ".join(parts) + "; " + "".join(labels) +
            f"amix=inputs={len(audio_inputs)}:normalize=0[outa]",
            "-map", "[outa]",
            "-c:a", "aac",
            narration_out,
        ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        final_audio = narration_out

    # --------------------------------------------------------
    # Mix Music — unchanged
    # --------------------------------------------------------
    try:
        total_duration = float(subprocess.check_output(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             final_video_source]
        ).decode().strip())
    except:
        total_duration = expected_total + cta_segment_len

    music_audio = _build_music_audio(cfg, total_duration)

    if final_audio and music_audio:
        mixed = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", final_audio,
                "-i", music_audio,
                "-filter_complex",
                "[0:a]volume=1.0[a0]; "
                "[1:a]volume=0.25[a1]; "
                "[a0][a1]amix=inputs=2:normalize=0[out]",
                "-map", "[out]",
                "-c:a", "aac",
                mixed,
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        final_audio = mixed

    # --------------------------------------------------------
    # 5. FINAL MUX — unchanged
    # --------------------------------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))
    mux_cmd = ["ffmpeg", "-y", "-i", final_video_source]

    if final_audio:
        mux_cmd += [
            "-i", final_audio,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            final_output,
        ]
    else:
        mux_cmd += [
            "-c:v", "copy",
            final_output,
        ]

    subprocess.run(mux_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    log_step(f"[EXPORT] Video rendered: {final_output}")
    return final_output
