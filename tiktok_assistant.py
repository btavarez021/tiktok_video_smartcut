# tiktok_assistant.py
import os
import logging
import tempfile
from typing import Dict, List, Optional

import boto3
import yaml

from assistant_log import log_step
from tiktok_template import normalize_video_ffmpeg, config_path, client

# =========================================================
# CONFIGURATION
# =========================================================
TEXT_MODEL = "gpt-4.1-mini"

S3_BUCKET_NAME = "tiktok-video-uploader"
S3_REGION = "us-east-2"

S3_PUBLIC_BASE = f"[https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com]https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com"

RAW_PREFIX = "raw_uploads/"
PROCESSED_PREFIX = "processed/"

s3 = boto3.client("s3", region_name=S3_REGION)

video_analyses_cache: Dict[str, str] = {}

# =========================================================
# S3 HELPERS
# =========================================================
def list_raw_s3_videos() -> List[str]:
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)
    files: List[str] = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.lower().endswith((".mp4", ".mov", ".avi", ".m4v")):
            files.append(key)
    return files


def download_s3_video(key: str) -> Optional[str]:
    ext = os.path.splitext(key)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        s3.download_fileobj(S3_BUCKET_NAME, key, tmp)
        tmp.close()
        return tmp.name
    except Exception as e:
        logging.error(f"Failed to download {key}: {e}")
        return None


def move_all_raw_to_processed() -> None:
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)
    contents = resp.get("Contents", [])
    if not contents:
        log_step("No files to move from raw_uploads/.")
        return

    for obj in contents:
        key = obj["Key"]
        if not key.lower().endswith((".mp4", ".mov", ".avi", ".m4v")):
            continue

        processed_key = key.replace(RAW_PREFIX, PROCESSED_PREFIX, 1)
        try:
            s3.copy_object(
                Bucket=S3_BUCKET_NAME,
                CopySource={"Bucket": S3_BUCKET_NAME, "Key": key},
                Key=processed_key,
            )
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
            log_step(f"Moved {key} → {processed_key}")
        except Exception as e:
            logging.error(f"Failed to move {key} → processed/: {e}")

# =========================================================
# NORMALIZATION
# =========================================================
def normalize_video(src: str, dst: str) -> None:
    normalize_video_ffmpeg(src, dst)

# =========================================================
# LLM ANALYSIS
# =========================================================
def analyze_video(path: str) -> str:
    basename = os.path.basename(path)

    prompt = f"""
You are helping script a vertical TikTok about a HOTEL or TRAVEL experience.

Write ONE short sentence describing what the viewer likely sees and the main selling point.

Tone: natural travel reviewer, no hashtags, no emojis.
Filename: {basename}
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        desc = (resp.choices[0].message.content or "").strip()
        logging.info("LLM analysis for %s: %s", basename, desc)
        return desc
    except Exception as e:
        logging.error(f"analyze_video LLM call failed for {basename}: {e}")
        return f"Hotel/travel clip showing scenery and amenities ({basename})."


def save_analysis_result(key: str, desc: str) -> None:
    video_analyses_cache[key] = desc
    log_step(f"Cached analysis for {key}.")

# =========================================================
# YAML GENERATION PROMPT
# =========================================================
def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    if not video_files:
        return "Generate an empty YAML config.yml."

    first = video_files[0]
    last = video_files[-1]
    middle = video_files[1:-1]

    lines = [
        "Generate a YAML storyboard for a hotel travel TikTok.",
        "Keep captions one sentence max.",
        "",
        f"First clip: {first}",
        f"Last clip: {last}",
        "",
    ]

    for vf, a in zip(video_files, analyses):
        lines.append(f"- file: {vf}")
        lines.append(f"  analysis: {a}")

    lines.append("")
    lines.append("Return ONLY YAML. No backticks, no explanations.")

    return "\n".join(lines)

# =========================================================
# CAPTION STYLE REWRITING
# =========================================================
STYLE_MAP = {
    "punchy": "Short punchy hooks.",
    "cinematic": "Cinematic descriptive travel tone.",
    "descriptive": "Literal descriptive hotel review sentence.",
    "influencer": "Influencer enthusiastic tone.",
    "travel_blog": "Travel blogger hotel stay commentary.",
}

def apply_overlay(style: str, target="all", filename=None) -> None:
    instructions = STYLE_MAP.get(style, STYLE_MAP["descriptive"])

    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception:
        return

    prompt = f"""
Rewrite ONLY the 'text' fields in this YAML.

Style: {instructions}
Each caption must be ONE sentence max.
Do not change structure.

YAML:
{current_yaml}

Return ONLY YAML.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        cfg = yaml.safe_load(new_yaml) or {}
        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        log_step(f"Overlay applied ({style}).")
    except Exception as e:
        logging.error(f"apply_overlay failed: {e}")

# =========================================================
# SMART TIMINGS
# =========================================================
def apply_smart_timings(pacing="standard") -> None:
    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception:
        return

    prompt = f"""
Adjust ONLY durations in YAML.

Mode: {pacing}
Total length <= 60s.
Do not modify text or structure.

YAML:
{current_yaml}

Return ONLY YAML.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        cfg = yaml.safe_load(new_yaml) or {}
        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        log_step(f"Smart timings applied ({pacing}).")
    except Exception as e:
        logging.error(f"apply_smart_timings failed: {e}")