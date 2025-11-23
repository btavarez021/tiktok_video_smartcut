# tiktok_assistant.py
import os
import logging
import tempfile
from typing import Dict, List, Optional

import boto3

from tiktok_template import normalize_video_ffmpeg  # your existing helper
from assistant_log import log_step

# -------------------------------------------------
# OpenAI model name used by assistant_api
# -------------------------------------------------
TEXT_MODEL = "gpt-4.1-mini"  # adjust if you want


# -------------------------------------------------
# S3 CONFIG
# -------------------------------------------------
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required")

S3_REGION = os.environ.get("S3_REGION", "us-east-1")

# Public URL base for exported videos
S3_PUBLIC_BASE = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com"

# Prefix for raw uploads (matches app.py)
RAW_PREFIX = "raw_uploads"

# Create S3 client
s3 = boto3.client("s3", region_name=S3_REGION)


# -------------------------------------------------
# GLOBAL ANALYSIS CACHE
# -------------------------------------------------
video_analyses_cache: Dict[str, str] = {}


# -------------------------------------------------
# S3 HELPERS
# -------------------------------------------------
def list_videos_from_s3() -> List[str]:
    """
    Return list of .mp4/.mov/.avi keys under raw_uploads/.
    """
    prefix = f"{RAW_PREFIX}/"
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
    files: List[str] = []

    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.lower().endswith((".mp4", ".mov", ".avi", ".m4v")):
            files.append(key)

    return files


def download_s3_video(key: str) -> Optional[str]:
    """
    Download S3 video to a temporary file and return its local path.
    """
    ext = os.path.splitext(key)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        s3.download_fileobj(S3_BUCKET_NAME, key, tmp)
        tmp.close()
        return tmp.name
    except Exception as e:
        logging.error(f"Failed to download {key} from S3: {e}")
        return None


def normalize_video(src: str, dst: str) -> None:
    """
    Thin wrapper around your existing normalize_video_ffmpeg helper.
   """
    normalize_video_ffmpeg(src, dst)


def save_analysis_result(key: str, desc: str) -> None:
    """
    Cache analysis results in memory (and optionally log them).
    """
    video_analyses_cache[key] = desc
    log_step(f"Cached analysis for {key}.")


# -------------------------------------------------
# STUBS / SIMPLE IMPLEMENTATIONS
# (Replace with your full logic as needed)
# -------------------------------------------------
def debug_video_dimensions(path: str) -> None:
    logging.info("debug_video_dimensions stub called for %s", path)


def analyze_video(path: str) -> str:
    """
    Minimal placeholder; replace with real video analysis if you want.
    """
    basename = os.path.basename(path)
    return f"Auto-analysis placeholder for {basename}."


def build_yaml_prompt(video_files, analyses) -> str:
    """
    Build a simple prompt combining filenames + analyses.
    """
    lines = ["Generate a YAML storyboard for these videos:"]
    for vf, a in zip(video_files, analyses):
        lines.append(f"- file: {vf}")
        lines.append(f"  analysis: {a}")
    return "\n".join(lines)


def apply_smart_timings(pacing: str = "standard") -> None:
    logging.info("apply_smart_timings stub called with pacing=%s", pacing)


def apply_overlay(style: str, target: str = "all", filename: Optional[str] = None) -> None:
    logging.info("apply_overlay stub called with style=%s", style)


def save_from_raw_yaml(*args, **kwargs):
    logging.info("save_from_raw_yaml stub called")
