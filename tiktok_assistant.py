# tiktok_assistant.py
import os
import logging
import tempfile
from typing import Dict, List, Optional
import json
import boto3
import yaml
from openai import OpenAI

from tiktok_template import normalize_video_ffmpeg, config_path, client as template_client
from assistant_log import log_step

# -------------------------------------------------
# Logging
# -------------------------------------------------
logger = logging.getLogger(__name__)

# -------------------------------------------------
# OpenAI client / model
# -------------------------------------------------
# Re-use client from tiktok_template if available, otherwise create here.
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = template_client or (OpenAI(api_key=api_key) if api_key else None)

if not api_key:
    logger.warning("OPENAI_API_KEY / open_ai_api_key not set; LLM features may fail.")

TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------------------------
# S3 CONFIG
# -------------------------------------------------
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME") or os.environ.get("AWS_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required")

S3_REGION = os.environ.get("S3_REGION", "us-east-2")

# Public style A: https://BUCKET.s3.amazonaws.com/...
S3_PUBLIC_BASE = f"https://{S3_BUCKET_NAME}.s3.amazonaws.com"

RAW_PREFIX = "raw_uploads/"
PROCESSED_PREFIX = "processed/"
EXPORT_PREFIX = "exports/"

# Create S3 client
s3 = boto3.client("s3", region_name=S3_REGION)

# -------------------------------------------------
# GLOBAL ANALYSIS CACHE
# -------------------------------------------------
video_analyses_cache: Dict[str, str] = {}

# -------------------------------------------------
# S3 HELPERS
# -------------------------------------------------
def ensure_prefix_folders() -> None:
    """
    Optionally ensure the logical S3 prefixes exist.
    (Not strictly needed for S3, but we can put a tiny marker if desired.)
    """
    for prefix in (RAW_PREFIX, PROCESSED_PREFIX, EXPORT_PREFIX):
        # S3 doesn't have real folders, so we don't *need* to create them.
        # This is a no-op placeholder in case you want to add behavior later.
        pass


def list_videos_from_s3() -> List[str]:
    """
    Return list of .mp4/.mov/.avi/.m4v keys under raw_uploads/.
    """
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)
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
        logger.error(f"Failed to download {key} from S3: {e}")
        return None


def normalize_video(src: str, dst: str) -> None:
    """
    Thin wrapper around your existing normalize_video_ffmpeg helper.
    """
    normalize_video_ffmpeg(src, dst)


def move_all_raw_to_processed() -> None:
    """
    Move ALL files under raw_uploads/ → processed/.
    Safe to call after successful export.
    """
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
            logger.error(f"Failed to move {key} to processed/: {e}")


ANALYSIS_CACHE_DIR = "video_analysis_cache"

def save_analysis_result(key: str, desc: str) -> None:
    """
    Cache analysis results BOTH in memory and to disk so API can read them.
    """
    # normalize key
    key_lower = key.lower()

    # update in-memory cache
    video_analyses_cache[key_lower] = desc

    # ensure folder exists
    os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)

    # write to file
    file_path = os.path.join(ANALYSIS_CACHE_DIR, f"{key_lower}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump({
            "filename": key_lower,
            "description": desc
        }, f, indent=2)

    log_step(f"Cached analysis for {key_lower} (memory + file).")

# -------------------------------------------------
# DEBUG HELPER (simple)
# -------------------------------------------------
def debug_video_dimensions(path: str) -> None:
    logger.info("debug_video_dimensions stub called for %s", path)

# -------------------------------------------------
# LLM HELPERS
# -------------------------------------------------
def analyze_video(path: str) -> str:
    """
    Use LLM to generate a hotel/travel-style one-sentence analysis for this clip.
    """
    basename = os.path.basename(path)

    # Fallback if client is not available
    if client is None:
        return f"Hotel / travel clip: describe views, room, or amenities in {basename}."

    prompt = f"""
You are helping script a vertical TikTok about a HOTEL or TRAVEL experience.

The raw clip filename is: {basename}

Assume this clip is part of a hotel-focused travel reel: rooms, lobby, pool,
views, restaurants, etc.

Write ONE short sentence (max ~150 characters) describing what the viewer
likely sees AND the main selling point of this clip.

Tone: natural, helpful travel review. No hashtags. No quotation marks.
Return ONLY the sentence.
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        desc = (resp.choices[0].message.content or "").strip()
        logger.info("LLM analysis for %s: %s", basename, desc)
        return desc
    except Exception as e:
        logger.error(f"analyze_video LLM call failed for {basename}: {e}")
        # Fallback generic text
        return f"Hotel / travel clip showcasing views or amenities in {basename}."


def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    """
    Build a prompt asking the LLM to output a full YAML storyboard config.yml.
    """
    if not video_files:
        return "Generate an empty YAML config.yml with first_clip, middle_clips, last_clip."

    lines = [
        "You are a TikTok editor for HOTEL / TRAVEL review videos.",
        "",
        "You will generate a YAML config.yml for a vertical TikTok.",
        "The video is about a hotel stay: rooms, lobby, pool, views, food, etc.",
        "",
        "Requirements:",
        "- Use the exact 'file' values I give (filenames).",
        "- Use the provided analyses as hints for what each clip shows.",
        "- Each caption must be ONE sentence, <= 150 characters.",
        "- First clip: strong hook.",
        "- Middle clips: show value and visuals.",
        "- Last clip: recap + soft CTA (eg. 'Would you stay here?').",
        "- Keep total video length ideally <= 60 seconds.",
        "",
        "Use this EXACT schema:",
        "",
        "first_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "  scale: 1.0",
        "",
        "middle_clips:",
        "  - file: <filename>",
        "    start_time: 0",
        "    duration: <seconds>",
        "    text: <caption>",
        "    scale: 1.0",
        "",
        "last_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "  scale: 1.0",
        "",
        "music:",
        '  style: \"chill travel\"',
        '  mood: \"uplifting\"',
        "  volume: 0.25",
        "",
        "render:",
        "  tts_enabled: false",
        '  tts_voice: \"alloy\"',
        "  fg_scale_default: 1.0",
        "",
        "cta:",
        "  enabled: false",
        '  text: \"\"',
        "  voiceover: false",
        "  duration: 3.0",
        '  position: \"bottom\"',
        "",
        "Now here are the clips and analyses:",
    ]

    for idx, (vf, a) in enumerate(zip(video_files, analyses)):
        lines.append(f"- clip_{idx+1}:")
        lines.append(f"    file: {vf}")
        lines.append(f"    analysis: {a or 'No analysis.'}")

    lines.append("")
    lines.append("Return ONLY valid YAML for config.yml. No backticks, no prose.")
    return "\n".join(lines)


def _style_instructions(style: str) -> str:
    style = (style or "").lower()
    if style == "punchy":
        return "Short, punchy hooks, one sentence, direct and energetic. A couple of emojis are okay."
    if style == "cinematic":
        return "Cinematic, atmospheric travel storytelling. Focus on mood and imagery. One sentence per clip, minimal emojis."
    if style == "descriptive":
        return "Clear, descriptive captions that literally describe what is on screen with a slight travel-review tone."
    if style == "influencer":
        return "First-person, enthusiastic influencer tone ('I', 'you'), high energy, social-media vibe, a few emojis."
    if style == "travel_blog":
        return ("Hotel-review travel blogger tone. Focus on room type, amenities, views, location, and why it's a great stay.")
    # default
    return "Friendly, hotel-focused travel review tone with light influencer enthusiasm."


def apply_overlay(style: str, target: str = "all", filename: Optional[str] = None) -> None:
    """
    Use LLM to rewrite caption texts in config.yml according to the selected style.
    """
    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception as e:
        logger.error(f"apply_overlay: failed to read config.yml: {e}")
        return

    if client is None:
        logger.warning("apply_overlay: no OpenAI client; skipping LLM overlay.")
        return

    instructions = _style_instructions(style)

    prompt = f"""
    You are rewriting captions for a HOTEL / TRAVEL TikTok.

    Caption style: {style}
    Style instructions: {instructions}

    Rules:
    - Work on the YAML config.yml below.
    - KEEP THE SAME STRUCTURE and keys.
    - Do NOT add or remove clips.
    - Only change the values of fields named "text" in:
    - first_clip
    - each item in middle_clips
    - last_clip
    - Each caption must be ONE sentence and <= 150 characters.
    - Assume the content is hotel-focused (property, rooms, views, pool, restaurants, location).
    - Make captions natural, not spammy. No hashtags.

    Current YAML:
    ```yaml
    {current_yaml}
    Return ONLY the updated YAML. No backticks, no explanation.
    """.strip()
        
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(new_yaml) or {}
        if not isinstance(cfg, dict):
            raise ValueError("Overlay LLM did not return a dict config.")

        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        logger.info("apply_overlay: captions updated with style '%s'", style)
        log_step(f"Overlay applied with style: {style}")
    except Exception as e:
        logger.error(f"apply_overlay LLM call failed: {e}")
    
def apply_smart_timings(pacing: str = "standard") -> None:
    """
    Use LLM to adjust clip durations in config.yml.
    pacing="standard": small adjustments.
    pacing="cinematic": more aggressive hook + flow.
    """
    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception as e:
        logger.error(f"apply_smart_timings: failed to read config.yml: {e}")
        return

    if client is None:
        logger.warning("apply_smart_timings: no OpenAI client; skipping LLM timings.")
        return

    if pacing == "cinematic":
        pacing_desc = (
            "Cinematic smart pacing: strong first hook (2–4s), flowing middle clips (3–7s), "
            "and a tight ending (2–4s). Keep total length ideally <= 60 seconds."
        )
    else:
        pacing_desc = (
            "Standard pacing: only make small improvements to timing. "
            "Respect current durations but nudge them to feel more natural."
        )

        prompt = f"""
            You are adjusting clip durations for a HOTEL / TRAVEL TikTok config.yml.

            Pacing mode: {pacing}
            Instructions: {pacing_desc}

            Rules:

            Work on the YAML below.

            KEEP THE SAME STRUCTURE and keys.

            Only change numeric "duration" values inside:

            first_clip

            each item in middle_clips

            last_clip

            If a duration is missing, add a reasonable one.

            Durations must be positive numbers (seconds).

            Try to keep the total video length <= 60 seconds.

            Do NOT change text, file names, or other fields.

            Current YAML:
            {current_yaml}

            Return ONLY the updated YAML. No backticks, no explanation.
            """.strip()
        
        try:
            resp = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            new_yaml = (resp.choices[0].message.content or "").strip()
            new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

            cfg = yaml.safe_load(new_yaml) or {}
            if not isinstance(cfg, dict):
                raise ValueError("Smart timings LLM did not return a dict config.")

            with open(config_path, "w") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)

            logger.info("apply_smart_timings: timings updated (pacing=%s)", pacing)
            log_step(f"Smart timings applied (pacing={pacing}).")
        except Exception as e:
            logger.error(f"apply_smart_timings LLM call failed: {e}")

def save_from_raw_yaml(*args, **kwargs):
    logger.info("save_from_raw_yaml stub called (unused)")