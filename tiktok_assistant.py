# tiktok_assistant.py — MOV/MP4 SAFE VERSION

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
from tiktok_template import config_path, edit_video, video_folder

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
RAW_PREFIX = os.getenv("S3_RAW_PREFIX", "raw_uploads")
RAW_PREFIX = RAW_PREFIX.rstrip("/") + "/"

EXPORT_PREFIX = os.getenv("S3_EXPORT", "exports/")

# -----------------------------------------
# Load AWS credentials (Render ENV VARS)
# -----------------------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise RuntimeError(
        "Missing AWS credentials! You MUST set AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY in Render."
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
def generate_signed_download_url(key: str, expires_in: int = 3600):
    return s3.generate_presigned_url(
        ClientMethod='get_object',
        Params={
            'Bucket': S3_BUCKET_NAME,
            'Key': key,
            'ResponseContentDisposition': 'attachment; filename="export.mp4"',
            'ResponseContentType': 'video/mp4'
        },
        ExpiresIn=expires_in
    )

def list_videos_from_s3() -> List[str]:
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=RAW_PREFIX)

    keys = [obj["Key"] for obj in resp.get("Contents", [])]
    log_step(f"S3 RAW KEYS: {keys}")

    files: List[str] = []
    for key in keys:
        ext = os.path.splitext(key)[1].lower()
        if ext in [".mp4", ".mov", ".avi", ".m4v"]:
            files.append(key)
    return files

# -----------------------------------------
# Normalize to MP4
# -----------------------------------------

def normalize_to_mp4(input_path, output_path):
    """
    Safely convert ANY uploaded video into clean H.264 MP4 + moov atom.
    """
    import subprocess
    from assistant_log import log_step

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", "scale=1080:-2,setsar=1",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path
    ]

    log_step(f"[NORMALIZE] Converting upload -> {output_path}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path


# -----------------------------------------
# Upload Order Tracking (S3 JSON)
# -----------------------------------------

UPLOAD_ORDER_KEY = RAW_PREFIX + "order.json"


def load_upload_order() -> List[str]:
    """Load upload order from S3. Returns [] if none exists."""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=UPLOAD_ORDER_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("order", [])
    except Exception:
        return []


def save_upload_order(order: List[str]) -> None:
    """Save upload order to S3 as a JSON file."""
    try:
        payload = json.dumps({"order": order}, indent=2).encode("utf-8")
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=UPLOAD_ORDER_KEY,
            Body=payload,
            ContentType="application/json"
        )
    except Exception as e:
        log_step(f"[UPLOAD_ORDER] Failed to save order.json: {e}")



def upload_raw_file(file):
    """
    Upload handler:
    - Save temp file
    - Normalize to safe MP4
    - Upload normalized version to S3
    - Store normalized copy locally (tik_tok_downloads)
    """

    from assistant_log import log_step
    import tempfile, os, subprocess

    # 1. Save upload to temp
    tmp = tempfile.NamedTemporaryFile(delete=False).name
    file.save(tmp)
    log_step(f"[UPLOAD] Saved temp upload: {tmp}")

    # Ensure tik_tok_downloads exists
    os.makedirs(video_folder, exist_ok=True)

    # 2. Normalize to mp4 (guarantees moov atom)
    filename = sanitize_yaml_filenames(file.filename)
    base, _ = os.path.splitext(filename)
    normalized_name = f"{base}.mp4"
    local_norm = os.path.join(video_folder, normalized_name)

    normalize_to_mp4(tmp, local_norm)

    # 3. Upload normalized video to S3
    key = f"{RAW_PREFIX}/{normalized_name}"
    log_step(f"[UPLOAD] Uploading normalized -> {S3_BUCKET_NAME}/{key}")
    s3.upload_file(local_norm, S3_BUCKET_NAME, key)

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
    """
    Normalize the video for analysis/export while preserving the original extension.
    E.g., if src is .mov, dst will also be .mov.
    """
    base = os.path.splitext(dst)[0]
    src_ext = os.path.splitext(src)[1] or ".mp4"
    final_dst = f"{base}{src_ext}".lower()

    os.makedirs(os.path.dirname(final_dst), exist_ok=True)

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
        final_dst,
    ]

    log_step(f"[FFMPEG] Normalizing {src} → {final_dst}")
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
    Filenames are used as-is (no forced .mp4).
    """

    lines: List[str] = [
        "You are generating a config.yml for a vertical TikTok HOTEL / TRAVEL video.",
        "",
        "IMPORTANT — You MUST use this exact YAML structure:",
        "Filenames MUST be returned exactly as provided (no renaming, no lowercase, no extension changes).",
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
        '  tts_voice: "alloy"',
        "  fg_scale_default: 1.0",
        "",
        "# ------------------------------",
        "# Background Music System",
        "# ------------------------------",
        "music:",
        "  enabled: false",
        "  file: ''",
        "  volume: 0.25",
        "",
        "cta:",
        "  enabled: false",
        '  text: ""',
        "  voiceover: false",
        "  duration: 3.0",
        '  position: "bottom"',
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
    """
    Save analysis keyed by the basename of the video (lowercased),
    preserving the original extension (.mov, .mp4, etc.).
    """
    base = os.path.basename(key)
    key_norm = base.lower()

    video_analyses_cache[key_norm] = desc

    os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)
    file_path = os.path.join(ANALYSIS_CACHE_DIR, f"{key_norm}.json")

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(
            {"filename": key_norm, "description": desc},
            f,
            indent=2,
        )

    log_step(f"Cached analysis for {key_norm}")


def _normalize_yaml_filename(name: str) -> str:
    """
    Normalize filenames in YAML to basename + lowercase,
    but DO NOT change the extension.
    """
    if not name:
        return name
    base = os.path.basename(name)
    return base.lower()


def sanitize_yaml_filenames(cfg: dict) -> dict:
    """
    Ensure YAML filenames are in a consistent form (basename + lowercase)
    so they match the normalized video filenames in video_folder.
    No extension conversion is done here.
    """
    if not isinstance(cfg, dict):
        return cfg

    if "first_clip" in cfg and isinstance(cfg["first_clip"], dict):
        if "file" in cfg["first_clip"]:
            cfg["first_clip"]["file"] = _normalize_yaml_filename(cfg["first_clip"]["file"])

    if "middle_clips" in cfg and isinstance(cfg["middle_clips"], list):
        for m in cfg["middle_clips"]:
            if isinstance(m, dict) and "file" in m:
                m["file"] = _normalize_yaml_filename(m["file"])

    if "last_clip" in cfg and isinstance(cfg["last_clip"], dict):
        if "file" in cfg["last_clip"]:
            cfg["last_clip"]["file"] = _normalize_yaml_filename(cfg["last_clip"]["file"])

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
        "ai_recommended": (
            "AI recommended hotel/travel captions: "
            "for each clip, pick the best mix of punchy hook, influencer tone, "
            "or cinematic vibe based on the existing text and clip order. "
            "Focus on scroll-stopping hooks, clarity, and getting the viewer to keep watching."
        ),
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
    except Exception:
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