# tiktok_template.py
import os
import subprocess
from openai import OpenAI

# ------------------------------------------------
# Paths
# ------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Where normalized videos will be stored locally
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

# YAML storyboard file
config_path = os.path.join(BASE_DIR, "config.yml")

# ------------------------------------------------
# OpenAI Client
# ------------------------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client = OpenAI(api_key=api_key) if api_key else None


# ------------------------------------------------
# FFmpeg normalize helper (1080x1920 vertical)
# ------------------------------------------------
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    """
    Normalizes video to 1080x1920 vertical using ffmpeg.
    Overwrites dst.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", src,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-preset", "fast",
        "-crf", "23",
        dst,
    ]

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate()

    print("[FFMPEG] Normalization complete for", src)
    if stderr:
        print("[FFMPEG STDERR]:", stderr.decode(errors="ignore"))