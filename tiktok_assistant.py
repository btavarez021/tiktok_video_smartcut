# tiktok_assistant.py
import os
import logging
import tempfile
import json
from typing import List, Dict, Optional
from tiktok_template import config_path

import boto3
import yaml
from openai import OpenAI

from assistant_log import log_step
from tiktok_template import normalize_video_ffmpeg, client as template_client

logger = logging.getLogger(__name__)

# ============================================================================
# OPENAI CLIENT / MODEL
# ============================================================================
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = template_client or (OpenAI(api_key=api_key) if api_key else None)

if not api_key:
    logger.warning("OPENAI_API_KEY not set. LLM calls will fail.")

TEXT_MODEL = "gpt-4.1-mini"


# ============================================================================
# S3 CONFIG
# ============================================================================
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME") or os.environ.get("AWS_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required")

S3_REGION = os.environ.get("S3_REGION", "us-east-2")
s3 = boto3.client("s3", region_name=S3_REGION)

RAW_PREFIX = "raw_uploads/"
PROCESSED_PREFIX = "processed/"
EXPORT_PREFIX = "exports/"


# ============================================================================
# ANALYSIS CACHE
# ============================================================================
video_analyses_cache: Dict[str, str] = {}

ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)


# ============================================================================
# S3 HELPERS
# ============================================================================

def load_all_analysis_results() -> Dict[str, str]:
    """
    Load all cached analysis JSON files from video_analysis_cache folder
    and merge them with in-memory cache.
    """
    results = {}

    # Load in-memory cache first
    try:
        for k, v in video_analyses_cache.items():
            results[k] = v
    except Exception:
        pass

    # Load on-disk files
    try:
        os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)
        for filename in os.listdir(ANALYSIS_CACHE_DIR):
            if filename.endswith(".json"):
                path = os.path.join(ANALYSIS_CACHE_DIR, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        fname = data.get("filename")
                        desc = data.get("description")
                        if fname and desc:
                            results[fname] = desc
                except Exception:
                    continue
    except Exception:
        pass

    return results

def list_videos_from_s3() -> List[str]:
    """
    Return a list of video files under raw_uploads/.
    """
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)
    files = []

    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    print("S3 RAW KEYS:", keys)

    for key in keys:
        ext = os.path.splitext(key)[1].lower()
        if ext in [".mp4", ".mov", ".avi", ".m4v"]:
            files.append(key)

    return files


def download_s3_video(key: str) -> Optional[str]:
    """
    Download a video from S3 → temporary file and return its path.
    """
    ext = os.path.splitext(key)[1] or ".mp4"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)

    try:
        s3.download_fileobj(S3_BUCKET_NAME, key, tmp)
        tmp.close()
        return tmp.name
    except Exception as e:
        log_step(f"S3 DOWNLOAD ERROR: {key} → {e}")
        return None


# ============================================================================
# SAVE ANALYSIS RESULT (DISK + MEMORY)
# ============================================================================
def save_analysis_result(key: str, desc: str):
    key_lower = key.lower()
    video_analyses_cache[key_lower] = desc

    os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)
    file_path = os.path.join(ANALYSIS_CACHE_DIR, f"{key_lower}.json")

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            {"filename": key_lower, "description": desc},
            f,
            indent=2,
        )

    log_step(f"Cached analysis → {key_lower}")


# ============================================================================
# LLM ANALYSIS
# ============================================================================
def analyze_video(path: str) -> str:
    """
    Generates one-sentence hotel/travel description using LLM.
    """
    base = os.path.basename(path).lower()

    if client is None:
        return f"Travel clip: {base}"

    prompt = f"""
You are helping describe a HOTEL or TRAVEL TikTok video.

Filename: {base}

Write **ONE** short sentence (~150 chars) describing what the viewer sees
and its main hotel/travel selling point.

Tone: natural, human.
Return ONLY the sentence.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        text = (resp.choices[0].message.content or "").strip()
        logger.info(f"LLM analysis for {base}: {text}")
        return text

    except Exception as e:
        logger.error(f"LLM failed for {base}: {e}")
        return f"Travel clip showing hotel visuals: {base}"


# ============================================================================
# YAML STORYBOARD PROMPT
# ============================================================================
def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    lines = [
        "You are generating a YAML storyboard for a HOTEL / TRAVEL TikTok.",
        "",
        "Rules:",
        "- Each caption must be ≤ 150 chars",
        "- 1 sentence per caption",
        "- schema: first_clip, middle_clips[], last_clip, music, render, cta",
        "",
        "CLIPS:",
    ]

    for vf, analysis in zip(video_files, analyses):
        lines.append(f"- file: {vf}")
        lines.append(f"  analysis: {analysis}")
        lines.append("")

    lines.append("Return ONLY valid YAML.")
    return "\n".join(lines)


# ============================================================================
# OVERLAY / STYLE TRANSFORM
# ============================================================================
def _style_instructions(style: str) -> str:
    style = style.lower()

    mapping = {
        "punchy": "Short, punchy, energetic captions.",
        "cinematic": "Atmospheric, emotional, cinematic travel tone.",
        "descriptive": "Clear, literal descriptions of what's on screen.",
        "influencer": "High-energy influencer tone, conversational.",
        "travel_blog": "Hotel/travel blogger tone: amenities, room, views.",
    }

    return mapping.get(style, "Friendly travel review tone.")


def apply_overlay(style: str, target="all", filename=None):
    """
    Rewrites caption text in config.yml using the chosen style.
    """
    from tiktok_template import config_path

    if client is None:
        logger.warning("No LLM client; overlay skipped.")
        return

    try:
        with open(config_path, "r") as f:
            current = f.read()
    except Exception as e:
        logger.error(f"Could not read config.yml: {e}")
        return

    instructions = _style_instructions(style)

    prompt = f"""
        Rewrite all 'text' fields in the YAML config below.

        Style: {style}
        Instructions: {instructions}

        Rules:
        - Keep structure identical
        - Only modify the text values
        - 1 sentence each, ≤ 150 chars

        YAML:
        ```yaml
        {current}

        Return ONLY updated YAML.
        """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )

        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "")

        with open(config_path, "w") as f:
            f.write(new_yaml)

        log_step(f"Overlay applied ({style})")

    except Exception as e:
        logger.error(f"Overlay failed: {e}")

#============================================================================
#SMART TIMINGS
#============================================================================

def apply_smart_timings(pacing="standard"):

    try:
        with open(config_path, "r") as f:
            current = f.read()
    except Exception as e:
        logger.error(f"Read config.yml failed: {e}")
        return

    if client is None:
        logger.warning("No LLM: timings skipped.")
        return

    pacing_desc = (
        "Cinematic pacing: strong hook 2–4s, flowing middle 3–7s, short ending."
        if pacing == "cinematic"
        else "Standard pacing: small improvements only."
    )

    prompt = f"""
        Adjust durations in the YAML.

        Pacing: {pacing}
        Description: {pacing_desc}

        Rules:

        Modify only 'duration' values

        Keep structure identical

        Keep total ≤ 60 seconds ideally

        YAML:
        {current}
        Return ONLY updated YAML.
        """
    
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )

        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "")

        with open(config_path, "w") as f:
            f.write(new_yaml)

        log_step(f"Timings updated ({pacing})")

    except Exception as e:
        logger.error(f"Timings failed: {e}")
        


