# tiktok_assistant.py
import os
import logging
import tempfile
import subprocess
import json
from typing import Dict, List, Optional

import boto3
import yaml
from openai import OpenAI

from assistant_log import log_step

# Use config_path/edit_video/video_folder from your existing renderer
from tiktok_template import config_path, edit_video, video_folder

logger = logging.getLogger(__name__)

# -----------------------------
# OpenAI client / model
# -----------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

if not api_key:
    logger.warning("OPENAI_API_KEY / open_ai_api_key not set; LLM features may fail.")

TEXT_MODEL = "gpt-4.1-mini"

# -----------------------------
# S3 CONFIG
# -----------------------------
S3_BUCKET_NAME = os.environ.get("S3_BUCKET_NAME") or os.environ.get("AWS_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME (or AWS_BUCKET_NAME) environment variable is required")

S3_REGION = os.environ.get("S3_REGION", "us-east-2")

RAW_PREFIX = "raw_uploads/"
PROCESSED_PREFIX = "processed/"
EXPORT_PREFIX = "exports/"

s3 = boto3.client("s3", region_name=S3_REGION)

# -----------------------------
# Analysis cache
# -----------------------------
video_analyses_cache: Dict[str, str] = {}

ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)


# -----------------------------
# S3 helpers
# -----------------------------
def list_videos_from_s3() -> List[str]:
    """
    Return list of .mp4/.mov/.avi/.m4v keys under raw_uploads/.
    """
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)
    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    log_step(f"S3 RAW KEYS: {keys}")

    files: List[str] = []
    for key in keys:
        ext = os.path.splitext(key)[1].lower()
        if ext in [".mp4", ".mov", ".avi", ".m4v"]:
            files.append(key)
    return files


def upload_raw_file(file_storage) -> str:
    """
    Upload a browser-uploaded file (Werkzeug FileStorage) into S3 RAW_PREFIX.
    Returns the S3 key used.
    """
    filename = os.path.basename(file_storage.filename or "").strip()
    if not filename:
        raise ValueError("Empty filename")

    # Simple "sanitization"
    filename = filename.replace(" ", "_")
    key = f"{RAW_PREFIX}{filename}"

    log_step(f"Uploading {filename} to s3://{S3_BUCKET_NAME}/{key} ...")
    s3.upload_fileobj(file_storage, S3_BUCKET_NAME, key)
    log_step(f"Uploaded to {key}")
    return key


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
        log_step(f"[S3 DOWNLOAD ERROR] Could not download {key}")
        log_step(f"S3 Exception: {e}")
        return None


# -----------------------------
# Video normalization for analysis
# -----------------------------
def normalize_video(src: str, dst: str) -> None:
    """
    Normalize a clip into a TikTok-friendly portrait format for analysis:
    - Force 1080px width, preserve aspect ratio
    - Ensure yuv420p pixel format
    - Strip rotation metadata

    This does NOT replace your final renderer; it just makes clips consistent
    for LLM analysis and for your tiktok_template pipeline.
    """

    # 🔥 FORCE LOWERCASE OUTPUT ALWAYS
    dst = dst.lower()

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

    log_step(f"[FFMPEG] Normalizing video {src} -> {dst}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        if proc.stderr:
            log_step(f"[FFMPEG STDERR for {src}] {proc.stderr.splitlines()[0]}")
    except subprocess.CalledProcessError as e:
        log_step(f"[FFMPEG ERROR] {e.stderr or e.stdout}")
        raise


# -----------------------------
# Analysis helpers (LLM)
# -----------------------------
def analyze_video(path: str) -> str:
    """
    Use LLM to generate a hotel/travel-style one-sentence analysis for this clip.
    """
    basename = os.path.basename(path)

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

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    desc = (resp.choices[0].message.content or "").strip()
    logger.info("LLM analysis for %s: %s", basename, desc)
    return desc


def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    """
    Build a prompt asking the LLM to output a full YAML storyboard config.yml.
    """
    if not video_files:
        return "Generate an empty config.yml with first_clip, middle_clips, last_clip."

    lines: List[str] = [
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
        "- Last clip: recap + soft CTA (e.g. 'Would you stay here?').",
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
        "  blur_background: true",
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
        lines.append(f"- clip_{idx + 1}:")
        lines.append(f"    file: {vf}")
        lines.append(f"    analysis: {a or 'No analysis.'}")

    lines.append("")
    lines.append("Return ONLY valid YAML for config.yml. Do not include any backticks or commentary.")
    return "\n".join(lines)


# -----------------------------
# Overlay / timings (LLM)
# -----------------------------
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
        return "Hotel-review travel blogger tone. Focus on room type, amenities, views, location, and why it's a great stay."
    return "Friendly, hotel-focused travel review tone with light influencer enthusiasm."


def apply_overlay(style: str, target: str = "all", filename: Optional[str] = None) -> None:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
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
{current_yaml}

Return ONLY the updated YAML. No extra commentary.
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

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        logger.info("apply_overlay: captions updated with style '%s'", style)
        log_step(f"Overlay applied with style: {style}")
    except Exception as e:
        logger.error(f"apply_overlay LLM call failed: {e}")


def apply_smart_timings(pacing: str = "standard") -> None:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
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
- Work on the YAML below.
- KEEP THE SAME STRUCTURE and keys.
- Only change numeric "duration" values inside:
  - first_clip
  - each item in middle_clips
  - last_clip
- If a duration is missing, add a reasonable one.
- Durations must be positive numbers (seconds).
- Try to keep the total video length <= 60 seconds.
- Do NOT change text, file names, or other fields.

Current YAML:
{current_yaml}

Return ONLY the updated YAML. No extra commentary.
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

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        logger.info("apply_smart_timings: timings updated (pacing=%s)", pacing)
        log_step(f"Smart timings applied (pacing={pacing}).")
    except Exception as e:
        logger.error(f"apply_smart_timings LLM call failed: {e}")


# -----------------------------
# Cache helper
# -----------------------------
def save_analysis_result(key: str, desc: str) -> None:
    """
    Cache analysis results BOTH in memory and to disk so API can read them.
    """
    try:
        key_lower = key.lower()

        # in-memory
        video_analyses_cache[key_lower] = desc

        # on disk
        os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)
        file_path = os.path.join(ANALYSIS_CACHE_DIR, f"{key_lower}.json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "filename": key_lower,
                    "description": desc,
                },
                f,
                indent=2,
            )

        log_step(f"Cached analysis for {key_lower} (memory + file).")
    except Exception as e:
        logger.error(f"save_analysis_result failed for {key}: {e}")
