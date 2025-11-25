# tiktok_template.py
import os
import logging
import subprocess
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

# -------------------------------------------------
# Paths
# -------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(BASE_DIR, "config.yml")
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")

os.makedirs(video_folder, exist_ok=True)

# -------------------------------------------------
# OpenAI client
# -------------------------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

if not api_key:
    logger.warning("OPENAI_API_KEY / open_ai_api_key not set; LLM features may fail.")


# -------------------------------------------------
# FFMPEG helpers
# -------------------------------------------------
def _run_ffmpeg(cmd: list[str]) -> None:
    logger.info("[FFMPEG] Running: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = proc.communicate()
    if stdout:
        logger.debug("[FFMPEG STDOUT] %s", stdout)
    if stderr:
        logger.info("[FFMPEG STDERR] %s", stderr[:4000])
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")


def normalize_video_ffmpeg(src: str, dst: str) -> None:
    """
    Normalize a clip to 1080x1920 vertical with padding and H.264 video.
    """
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    logger.info("[FFMPEG] Normalizing video %s → %s", src, dst)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        dst,
    ]
    _run_ffmpeg(cmd)


# -------------------------------------------------
# edit_video – minimal but working
# -------------------------------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False) -> None:
    """
    Very simple editor:

    - Reads config.yml
    - Takes all unique `file` fields from first_clip, middle_clips, last_clip
    - Concatenates those normalized videos in order
    - Writes final MP4 to `output_file`

    NOTE: This does NOT implement TTS, CTA overlays, or text on screen yet.
    But all the related flags (tts, cta, etc.) are still stored in config.yml
    and accessible for future extensions.
    """
    import yaml

    if not os.path.exists(config_path):
        raise FileNotFoundError("config.yml not found. Generate YAML first.")

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f) or {}

    clips = []

    def _add_clip(section: dict) -> None:
        if not isinstance(section, dict):
            return
        fname = section.get("file")
        if not fname:
            return
        clips.append(fname)

    _add_clip(cfg.get("first_clip", {}))
    for m in cfg.get("middle_clips", []) or []:
        _add_clip(m)
    _add_clip(cfg.get("last_clip", {}))

    # Deduplicate while preserving order
    seen = set()
    ordered_unique = []
    for c in clips:
        if c not in seen:
            seen.add(c)
            ordered_unique.append(c)

    if not ordered_unique:
        raise ValueError("No clips specified in config.yml")

    # Build ffmpeg concat file list
    concat_txt = os.path.join(BASE_DIR, "concat_list.txt")
    with open(concat_txt, "w") as f:
        for filename in ordered_unique:
            local_path = os.path.join(video_folder, filename)
            if not os.path.exists(local_path):
                raise FileNotFoundError(
                    f"Normalized video not found: {local_path} (did analysis normalization run?)"
                )
            f.write(f"file '{local_path}'\n")

    # If optimized flag – use slightly lower bitrate / quality
    crf_value = "22" if optimized else "20"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_txt,
        "-c:v",
        "libx264",
        "-crf",
        crf_value,
        "-preset",
        "medium",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        output_file,
    ]
    _run_ffmpeg(cmd)
    logger.info("edit_video: render finished → %s", output_file)