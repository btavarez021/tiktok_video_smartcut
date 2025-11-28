import os
import logging
import subprocess
import gc
from typing import List, Dict, Any, Optional
from assistant_log import log_step
# Pillow compatibility fix for MoviePy
from PIL import Image, ImageFilter
from assistant_api import ensure_local_video
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

import numpy as np
import yaml

from utils_video import enforce_mp4

from moviepy.editor import (
    VideoFileClip,
    TextClip,
    CompositeVideoClip,
    AudioFileClip,
    CompositeAudioClip,
    concatenate_videoclips,
    concatenate_audioclips,
    ColorClip,
    vfx,
    ImageClip
)
import imageio_ffmpeg

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

# -----------------------------------------
# Paths / Globals
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

MUSIC_DIR = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_DIR, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920


# -----------------------------------------
# Simple Gaussian blur via Pillow
# -----------------------------------------
def blur_frame(frame, radius: int = 18):
    """Blur a single RGB frame using Pillow (MoviePy 1.0.3 safe)."""
    try:
        img = Image.fromarray(frame)
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        return np.array(img)
    except Exception as e:
        logger.warning(f"[BLUR] Frame blur failed: {e}")
        return frame


# -----------------------------------------
# FFmpeg normalization (helper, if needed)
# -----------------------------------------
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        "scale='min(1080,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        dst,
    ]

    log_step(f"[FFMPEG] Normalizing video {src} -> {dst}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        if result.stderr:
            lines = result.stderr.split("\n")
            important = [
                ln
                for ln in lines
                if "warning" in ln.lower() or "error" in ln.lower()
            ]
            for ln in important:
                log_step(f"[FFMPEG WARN] {ln}")

    except subprocess.CalledProcessError as e:
        err = e.stderr or ""
        lines = err.split("\n")
        important = [
            ln
            for ln in lines
            if any(word in ln.lower() for word in ["error", "failed", "invalid"])
        ]
        if important:
            for ln in important:
                log_step(f"[FFMPEG ERROR] {ln}")
        else:
            log_step("[FFMPEG ERROR] Video normalization failed")
        raise


# -----------------------------------------
# Config helpers
# -----------------------------------------
def _load_config() -> Dict[str, Any]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# -----------------------------------------
# Clip loading (case-insensitive, ext-tolerant)
# -----------------------------------------
def _load_clip_from_config(conf: Dict[str, Any]):
    """
    Load a clip described in the YAML config.

    Tolerant to:
    - case differences (IMG_3753.mp4 vs img_3753.mp4)
    - different video extensions (.mov, .m4v, .mp4) as long as basename matches.
    """
    filename = conf.get("file")
    if not filename:
        return None

    # Ensure .mp4 extension, but keep original basename
    filename = enforce_mp4(filename)

    def _find_clip_path(fname: str) -> Optional[str]:
        target_name = fname
        target_base, _ = os.path.splitext(target_name)

        direct_path = os.path.join(video_folder, target_name)
        if os.path.exists(direct_path):
            return direct_path

        if not os.path.isdir(video_folder):
            os.makedirs(video_folder, exist_ok=True)

        try:
            for f in os.listdir(video_folder):
                base, ext = os.path.splitext(f)
                if base.lower() == target_base.lower() and ext.lower() in (
                    ".mp4",
                    ".mov",
                    ".m4v",
                ):
                    return os.path.join(video_folder, f)
        except FileNotFoundError:
            return None

        return None

    path = _find_clip_path(filename)

    if not path or not os.path.exists(path):
        msg = f"[LOAD FAILED] File not found (case-insensitive search): {os.path.join(video_folder, filename)}"
        logger.warning(msg)
        log_step(msg)
        return None

    start = float(conf.get("start_time", 0))
    dur = conf.get("duration")
    scale = float(conf.get("scale", 1.0))

    clip = VideoFileClip(path)

    if dur is not None:
        dur = float(dur)
        end = min(clip.duration, start + dur)
        if end <= start:
            end = min(clip.duration, start + 0.5)
        clip = clip.subclip(start, end)
    else:
        if 0 < start < clip.duration:
            clip = clip.subclip(start)

    return clip, scale


# -----------------------------------------
# Vertical resize + crop
# -----------------------------------------
def _scale_and_crop_vertical(clip: VideoFileClip, fg_scale: float = 1.0):
    if fg_scale != 1.0:
        clip = clip.resize(fg_scale)

    clip = clip.resize(height=TARGET_H)

    w, _ = clip.size
    if w < TARGET_W:
        clip = clip.resize(width=TARGET_W)
        w, _ = clip.size

    x1 = (w - TARGET_W) / 2
    x2 = x1 + TARGET_W

    return clip.crop(x1=x1, x2=x2, y1=0, y2=TARGET_H)


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg):
    """
    Build a single TTS narration track using low memory.
    Returns a .m4a file path or None.
    """
    import tempfile, subprocess, os
    from assistant_log import log_step
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

    full_text = "\n".join(texts).strip()
    if not full_text:
        return None

    log_step("[TTS] Generating full narration…")

    # Generate temp MP3
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

    # Convert to AAC for clean FFmpeg mixing
    out_path = temp_mp3.replace(".mp3", ".m4a")

    subprocess.run([
        "ffmpeg", "-y",
        "-i", temp_mp3,
        "-c:a", "aac",
        "-b:a", "192k",
        out_path
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

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
    import os, subprocess, tempfile
    from assistant_log import log_step

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

    # Raw output (looped & trimmed safely)
    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    # FFmpeg pipeline:
    # 1. apad extends audio indefinitely
    # 2. atrim trims EXACTLY to final video duration
    # 3. apply volume
    cmd = [
        "ffmpeg", "-y",
        "-i", music_path,
        "-filter_complex",
        f"apad,atrim=0:{total_duration},volume={volume}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_path


def _build_base_audio(video_path, total_duration):
    """
    Extract original audio from the stitched video, memory-safe.
    Returns a .m4a file path or None.
    """
    import subprocess, tempfile, os
    from assistant_log import log_step

    if not os.path.exists(video_path):
        log_step(f"[AUDIO] Base video missing: {video_path}")
        return None

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    # apad = extend audio if too short
    # atrim = cut to exact duration
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-af", f"apad,atrim=0:{total_duration}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out_path



def edit_video(output_file="output_tiktok_final.mp4", optimized: bool = False):
    import subprocess, tempfile, os
    from assistant_log import log_step

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building low-memory FFmpeg timeline…")

    # ============================================================
    # Helper: Safe escape for FFmpeg drawtext
    # ============================================================
    def esc(text: str) -> str:
        if not text:
            return ""
        return (
            text.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
        )

    # ============================================================
    # Build clip list
    # ============================================================
    def collect(c, is_last=False):
        raw_file = c["file"]

        # get just filename (config stores relative name)
        filename = os.path.basename(raw_file)

        # ensure file exists locally (download if missing)
        local_file = ensure_local_video(filename)

        return {
            "file": local_file,
            "start": float(c.get("start_time", 0)),
            "duration": float(c.get("duration", 3)),
            "text": (c.get("text") or "").strip(),
            "is_last": is_last,
        }


    # ============================================================
    # 1. TRIM EACH CLIP
    # ============================================================
    trimmed_files = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    with open(trimlist, "w") as lf:
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            vf = "scale=1080:-2,setsar=1"

            if clip["text"]:
                vf += (
                    f",drawtext=text='{clip['text']}':"
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
                trimmed_path
            ]

            log_step(f"[TRIM] {clip['file']} -> {trimmed_path}")

            # --- DEBUG TRIM EXECUTION ---
            trim_proc = subprocess.run(
                trim_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # Log FFmpeg stderr for this clip
            if trim_proc.stderr:
                log_step(f"[TRIM-FFMPEG] stderr for {clip['file']}:\n{trim_proc.stderr}")

            # Verify trimmed file exists
            if not os.path.exists(trimmed_path):
                raise RuntimeError(f"[TRIM ERROR] Output not created for {clip['file']}")

            # Verify file is not empty
            if os.path.getsize(trimmed_path) < 50 * 1024:  # 50KB
                raise RuntimeError(
                    f"[TRIM ERROR] Output too small (<50KB) for {clip['file']}. "
                    f"Likely corrupt input or failed trim."
                )

            # Verify trimmed file is a valid MP4 via ffprobe
            try:
                _ = subprocess.check_output([
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    trimmed_path
                ])
            except Exception as e:
                raise RuntimeError(
                    f"[TRIM ERROR] Invalid MP4 produced for {clip['file']} — ffprobe error: {e}"
                )
            
            subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")

    # ============================================================
    # 2. CONCAT USING DEMUXER
    # ============================================================
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
        concat_output
    ]

    log_step("[CONCAT] Merging all clips…")
    
    subprocess.run(concat_cmd)

    # -----------------------------------------------------------
    # SANITY CHECK: concat_output must be a valid MP4
    # -----------------------------------------------------------
    if not os.path.exists(concat_output):
        raise RuntimeError("Concat failed: output file not created.")

    if os.path.getsize(concat_output) < 150 * 1024:  # <150KB
        raise RuntimeError("Concat failed: output file too small (corrupt).")

    # Try ffprobe
    try:
        concat_duration = float(subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            concat_output
        ]).decode().strip())
        if concat_duration <= 0:
            raise RuntimeError("Concat failed: zero duration.")
    except Exception as e:
        raise RuntimeError(f"Concat failed: invalid MP4. ffprobe error: {e}")


    final_video_source = concat_output

    # ============================================================
    # 3. CTA OUTRO BLUR (SAFE)
    # ============================================================
    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = cta_cfg.get("enabled", False)
    cta_text = esc(cta_cfg.get("text", ""))
    cta_dur = float(cta_cfg.get("duration", 3.0))

    if cta_enabled:
        dur_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            concat_output
        ]
        total_dur = float(subprocess.check_output(dur_cmd).decode().strip())
        start_cta = max(0, total_dur - cta_dur)

        blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        # SAFEST POSSIBLE CTA FILTERGRAPH
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
            blurred
        ]

        log_step("[CTA] Applying blur+text (SAFE)…")
        subprocess.run(blur_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        final_video_source = blurred

    # ============================================================
    # 4. AUDIO PIPELINE
    # ============================================================
    total_duration = float(subprocess.check_output([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        final_video_source
    ]).decode().strip())

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

    if idx == 1:
        # Single track optimization
        audio_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        log_step("[AUDIO] 1 track → copying directly…")
        subprocess.run([
            "ffmpeg", "-y",
            "-i", mix_inputs[1],
            "-c:a", "aac",
            audio_out
        ])
    elif idx > 1:
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
            audio_out
        ]

        log_step("[AUDIO] Mixing…")
        subprocess.run(audio_cmd)

    # Empty audio file fail-safe
    if audio_out and os.path.exists(audio_out) and os.path.getsize(audio_out) == 0:
        log_step("[AUDIO] Empty audio file → disabling audio.")
        audio_out = None

    # ============================================================
    # 5. FINAL MUX
    # ============================================================
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))

    mux_cmd = ["ffmpeg", "-y", "-i", final_video_source]

    if audio_out:
        mux_cmd.extend(["-i", audio_out, "-c:v", "copy", "-c:a", "aac", final_output])
    else:
        mux_cmd.extend(["-c:v", "copy", final_output])

    log_step("[MUX] Writing final video…")
    subprocess.run(mux_cmd)

    # ============================================================
    # VERIFY OUTPUT
    # ============================================================
    if not os.path.exists(final_output):
        raise RuntimeError(f"Final output missing! {final_output}")

    if os.path.getsize(final_output) < 1024 * 100:  # <100 KB → invalid MP4
        raise RuntimeError("Output file is suspiciously small (likely a mux failure).")

    log_step(f"[EXPORT] Video rendered: {final_output}")
    return final_output
