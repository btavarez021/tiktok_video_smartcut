# tiktok_assistant.py
import os
import logging
import tempfile
from typing import Dict, List, Optional

import boto3
import yaml

from tiktok_template import normalize_video_ffmpeg, config_path, client
from assistant_log import log_step

# -------------------------------------------------
# OpenAI model name used by assistant_api + overlay
# -------------------------------------------------
TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------------------------
# S3 CONFIG
# -------------------------------------------------
# Expect S3_BUCKET_NAME and S3_REGION in environment on Render
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required")

S3_REGION = os.environ.get("S3_REGION", "us-east-2")

# Public base URL (bucket must allow public read for exports/)
S3_PUBLIC_BASE = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com"

# Prefixes
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


def list_raw_s3_videos() -> List[str]:
    """
    Same as list_videos_from_s3, kept for compatibility.
    """
    return list_videos_from_s3()


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
            logging.error(f"Failed to move {key} to processed/: {e}")


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
    Cache analysis results in memory (by basename) and log them.
    """
    video_analyses_cache[key] = desc
    log_step(f"Cached analysis for {key}.")


# -------------------------------------------------
# DEBUG HELPER (optional)
# -------------------------------------------------
def debug_video_dimensions(path: str) -> None:
    logging.info("debug_video_dimensions stub called for %s", path)


# -------------------------------------------------
# LLM-POWERED ANALYSIS
# -------------------------------------------------
def analyze_video(path: str) -> str:
    """
    Use LLM to generate a travel/hotel-style analysis for this clip.
    We don't "see" the video, but we assume hotel/travel content.
    """
    basename = os.path.basename(path)

    prompt = f"""
You are helping script a vertical TikTok about a HOTEL or TRAVEL experience.

The raw clip filename is: {basename}

Assume this clip is part of a hotel-focused travel reel: rooms, lobby, pool,
views, restaurants, etc.

Write 1–2 short sentences (MAX ~220 characters total) describing what the
viewer likely sees AND the main selling point of this clip.

Tone: travel review + influencer enthusiasm + hotel-focused.
No hashtags. No quotes. No emojis.
Return ONLY the 1–2 sentence description.
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
        # Fallback generic text
        return f"Travel/hotel clip: highlight views, room details, and amenities in {basename}."


# -------------------------------------------------
# YAML PROMPT BUILDER
# -------------------------------------------------
def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    """
    Build a prompt asking the LLM to output a full YAML storyboard.
    video_files: basenames of local clips (same as in config.yml 'file' fields).
    analyses: per-clip description from analyze_video.
    """
    if not video_files:
        return "Generate an empty YAML config.yml."

    lines = [
        "You are a TikTok editor for HOTEL / TRAVEL review videos.",
        "",
        "You will generate a YAML config.yml for a vertical TikTok.",
        "Clip tone: travel review, influencer enthusiasm, hotel-focused.",
        "",
        "Requirements:",
        "- Use the exact 'file' values I give (they are filenames).",
        "- Choose engaging captions based on the analyses.",
        "- Each caption text <= 150 characters.",
        "- First clip: strong hook; middle: value/visuals; last: recap + soft CTA.",
        "- Total video length ideally <= 60 seconds.",
        "- Use this schema exactly:",
        "",
        "first_clip:",
        "  file: ...",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "  scale: 1.0",
        "",
        "middle_clips:",
        "  - file: ...",
        "    start_time: 0",
        "    duration: <seconds>",
        "    text: <caption>",
        "    scale: 1.0",
        "",
        "last_clip:",
        "  file: ...",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "  scale: 1.0",
        "",
        "music:",
        '  style: "chill travel"',
        '  mood: "uplifting"',
        "  volume: 0.25",
        "",
        "render:",
        '  tts_enabled: false',
        '  tts_voice: "alloy"',
        "  fg_scale_default: 1.0",
        "",
        "cta:",
        "  enabled: false",
        '  text: ""',
        "  voiceover: false",
        "  duration: 3.0",
        '  position: "bottom"',
        "",
        "Here are the clips and analyses:",
    ]

    for idx, (vf, a) in enumerate(zip(video_files, analyses)):
        lines.append(f"- clip_{idx + 1}:")
        lines.append(f"    file: {vf}")
        lines.append(f"    analysis: {a or 'No analysis.'}")

    lines.append("")
    lines.append("Return ONLY valid YAML for config.yml. No backticks, no prose.")

    return "\n".join(lines)


# -------------------------------------------------
# CAPTION STYLE OVERLAY (chips)
# -------------------------------------------------
def _style_instructions(style: str) -> str:
    """
    Map style keys from the UI chips to natural-language instructions.
    """
    style = (style or "").lower()
    if style == "punchy":
        return "Short, punchy hooks, 1–2 sentences, direct and energetic. A couple of emojis are okay."
    if style == "cinematic":
        return "Cinematic, atmospheric travel storytelling. Focus on mood, imagery, and flow. Minimal emojis."
    if style == "descriptive":
        return "Clear, descriptive captions literally describing what is on screen with a slight travel-review tone."
    if style == "influencer":
        return "First-person, enthusiastic influencer tone ('I', 'you'), high energy, social-media vibe, a few emojis."
    if style == "travel_blog":
        return (
            "Hotel-review travel blogger tone. Focus on property, room type, "
            "amenities, views, and why it's a great stay. Friendly, helpful."
        )
    # default
    return "Friendly, hotel-focused travel review tone with light influencer enthusiasm."


def apply_overlay(style: str, target: str = "all", filename: Optional[str] = None) -> None:
    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception as e:
        logging.error(f"apply_overlay: failed to read config.yml: {e}")
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
        - Each caption <= 150 characters.
        - Assume the content is hotel-focused: property, rooms, views, pool, restaurants, location.
        - Make captions natural, not spammy. No hashtags.

        Current YAML:
        ```yaml
        {current_yaml}
        Return ONLY the updated YAML. No backticks, no explanation.
        """.strip()
    ...

    
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        new_yaml = (resp.choices[0].message.content or "").strip()
        # Strip code fences defensively
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(new_yaml) or {}
        if not isinstance(cfg, dict):
            raise ValueError("Overlay LLM did not return a dict config.")

        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        logging.info("apply_overlay: captions updated with style '%s'", style)
        log_step(f"Overlay applied with style: {style}")
    except Exception as e:
        logging.error(f"apply_overlay LLM call failed: {e}")

#-------------------------------------------------
#SMART TIMINGS
#-------------------------------------------------

def apply_smart_timings(pacing: str = "standard") -> None:
    """
    Use LLM to adjust clip durations in config.yml.
    - pacing="standard": small tweaks.
    - pacing="cinematic": stronger smart pacing (hooks & flow).
    """
    try:
        with open(config_path, "r") as f:
            current_yaml = f.read()
    except Exception as e:
        logging.error(f"apply_smart_timings: failed to read config.yml: {e}")
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

        logging.info("apply_smart_timings: timings updated (pacing=%s)", pacing)
        log_step(f"Smart timings applied (pacing={pacing}).")
    except Exception as e:
        logging.error(f"apply_smart_timings LLM call failed: {e}")

#-------------------------------------------------
#PLACEHOLDER (unused but kept for import)
#-------------------------------------------------

def save_from_raw_yaml(*args, **kwargs):
    logging.info("save_from_raw_yaml stub called (unused)")