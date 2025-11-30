# tiktok_assistant.py — MOV/MP4 SAFE VERSION (Option A)
# - No upload_raw_file here
# - No video_folder / edit_video imports
# - S3 config comes from s3_config
# - Only: analysis, YAML prompt, overlay, timings, filename sanitation

import os
import logging
import tempfile
import subprocess
import json
from typing import Dict, List, Optional

import yaml
from openai import OpenAI

from assistant_log import log_step
from tiktok_template import config_path        # only need config_path, not edit_video/video_folder
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX  # shared S3 client + config

logger = logging.getLogger(__name__)

# -----------------------------------------
# OpenAI Setup
# -----------------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

TEXT_MODEL = "gpt-4.1-mini"

# -----------------------------------------
# Analysis Cache
# -----------------------------------------
video_analyses_cache: Dict[str, str] = {}

ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)


# -----------------------------------------
# S3 Helpers
# -----------------------------------------
def generate_signed_download_url(key: str, expires_in: int = 3600) -> str:
    """
    Generate a pre-signed download URL for an exported video.
    """
    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={
            "Bucket": S3_BUCKET_NAME,
            "Key": key,
            "ResponseContentDisposition": 'attachment; filename="export.mp4"',
            "ResponseContentType": "video/mp4",
        },
        ExpiresIn=expires_in,
    )


def list_videos_from_s3(prefix: str, return_full_keys: bool = False):
    resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
    contents = resp.get("Contents", [])
    files = []

    for obj in contents:
        key = obj["Key"]
        ext = os.path.splitext(key)[1].lower()
        if ext not in [".mp4", ".mov", ".avi", ".m4v"]:
            continue

        if return_full_keys:
            files.append(key)
        else:
            short = key[len(prefix):]
            if short and "/" not in short:
                files.append(short)

    return files



def download_s3_video(key: str) -> Optional[str]:
    """
    Download a single S3 object to a temp file and return its local path.
    """
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
# Normalize video for analysis (optional helper)
# -----------------------------------------
def normalize_video(src: str, dst: str) -> None:
    """
    Normalize the uploaded video to a safe .mp4 file using ffmpeg.
    Ensures correct pixel format, no rotation metadata, and stable
    output for analysis/export.

    This version:
    - ALWAYS outputs .mp4 (fixes .upload extension bug)
    - Logs full ffmpeg stderr on failure
    - Logs success cleanly
    """

    import subprocess
    import os

    # Always force output to .mp4 (fix for incorrect .upload output)
    base = os.path.splitext(dst)[0]
    final_dst = f"{base}.mp4"

    # Ensure directory exists
    os.makedirs(os.path.dirname(final_dst), exist_ok=True)

    # Build ffmpeg normalization command
    cmd = [
        "ffmpeg",
        "-y",
        "-i", src,
        "-vf", "scale=1080:-2,setsar=1,format=yuv420p",
        "-metadata:s:v:0", "rotate=0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-an",
        final_dst,
    ]

    log_step(f"[FFMPEG] Normalizing {src} → {final_dst}")

    # Execute ffmpeg and capture output
    process = subprocess.run(cmd, capture_output=True, text=True)

    # Failure path
    if process.returncode != 0:
        log_step(f"[FFMPEG ERROR] {process.stderr.strip()}")
        raise RuntimeError(f"FFmpeg failed: {process.stderr}")

    # Success
    log_step(f"[FFMPEG] Success → {final_dst}")


# -----------------------------------------
# LLM Clip Analysis
# -----------------------------------------
def analyze_video(path: str) -> str:
    """
    Given a local video path, return a short 1-sentence description
    suitable for a TikTok hotel/travel caption seed.
    """
    basename = os.path.basename(path)

    if client is None:
        # Fallback if no OpenAI key set
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
        "  fg_scale_default: 1.0",
        "  layout_mode: tiktok",
        "",
        "# ------------------------------",
        "# TTS (Voice Narration)",
        "# ------------------------------",
        "tts:",
        "  enabled: false",
        '  voice: "alloy"',
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

SESSION_CONFIG_DIR = "session_configs"

def ensure_session_config_dir():
    os.makedirs(SESSION_CONFIG_DIR, exist_ok=True)


def session_config_path(session: str) -> str:
    ensure_session_config_dir()
    safe = session.replace("/", "_")
    return os.path.join(SESSION_CONFIG_DIR, f"{safe}.yml")


def load_session_config(session: str) -> dict:
    """
    Load YAML config for a specific session (hotel).
    If not found, return an empty default config.
    """
    ensure_session_config_dir()
    path = session_config_path(session)

    if not os.path.exists(path):
        return {}  # brand new session, no config yet

    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def save_session_config(session: str, cfg: dict):
    """
    Save YAML config for a specific session.
    """
    ensure_session_config_dir()
    path = session_config_path(session)

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

# =========================================
# SESSION → GLOBAL CONFIG MERGER
# =========================================
def merge_session_config_into(cfg: dict, session: str) -> dict:
    """
    Return a config.yml dict with session-specific overrides applied.
    Only touches the 'render' section for now.
    """
    try:
        s_cfg = load_session_config(session)
        if not isinstance(s_cfg, dict):
            return cfg

        s_render = s_cfg.get("render", {})
        if not s_render:
            return cfg

        render = cfg.setdefault("render", {})
        for k, v in s_render.items():
            render[k] = v

        return cfg

    except Exception:
        return cfg

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
    return os.path.basename(name)


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
    """
    Rewrite ONLY the 'text' fields in config.yml based on a caption style.
    """
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
    """
    Adjust only the duration fields in config.yml via LLM.
    """
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