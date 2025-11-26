# tiktok_assistant.py  — FINAL FIXED FULL FILE

import os
import logging
import tempfile
import subprocess
import json
from typing import Dict, List, Optional

import boto3
import yaml
from openai import OpenAI
from utils_video import enforce_mp4
from assistant_log import log_step
from tiktok_template import config_path, edit_video, video_folder
from dotenv import load_dotenv
load_dotenv()

import os

# Load environment variables from .env file

logger = logging.getLogger(__name__)

# -----------------------------------------
# OpenAI Setup
# -----------------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

TEXT_MODEL = "gpt-4.1-mini"

# -----------------------------------------
# S3 CONFIG (REQUIRED FOR RENDER)
# -----------------------------------------
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError(
        "S3_BUCKET_NAME (or AWS_BUCKET_NAME) environment variable is required"
    )

S3_REGION = os.getenv("S3_REGION", "us-east-2")
RAW_PREFIX = "raw_uploads"

EXPORT_PREFIX = os.getenv("S3_EXPORT", "exports/").lstrip("/")
if not EXPORT_PREFIX.endswith("/"):
    EXPORT_PREFIX += "/"

# -----------------------------------------
# Load AWS credentials (Render ENV VARS)
# -----------------------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise RuntimeError(
        "Missing AWS credentials! You MUST set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in Render."
    )



# -----------------------------------------
# Create S3 client with explicit credentials
# -----------------------------------------
s3 = boto3.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

# -----------------------------------------
# Analysis Cache
# -----------------------------------------
video_analyses_cache: Dict[str, str] = {}

ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)

# -----------------------------------------
# S3 Helpers
# -----------------------------------------

def list_videos_from_s3() -> List[str]:
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)

    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    log_step(f"S3 RAW KEYS: {keys}")

    files = []
    for key in keys:
        ext = os.path.splitext(key)[1].lower()
        if ext in [".mp4", ".mov", ".avi", ".m4v"]:
            files.append(key)
    return files


def upload_raw_file(file_storage) -> str:
    filename = os.path.basename(file_storage.filename or "").strip()
    if not filename:
        raise ValueError("Empty filename")

    filename = filename.replace(" ", "_")
    key = f"{RAW_PREFIX}{filename}"

    log_step(f"Uploading {filename} to s3://{S3_BUCKET_NAME}/{key}")
    s3.upload_fileobj(file_storage, S3_BUCKET_NAME, key)
    log_step(f"Uploaded to {key}")

    return key


def download_s3_video(key: str) -> Optional[str]:
    ext = os.path.splitext(key)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        s3.download_fileobj(S3_BUCKET_NAME, key, tmp)
        tmp.close()
        return tmp.name
    except Exception as e:
        log_step(f"[S3 DOWNLOAD ERROR] {e}")
        return None


# -----------------------------------------
# Normalize video for analysis
# -----------------------------------------
def normalize_video(src: str, dst: str) -> None:
    base = os.path.splitext(dst)[0]
    dst = f"{base}.mp4".lower()
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        "scale=1080:-2,setsar=1,format=yuv420p",
        "-metadata:s:v:0",
        "rotate=0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
        dst,
    ]

    log_step(f"[FFMPEG] Normalizing {src} → {dst}")
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except Exception as e:
        log_step(f"[FFMPEG ERROR] {e}")
        raise


# -----------------------------------------
# LLM Clip Analysis
# -----------------------------------------
def analyze_video(path: str) -> str:
    basename = os.path.basename(path)

    if client is None:
        return f"Hotel clip describing scene in {basename}"

    prompt = f"""
You are a TikTok travel editor.

Write ONE short sentence (max 150 chars) describing what this hotel/travel clip likely shows.

Filename: {basename}

No hashtags. No quotes. Return only the sentence.
""".strip()

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    desc = (resp.choices[0].message.content or "").strip()
    return desc


# -----------------------------------------
# YAML Prompt Builder
# -----------------------------------------
def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    """
    Build a prompt asking the LLM to output a valid config.yml
    using the EXACT schema required by tiktok_template.py.
    """

    lines = [
        "You are generating a config.yml for a vertical TikTok HOTEL / TRAVEL video.",
        "",
        "IMPORTANT — You MUST use this exact YAML structure:",
        "",
        "first_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <one-sentence caption>",
        "  scale: 1.0",
        "",
        "middle_clips:",
        "  - file: <filename>",
        "    start_time: 0",
        "    duration: <seconds>",
        "    text: <one-sentence caption>",
        "    scale: 1.0",
        "",
        "last_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <one-sentence caption>",
        "  scale: 1.0",
        "",
        "render:",
        "  tts_enabled: false",
        "  tts_voice: \"alloy\"",
        "  fg_scale_default: 1.0",
        "  blur_background: false",
        "",
        "cta:",
        "  enabled: false",
        "  text: \"\"",
        "  voiceover: false",
        "  duration: 3.0",
        "  position: \"bottom\"",
        "",
        "",
        "Here are your clips and their meanings:",
    ]

    for vf, a in zip(video_files, analyses):
        lines.append(f"- file: {vf}")
        lines.append(f"  analysis: {a}")

    lines.append("")
    lines.append("Return ONLY valid YAML. No backticks, no explanation.")
    lines.append("Ensure you output first_clip, middle_clips, and last_clip fields exactly.")
    
    return "\n".join(lines)



# -----------------------------------------
# Save analysis to memory + disk
# -----------------------------------------
def save_analysis_result(key: str, desc: str) -> None:
    # IMPORTANT FIX: always store basename, lowercase
    key_lower = enforce_mp4(key)

    video_analyses_cache[key_lower] = desc

    os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)
    file_path = os.path.join(ANALYSIS_CACHE_DIR, f"{key_lower}.json")

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            {"filename": key_lower, "description": desc},
            f,
            indent=2,
        )

    log_step(f"Cached analysis for {key_lower}")

def sanitize_yaml_filenames(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        return cfg

    if "first_clip" in cfg and "file" in cfg["first_clip"]:
        cfg["first_clip"]["file"] = enforce_mp4(cfg["first_clip"]["file"])

    if "middle_clips" in cfg:
        for m in cfg["middle_clips"]:
            if "file" in m:
                m["file"] = enforce_mp4(m["file"])

    if "last_clip" in cfg and "file" in cfg["last_clip"]:
        cfg["last_clip"]["file"] = enforce_mp4(cfg["last_clip"]["file"])

    return cfg


# -----------------------------------------
# Overlay / Style / Timings (LLM)
# -----------------------------------------
def _style_instructions(style: str) -> str:
    style = style.lower()
    return {
        "punchy": "Direct, energetic, short, with optional emojis.",
        "cinematic": "Atmospheric, slow, cinematic wording.",
        "descriptive": "Literal descriptions of what is on screen.",
        "influencer": "First-person energetic influencer tone.",
        "travel_blog": "Hotel travel blogger tone focused on amenities.",
    }.get(style, "Friendly hotel travel tone.")


def apply_overlay(style: str, target: str = "all", filename: Optional[str] = None) -> None:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_text = f.read()
    except Exception:
        return

    if client is None:
        return

    prompt = f"""
Rewrite ONLY the caption fields ("text") in this YAML.

Style: {style}
Instructions: {_style_instructions(style)}

Rules:
- Keep structure EXACTLY the same.
- Only modify the text fields.
- One sentence each (<150 chars).
- No hashtags.

YAML:
{yaml_text}

Return ONLY YAML.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(new_yaml)
        if not isinstance(cfg, dict):
            raise ValueError("Overlay returned invalid YAML")

        cfg = sanitize_yaml_filenames(cfg)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        log_step(f"Overlay applied: {style}")

    except Exception as e:
        logger.error(f"Overlay error: {e}")


def apply_smart_timings(pacing: str = "standard") -> None:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            yaml_text = f.read()
    except:
        return

    if client is None:
        return

    pacing_desc = (
        "Cinematic pacing: hook (2-4s), value shots (3-7s), ending (2-4s). Total <=60s."
        if pacing == "cinematic"
        else "Standard pacing: small duration improvements only."
    )

    prompt = f"""
Adjust ONLY the numeric duration fields in this config.yml.

Pacing: {pacing}
Rules:
- Keep same structure.
- Do NOT modify text or filenames.
- Durations should be natural.
- Total <= ~60 seconds.

Instructions: {pacing_desc}

YAML:
{yaml_text}

Return ONLY YAML.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(new_yaml)
        if not isinstance(cfg, dict):
            raise ValueError("Timings returned invalid YAML")

        cfg = sanitize_yaml_filenames(cfg)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        log_step(f"Smart timings applied ({pacing})")

    except Exception as e:
        logger.error(f"Timings error: {e}")
