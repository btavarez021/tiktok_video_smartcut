# tiktok_assistant.py — MOV/MP4 SAFE VERSION (aligned with single config.yml per session)
# - No upload_raw_file here
# - No video_folder / edit_video imports
# - S3 config comes from s3_config
# - Only: analysis, YAML prompt, overlay, timings, filename sanitation
# - Uses get_config_path(session) as the single source of truth

import os
import logging
import tempfile
import subprocess
import json
from typing import Dict, List, Optional
import re
import yaml
from openai import OpenAI

from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX  # shared S3 client + config
from tiktok_template import get_config_path

logger = logging.getLogger(__name__)

# -----------------------------------------
# OpenAI Setup
# -----------------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None

TEXT_MODEL = "gpt-4.1-mini"


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
# Hook Score
#-------------------------------------------

def extract_hook_text(cfg: dict) -> str:
    """
    Extract the first spoken sentence (hook) from config.yml.
    Uses first_clip.text.
    """
    try:
        return (cfg.get("first_clip", {}) or {}).get("text", "").strip()
    except Exception:
        return ""

def score_hook_text(text: str) -> dict:
    """
    Score the hook text (first sentence) from 0–100.
    Returns score + reasons.
    """

    import re

def extract_hook_text(cfg: dict) -> str:
    """Return first_clip.text as the hook."""
    try:
        return (cfg.get("first_clip", {}) or {}).get("text", "").strip()
    except Exception:
        return ""

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def score_hook_text(text: str) -> dict:
    """
    Score hook from 0-100 with calm reasons (max 2).
    Deterministic V1 (no LLM).
    """
    text = _normalize_spaces(text)
    if not text:
        return {"score": 0, "reasons": ["No opening sentence detected."]}

    lower = text.lower()
    words = lower.split()
    wc = len(words)

    score = 0
    reasons = []

    # 1) Clarity (0–30)
    if any(k in lower for k in ["hotel", "room", "stay", "resort"]):
        score += 30
    elif any(k in lower for k in ["this place", "this spot", "this stay"]):
        score += 15
        reasons.append("Subject is vague; consider naming the hotel or location.")
    else:
        reasons.append("Opening doesn’t clearly say what’s being reviewed.")

    # 2) Curiosity / tension (0–30)
    curiosity_terms = ["surprised", "unexpected", "didn't expect", "but", "however", "until", "for one reason"]
    if any(t in lower for t in curiosity_terms):
        score += 30
    else:
        reasons.append("Opening lacks curiosity/tension (no open loop).")

    # 3) Brevity (0–20)
    if wc <= 12:
        score += 20
    elif wc <= 18:
        score += 10
        reasons.append("Opening sentence is a bit long.")
    else:
        reasons.append("Opening sentence is too long for a strong hook.")

    # 4) Spoken safety (0–20)
    filler_starters = {"so", "today", "we", "okay", "basically", "alright"}
    if words and words[0] not in filler_starters:
        score += 20
    else:
        reasons.append("Opening starts with filler words (hurts scroll-stop).")

    return {"score": min(score, 100), "reasons": reasons[:2]}

def improve_hook_text(original: str) -> str:
    """
    V1: simple rule-based improvement.
    (We can upgrade to LLM later.)
    """
    original = _normalize_spaces(original)
    if not original:
        return original

    # If it already has strong curiosity keyword, keep it
    if any(k in original.lower() for k in ["surprised", "unexpected", "didn't expect", "for one reason"]):
        return original

    return "This hotel surprised me more than I expected."


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


def build_yaml_prompt(video_files: List[str], analyses: List[str]) -> str:
    """
    Build a prompt asking the LLM to output a clean, modern config.yml
    using the EXACT schema supported by tiktok_template.py and the UI.
    """

    lines = [
        "You are generating a config.yml for a vertical TikTok HOTEL / TRAVEL video.",
        "",
        "IMPORTANT RULES:",
        f"- The uploaded video files for this session are EXACTLY (in order): {video_files}.",
        "- Output ONLY valid YAML (no backticks).",
        "- Use the EXACT schema below, no extra keys.",
        "- Filenames must be returned EXACTLY as provided (case and extension preserved).",
        "- You MUST NOT reuse the same video file name in multiple clips unless it appears multiple times in the upload list.",
        "- If ONLY TWO videos exist, produce EXACTLY two clips:",
        "    • first_clip → video 1",
        "    • last_clip → video 2",
        "    • middle_clips MUST be an empty list.",
        "- If ONLY ONE video exists, generate ONLY a first_clip and last_clip using different start_time segments.",
        "- If THREE OR MORE videos exist, use:",
        "    • first_clip → first file",
        "    • middle_clips → all files except first and last",
        "    • last_clip → last file",
        "",
        "======================================",
        "REQUIRED YAML SCHEMA (FOLLOW EXACTLY)",
        "======================================",
        "",
        "first_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "",
        "middle_clips:",
        "  - file: <filename>",
        "    start_time: 0",
        "    duration: <seconds>",
        "    text: <caption>",
        "",
        "last_clip:",
        "  file: <filename>",
        "  start_time: 0",
        "  duration: <seconds>",
        "  text: <caption>",
        "",
        "render:",
        "  layout_mode: tiktok",
        "  fgscale_mode: auto",
        "  fgscale: null",
        "  story_mode: false",
        "  transition:",
        "    type: fade",
        "    duration: 0.4",
        "",
        "tts:",
        "  enabled: false",
        '  voice: "shimmer"',
        "",
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
        "",
        "",
        "======================================",
        "CLIPS AND THEIR ANALYSIS (FOR CAPTIONS)",
        "======================================",
    ]

    # Insert clip analyses
    for vf, a in zip(video_files, analyses):
        lines.append(f"- file: {vf}")
        lines.append(f"  analysis: {a}")

    lines.append("")
    lines.append("Return ONLY VALID YAML with no explanation. DO NOT wrap in code fences.")
    lines.append("Ensure you output first_clip, middle_clips, and last_clip sections.")

    return "\n".join(lines)


def _normalize_yaml_filename(name: str) -> str:
    """
    Normalize filenames in YAML to basename only.
    """
    if not name:
        return name
    return os.path.basename(name)


def sanitize_yaml_filenames(cfg: dict) -> dict:
    """
    Ensure YAML filenames are in a consistent form (basename only)
    so they match the video filenames from S3.
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


def apply_overlay(session: str, style: str, target: str = "all", filename: Optional[str] = None) -> None:
    """
    Apply overlay style (caption rewrite) for THIS session.
    - Only rewrites text fields
    - Preserves layout_mode, fgscale, CTA, TTS, music, timing, etc. via prompt rules
    - Reads and writes the SAME per-session config.yml (get_config_path)
    """
    try:
        config_path = get_config_path(session)
        if not os.path.exists(config_path):
            return

        with open(config_path, "r", encoding="utf-8") as f:
            original_text = f.read()
    except Exception:
        return

    if client is None:
        return

    prompt = f"""
Rewrite ONLY the caption text fields ("text") inside this YAML.

IMPORTANT:
Below is a YAML structure. You must return the EXACT SAME structure.
You may ONLY modify the values of fields named "text".
Do NOT modify:
- duration
- start_time
- file
- render.*
- cta.*
- tts.*
- music.*
- fgscale or fgscale_mode
- layout_mode

Overlay style: {style}
Instructions: {_style_instructions(style)}

STRICT RULES:
- Modify ONLY "text:" values.
- Do NOT add or remove any clips.
- Keep all filenames EXACTLY the same.
- Keep all durations EXACTLY the same.
- Do NOT modify layout_mode, fgscale, tts, cta, music, or render settings.
- One sentence per clip (<150 chars).
- No hashtags.
- No quotes.

ORIGINAL YAML:
{original_text}

Return ONLY valid YAML (no backticks).
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

    except Exception as e:
        logger.error(f"[OVERLAY] LLM error: {e}")
        return

    # Tag overlay style inside config
    render = cfg.setdefault("render", {})
    render["overlay_style"] = style

    # Save directly to this session's config.yml
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

        log_step(f"Overlay applied for session={session}, style={style}")
    except Exception as e:
        logger.error(f"[OVERLAY SAVE ERROR] {e}")


def apply_smart_timings(session: str, pacing: str = "standard") -> None:
    """
    Apply timing adjustments using LLM, while preserving ALL other settings:
    - overlay text
    - layout_mode
    - fgscale + fgscale_mode
    - tts settings
    - music settings
    - CTA settings
    - clip order & file names
    """
    try:
        config_path = get_config_path(session)
        if not os.path.exists(config_path):
            return

        with open(config_path, "r", encoding="utf-8") as f:
            original_text = f.read()
    except Exception:
        return

    if client is None:
        return

    pacing_desc = (
        "Cinematic pacing: hook (2–4s), value shots (3–7s), ending (2–4s). Keep total <= 60s."
        if pacing == "cinematic"
        else "Standard pacing: small duration optimizations only."
    )

    prompt = f"""
You MUST ONLY modify the duration fields in this YAML.

❗ DO NOT CHANGE anything else, including:
- text captions
- overlay style
- layout_mode
- fgscale_mode or fgscale
- tts settings
- music settings
- cta fields (text, voiceover, enabled, duration)
- filenames
- clip order
- start_time
- any other keys

Pacing mode: "{pacing}"

Guidelines:
{pacing_desc}

ORIGINAL YAML:
{original_text}

Return ONLY VALID YAML (no backticks).
""".strip()

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.15,
        )

        new_yaml = (resp.choices[0].message.content or "").strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(new_yaml)
        if not isinstance(cfg, dict):
            raise ValueError("LLM returned invalid YAML")

        cfg = sanitize_yaml_filenames(cfg)

    except Exception as e:
        logger.error(f"[TIMINGS] YAML error: {e}")
        return

    # Tag timing mode
    render = cfg.setdefault("render", {})
    render["timing_mode"] = pacing

    # Save directly to this session's config.yml
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

        log_step(f"Smart timings applied for session={session} (mode={pacing})")
    except Exception as e:
        logger.error(f"[TIMINGS SAVE ERROR] {e}")
