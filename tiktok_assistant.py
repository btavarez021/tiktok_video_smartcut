# tiktok_assistant.py
import os
import logging
import tempfile
from typing import Dict, List, Optional

import boto3

from tiktok_template import normalize_video_ffmpeg  # still available if needed
from assistant_log import log_step

# -------------------------------------------------
# OpenAI text model used by assistant_api
# -------------------------------------------------
TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------------------------
# S3 CONFIG
# -------------------------------------------------
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required")

# e.g. "us-east-2"
S3_REGION = os.environ.get("S3_REGION", "us-east-2")

# Public URL base for exported videos
S3_PUBLIC_BASE = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com"

RAW_PREFIX = "raw_uploads"
PROCESSED_PREFIX = "processed"
EXPORTS_PREFIX = "exports"

# Create S3 client
s3 = boto3.client("s3", region_name=S3_REGION)

# -------------------------------------------------
# GLOBAL ANALYSIS CACHE
# -------------------------------------------------
# Keys will be *basenames* (e.g. "clip1.mp4")
video_analyses_cache: Dict[str, str] = {}

# -------------------------------------------------
# S3 HELPERS
# -------------------------------------------------
def list_videos_from_s3() -> List[str]:
    """
    Return list of .mp4/.mov/.avi/.m4v keys under raw_uploads/.
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
    (Currently unused by api_analyze, but kept for compatibility.)
    """
    normalize_video_ffmpeg(src, dst)


def save_analysis_result(key: str, desc: str) -> None:
    """
    Cache analysis results in memory (by basename) and log them.
    """
    base = os.path.basename(key)
    video_analyses_cache[base] = desc
    log_step(f"Cached analysis for {base}.")


def move_raw_to_processed() -> None:
    """
    Move all objects from raw_uploads/ → processed/ (copy + delete).
    Called after successful export.
    """
    prefix = f"{RAW_PREFIX}/"
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
    contents = resp.get("Contents", [])

    if not contents:
        log_step("No raw_uploads/ objects to move.")
        return

    for obj in contents:
        key = obj["Key"]  # e.g. raw_uploads/clip1.mp4
        base = os.path.basename(key)
        if not base:
            continue

        new_key = f"{PROCESSED_PREFIX}/{base}"

        try:
            s3.copy_object(
                Bucket=S3_BUCKET_NAME,
                CopySource={"Bucket": S3_BUCKET_NAME, "Key": key},
                Key=new_key,
            )
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
            log_step(f"Moved {key} → {new_key}")
        except Exception as e:
            logging.error(f"Failed to move {key} to {new_key}: {e}")


# -------------------------------------------------
# STUBS / SIMPLE IMPLEMENTATIONS
# (Replace with your real logic if you want)
# -------------------------------------------------
def debug_video_dimensions(path: str) -> None:
    logging.info("debug_video_dimensions stub called for %s", path)


def analyze_video(path: str) -> str:
    """
    Minimal placeholder; replace with real video analysis if desired.
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
