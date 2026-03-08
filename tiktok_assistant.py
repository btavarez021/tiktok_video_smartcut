# tiktok_assistant.py — MOV/MP4 SAFE VERSION (aligned with single config.yml per session)
# - No upload_raw_file here
# - No video_folder / edit_video imports
# - S3 config comes from s3_config
# - Only: analysis, YAML prompt, overlay, timings, filename sanitation
# - Uses config_store(session) as the single source of truth

import os
import logging
import tempfile
import subprocess
import json
from typing import Dict, List, Optional
import re
import yaml
from openai import OpenAI
import base64
from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX  # shared S3 client + config
from config_store import load_config, save_config

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
    
def caption_from_filename(filename: str) -> str:
    """
    Convert a filename into a human-readable caption.
    Example:
    LeMeridien_Cocktail_Rooftop.mov
    → Rooftop cocktails at Le Meridien
    """
    name = os.path.splitext(filename)[0]

    # Replace separators
    name = re.sub(r"[_\-]+", " ", name)

    # Split CamelCase
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)

    words = name.split()
    if not words:
        return ""

    # Light cleanup
    cleaned = " ".join(words).strip()

    # Capitalize naturally
    cleaned = cleaned.capitalize()

    return cleaned


# -----------------------------------------
# Hook Score
#-------------------------------------------
def extract_hook_text(cfg: dict) -> str:
    """Return first_clip.text as the hook."""
    try:
        return (cfg.get("first_clip", {}) or {}).get("text", "").strip()
    except Exception:
        return ""

def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def score_hook_text(text: str, intent: str = "discovery") -> dict:
    text = _normalize_spaces(text)
    if not text:
        return {"score": 0, "reasons": ["No opening sentence detected."]}

    lower = text.lower()
    words = lower.split()
    wc = len(words)

    score = 0
    reasons = []

    # --- Base scoring (unchanged logic) ---
    if any(k in lower for k in ["hotel", "room", "stay", "resort"]):
        score += 30
    elif re.search(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b", text):
        score += 20
    elif any(k in lower for k in ["this place", "this spot", "this stay"]):
        score += 15
        reasons.append("Subject is vague; consider naming the hotel or location.")
    else:
        reasons.append("Opening doesn’t clearly say what’s being reviewed.")

    # -----------------------------
    # 2) Curiosity / tension (0–30)
    # -----------------------------

    strong_curiosity = [
        "wait until",
        "until you",
        "didn't expect",
        "did not expect",
        "for one reason",
        "but then",
    ]

    soft_curiosity = [
        "you won't believe",
        "you won’t believe",
        "what happens",
        "what's behind",
        "what’s behind",
        "secret",
        "hidden",
    ]

    if any(t in lower for t in strong_curiosity):
        score += 30
    elif any(t in lower for t in soft_curiosity):
        score += 14
        reasons.append("Curiosity is present, but the phrasing is somewhat generic.")
    else:
        reasons.append("Opening lacks curiosity/tension (no open loop).")

    # -----------------------------
    # Pattern interrupt bonus (+10)
    # -----------------------------
    pattern_interrupt_starts = (
        "they said",
        "i thought",
        "no one told me",
        "everyone said",
    )

    if lower.startswith(pattern_interrupt_starts):
        score += 10
    
    if wc <= 12:
        score += 20
    elif wc <= 18:
        score += 10
        reasons.append("Opening sentence is a bit long.")
    else:
        reasons.append("Opening sentence is too long for a strong hook.")

    filler_starters = {"so", "today", "we", "okay", "basically", "alright"}
    if words and words[0] not in filler_starters:
        score += 20
    else:
        reasons.append("Opening starts with filler words (hurts scroll-stop).")


    # -----------------------------
    # Generic marketing opener penalty (-10)
    # -----------------------------
    generic_starts = {"experience", "discover", "welcome", "step", "explore"}

    if words and words[0] in generic_starts:
        score -= 10
    
    # ----------------------------
    # 🔥 Light intent weighting
    # ----------------------------
    if intent == "aesthetic":
        if wc <= 10:
            score += 5
        aesthetic_terms = ["rooftop", "glow", "sunset", "vibes", "city lights", "view", "lounge", "skyline"]
        if any(t in lower for t in aesthetic_terms):
            score += 5

    elif intent == "discovery":
        if any(t in lower for t in strong_curiosity):
            score += 5
        elif any(t in lower for t in soft_curiosity):
            score += 3

    elif intent == "informational":
        if wc <= 14:
            score += 3
        if any(t in lower for t in ["inside", "how", "why", "what", "tour"]):
            score += 3

    elif intent == "personal":
        if any(t in lower for t in ["i", "my", "me", "we", "our"]):
            score += 4
        if any(t in lower for t in ["favorite", "love", "felt", "didn’t expect", "didn't expect"]):
            score += 4

    specific_nouns = [
    "rooftop", "cocktail", "gym", "suite", "pool",
    "lounge", "view", "skyline", "bar", "spa"
    ]

    if any(n in lower for n in specific_nouns):
        score += 6

    return {
        "score": score,
        "reasons": reasons[:2]
    }

def improve_hook_text(original: str, filename: str | None = None, label: str | None = None) -> str:
    original = _normalize_spaces(original)
    if not original:
        return original

    lower = original.lower()

    # If already strong, leave it alone
    if any(k in lower for k in ["surprised", "unexpected", "didn't expect", "for one reason"]):
        return original

    # Priority 1: user label (best hooks)
    if label:
        return f"I didn’t expect this, but {label.lower()}."

    # Priority 2: filename-based specificity
    if filename:
        human = humanize_filename(filename).lower()
        return f"I didn’t expect this, but {human}."

    # Fallback — curiosity without influencer fluff
    return "This wasn’t supposed to be the best part of the stay."


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


def analyze_video(path: str, session: str, label: str = "") -> str:
    basename = os.path.basename(path)

    if client is None:
        return f"Hotel clip showing {label or basename}"

    # 1️⃣ Extract a frame from the clip
    frame = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg").name

    subprocess.run(
        ["ffmpeg", "-y", "-ss", "00:00:01.5", "-i", path, "-vframes", "1", frame],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    with open(frame, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # 2️⃣ Vision-based prompt
    prompt = [
        {
            "role": "system",
            "content": "You are a visual hotel & travel scene describer. Be factual. No guessing."
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""
Project:
{session}

User label (primary intent):
{label or "(none)"}

Describe ONLY what you see in this frame.

Rules:
- Do not invent oceans, beaches, or resorts
- Do not invent interiors if outdoors
- Do not contradict the label
- Use neutral factual language
- If unsure, say what is visible (e.g. rooftop, bar, skyline)
"""
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    }
                }
            ]
        }
    ]

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=prompt,
        max_tokens=60,
        temperature=0.2
    )

    return resp.choices[0].message.content.strip()



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
        "  captions_mode: all",
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

def humanize_filename(filename: str) -> str:
    """
    Convert filename into a readable caption.
    Example: LeMeridien_RooftopVibes.mov → Le Meridien rooftop vibes
    """
    name = os.path.splitext(filename)[0]
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name



def apply_filename_captions(session: str) -> None:
    """
    Replace ALL clip text fields using:
    1) label (if provided)
    2) filename (humanized)
    """

    cfg = load_config(session)
    if not cfg:
        log_step(f"[CAPTIONS] config missing for session={session}")
        return

    labels = load_labels(session)

    def caption_for(file: str) -> str:
        label = (labels.get(file) or "").strip()
        if label:
            return label

        name = os.path.splitext(file)[0]
        name = name.replace("_", " ").replace("-", " ")
        return name.strip().title()

    # first clip
    if cfg.get("first_clip"):
        f = cfg["first_clip"].get("file")
        if f:
            cfg["first_clip"]["text"] = caption_for(f)

    # middle clips
    for clip in cfg.get("middle_clips", []):
        f = clip.get("file")
        if f:
            clip["text"] = caption_for(f)

    # last clip
    if cfg.get("last_clip"):
        f = cfg["last_clip"].get("file")
        if f:
            cfg["last_clip"]["text"] = caption_for(f)

    save_config(session, cfg)
    log_step(f"[CAPTIONS] Generated from filenames/labels for session={session}")





# -----------------------------------------
# Overlay / Style / Timings (LLM)
# -----------------------------------------
def _style_instructions(style: str) -> str:
    style = style.lower()
    return {
        "punchy": "Direct, energetic, short, punchy wording. No emojis.",
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


def apply_overlay(
    session: str,
    style: str,
    rewrite: bool = True,
    target: str = "all",
    filename: Optional[str] = None
) -> None:
    """
    Overlay handler.

    rewrite = False → visual-only (no text rewrite)
    rewrite = True  → rewrite caption text via LLM
    """

    cfg = load_config(session)
    if not cfg:
        log_step(f"[OVERLAY] config missing for session={session}")
        return

    # Ensure defaults
    render = cfg.setdefault("render", {})
    render.setdefault("captions_mode", "all")

    # -----------------------------------------
    # VISUAL ONLY (NO LLM)
    # -----------------------------------------
    if not rewrite:
        render["overlay_style"] = style
        save_config(session, cfg)

        log_step(f"[OVERLAY] Visual-only applied (style={style})")
        return

    # -----------------------------------------
    # REWRITE MODE
    # -----------------------------------------
    if client is None:
        log_step("[OVERLAY] No OpenAI client — skipping rewrite")
        return

    original_text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)

    prompt = f"""
Rewrite ONLY the caption text fields ("text") inside this YAML.

STRICT RULES:
- Modify ONLY "text:" values
- Do NOT add/remove clips
- Do NOT change duration, start_time, file
- Do NOT change render, tts, music, cta, fgscale, layout
- One sentence per clip
- No hashtags
- No quotes
- DO NOT modify cta.text
- If a location or hotel name is already established, do NOT repeat it in every caption unless it adds new meaning

Overlay style: {style}
Instructions: {_style_instructions(style)}

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

        new_yaml = resp.choices[0].message.content.strip()
        new_yaml = new_yaml.replace("```yaml", "").replace("```", "")

        new_cfg = yaml.safe_load(new_yaml)
        if not isinstance(new_cfg, dict):
            raise ValueError("Invalid YAML from LLM")

        new_cfg = sanitize_yaml_filenames(new_cfg)

        render = new_cfg.setdefault("render", {})
        render.setdefault("captions_mode", "all")
        render["overlay_style"] = style

        save_config(session, new_cfg)

        log_step(f"[OVERLAY] Rewrite applied (style={style})")

    except Exception as e:
        logger.error(f"[OVERLAY REWRITE ERROR] {e}")

LABELS_DIR = os.path.join(os.path.dirname(__file__), "session_labels")
os.makedirs(LABELS_DIR, exist_ok=True)

def _labels_path(session: str) -> str:
    # session is already sanitized upstream
    return os.path.join(LABELS_DIR, session, "labels.json")

def load_labels(session: str) -> dict:
    path = _labels_path(session)
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def apply_smart_timings(session: str, pacing: str = "standard") -> None:
    """
    Apply timing adjustments using LLM while preserving ALL other settings.
    """

    cfg = load_config(session)
    if not cfg:
        log_step(f"[TIMINGS] config missing for session={session}")
        return

    if client is None:
        log_step("[TIMINGS] No OpenAI client — skipping")
        return

    original_text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)

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

        new_cfg = yaml.safe_load(new_yaml)
        if not isinstance(new_cfg, dict):
            raise ValueError("LLM returned invalid YAML")

        new_cfg = sanitize_yaml_filenames(new_cfg)

        # Preserve render safety defaults
        render = new_cfg.setdefault("render", {})
        render.setdefault("captions_mode", "all")
        render["timing_mode"] = pacing

        save_config(session, new_cfg)

        log_step(f"[TIMINGS] Applied successfully (mode={pacing})")

    except Exception as e:
        logger.error(f"[TIMINGS ERROR] {e}")