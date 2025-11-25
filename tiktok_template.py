import os
import subprocess
import logging
from typing import Optional, List, Dict

from openai import OpenAI

logger = logging.getLogger(__name__)

# ----------------------------
# Paths
# ----------------------------
BASE_DIR = os.path.dirname(__file__)
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

# ----------------------------
# OpenAI client (shared)
# ----------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None
if not api_key:
    logger.warning("OPENAI_API_KEY/open_ai_api_key not set; LLM features may fail.")


# ----------------------------
# FFmpeg normalization
# ----------------------------
def normalize_video_ffmpeg(src: str, dst: str):
    """
    Normalize video to 1080x1920, 30fps, h264.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-r",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-an",
        dst,
    ]

    logger.info("Running ffmpeg normalize: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg failed: %s", e.stderr.decode("utf-8", errors="ignore"))
        raise


# ----------------------------
# Simple renderer using MoviePy
# ----------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Very simple renderer:
    - Reads config.yml
    - Loads first_clip + middle_clips + last_clip from tik_tok_downloads
    - Applies subclips and concatenates
    - Ignores TTS/CTA (flags only) for now
    """
    import yaml
    from moviepy.editor import VideoFileClip, concatenate_videoclips

    if not os.path.exists(config_path):
        raise FileNotFoundError("config.yml not found; generate YAML first.")

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    clips: List = []

    def build_clip(entry: Dict) -> Optional["VideoFileClip"]:
        fname = entry.get("file")
        if not fname:
            return None
        start = float(entry.get("start_time", 0))
        dur = float(entry.get("duration", 3.0))

        path = os.path.join(video_folder, fname)
        if not os.path.exists(path):
            logger.warning("Clip file not found for render: %s", path)
            return None

        base_clip = VideoFileClip(path)
        end_time = min(base_clip.duration, start + dur)
        clip = base_clip.subclip(start, end_time)
        return clip

    # first
    if "first_clip" in cfg:
        c = build_clip(cfg["first_clip"])
        if c:
            clips.append(c)

    # middle
    for m in cfg.get("middle_clips", []):
        c = build_clip(m)
        if c:
            clips.append(c)

    # last
    if "last_clip" in cfg:
        c = build_clip(cfg["last_clip"])
        if c:
            clips.append(c)

    if not clips:
        raise RuntimeError("No clips available to render; check config.yml and tik_tok_downloads/.")

    final = concatenate_videoclips(clips, method="compose")

    # Basic export settings; "optimized" toggles bitrate/preset a bit
    bitrate = "3500k" if optimized else "2500k"
    preset = "faster" if optimized else "veryfast"

    logger.info("Rendering video to %s (optimized=%s)", output_file, optimized)
    final.write_videofile(
        output_file,
        fps=30,
        codec="libx264",
        audio=False,
        bitrate=bitrate,
        preset=preset,
        threads=4,
    )

    for c in clips:
        c.close()
    final.close()
