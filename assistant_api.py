# assistant_api.py — session-aware uploads + YAML + analysis

import os
import json
import glob
import logging
import re
from typing import Dict, Any, List
import yaml
from openai import OpenAI, RateLimitError
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import subprocess
from flask import request
from assistant_log import log_step, log_error, log_success
from tiktok_template import edit_video, video_folder, STYLE_PRESETS
from s3_config import (
    s3,
    S3_BUCKET_NAME,
    RAW_PREFIX,
    EXPORT_PREFIX,
    clean_s3_key,
    PROCESSED_PREFIX,
)
from config_store import load_config, save_config
import shutil
from tiktok_template import reorder_clips
import json
import time
from datetime import datetime, timezone
from threading import Lock
# Import ONLY non-circular functions from tiktok_assistant
from tiktok_assistant import (
    generate_signed_download_url,
    list_videos_from_s3,
    download_s3_video,
    analyze_video,
    build_yaml_prompt,
    sanitize_yaml_filenames,
    apply_smart_timings,
    extract_hook_text, score_hook_text, improve_hook_text
)
from tiktok_assistant import apply_overlay
import time
import threading
from collections import Counter
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
EVENTS_PATH = os.path.join(DATA_DIR, "feedback_events.jsonl")
AGG_PATH = os.path.join(DATA_DIR, "feedback_aggregates.json")

ANALYSIS_JOBS: dict[str, dict] = {}
VARIANT_JOBS: dict[str, dict] = {}
YAML_JOBS = {}
# --- Flow score cache (in-memory) ---
FLOW_SCORE_CACHE: dict[str, dict] = {}

ANALYSIS_STATUS_DIR = os.path.join(DATA_DIR, "analysis_status")
os.makedirs(ANALYSIS_STATUS_DIR, exist_ok=True)

def _load_config(session: str) -> dict:
    return load_config(session)

def _analysis_status_path(session: str) -> str:
    return os.path.join(ANALYSIS_STATUS_DIR, f"{session}.json")

def save_analysis_status(session: str, data: dict):
    with open(_analysis_status_path(session), "w", encoding="utf-8") as f:
        json.dump(data, f)


def score_first_clip_alignment_bonus(hook: str, first_clip_text: str) -> int:
    """
    Rewards hooks that align with the first clip, since the first clip is the visual hook anchor.
    """
    if not hook or not first_clip_text:
        return 0

    hook_lower = hook.lower()
    first_lower = first_clip_text.lower()

    first_tokens = _tokenize_subject_text(first_lower)
    if not first_tokens:
        return 0

    matches = sum(1 for token in first_tokens if token in hook_lower)

    if matches >= 3:
        return 8
    elif matches == 2:
        return 5
    elif matches == 1:
        return 2

    return 0

def safe_json_extract(text: str) -> dict:
    if not text:
        return {}

    try:
        start = text.find("{")
        end = text.rfind("}") + 1

        if start == -1 or end <= start:
            return {}

        candidate = text[start:end].strip()

        return json.loads(candidate)

    except Exception:
        return {}
    
def load_analysis_status(session: str) -> dict | None:
    path = _analysis_status_path(session)
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None

def api_set_auto_assist(session: str, enabled: bool) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    settings = cfg.setdefault("settings", {})
    settings["auto_assist"] = bool(enabled)

    save_config(session, cfg)

    return {
        "status": "ok",
        "session": session,
        "auto_assist": settings["auto_assist"]
    }

def api_get_auto_assist(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    enabled = (
        cfg.get("settings", {}).get("auto_assist", False)
    )

    return {
        "status": "ok",
        "session": session,
        "auto_assist": bool(enabled)
    }

def score_primary_experience_variant_bonus(variant: dict, primary_experience: str) -> int:
    """
    Rewards variants whose tone + wording match the reel's dominant experience.
    Light bonus only — should guide ranking, not overpower hook/flow.
    """
    if not variant or not primary_experience or primary_experience == "mixed":
        return 0

    text = (variant.get("text") or "").lower()
    tone = (variant.get("tone") or "").lower()

    bonus = 0

    if primary_experience == "relaxation":
        if any(w in text for w in ["unwind", "calm", "peaceful", "sunset", "ocean", "pool", "relax", "breeze"]):
            bonus += 4
        if any(w in tone for w in ["minimal", "luxury", "cinematic"]):
            bonus += 3

    elif primary_experience == "luxury":
        if any(w in text for w in ["rooftop", "suite", "exclusive", "elevated", "gourmet", "skyline", "vip", "premium"]):
            bonus += 4
        if any(w in tone for w in ["minimal", "luxury", "influencer"]):
            bonus += 3

    elif primary_experience == "energy":
        if any(w in text for w in ["night", "party", "electric", "crowd", "dance", "hype", "celebration", "lights"]):
            bonus += 4
        if any(w in tone for w in ["punchy", "influencer"]):
            bonus += 3

    elif primary_experience == "exploration":
        if any(w in text for w in ["discover", "explore", "city", "wander", "tour", "hidden", "destination", "view"]):
            bonus += 4
        if any(w in tone for w in ["story", "influencer", "rewrite"]):
            bonus += 3

    elif primary_experience == "fitness":
        if any(w in text for w in ["gym", "workout", "training", "strength", "push", "performance", "lift"]):
            bonus += 4
        if any(w in tone for w in ["punchy", "influencer"]):
            bonus += 2

    elif primary_experience == "romance":
        if any(w in text for w in ["romantic", "together", "sunset", "dinner", "shared", "love", "date"]):
            bonus += 4
        if any(w in tone for w in ["story", "minimal", "luxury"]):
            bonus += 3

    return min(bonus, 7)

def generate_voiceover_script(text: str, hook: str | None = None, tone: str | None = None):
    text = (text or "").strip()
    if not text:
        raise ValueError("No text provided")

    prompt = f"""
        Rewrite these captions into a natural TikTok-style voiceover script for a multi-clip short-form video.

        Goals:
        - sound conversational and creator-like
        - feel natural when spoken out loud
        - preserve the same overall meaning
        - follow the clip order naturally
        - connect the moments so the video feels like one experience
        - use short, clean, spoken sentences
        - sound like a real person talking, not writing
        - prefer simple, natural wording over descriptive or poetic phrasing
        - slightly casual tone (like speaking to a friend)
        - include light reactions (e.g. "honestly", "actually", "I didn’t expect this")
        - break ideas into short spoken beats
        - output only the final script

        Rules:
        - no hashtags
        - no emojis
        - no bullet points
        - no headings
        - no assistant commentary
        - do not sound robotic or corporate
        - do not repeat the captions word-for-word
        - avoid overly long paragraphs
        - avoid salesy language unless the original captions clearly support it

        Style guidance:
        - write like a real creator narrating over clips
        - use natural transitions, but vary them — avoid repeating the same phrasing
        - keep it concise but smooth
        - make it easy to record in CapCut
        - avoid repeating the same transition patterns (e.g. "then", "after that") every line
        - vary sentence openings to feel more natural
        - end with a strong or memorable final thought, not a generic summary
        - aim for how someone would say it out loud in one take
        - avoid filler phrases like "we’ve got", "there’s", "you can see" unless they add value
        - use the hook as the opening tone and direction for the script
        - the first line should feel like a continuation of the hook, not a reset
        - maintain the same curiosity, emotion, or energy introduced by the hook

        Avoid:
        - overly descriptive or poetic phrases
        - formal or “written” sounding sentences
        - phrases like "this view hides", "where X meets Y"
        - anything that feels like caption copy instead of speech

        Hook:
        {hook or "None"}

        Captions:
        {text}
        """.strip()

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt,
    )

    return response.output_text.strip()

def get_weighted_video_subjects(session: str) -> dict[str, int]:
    """
    Returns subject frequency weights from labels, analyses, and filenames.
    Higher count = more central subject in this reel.
    """
    session = sanitize_session(session)
    counts = Counter()

    # Start with a small base prior
    for s in BASE_SUBJECTS:
        counts[s] += 1

    # Labels = strongest signal
    labels = load_labels(session) or {}
    for label in labels.values():
        for word in _tokenize_subject_text(label):
            counts[word] += 3

    # Analysis descriptions = medium signal
    analyses = load_analysis_results_session(session) or {}
    for desc in analyses.values():
        for word in _tokenize_subject_text(desc):
            counts[word] += 2

    # Filenames = weak signal
    for fname in labels.keys():
        cleaned = fname.replace("_", " ").replace("-", " ")
        cleaned = re.sub(r"\.[a-zA-Z0-9]+$", "", cleaned)
        for word in _tokenize_subject_text(cleaned):
            counts[word] += 1

    # keep only meaningful anchors
    filtered = {
        word: count
        for word, count in counts.items()
        if len(word) >= 4 and not word.isdigit()
    }

    return dict(sorted(filtered.items(), key=lambda x: x[1], reverse=True))

def get_content_context(session: str) -> str:
    cfg = _load_config(session)
    return cfg.get("content_context", "auto")

def _run_variant_job(
    session: str,
    modes: dict,
    selected_hook: str | None,
    content_mode: str = "caption",
):
    try:
        status = {
            "status": "running",
            "started_at": time.time(),
            "error": None,
            "result": None,
        }
        VARIANT_JOBS[session] = status

        result = api_generate_variants(session, modes, selected_hook, content_mode)

        status["status"] = "done"
        status["result"] = result

    except Exception as e:
        VARIANT_JOBS[session] = {
            "status": "error",
            "error": str(e),
        }

BASE_SUBJECTS = {
    "rooftop", "lounge", "cocktail", "bar",
    "hotel", "gym", "pool", "suite", "view",
    "spa", "restaurant", "skyline", "terrace"
}

SUBJECT_STOPWORDS = {
    "this", "that", "with", "from", "into", "your", "their",
    "video", "clip", "scene", "stay", "night", "day",
    "good", "great", "best", "amazing", "beautiful",
    "feel", "vibe", "vibes", "place", "spot", "thing",
    "here", "there", "just", "made", "every", "after",
    "before", "while", "when", "where"
}

def _tokenize_subject_text(text: str) -> list[str]:
    if not text:
        return []

    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower())
    return [w for w in words if w not in SUBJECT_STOPWORDS]


def get_video_subjects(session: str) -> list[str]:
    """
    Hybrid subject extraction:
    - stable base anchors
    - dynamic anchors from labels, analyses, and filenames
    """
    session = sanitize_session(session)

    subjects = set(BASE_SUBJECTS)

    # Labels
    labels = load_labels(session) or {}
    for label in labels.values():
        for word in _tokenize_subject_text(label):
            subjects.add(word)

    # Analysis descriptions
    analyses = load_analysis_results_session(session) or {}
    for desc in analyses.values():
        for word in _tokenize_subject_text(desc):
            subjects.add(word)

    # Filenames
    for fname in labels.keys():
        cleaned = fname.replace("_", " ").replace("-", " ")
        cleaned = re.sub(r"\.[a-zA-Z0-9]+$", "", cleaned)
        for word in _tokenize_subject_text(cleaned):
            subjects.add(word)

    # keep only useful-looking anchors
    filtered = {
        s for s in subjects
        if len(s) >= 4 and not s.isdigit()
    }

    return sorted(filtered)

def score_hook_from_text(text: str, session: str, intent: str = "discovery") -> Dict[str, Any]:
    if not text:
        return {
            "hook": "",
            "score": 0,
            "reasons": ["No hook found."]
        }

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    hook = blocks[0] if blocks else ""

    scored = score_hook_unified(session, hook, intent)

    return {
        "hook": hook,
        "score": scored["score"],
        "reasons": scored["reasons"]
    }

def _run_yaml_job(session: str):
    try:
        api_generate_yaml(session)
        job = YAML_JOBS.get(session) or {}
        job["status"] = "done"
        YAML_JOBS[session] = job
    except Exception as e:
        job = YAML_JOBS.get(session) or {}
        job["status"] = "error"
        job["error"] = str(e)
        YAML_JOBS[session] = job


def api_generate_yaml_start(session: str):
    session = sanitize_session(session)

    job = YAML_JOBS.get(session)
    if job and job["status"] == "running":
        return {"status": "already_running"}

    YAML_JOBS[session] = {"status": "running"}

    thread = threading.Thread(
        target=_run_yaml_job,
        args=(session,),
        daemon=True
    )
    thread.start()

    return {"status": "started"}

def api_edit_strategy(session: str):
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    suggestions = []

    # -----------------------------
    # Hook
    # -----------------------------
    hook = extract_hook_text(cfg)
    hook_score = score_hook_unified(session, hook).get("score", 0)

    if hook_score < 60:
        suggestions.append({
            "area": "hook",
            "issue": "Opening is weak.",
            "impact": "high",
            "action": "Rewrite hook to create curiosity or tension."
        })
    elif hook_score < 80:
        suggestions.append({
            "area": "hook",
            "issue": "Hook is decent but could be tighter.",
            "impact": "medium",
            "action": "Shorten the sentence and sharpen the promise."
        })
    
    # -----------------------------
    # Story Flow
    # -----------------------------
    flow_result = api_story_flow_score(session)
    flow_score = flow_result.get("score", 0)

    if flow_score < 60:
        suggestions.append({
            "area": "flow",
            "issue": "Story flow feels disconnected.",
            "impact": "high",
            "action": "Rewrite middle captions to create smoother progression."
        })
    elif flow_score < 75:
        suggestions.append({
            "area": "flow",
            "issue": "Story flow is decent but transitions could feel smoother.",
            "impact": "medium",
            "action": "Polish caption order and transitions for a more natural sequence."
        })

    # -----------------------------
    # Pacing
    # -----------------------------
    first = (cfg.get("first_clip") or {}).get("duration", 0)
    if first > 5:
        suggestions.append({
            "area": "pacing",
            "issue": "Hook runs long.",
            "impact": "high",
            "action": "Trim first clip to 3–4 seconds."
        })

    # -----------------------------
    # CTA
    # -----------------------------
    cta = cfg.get("cta", {})
    if cta.get("enabled") and cta.get("duration", 0) < 3:
        suggestions.append({
            "area": "cta",
            "issue": "CTA too short.",
            "impact": "low",
            "action": "Increase CTA duration to improve conversions."
        })

    return {
        "status": "ok",
        "suggestions": suggestions
    }

def auto_optimize_hook(session: str, hook: str, intent: str = "discovery",
                       max_rounds: int = 6, target_score: int = 80):
    """
    Repeatedly boost a hook and keep the best result.
    """

    if not hook:
        return {"text": hook, "score": 0, "attempts": 0}

    best_text = hook
    best_score = score_hook_unified(session, hook, intent)["score"]

    history = []
    stall_count = 0

    for i in range(max_rounds):

        candidate = boost_hook(session, best_text, intent)
        score = score_hook_unified(session, candidate, intent)["score"]

        history.append({
            "text": candidate,
            "score": score
        })

        if score > best_score:
            best_text = candidate
            best_score = score
            stall_count = 0
        else:
            stall_count += 1

        # 🎯 stop conditions
        if best_score >= target_score:
            break

        if stall_count >= 2:
            break

    return {
        "text": best_text,
        "score": best_score,
        "attempts": len(history),
        "history": history
    }


def boost_hook(session: str, hook: str, intent: str = "discovery") -> str:
    """
    Upgrade a hook by generating multiple rewrites
    and returning the highest scoring one.
    """

    if not hook:
        return ""

    if not client:
        return hook  # fail safe


    prompt = f"""
You are an elite viral TikTok hook strategist.

We will improve ONE hook by attacking it from multiple
psychological trigger angles.

Intent: {intent}

Original hook:
"{hook}"

Create 8 NEW hook options.

Each option must use a DIFFERENT strategy:
1. Curiosity gap
2. Secret / hidden
3. Exclusive / insider
4. Unexpected / surprise
5. Status / luxury
6. Transformation
7. Challenge / dare
8. Dramatic promise

RULES:
- Under 12 words
- One line each
- Scroll-stopping
- Natural human voice
- No corporate phrasing
- Do NOT repeat the original
- Avoid generic filler
- Avoid weak phrasing like:
  "wait until you see"
  "you won't believe"
  "this place"
  unless made highly specific
- Reward specificity, contrast, exclusivity, or transformation

Return JSON:
{{
  "hooks": [
    {{"text": "hook", "strategy": "curiosity"}},
    {{"text": "hook", "strategy": "secret"}},
    {{"text": "hook", "strategy": "insider"}},
    {{"text": "hook", "strategy": "surprise"}},
    {{"text": "hook", "strategy": "luxury"}},
    {{"text": "hook", "strategy": "transformation"}},
    {{"text": "hook", "strategy": "challenge"}},
    {{"text": "hook", "strategy": "dramatic"}}
  ]
}}
"""

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": "Return JSON only."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.9,
        )

        content = (resp.choices[0].message.content or "").strip()

        # ================================
        # 🔒 BULLETPROOF JSON PARSE
        # ================================
        try:
            data = safe_json_extract(content)

        except Exception as e:
            logger.error(f"[HOOK_BOOST PARSE ERROR] {e}")
            logger.error(f"[HOOK_BOOST RAW OUTPUT]\n{content}")
            return hook  # graceful fallback


        candidates = []

        for item in data.get("hooks", []):
            # Model might return string instead of object
            if isinstance(item, dict):
                text = item.get("text", "")
                strategy = item.get("strategy", "unknown")
            else:
                text = str(item)
                strategy = "unknown"

            clean = strip_emojis(text).strip()

            if not clean:
                continue

            try:
                s = score_hook_unified(session, clean, intent)["score"]
            except Exception:
                s = 0

            candidates.append({
                "text": clean,
                "score": s,
                "strategy": strategy
            })


        if not candidates:
            return hook

        # pick best
        candidates.sort(key=lambda x: x["score"], reverse=True)
        best = candidates[0]

        print(f"[HOOK BOOST] winner ({best['score']}) [{best['strategy']}]: {best['text']}")

        return best["text"]


    except Exception as e:
        logger.error(f"[HOOK_BOOST FATAL] {e}")
        return hook

def api_generate_yaml_status(session: str):
    session = sanitize_session(session)

    job = YAML_JOBS.get(session)
    if not job:
        return {"status": "idle"}

    return job


def api_generate_variants_start(
    session: str,
    modes: dict,
    selected_hook: str | None,
    content_mode: str = "caption",
):
    session = sanitize_session(session)

    job = VARIANT_JOBS.get(session)
    if job and job["status"] == "running":
        return {"status": "already_running"}

    VARIANT_JOBS[session] = {"status": "running"}

    thread = threading.Thread(
        target=_run_variant_job,
        args=(session, modes, selected_hook, content_mode),
        daemon=True
    )
    thread.start()

    return {"status": "started"}

def api_generate_variants_status(session: str):
    session = sanitize_session(session)
    return VARIANT_JOBS.get(session, {"status": "idle"})


_feedback_lock = Lock()

# ========== TASK REGISTRY ==========
export_tasks = {}  
# Structure:
# export_tasks[task_id] = {
#     "status": "pending" | "processing" | "done" | "error" | "cancelled",
#     "download_url": None,
#     "error": None,
#     "cancel_requested": False,
# }

CAPTION_ONLY_GUARDRAIL = (
    "Output ONLY caption text. "
    "Do NOT include explanations, introductions, headings, labels, or assistant commentary. "
    "Do NOT say things like 'Here is', 'Sure', or 'Let me know'."
)


# -------------------------------
# OpenAI client
# -------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client = OpenAI(api_key=api_key) if api_key else None
if not client:
    log_step("[OPENAI] API key missing — AI features disabled.")

TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------
# Helpers
# -------------------------------

INTENT_PROFILE = {
    "discovery": {
        "hook_weight": 0.6,
        "flow_weight": 0.4,
        "tone_bias": ["punchy", "influencer"],
        "min_hook": 60,
    },
    "personal": {
        "hook_weight": 0.5,
        "flow_weight": 0.5,
        "tone_bias": ["story", "influencer"],
        "min_hook": 50,
    },
    "aesthetic": {
        "hook_weight": 0.4,
        "flow_weight": 0.6,
        "tone_bias": ["minimal", "luxury"],
        "min_hook": 45,
    },
    "informational": {
        "hook_weight": 0.55,
        "flow_weight": 0.45,
        "tone_bias": ["rewrite", "story"],
        "min_hook": 50,
    },
}

def _utc_iso():
    return datetime.now(timezone.utc).isoformat()

def _ensure_data_files():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(EVENTS_PATH):
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            pass
    if not os.path.exists(AGG_PATH):
        with open(AGG_PATH, "w", encoding="utf-8") as f:
            f.write("{}")

def _load_aggregates():
    # 🔒 Safety: file may not exist yet
    if not os.path.exists(AGG_PATH):
        return {}

    try:
        with open(AGG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_feedback_adjustment(
    intent: str,
    tone: str,
    confidence: str,
    recommended: bool = False
) -> float:

    if confidence == "clear":
        return 0.0

    key = f"{intent}||{tone}"
    aggs = _load_aggregates()
    row = aggs.get(key)
    if not row:
        return 0.0

    views = float(row.get("views", 0))
    chosen = float(row.get("chosen", 0))

    # guardrail: insufficient signal
    if views < 3:
        return 0.0

    ratio = chosen / max(views, 1)  # 0..1

    MAX_BOOST = 5.0
    adj = (ratio - 0.5) * MAX_BOOST

    # soften when moderately confident
    if confidence == "moderate":
        adj *= 0.5

    # recommended variants should move slower
    if recommended:
        adj *= 0.8

    return round(max(min(adj, MAX_BOOST), -MAX_BOOST), 2)


def _atomic_write_json(path, data):
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


# ================================
# Emoji-safe text helper
# ================================
def strip_emojis(text: str) -> str:
    if not text:
        return text
    # Remove emojis & pictographs (Unicode-safe)
    return re.sub(r"[\U00010000-\U0010ffff]", "", text).strip()

# ================================
# Clip label validation & cleanup
# ================================
def normalize_label(label: str) -> str:
    """
    Enforces clean, AI-safe clip labels.
    """
    if not label:
        return ""

    label = strip_emojis(label)
    label = label.strip()

    bad_prefixes = ("e.g", "example", "ex:")

    if label.lower().startswith(bad_prefixes):
        return ""

    # Collapse spaces
    label = re.sub(r"\s+", " ", label)

    # Max length
    label = label[:60]

    # Remove junk characters
    label = re.sub(r"[^a-zA-Z0-9 \-]", "", label)

    # Block useless labels
    banned = {"video", "clip", "test", "file", "upload", "sample"}
    if label.lower() in banned:
        return ""

    # Must contain letters
    if not re.search(r"[a-zA-Z]", label):
        return ""

    return label.strip()

def is_weak_label(label: str) -> bool:
    if not label:
        return True

    label = label.lower().strip()

    # Too short
    if len(label) < 4:
        return True

    weak_words = {
        "video", "clip", "shot", "scene",
        "test", "sample", "file", "upload"
    }

    # Only reject truly useless generic labels
    if label in weak_words:
        return True

    # Reject single-word labels only if they are overly generic
    weak_single_words = {
        "food", "drink", "hotel", "lobby",
        "cocktail", "view", "room"
    }

    if " " not in label and label in weak_single_words:
        return True

    return False


# ==========================================
# SESSION-SCOPED ANALYSIS CACHE (NEW SYSTEM)
# ==========================================
ANALYSIS_BASE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_BASE_DIR, exist_ok=True)


def _session_cache_dir(session: str) -> str:
    """Return full path to the session-specific analysis directory."""
    safe = sanitize_session(session)
    path = os.path.join(ANALYSIS_BASE_DIR, safe)
    os.makedirs(path, exist_ok=True)
    return path


def save_analysis_result_session(session: str, filename: str, description: str) -> None:
    """Save a single analysis result inside the session-specific folder."""
    folder = _session_cache_dir(session)
    out_path = os.path.join(folder, filename + ".json")
    payload = {"filename": filename, "description": description}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def load_analysis_results_session(session: str) -> Dict[str, str]:
    """Load all analysis results for a given session only."""
    folder = _session_cache_dir(session)
    results = {}

    for path in glob.glob(os.path.join(folder, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            fname = data.get("filename")
            desc = data.get("description")
            if fname and desc:
                results[fname] = desc
        except Exception as e:
            logger.error(f"[LOAD_ANALYSIS][{session}] failed for {path}: {e}")

    return results


# -------------------------------
# CAPTIONS MODE (global render controls)
# -------------------------------
def api_set_captions_mode(session: str, mode: str) -> Dict[str, Any]:
    session = sanitize_session(session)

    if mode not in ("all", "first_only", "none"):
        return {"status": "error", "error": "Invalid captions_mode"}

    cfg = _load_config(session)
    r = cfg.setdefault("render", {})
    r["captions_mode"] = mode

    save_config(session, cfg)

    log_step(f"[CAPTIONS_MODE] {session} -> {mode}")
    return {"status": "ok", "captions_mode": mode}

# -----------------------------------------
# Hook Score
#-------------------------------------------

def api_hook_score(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    hook = extract_hook_text(cfg)
    scored = score_hook_unified(session, hook)

    return {
        "hook": hook,
        "score": scored["score"],
        "reasons": scored["reasons"],
    }


def api_improve_hook(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    if not cfg.get("first_clip", {}).get("text"):
        return {"status": "error", "error": "No hook found"}

    original = cfg["first_clip"]["text"]

    new_hook = improve_hook_text(original)

    # Build full caption block with only hook changed
    captions = []

    captions.append(new_hook)

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            captions.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        captions.append(cfg["last_clip"]["text"])

    proposed = "\n\n".join(captions)

    score = score_hook_unified(session, new_hook)

    return {
        "status": "proposed",
        "proposed": proposed,
        "hook": new_hook,
        "score": score["score"],
        "reasons": score["reasons"]
    }

def choose_best_hook(hooks, intent="discovery"):
    if not hooks:
        return None

    intent_cfg = INTENT_PROFILE.get(intent, INTENT_PROFILE["discovery"])

    def score(h):
        base = h.get("score", 0)
        tone = h.get("tone", "").lower()

        for t in intent_cfg["tone_bias"]:
            if t in tone:
                base += 3

        return base

    scored = sorted(hooks, key=score, reverse=True)
    best = scored[0]

    avg = sum(h.get("score", 0) for h in hooks) / len(hooks)

    if best["score"] > avg + 8:
        reason = "Higher curiosity and scroll-stopping power than other hooks"
    else:
        reason = "Best overall hook for this video goal"

    return {
        "text": best["text"],
        "reason": reason
    }

def _update_aggregate(aggs, event):
    intent = event.get("intent") or "unknown"
    tone = event.get("tone") or "unknown"
    confidence = event.get("confidence") or "unknown"   # can be unknown if null
    recommended = bool(event.get("recommended") is True)
    action = event.get("action") or "viewed"

    key = f"{intent}||{tone}"
    if key not in aggs:
        aggs[key] = {
            "intent": intent,
            "tone": tone,
            "views": 0,
            "chosen": 0,
            "recommended_views": 0,
            "recommended_chosen": 0,
            "confidence_breakdown": {},
            "last_updated": None
        }

    row = aggs[key]

    # Ensure confidence bucket exists
    cbd = row["confidence_breakdown"]
    if confidence not in cbd:
        cbd[confidence] = {"views": 0, "chosen": 0}

    # Update counts
    if action == "viewed":
        row["views"] += 1
        cbd[confidence]["views"] += 1
        if recommended:
            row["recommended_views"] += 1

    elif action == "chosen":
        row["chosen"] += 1
        cbd[confidence]["chosen"] += 1
        if recommended:
            row["recommended_chosen"] += 1

    row["last_updated"] = _utc_iso()


def record_variant_feedback(payload: dict):
    """
    v2 = write raw event (jsonl) + update aggregates (json)
    """
    _ensure_data_files()

    if payload.get("tone") == "Error":
        return{"ok": False, "ignored":"error variant"}

    # Normalize / validate minimal fields
    event = {
        "session": payload.get("session") or "unknown",
        "variant_id": payload.get("variant_id") or payload.get("variantId") or "unknown",
        "intent": payload.get("intent") or "unknown",
        "tone": payload.get("tone") or "unknown",
        "confidence": payload.get("confidence"),  # can be None
        "recommended": bool(payload.get("recommended") is True),
        "action": payload.get("action") or "viewed",
        "timestamp": _utc_iso(),
    }

    # Optional: allow only specific actions
    if event["action"] not in ("viewed", "chosen"):
        event["action"] = "viewed"

    with _feedback_lock:
        # 1) Append raw event
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

        # 2) Update aggregates
        aggs = _load_aggregates()
        _update_aggregate(aggs, event)
        _atomic_write_json(AGG_PATH, aggs)

    if event["tone"] == "Error":
        return {"ok": False, "ignored": "error variant"}

    return {
        "ok": True,
        "stored": event,
        "aggregate_key": f"{event['intent']}||{event['tone']}"
    }

def score_hook_subject_bonus(hook: str, subject_weights: dict[str, int] | list[str] | None = None) -> int:
    """
    Rewards hooks more when they match the primary subjects of the reel.
    Supports both:
    - dict[str, int]  -> weighted subjects
    - list[str]       -> plain subjects
    """
    if not hook:
        return 0

    text = hook.lower()

    if isinstance(subject_weights, dict):
        weights = subject_weights
    elif isinstance(subject_weights, list):
        weights = {s: 1 for s in subject_weights}
    else:
        weights = {s: 1 for s in BASE_SUBJECTS}

    matched = [
        (word, weight)
        for word, weight in weights.items()
        if word in text
    ]

    if not matched:
        return 0

    total_weight = sum(weight for _, weight in matched)

    if total_weight >= 8:
        return 8
    elif total_weight >= 5:
        return 6
    elif total_weight >= 3:
        return 4
    else:
        return 2

def detect_hook_pattern(text: str) -> str:
    if not text:
        return "generic"

    lower = text.lower().strip()

    if "?" in text or lower.startswith(("what", "why", "how")):
        return "question"

    if any(p in lower for p in ["secret", "hidden", "look closer", "notice"]):
        return "curiosity"

    if any(p in lower for p in ["only vip", "exclusive", "elite", "private"]):
        return "exclusivity"

    if any(p in lower for p in ["i didn’t expect", "i didn't expect", "never expected", "changed my"]):
        return "transformation"

    if any(p in lower for p in ["feel", "taste", "breeze", "view", "pulse"]):
        return "sensory"

    return "statement"

def get_hook_subject_matches(text: str, subjects: list[str] | None = None) -> list[str]:
    if not text:
        return []

    lower = text.lower()
    subject_list = subjects or list(BASE_SUBJECTS)

    matches = [word for word in subject_list if word in lower]

    # de-dupe, stable order
    seen = set()
    cleaned = []
    for m in matches:
        if m not in seen:
            cleaned.append(m)
            seen.add(m)

    return cleaned[:3]

def score_hook_unified(session: str, hook: str, intent: str | None = None) -> dict:
    session = sanitize_session(session)
    cfg = _load_config(session) or {}

    intent = intent or cfg.get("intent", "discovery")
    content_context = cfg.get("content_context", "auto")
    video_subjects = get_weighted_video_subjects(session)
    first_clip_text = cfg.get("first_clip", {}).get("text", "") or ""

    base_result = score_hook_text(hook, intent)
    first_clip_text = cfg.get("first_clip", {}).get("text", "") or ""

    scored = score_generated_hook(
        hook,
        intent,
        video_subjects=video_subjects,
        context=content_context,
        first_clip_text=first_clip_text
    )

    reasons = list(base_result.get("reasons", []))

    if scored.get("subject_bonus", 0) >= 4:
        reasons.append("Hook aligns well with the detected video subjects.")
    elif scored.get("subject_bonus", 0) >= 2:
        reasons.append("Hook has some alignment with the detected video subjects.")

    if scored.get("visual_anchor_bonus", 0) >= 4:
        reasons.append("Hook references a strong visual element from the reel.")
    elif scored.get("visual_anchor_bonus", 0) >= 2:
        reasons.append("Hook connects to a visible scene element.")

    if scored.get("context_bonus", 0) >= 4:
        reasons.append("Hook strongly matches the selected content context.")
    elif scored.get("context_bonus", 0) >= 2:
        reasons.append("Hook fits the selected content context.")

    if scored.get("first_clip_bonus", 0) >= 5:
        reasons.append("Hook strongly matches the opening clip.")
    elif scored.get("first_clip_bonus", 0) >= 2:
        reasons.append("Hook has some alignment with the opening clip.")

    cleaned = []
    seen = set()
    for r in reasons:
        if r not in seen:
            cleaned.append(r)
            seen.add(r)

    return {
        "score": scored["score"],
        "reasons": cleaned[:4],
        "base_score": scored.get("base_score", 0),
        "curiosity_bonus": scored.get("curiosity_bonus", 0),
        "subject_bonus": scored.get("subject_bonus", 0),
        "visual_anchor_bonus": scored.get("visual_anchor_bonus", 0),
        "context_bonus": scored.get("context_bonus", 0),
        "first_clip_bonus": scored.get("first_clip_bonus", 0),
    }

def build_hook_reason(
    best: dict,
    hooks: list[dict],
    intent: str,
    subjects: list[str] | None = None,
    primary_experience: str = "mixed"
) -> str:
    reasons = []

    text = best.get("text", "") or ""
    score = best.get("score", 0)
    curiosity_bonus = best.get("curiosity_bonus", 0)
    subject_bonus = best.get("subject_bonus", 0)
    visual_anchor_bonus = best.get("visual_anchor_bonus", 0)

    sorted_scores = sorted((h.get("score", 0) for h in hooks), reverse=True)
    second_score = sorted_scores[1] if len(sorted_scores) > 1 else 0
    margin = max(score - second_score, 0)

    # ----------------------------------
    # 1) Comparative strength
    # ----------------------------------
    if margin >= 10:
        reasons.append("Clear top hook in this set.")
    elif margin >= 4:
        reasons.append("Strongest overall hook in this set.")
    else:
        reasons.append("Edges out other strong hook options.")

    # ----------------------------------
    # 2) Curiosity / structure
    # ----------------------------------
    pattern = detect_hook_pattern(text)

    if curiosity_bonus >= 8:
        reasons.append("Creates a strong curiosity gap right away.")
    elif curiosity_bonus >= 4:
        reasons.append("Uses a curiosity-driven opening.")
    elif pattern == "question":
        reasons.append("Question format helps pull the viewer in.")
    elif pattern == "exclusivity":
        reasons.append("Uses exclusivity to make the hook feel more compelling.")
    elif pattern == "transformation":
        reasons.append("Hints at change or payoff, which adds intrigue.")
    elif pattern == "sensory":
        reasons.append("Uses sensory phrasing that fits short-form visuals.")

    # ----------------------------------
    # 3) Subject / visual alignment
    # ----------------------------------
    matches = get_hook_subject_matches(text, subjects)

    if matches:
        reasons.append(f"Matches key reel subjects ({', '.join(matches)}).")
    elif subject_bonus >= 4:
        reasons.append("Aligns strongly with the reel’s main subjects.")
    elif subject_bonus >= 2:
        reasons.append("Has some alignment with the reel’s subjects.")

    if visual_anchor_bonus >= 4:
        reasons.append("Anchors the hook to a strong visual moment.")
    elif visual_anchor_bonus >= 2:
        reasons.append("References a concrete visual detail from the reel.")

    # ----------------------------------
    # 4) Intent alignment
    # ----------------------------------
    if intent == "discovery":
        reasons.append("Fits a discovery-style opening with strong scroll-stop potential.")
    elif intent == "personal":
        reasons.append("Fits a more personal, experience-led opening.")
    elif intent == "aesthetic":
        reasons.append("Fits a more polished, visual-first hook style.")
    elif intent == "informational":
        reasons.append("Fits a clearer, more guided opening style.")

    # ----------------------------------
    # 5) Primary experience alignment
    # ----------------------------------
    if primary_experience == "exploration":
        reasons.append("Matches the exploration feel of the reel.")
    elif primary_experience == "relaxation":
        reasons.append("Supports a calmer, more scenic opening.")
    elif primary_experience == "energy":
        reasons.append("Keeps the opening more dynamic and high-energy.")
    elif primary_experience == "fitness":
        reasons.append("Fits an active, performance-driven opening.")
    elif primary_experience == "luxury":
        reasons.append("Supports a more elevated, premium feel.")
    elif primary_experience == "romance":
        reasons.append("Supports a softer, more emotional tone.")

    # ----------------------------------
    # 6) Deduplicate + return
    # ----------------------------------
    cleaned = []
    seen = set()

    for r in reasons:
        if r not in seen:
            cleaned.append(r)
            seen.add(r)

    return " ".join(cleaned[:4])

def score_hook_curiosity_bonus(hook: str, intent: str = "discovery") -> int:
    """
    Small heuristic bonus for stronger curiosity / tension patterns.
    Keeps bonuses modest so base hook scoring still dominates.
    """
    if not hook:
        return 0

    text = hook.lower().strip()
    bonus = 0

    # Curiosity / mystery
    if "secret" in text:
        bonus += 8
    if "hidden" in text:
        bonus += 7
    if "look closer" in text:
        bonus += 6
    if "what’s" in text or "what's" in text or "what is" in text:
        bonus += 5
    if text.startswith("why "):
        bonus += 4
    if text.startswith("how "):
        bonus += 4
    if "inside" in text:
        bonus += 4
    if "behind" in text:
        bonus += 4
    if "locals" in text:
        bonus += 6
    if "vip" in text or "exclusive" in text:
        bonus += 5

    # Surprise / contrast
    if "didn't expect" in text or "did not expect" in text:
        bonus += 8
    if "never expected" in text:
        bonus += 8
    if "twist" in text:
        bonus += 5
    if "changes everything" in text:
        bonus += 5

    # Question format helps slightly
    if "?" in hook:
        bonus += 4

    # Intent tuning
    if intent == "discovery":
        bonus = round(bonus * 1.1)
    elif intent == "aesthetic":
        bonus = round(bonus * 0.8)
    elif intent == "informational":
        bonus = round(bonus * 0.9)

    # Keep it a light modifier
    return min(bonus, 12)

HOOK_TYPE_RULES = {
    "curiosity": ["secret", "hidden", "look closer", "why", "what", "?"],
    "luxury": ["luxury", "exclusive", "vip", "elite", "private"],
    "transformation": ["from", "turns", "changed", "transformed"],
    "detail": ["detail", "tiny", "ingredient", "inside"],
    "sensory": ["taste", "breeze", "view", "sound", "feel"],
    "status": ["only", "members", "guests", "elite"],
    "emotion": ["i didn't expect", "never expected", "surprised"]
}

def classify_hook_type(text: str) -> str:
    if not text:
        return "generic"

    lower = text.lower()

    for hook_type, keywords in HOOK_TYPE_RULES.items():
        if any(k in lower for k in keywords):
            return hook_type

    return "generic"

def score_hook_visual_anchor_bonus(hook: str) -> int:
    """
    Rewards hooks that mention a concrete, visually filmable object/place.
    This improves short-form clarity.
    """
    if not hook:
        return 0

    text = hook.lower()

    strong_visual_anchors = [
        "cocktail", "drink", "garnish", "bartender",
        "rooftop", "bar", "lounge", "gym",
        "view", "skyline", "pool", "suite", "cherry"
    ]

    weak_abstract_terms = [
        "experience", "moment", "vibe", "feeling", "night", "place"
    ]

    strong_matches = sum(1 for w in strong_visual_anchors if w in text)
    weak_matches = sum(1 for w in weak_abstract_terms if w in text)

    if strong_matches >= 2:
        return 4
    elif strong_matches == 1:
        return 2
    elif weak_matches >= 1:
        return 0

    return 0

def api_generate_hooks(session: str, intent: str | None = None):

    
    print("[HOOK_LAB] Generating hooks for", session)
    session = sanitize_session(session)
    cfg = _load_config(session)

    first_clip_text = cfg.get("first_clip", {}).get("text", "") or ""

    scenes = []
    if cfg.get("first_clip", {}).get("text"):
        scenes.append(cfg["first_clip"]["text"])
    for c in cfg.get("middle_clips", []):
        if c.get("text"):
            scenes.append(c["text"])
    if cfg.get("last_clip", {}).get("text"):
        scenes.append(cfg["last_clip"]["text"])

    if not scenes:
        return {"hooks": []}

    # ----------------------------------------
    # Intent-aware generation guidance
    # ----------------------------------------
    intent = intent or cfg.get("intent", "discovery")

    intent_guidance = ""

    if intent == "discovery":
        intent_guidance = """
        Focus on curiosity gaps, surprise, and open loops.
        Create tension or withheld information.
        Prioritize scroll-stopping energy.
        """

    elif intent == "personal":
        intent_guidance = """
        Focus on emotional pull, intimacy, and personal reaction.
        Make the hook feel human, relatable, and experience-driven.
        """

    elif intent == "aesthetic":
        intent_guidance = """
        Focus on mood, elegance, atmosphere, and sensory intrigue.
        Avoid loud hype. Make it feel refined and visually elevated.
        """

    elif intent == "informational":
        intent_guidance = """
        Focus on clarity, structure, and a clean promise.
        Make the viewer understand what is interesting and why it matters.
        """

    video_subjects = get_weighted_video_subjects(session)
    print("[HOOK_LAB] Weighted subjects:", video_subjects)

    session_context = infer_session_context(session)
    print("[HOOK_LAB] Session context:", session_context)

    content_context = get_content_context(session)

    context_guidance = ""

    if content_context != "auto":

        context_guidance = f"""
        CONTENT CONTEXT: {content_context}

        Hooks should reflect experiences typical to this context.

        Examples:
        cruise → ship life, ocean views, port stops, onboard nightlife
        fitness → workouts, energy, training, strength
        disney → magic moments, rides, theme park atmosphere
        restaurant → dining experience, flavors, chef craft
        nightlife → party energy, music, crowd
        """

    if not client:
        # fallback
        return {
            "hooks": [{"text": scenes[0], "score": 70}]
        }

    prompt = f"""
            Generate 8 high-performing TikTok hooks for a hotel / travel / lifestyle reel.

            Intent: {intent}

            {context_guidance}

            Intent Guidance:
            {intent_guidance}

            Session Context:
            {format_session_context_label(session_context.get("label"))}
            Primary experience: {session_context.get("primary_experience", "mixed")}
            Confidence: {session_context.get("confidence")}
            Signals: {", ".join(session_context.get("signals", [])) or "none"}

            - The hook should match the dominant feeling of the reel.
            - If primary experience is relaxation, prefer calm luxury / unwind / scenic framing.
            - If primary experience is energy, prefer momentum / nightlife / action framing.
            - If primary experience is luxury, prefer exclusivity / elevated experience / premium details.
            - If primary experience is exploration, prefer discovery / movement / destination framing.
            - If primary experience is fitness, prefer strength / effort / discipline / performance framing.
            - If primary experience is romance, prefer intimacy / atmosphere / shared experience framing.

            CRITICAL GOAL:
            These hooks should score highly for:
            - curiosity
            - clarity
            - specificity
            - scroll-stopping power

            STRICT RULES:
            - 6 to 12 words maximum
            - No emojis
            - No hashtags
            - No filler intros
            - Do NOT summarize the whole reel
            - Do NOT sound corporate
            - Do NOT use weak generic phrases unless made highly specific:
            "wait until you see"
            "you won’t believe"
            "hidden gem"
            "this place"
            - The subject should feel clear immediately

            FIRST CLIP ANCHOR RULE:
            - The first clip is the opening visual hook.
            - Hooks must strongly match the visual experience of the FIRST CLIP.
            - Later scenes may support the hook, but should not replace the main opening experience.
            - Hooks should still feel correct if the viewer only saw the first clip.

            HOOK PRIORITY:
            1. First clip visual moment
            2. Emotional curiosity or tension
            3. Supporting trip context if it improves clarity

            - Session context may be used as supporting framing when it strengthens the hook.
            - Avoid generic hooks that could apply to any beach, hotel, or vacation.
            - If possible, anchor the hook to a distinctive detail from the first scene.


            First clip:
            {cfg.get("first_clip", {}).get("text", "")}

            Example:
                If the first clip shows beach relaxation with a cigar,
                good hooks might reference:
                - beach calm
                - ocean breeze
                - cruise relaxation
                - slow luxury moments

                Bad hooks would focus primarily on:
                - dining
                - pool party
                - nightlife

            REQUIRED ANGLES:
            Generate exactly 8 hooks using these 8 angles:
            STRUCTURE DIVERSITY RULE:
            Each hook must use a DIFFERENT structure pattern.

            Use these structures across the hooks:

            1. Question hook
            2. Curiosity reveal
            3. Status / exclusivity
            4. Transformation
            5. Hidden detail
            6. Sensory / vibe
            7. Emotional reaction
            8. Bold statement

            Avoid repeating the same pattern like multiple "What makes..." or "How this..." hooks.

            Scene sequence:
            1. {cfg.get("first_clip", {}).get("text", "")}
            2. {scenes[1] if len(scenes) > 1 else ""}
            3. {scenes[2] if len(scenes) > 2 else ""}
            4. {scenes[3] if len(scenes) > 3 else ""}

            The first scene should carry the hook. Later scenes should feel like supporting payoff or progression.

            Return JSON only:
            {{ "hooks": ["hook1", "hook2", "hook3", "hook4", "hook5", "hook6", "hook7", "hook8"] }}
            """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        primary_experience = session_context.get("primary_experience", "mixed")

        content = resp.choices[0].message.content.strip()
        data = safe_json_extract(content)

        hooks = []
        for text in data.get("hooks", []):

            clean = strip_emojis(text).strip()
            lower = clean.lower()

            scored = score_generated_hook(
                clean,
                intent,
                video_subjects=video_subjects,
                context=content_context,
                first_clip_text=first_clip_text,
                primary_experience=primary_experience,
            )

            score = scored["score"]
            base_score = scored["base_score"]
            curiosity_bonus = scored["curiosity_bonus"]
            subject_bonus = scored["subject_bonus"]
            visual_anchor_bonus = scored["visual_anchor_bonus"]
            vague_penalty = scored["vague_penalty"]

            if any(w in lower for w in ["wait", "watch", "this", "you", "from"]):
                tone = "punchy"
            elif any(w in lower for w in ["calm", "quiet", "slow", "peaceful"]):
                tone = "cinematic"
            else:
                tone = "neutral"

            hook_type = classify_hook_type(clean)

            hooks.append({
            "text": clean,
            "score": score,
            "tone": tone,
            "type": hook_type,
            "base_score": base_score,
            "curiosity_bonus": curiosity_bonus,
            "subject_bonus": subject_bonus,
            "visual_anchor_bonus": visual_anchor_bonus,
            "vague_penalty": vague_penalty
        })



        # Sort best first
        hooks.sort(key=lambda x: x["score"], reverse=True)

        # -----------------------------------------
        # Hook Diversity Enforcement
        # -----------------------------------------

        unique_hooks = []
        seen_types = set()

        for hook in hooks:
            hook_type = hook.get("type", "generic")

            # prioritize unique hook types first
            if hook_type not in seen_types:
                unique_hooks.append(hook)
                seen_types.add(hook_type)

        # fill remaining slots with best remaining hooks
        for hook in hooks:
            if hook not in unique_hooks:
                unique_hooks.append(hook)

        hooks = unique_hooks[:8]

        # 🎯 Intent-based recommendation
        print("choose_best_hook exists:", "choose_best_hook" in globals())
        best = choose_best_hook(hooks, intent)

        if best:
            for h in hooks:
                if h["text"] == best["text"]:
                    h["recommended"] = True
                    h["reason"] = best["reason"]
                    h["why"] = build_hook_reason(
                        h,
                        hooks,
                        intent,
                        video_subjects,
                        session_context.get("primary_experience", "mixed")
                    )



        print("[HOOK_LAB] Hooks generated with intent:", intent)

        return {
            "hooks": hooks,
            "intent": intent
        }
    except RateLimitError:
        return{"hooks": [],
               "error": "quota_exceeded"}

    except Exception as e:
        log_error("[HOOK_LAB]", e)
        return {"hooks": []}


def api_variant_feedback():
    data = request.json or {}
    return record_variant_feedback(data)


def api_generate_body_from_hook(session, hook, style):
    session = sanitize_session(session)
    cfg = _load_config(session)

    scenes = []
    for c in cfg.get("middle_clips", []):
        if c.get("text"):
            scenes.append(c["text"])
    if cfg.get("last_clip", {}).get("text"):
        scenes.append(cfg["last_clip"]["text"])

    if not scenes:
        return {"status": "error", "error": "No scenes found"}

    if not client:
        return {"status": "error", "error": "AI unavailable"}

    prompt = f"""..."""  # your prompt

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[
            {"role": "system", "content": "Return ONLY valid JSON. No markdown. No commentary."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )

    content = (resp.choices[0].message.content or "").strip()
    data = safe_json_extract(content)
    return {"status": "ok", "body": data.get("body", [])}


# -----------------------------------------
# Story Flow Score
# -----------------------------------------

def api_story_flow_score(session: str, captions_text: str | None = None) -> Dict[str, Any]:
    session = sanitize_session(session)
    # If captions provided directly from editor use them
    if captions_text:
        captions = [c.strip() for c in captions_text.split("\n\n") if c.strip()]
    else:
        cfg = _load_config(session)

        captions = []

        if cfg.get("first_clip", {}).get("text"):
            captions.append(cfg["first_clip"]["text"])

        for clip in cfg.get("middle_clips", []):
            if clip.get("text"):
                captions.append(clip["text"])

        if cfg.get("last_clip", {}).get("text"):
            captions.append(cfg["last_clip"]["text"])

    middle = captions[1:]

    if len(middle) < 2:
        return {
            "score": 0,
            "reasons": ["Add at least two captions after the hook to evaluate story flow."]
        }

    if not client:
        return {
            "score": 70,
            "reasons": ["AI unavailable — using default score."]
        }

    prompt = f"""
                Score the narrative flow of these captions from 1–100.

                Important context:
                These captions may represent a short-form influencer highlight reel,
                not a traditional story. Do NOT require a strict beginning–middle–end
                arc to score well.

                Evaluate positively if:
                - Captions feel cohesive as part of the same experience
                - There is a natural progression (arrival → enjoyment → wind-down)
                - Transitions feel logical even if topics change
                - Tone and energy feel consistent

                Evaluate negatively if:
                - Captions feel random or disconnected
                - Order feels confusing
                - Experiences contradict each other

                Captions:
                {json.dumps(middle, indent=2)}

                Return JSON ONLY with:
                score: number
                reasons: list of short bullet points
                """


    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
            {"role": "system", "content": "Return ONLY valid JSON. No markdown. No commentary."},
            {"role": "user", "content": prompt}],
            temperature=0.4,
        )

        content = resp.choices[0].message.content.strip()

        # Extract JSON safely
        result = safe_json_extract(content)


        return {
            "score": int(result.get("score", 70)),
            "reasons": result.get("reasons", []),
        }

    except Exception as e:
        log_error("[STORY_FLOW]", e)
        return {
            "score": 70,
            "reasons": ["Could not evaluate story flow."]
        }

def api_story_flow_improve(session: str, intent: str = "discovery"):
    session = sanitize_session(session)
    cfg = _load_config(session)

    intent = (cfg.get("intent") or "discovery").strip().lower()

    # Collect captions
    hook = cfg.get("first_clip", {}).get("text", "")

    INTENT_GUIDANCE = {
    "discovery": """
    Increase escalation between captions.
    Build momentum.
    Make each scene feel like it raises energy.
    """,
        "luxury": """
    Smooth transitions.
    Maintain elegant tone.
    Avoid abrupt pacing changes.
    """,
        "informational": """
    Improve logical sequencing.
    Clarify transitions between ideas.
    Ensure structured progression.
    """,
        "personal": """
    Strengthen emotional continuity.
    Make transitions feel human and natural.
    Deepen connection between scenes.
    """,
    }

    intent_guidance = INTENT_GUIDANCE.get(intent, INTENT_GUIDANCE["discovery"])


    middle = []
    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            middle.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        middle.append(cfg["last_clip"]["text"])

    if len(middle) < 2:
        return {"error": "Need at least 2 captions"}

    if not client:
        return {"error": "AI unavailable"}

    prompt = f"""
        Improve the narrative flow of these captions based on selected intent.

        Intent: {intent}

        Intent focus:
        {intent_guidance}

        Rules:
        - Do NOT rewrite the opening hook
        - Do NOT add or remove captions
        - Improve flow by rephrasing sentences only
        - Keep captions concise and natural
        - Return JSON only
        - Keep the same meaning per caption (no new facts)

        Captions:
        {json.dumps(middle, indent=2)}

        Return:
        {{ "rewrites": ["caption 1", "caption 2", "..."] }}
        """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )

        content = resp.choices[0].message.content.strip()
        result = safe_json_extract(content)

        rewrites = result.get("rewrites", [])

        if len(rewrites) != len(middle):
            return {
                "status": "ok",
                "updated": False,
                "reason": "No stronger flow rewrite was generated."
            }

        full = [hook] + rewrites

        return {
            "status": "ok",
            "updated": True,
            "text": "\n\n".join(full)
        }

    except Exception as e:
        log_error("[STORY_FLOW_IMPROVE]", e)
        return {
            "status": "error",
            "error": "Failed to improve story flow"
        }


# -------------------------------
# Export mode
# -------------------------------
_EXPORT_MODE = "standard"  # or "fast"


def get_export_mode() -> Dict[str, Any]:
    return {"mode": _EXPORT_MODE}


def set_export_mode(mode: str) -> Dict[str, Any]:
    global _EXPORT_MODE
    if mode not in ("standard", "fast"):
        mode = "standard"
    _EXPORT_MODE = mode
    log_step(f"[EXPORT_MODE] set to {mode}")
    return {"mode": _EXPORT_MODE}

# -------------------------------
# Session sanitizer (backend)
# -------------------------------
def sanitize_session(s: str) -> str:
    if not s:
        return "default"
    s = s.strip().lower().replace(" ", "_")
    return "".join(c for c in s if c.isalnum() or c == "_") or "default"

# ================================
# Session-scoped clip labels
# ================================
LABELS_DIR = os.path.join(os.path.dirname(__file__), "session_labels")
os.makedirs(LABELS_DIR, exist_ok=True)

def _labels_path(session: str) -> str:
    session = sanitize_session(session)
    return os.path.join(LABELS_DIR, session, "labels.json")

def load_labels(session: str) -> Dict[str, str]:
    path = _labels_path(session)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_labels(session: str, labels: Dict[str, str]) -> None:
    path = _labels_path(session)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(labels, f, indent=2)


# -------------------------------
# Upload order (S3 JSON index)
# -------------------------------
def _upload_order_key(session: str) -> str:
    session = sanitize_session(session)
    return clean_s3_key(f"{RAW_PREFIX}{session}/order.json")

def upload_files_to_session(session: str, files) -> dict:
    session = sanitize_session(session)
    uploaded_files = []

    order = load_upload_order(session)

    for file in files:
        if not file or not getattr(file, "filename", ""):
            continue

        filename = secure_filename(file.filename)
        if not filename:
            continue

        key = f"{RAW_PREFIX}{session}/{filename}"
        s3.upload_fileobj(file, S3_BUCKET_NAME, key)
        uploaded_files.append(filename)

        if filename not in order:
            order.append(filename)

    save_upload_order(session, order)

    return {"uploaded": uploaded_files}

def load_upload_order(session: str) -> List[str]:
    key = _upload_order_key(session)
    try:
        obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=key)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("order", [])
    except Exception:
        return []
    
def move_upload_for_session(src: str, dest: str) -> dict:
    """
    Move a file in S3 and keep upload order accurate when moving in/out of raw_uploads.
    """
    if not src or not dest:
        return {"ok": False, "error": "missing_src_or_dest"}

    s3.copy_object(
        Bucket=S3_BUCKET_NAME,
        CopySource=f"{S3_BUCKET_NAME}/{src}",
        Key=dest,
    )
    s3.delete_object(Bucket=S3_BUCKET_NAME, Key=src)

    src_session = None
    src_file = None
    if src.startswith(RAW_PREFIX):
        rel = src[len(RAW_PREFIX):].strip("/")
        parts = rel.split("/", 1)
        if len(parts) == 2:
            src_session = sanitize_session(parts[0])
            src_file = parts[1]

    dest_session = None
    dest_file = None
    if dest.startswith(RAW_PREFIX):
        rel = dest[len(RAW_PREFIX):].strip("/")
        parts = rel.split("/", 1)
        if len(parts) == 2:
            dest_session = sanitize_session(parts[0])
            dest_file = parts[1]

    if src_session and src_file:
        order = load_upload_order(src_session)
        if src_file in order:
            order = [f for f in order if f != src_file]
            save_upload_order(src_session, order)

    if dest_session and dest_file:
        order = load_upload_order(dest_session)
        if dest_file not in order:
            order.append(dest_file)
            save_upload_order(dest_session, order)

    return {"ok": True}


def delete_upload_for_session(key: str) -> dict:
    """
    Delete a file from S3 and remove it from that session's upload order if needed.
    """
    if not key:
        return {"ok": False, "error": "missing_key"}

    s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)

    if key.startswith(RAW_PREFIX):
        rel = key[len(RAW_PREFIX):].strip("/")
        parts = rel.split("/", 1)

        if len(parts) == 2:
            session, filename = parts
            session = sanitize_session(session)

            order = load_upload_order(session)
            if filename in order:
                order = [f for f in order if f != filename]
                save_upload_order(session, order)

    return {"ok": True}

 
def save_upload_order(session: str, order: List[str]) -> None:
    key = _upload_order_key(session)
    try:
        payload = json.dumps({"order": order}, indent=2).encode("utf-8")
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=payload,
            ContentType="application/json",
        )
    except Exception as e:
        logger.error(f"[UPLOAD_ORDER] Failed to save order for {session}: {e}")

# ================================
# UPLOAD MANAGER HELPERS (SESSION)
# ================================
def list_uploads(session: str) -> Dict[str, List[str]]:
    """
    List raw + processed uploads for a given session.
    Returns just filenames (no prefixes), since the JS reconstructs keys.
    """
    session = sanitize_session(session)

    raw_prefix = f"{RAW_PREFIX}{session}/"
    processed_prefix = f"{PROCESSED_PREFIX}{session}/"

    raw = list_videos_from_s3(prefix=raw_prefix)
    processed = list_videos_from_s3(prefix=processed_prefix)

    return {"raw": raw, "processed": processed}

def api_get_labels(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    return {"labels": load_labels(session)}

def get_clip_preview_frames_base64(session: str, filename: str) -> list[str]:
    session = sanitize_session(session)

    video_path = os.path.join(video_folder, session, filename)

    if not os.path.exists(video_path):
        from tiktok_assistant import download_s3_video
        key = f"{RAW_PREFIX}{session}/{filename}"
        tmp = download_s3_video(key)
        if not tmp:
            raise RuntimeError("video not found")
        os.makedirs(os.path.dirname(video_path), exist_ok=True)
        shutil.copy2(tmp, video_path)

    preview_dir = os.path.join("preview_frames", session)
    os.makedirs(preview_dir, exist_ok=True)

    def get_duration_seconds(path: str) -> float:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return max(float(result.stdout.strip()), 0.0)
        except Exception:
            return 0.0

    duration = get_duration_seconds(video_path)

    if duration and duration > 4:
        timestamps = [
            max(0.5, duration * 0.15),
            max(1.0, duration * 0.50),
            max(1.5, duration * 0.85),
        ]
    else:
        timestamps = [0.8, 1.5, 2.2]

    images = []

    for i, ts in enumerate(timestamps, start=1):
        frame_path = os.path.join(preview_dir, f"{filename}_{i}.jpg")

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss", str(ts),
                "-i", video_path,
                "-vframes", "1",
                frame_path
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
            with open(frame_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
                images.append(f"data:image/jpeg;base64,{encoded}")

    if not images:
        raise RuntimeError("No preview frames")

    return images

def api_set_label(session: str, filename: str, label: str | None) -> Dict[str, Any]:
    
    session = sanitize_session(session)

    key = f"{RAW_PREFIX}{session}/{filename}"

    try:
        s3.head_object(Bucket=S3_BUCKET_NAME, Key=key)
    except Exception:
        return {
            "status": "error",
            "error": "video_not_found",
            "file": filename,
            "session": session
        }
    labels = load_labels(session)

    raw = (label or "").strip()
    clean = normalize_label(raw)

    # If label is invalid or too generic, repair it using vision
    if not clean:
        fixed = repair_label(filename, raw, session)
        if fixed:
            labels[filename] = fixed
            save_labels(session, labels)
            return {
                "status": "ok",
                "file": filename,
                "label": fixed,
                "auto_fixed": True,
                "weak": False
            }

        else:
            labels.pop(filename, None)
            save_labels(session, labels)
            return {
                "status": "ok",
                "file": filename,
                "label": "",
                "auto_fixed": False
            }

    # Valid label → save directly
    labels[filename] = clean
    save_labels(session, labels)

    return {
        "status": "ok",
        "file": filename,
        "label": clean,
        "auto_fixed": False,
        "weak": is_weak_label(clean)
    }


def get_clip_preview_base64(session: str, filename: str) -> str:
    """
    Returns data:image/png;base64,... for a clip frame
    """
    img = api_clip_preview(session, filename)

    if not img or "image" not in img:
        raise RuntimeError("No preview frame")

    return img["image"]


def repair_label(filename: str, label: str, session: str) -> str:
    existing_desc = load_analysis_results_session(session).get(filename, "")

    try:
        images_b64 = get_clip_preview_frames_base64(session, filename)
    except Exception as e:
        logger.error(f"[REPAIR_LABEL] No preview frames: {e}")
        return normalize_label(label)

    messages = [
        {
            "role": "system",
            "content": "You generate short, broad visual labels for video clips."
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""
Fix or create a short label for this video.

Current label: "{label or '(empty)'}"
Existing clip analysis: "{existing_desc or '(none)'}"

Rules:
- Max 8 words
- No emojis
- No hashtags
- Do not use hotel name unless visible
- Describe the MAIN scene across these frames, not a tiny detail
- Prefer the broader scene or experience rather than listing specific objects

FOOD SCENE RULE:
If a meal is visible, describe the dining experience rather than listing ingredients.
Example: "elegant cruise dinner" instead of "asparagus and potatoes".

SCENE PRIORITY RULE:
If multiple objects are visible, choose the main activity or environment rather than a small item.

- Use the existing clip analysis if it provides useful context
- Label should help captions and storytelling


"""
                },
                *[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": img
                        }
                    }
                    for img in images_b64
                ]
            ]
        }
    ]

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=20,
            temperature=0.2
        )

        fixed = (resp.choices[0].message.content or "").strip()
        return normalize_label(fixed)

    except Exception as e:
        logger.error(f"[REPAIR_LABEL] Vision failed: {e}")
        return normalize_label(label)

def list_sessions():
    response = s3.list_objects_v2(
        Bucket=S3_BUCKET_NAME,
        Prefix=f"{RAW_PREFIX}",   # e.g. "raw_uploads/"
        Delimiter="/"
    )

    folders = []
    for cp in response.get("CommonPrefixes", []):
        prefix = cp.get("Prefix")
        # remove the raw_uploads/ prefix
        session = prefix.replace(RAW_PREFIX, "").strip("/")
        if session:
            folders.append(session)

    return folders

def rename_session(old_session: str, new_session: str) -> dict:
    old_session = sanitize_session(old_session)
    new_session = sanitize_session(new_session)

    if not old_session or old_session == "default":
        return {"ok": False, "error": "Cannot rename default session"}

    if not new_session:
        return {"ok": False, "error": "New session name is invalid"}

    if old_session == new_session:
        return {"ok": False, "error": "New session name must be different"}

    existing = set(list_sessions())
    if new_session in existing:
        return {"ok": False, "error": "Target session already exists"}

    # -------------------------
    # Block rename during active jobs
    # -------------------------
    if ANALYSIS_JOBS.get(old_session, {}).get("status") == "running":
        return {"ok": False, "error": "Cannot rename while analysis is running"}

    if VARIANT_JOBS.get(old_session, {}).get("status") == "running":
        return {"ok": False, "error": "Cannot rename while variants are running"}

    if YAML_JOBS.get(old_session, {}).get("status") == "running":
        return {"ok": False, "error": "Cannot rename while storyboard generation is running"}

    def list_keys(prefix: str) -> List[str]:
        keys = []
        continuation_token = None

        while True:
            kwargs = {
                "Bucket": S3_BUCKET_NAME,
                "Prefix": clean_s3_key(prefix),
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token

            resp = s3.list_objects_v2(**kwargs)

            for obj in resp.get("Contents", []):
                key = obj.get("Key")
                if key:
                    keys.append(key)

            if resp.get("IsTruncated"):
                continuation_token = resp.get("NextContinuationToken")
            else:
                break

        return keys

    def build_move_plan(old_prefix: str, new_prefix: str) -> List[dict]:
        source_keys = list_keys(old_prefix)
        plan = []

        for old_key in source_keys:
            new_key = clean_s3_key(old_key.replace(old_prefix, new_prefix, 1))
            plan.append({
                "old_key": old_key,
                "new_key": new_key,
            })

        return plan

    def ensure_no_destination_collisions(plan: List[dict]) -> str | None:
        for item in plan:
            try:
                s3.head_object(Bucket=S3_BUCKET_NAME, Key=item["new_key"])
                return item["new_key"]
            except Exception:
                pass
        return None

    def copy_plan(plan: List[dict]):
        for item in plan:
            s3.copy_object(
                Bucket=S3_BUCKET_NAME,
                CopySource={"Bucket": S3_BUCKET_NAME, "Key": item["old_key"]},
                Key=item["new_key"],
            )

    def verify_plan(plan: List[dict]) -> str | None:
        for item in plan:
            try:
                s3.head_object(Bucket=S3_BUCKET_NAME, Key=item["new_key"])
            except Exception:
                return item["new_key"]
        return None

    def delete_plan(plan: List[dict]):
        for item in plan:
            s3.delete_object(Bucket=S3_BUCKET_NAME, Key=item["old_key"])

    def move_dir(old_path: str, new_path: str):
        if os.path.exists(old_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)

    def move_file(old_path: str, new_path: str):
        if os.path.exists(old_path):
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.move(old_path, new_path)

    try:
        # -------------------------
        # Build S3 move plans
        # -------------------------
        raw_old = clean_s3_key(f"{RAW_PREFIX}{old_session}/")
        raw_new = clean_s3_key(f"{RAW_PREFIX}{new_session}/")

        processed_old = clean_s3_key(f"{PROCESSED_PREFIX}{old_session}/")
        processed_new = clean_s3_key(f"{PROCESSED_PREFIX}{new_session}/")

        export_old = clean_s3_key(f"{EXPORT_PREFIX}{old_session}/")
        export_new = clean_s3_key(f"{EXPORT_PREFIX}{new_session}/")

        raw_plan = build_move_plan(raw_old, raw_new)
        processed_plan = build_move_plan(processed_old, processed_new)
        export_plan = build_move_plan(export_old, export_new)

        full_plan = raw_plan + processed_plan + export_plan

        # -------------------------
        # Collision check
        # -------------------------
        collision_key = ensure_no_destination_collisions(full_plan)
        if collision_key:
            return {
                "ok": False,
                "error": f"Rename blocked because destination key already exists: {collision_key}"
            }

        # -------------------------
        # Phase 1: copy all S3 objects
        # -------------------------
        copy_plan(full_plan)

        # -------------------------
        # Phase 2: verify all copies exist
        # -------------------------
        missing_key = verify_plan(full_plan)
        if missing_key:
            return {
                "ok": False,
                "error": f"Rename verification failed. Missing copied object: {missing_key}"
            }

        # -------------------------
        # Phase 3: delete old S3 objects
        # -------------------------
        delete_plan(full_plan)

        # -------------------------
        # Move local/session state
        # -------------------------
        move_dir(
            os.path.join("session_configs", old_session),
            os.path.join("session_configs", new_session),
        )

        move_dir(
            os.path.join(ANALYSIS_BASE_DIR, old_session),
            os.path.join(ANALYSIS_BASE_DIR, new_session),
        )

        move_dir(
            os.path.join(LABELS_DIR, old_session),
            os.path.join(LABELS_DIR, new_session),
        )

        move_dir(
            os.path.join("preview_frames", old_session),
            os.path.join("preview_frames", new_session),
        )

        move_dir(
            os.path.join(video_folder, old_session),
            os.path.join(video_folder, new_session),
        )

        move_file(
            os.path.join(SESSION_PREFS_DIR, f"{old_session}.json"),
            os.path.join(SESSION_PREFS_DIR, f"{new_session}.json"),
        )

        move_file(
            _analysis_status_path(old_session),
            _analysis_status_path(new_session),
        )

        # -------------------------
        # Move in-memory job state
        # -------------------------
        if old_session in ANALYSIS_JOBS:
            ANALYSIS_JOBS[new_session] = ANALYSIS_JOBS.pop(old_session)

        if old_session in VARIANT_JOBS:
            VARIANT_JOBS[new_session] = VARIANT_JOBS.pop(old_session)

        if old_session in YAML_JOBS:
            YAML_JOBS[new_session] = YAML_JOBS.pop(old_session)

        return {
            "ok": True,
            "old_session": old_session,
            "new_session": new_session,
            "moved_s3_objects": len(full_plan),
            "moved_raw_objects": len(raw_plan),
            "moved_processed_objects": len(processed_plan),
            "moved_export_objects": len(export_plan),
        }

    except Exception as e:
        logger.exception("[SESSION_RENAME] failed")
        return {"ok": False, "error": str(e)}

def delete_session(session):
    """Delete ENTIRE session: S3 files + session config + analysis cache."""
    session = sanitize_session(session)

    # ---- 1. Delete S3 raw + processed ----
    raw_pref = f"{RAW_PREFIX}{session}/"
    proc_pref = f"{PROCESSED_PREFIX}{session}/"

    def delete_prefix(prefix):
        resp = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=prefix)
        keys = [{'Key': obj['Key']} for obj in resp.get('Contents', [])]

        if keys:
            s3.delete_objects(
                Bucket=S3_BUCKET_NAME,
                Delete={'Objects': keys, 'Quiet': True}
            )

    delete_prefix(raw_pref)
    delete_prefix(proc_pref)

    # ---- 2. Delete session config directory ----
    cfg_dir = os.path.join("session_configs", session)
    shutil.rmtree(cfg_dir, ignore_errors=True)

    # ---- 3. Delete session analysis cache ----
    cache_dir = os.path.join(ANALYSIS_BASE_DIR, session)
    shutil.rmtree(cache_dir, ignore_errors=True)

    return True

# -------------------------------
# Sync S3 → local tik_tok_downloads/ (per session)
# -------------------------------
def _sync_s3_videos_to_local(session: str) -> List[str]:
    """
    Download all raw videos for a given session from S3 → local cache folder.
    """
    session = sanitize_session(session)
    raw_prefix = f"{RAW_PREFIX}{session}/"

    os.makedirs(video_folder, exist_ok=True)

    keys = list_videos_from_s3(prefix=raw_prefix, return_full_keys=True)
    local_files: List[str] = []

    if not keys:
        log_step(f"[SYNC] No videos found in S3 for session '{session}'")
        return []

    log_step(f"[SYNC] Found {len(keys)} video(s) in S3 under session '{session}'")

    # Maintain custom upload order if present
    order = load_upload_order(session)
    if order:
        keys = sorted(
            keys,
            key=lambda k: order.index(os.path.basename(k))
            if os.path.basename(k) in order
            else 9999,
        )

    # Sync each file
    for key in keys:
        filename = os.path.basename(key)
        session_dir = os.path.join(video_folder, session)
        os.makedirs(session_dir, exist_ok=True)
        local_path = os.path.join(session_dir, filename)


        log_step(f"[SYNC] Checking cache for {filename}")

        if not os.path.exists(local_path):
            log_step(f"[SYNC] Download required: {key}")
            tmp = download_s3_video(key)

            if tmp:
                import shutil

                shutil.copy2(tmp, local_path)
                log_step(f"[SYNC] Downloaded {key} → {local_path}")
            else:
                log_step(f"[SYNC ERROR] Failed to download {key}")
                continue

        local_files.append(filename)

    log_step(f"[SYNC] Synced {len(local_files)} videos for session '{session}'")
    return local_files


def reorder_storyboard(session, new_order):
    session = sanitize_session(session)
    cfg = _load_config(session)

    if not cfg:
        return {"error": "config not found"}

    cfg = reorder_clips(cfg, new_order)

    save_config(session, cfg)

    log_step(f"[REORDER] Updated clip order for session '{session}'")
    return cfg


def build_variant_reason(
    best: dict,
    variants: list,
    intent: str,
    primary_experience: str = "mixed"
) -> str:
    hook = best.get("hook_score", 0)
    flow = best.get("flow_score", best.get("story_flow", 0))
    tone = (best.get("tone") or "").lower()
    text = best.get("text", "") or ""

    reasons = []

    rhythm = score_caption_rhythm(text)
    ending = score_variant_ending(text)

    max_hook = max((v.get("hook_score", 0) for v in variants), default=0)
    max_flow = max((v.get("flow_score", v.get("story_flow", 0)) for v in variants), default=0)

    # ----------------------------------
    # 1) Core win reason
    # ----------------------------------
    if hook >= 75 and flow >= 70:
        reasons.append("Strong hook with smooth scene-to-scene flow.")
    elif hook == max_hook and flow >= 65:
        reasons.append("Best mix of hook strength and pacing.")
    elif hook == max_hook and hook > 0:
        reasons.append("Highest hook strength among the options.")
    elif flow == max_flow and flow > 0:
        reasons.append("Smoothest pacing and progression among the options.")
    elif hook >= 70:
        reasons.append("Strong opening hook that grabs attention.")
    elif flow >= 70:
        reasons.append("Natural progression makes the reel feel more cohesive.")
    else:
        reasons.append("Most balanced overall option.")

    # ----------------------------------
    # 2) Rhythm / ending polish
    # ----------------------------------
    if rhythm >= 80:
        reasons.append("Captions have strong short-form rhythm.")

    if ending >= 75:
        reasons.append("Ending lands cleanly for a stronger finish.")

    # ----------------------------------
    # 3) Intent alignment
    # ----------------------------------
    if intent == "discovery":
        if hook >= 70:
            reasons.append("Fits a discovery-style reel with strong scroll-stopping potential.")
        if "punchy" in tone or "influencer" in tone:
            reasons.append("Tone supports a more attention-grabbing discovery style.")

    elif intent == "personal":
        if flow >= 70:
            reasons.append("Flow supports a more personal storytelling arc.")
        if "story" in tone or "creator" in tone or "influencer" in tone:
            reasons.append("Tone feels more human and experience-driven.")

    elif intent == "aesthetic":
        if flow >= 70:
            reasons.append("Smooth pacing fits a more aesthetic reel.")
        if "minimal" in tone or "cinematic" in tone or "luxury" in tone:
            reasons.append("Tone matches a more polished visual style.")

    elif intent == "informational":
        if flow >= 70:
            reasons.append("Clear progression makes the sequence easier to follow.")
        if "rewrite" in tone or "descriptive" in tone or "story" in tone:
            reasons.append("Tone supports a clearer, more guided format.")

    # ----------------------------------
    # 4) Primary experience alignment
    # ----------------------------------
    if primary_experience == "exploration":
        reasons.append("Matches the exploration vibe of the reel.")
    elif primary_experience == "relaxation":
        reasons.append("Keeps the reel calm, smooth, and easy to watch.")
    elif primary_experience == "energy":
        reasons.append("Maintains stronger energy across the sequence.")
    elif primary_experience == "fitness":
        reasons.append("Keeps the sequence active and momentum-driven.")
    elif primary_experience == "luxury":
        reasons.append("Fits the elevated, premium feel of the reel.")
    elif primary_experience == "romance":
        reasons.append("Supports a softer, more emotional reel tone.")

    # ----------------------------------
    # 5) Deduplicate + return
    # ----------------------------------
    cleaned = []
    seen = set()

    for r in reasons:
        if r not in seen:
            cleaned.append(r)
            seen.add(r)

    return " ".join(cleaned[:4])

SESSION_PREFS_DIR = "session_prefs"
os.makedirs(SESSION_PREFS_DIR, exist_ok=True)

def save_session_pref(session, key, value):
    path = os.path.join(SESSION_PREFS_DIR, sanitize_session(session) + ".json")
    data = {}
    if os.path.exists(path):
        data = json.load(open(path))
    data[key] = value
    json.dump(data, open(path, "w"), indent=2)

def score_variant_ending(text: str) -> int:
    """
    Rewards stronger last-caption endings.
    Doesn't require a literal CTA, just a satisfying close.
    """
    if not text:
        return 0

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return 0

    last = blocks[-1].lower()
    score = 50

    strong_words = [
        "view", "views", "night", "tonight", "sunset", "skyline",
        "unwind", "relax", "escape", "vibes", "rooftop", "city"
    ]

    cta_words = [
        "book", "follow", "save", "visit", "come back", "come here"
    ]

    if any(w in last for w in strong_words):
        score += 20

    if any(w in last for w in cta_words):
        score += 15

    # reward concise endings
    wc = len(last.split())
    if 3 <= wc <= 9:
        score += 10

    return min(score, 100)

def score_generic_phrase_penalty(text: str) -> int:
    lower = text.lower()

    generic_patterns = [
        "check out",
        "caught the",
        "and here's",
        "look at this",
        "watch this",
        "this is",
        "here is",
    ]

    penalty = 0

    for phrase in generic_patterns:
        if phrase in lower:
            penalty -= 3

    return penalty

def compute_variant_smart_score(
    variant: dict,
    intent: str,
    primary_experience: str = "mixed"
) -> int:
    """
    Smart ranking score for choosing the best caption variant.
    """
    hook = variant.get("hook_score", 0)
    flow = variant.get("flow_score", variant.get("story_flow", 0))
    tone = (variant.get("tone") or "").lower()
    text = variant.get("text") or ""

    rhythm = score_caption_rhythm(text)
    ending = score_variant_ending(text)

    intent_cfg = INTENT_PROFILE.get(intent, INTENT_PROFILE["discovery"])

    # start with intent-aware hook/flow weighting
    base = (
        hook * intent_cfg["hook_weight"] +
        flow * intent_cfg["flow_weight"]
    )

    # rhythm + ending polish
    base += rhythm * 0.15
    base += ending * 0.10

    # tone bias bonus
    for t in intent_cfg["tone_bias"]:
        if t in tone:
            base += 3

    primary_bonus = score_primary_experience_variant_bonus(variant, primary_experience)
    base += primary_bonus

    tone_fit_bonus = score_context_tone_fit(primary_experience, tone)
    base += tone_fit_bonus

    generic_penalty = score_generic_phrase_penalty(text)
    base += generic_penalty

    return round(min(base, 100), 2)

def choose_best_variant(
    variants: list,
    intent: str,
    primary_experience: str = "mixed"
):
    if not variants:
        return None

    intent_cfg = INTENT_PROFILE.get(intent, INTENT_PROFILE["discovery"])

    # ----------------------------------
    # 1️⃣ BASE SCORE
    # ----------------------------------
    def base_score(v):
        return compute_variant_smart_score(v, intent, primary_experience)

    scored = [{**v, "_base": base_score(v)} for v in variants]
    scored.sort(key=lambda v: v["_base"], reverse=True)

    # ----------------------------------
    # 🔥 SAFETY FLOOR — prevent weak hook wins
    # ----------------------------------
    MIN_HOOK_RECOMMEND = intent_cfg.get("min_hook", 55)

    strong_hooks = [
        v for v in scored
        if v.get("hook_score", 0) >= MIN_HOOK_RECOMMEND
    ]

    if strong_hooks:
        if len(strong_hooks) > 1:
            # prefer variants aligned with intent tone
            preferred = [
                v for v in strong_hooks
                if any(t in (v.get("tone") or "").lower() for t in intent_cfg["tone_bias"])
            ]
            if preferred:
                best = preferred[0]
            else:
                best = strong_hooks[0]
        else:
            best = strong_hooks[0]
    else:
        # fallback if all hooks are weak
        best = scored[0]

    second = scored[1] if len(scored) > 1 else None
    gap = best["_base"] - (second["_base"] if second else 0)

    # ----------------------------------
    # 2️⃣ CONFIDENCE
    # ----------------------------------
    clear_gap = intent_cfg.get("clear_gap", 15)
    moderate_gap = intent_cfg.get("moderate_gap", 7)

    if gap > clear_gap:
        confidence = "clear"
    elif gap > moderate_gap:
        confidence = "moderate"
    else:
        confidence = "close"

    # attach confidence to all variants
    for v in scored:
        v["confidence"] = confidence

    print(
        "[V3 BASE]",
        "intent=", intent,
        "gap=", round(gap, 2),
        "confidence=", confidence
    )

    # ----------------------------------
    # 3️⃣ FEEDBACK-AWARE FINAL SCORE
    # ----------------------------------
    def final_score(v):
        fb = get_feedback_adjustment(
            intent=intent,
            tone=v.get("tone") or "unknown",
            confidence=confidence,
            recommended=v.get("recommended", False)
        )

        fb = max(min(fb, 6), -6)  # safety clamp
        final = v["_base"] + fb
        v["_final"] = final

        print(
            "[V3 FEEDBACK]",
            "tone=", v.get("tone"),
            "base=", round(v["_base"], 2),
            "fb=", round(fb, 2),
            "final=", round(final, 2)
        )

        print(
            "[VARIANT_RANK]",
            "intent=", intent,
            "primary_experience=", primary_experience,
            "top_scores=", [(v.get("tone"), v.get("_base")) for v in scored[:3]]
        )

        return final

    # ----------------------------------
    # 4️⃣ APPLY FEEDBACK IF UNCERTAIN
    # ----------------------------------
    if confidence != "clear":
        scored.sort(key=final_score, reverse=True)

        # reapply safety floor after feedback sort
        strong_hooks = [
            v for v in scored
            if v.get("hook_score", 0) >= MIN_HOOK_RECOMMEND
        ]

        if strong_hooks:
            best = strong_hooks[0]
        else:
            best = scored[0]

    # ----------------------------------
    # 5️⃣ RETURN DECISION
    # ----------------------------------
    return {
        "id": best["id"],
        "reason": build_variant_reason(
                    best,
                    variants,
                    intent,
                    primary_experience
                ),
        "confidence": confidence
    }

def score_context_tone_fit(primary_experience: str, tone: str) -> int:
    tone = (tone or "").lower()

    if primary_experience == "exploration":
        if "story" in tone or "influencer" in tone or "rewrite" in tone:
            return 4
        if "luxury" in tone or "minimal" in tone:
            return -2

    if primary_experience == "relaxation":
        if "minimal" in tone or "story" in tone or "luxury" in tone:
            return 4
        if "punchy" in tone:
            return -1

    if primary_experience == "energy":
        if "punchy" in tone or "influencer" in tone:
            return 4
        if "minimal" in tone:
            return -2

    if primary_experience == "fitness":
        if "punchy" in tone or "influencer" in tone:
            return 4
        if "minimal" in tone:
            return -1

    if primary_experience == "luxury":
        if "luxury" in tone or "minimal" in tone or "story" in tone:
            return 4

    if primary_experience == "romance":
        if "story" in tone or "minimal" in tone or "luxury" in tone:
            return 4
        if "punchy" in tone:
            return -1

    return 0
    

# -------------------------------
# Analyze APIs (per session)
# -------------------------------
def _analyze_all_videos(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    raw_prefix = f"{RAW_PREFIX}{session}/"

    # 🔥 LOAD LABELS FOR THIS SESSION
    labels = load_labels(session)

    keys = list_videos_from_s3(prefix=raw_prefix, return_full_keys=True)

    if not keys:
        log_step(f"[ANALYZE] No videos found for session '{session}'")
        return {"status": "no_videos", "count": 0}

    count = 0
    for key in keys:
        tmp = download_s3_video(key)
        if not tmp:
            continue

        try:
            basename = os.path.basename(key)

           # 🔥 Pull user label
            label = labels.get(basename, "")

            # 🔥 Only allow Vision to invent a label if user gave nothing
            clean = normalize_label(label)
            if not clean:
                clean = ""   # force Vision-based labeling inside analyze_video()

            # 🔥 Now analysis respects user intent
            desc = analyze_video(tmp, session, clean)


            save_analysis_result_session(session, basename, desc)
            count += 1

        except Exception as e:
            logger.error(f"[ANALYZE][{session}] Failed for {key}: {e}")

    log_step(f"[ANALYZE] Completed analysis for {count} video(s) in session '{session}'")
    return {"status": "ok", "count": count}

def _run_analysis_job(session: str):
    try:
        status = {
            "status": "running",
            "started_at": time.time(),
            "error": None
        }
        ANALYSIS_JOBS[session] = status
        save_analysis_status(session, status)

        _analyze_all_videos(session)

        status["status"] = "done"
        save_analysis_status(session, status)

    except Exception as e:
        logger.exception(f"[ANALYZE][{session}] Background job failed")
        status = {
            "status": "error",
            "error": str(e)
        }
        ANALYSIS_JOBS[session] = status
        save_analysis_status(session, status)

def api_analyze(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)

    # Prevent duplicate runs
    job = ANALYSIS_JOBS.get(session)
    if job and job["status"] == "running":
        return {"status": "already_running"}

    ANALYSIS_JOBS[session] = {
        "status": "running",
        "started_at": time.time(),
        "error": None
    }

    thread = threading.Thread(
        target=_run_analysis_job,
        args=(session,),
        daemon=True
    )
    thread.start()

    return {
        "status": "started"
    }

def api_analyze_status(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)

    job = ANALYSIS_JOBS.get(session)
    if job:
        return job

    # 🔥 fallback to disk (survives reloads)
    saved = load_analysis_status(session)
    if saved:
        return saved

    return {"status": "idle"}
# -------------------------------
# YAML generation (per session)
# -------------------------------
def api_generate_yaml(session: str = "default") -> Dict[str, Any]:
    try:
        session = sanitize_session(session)
        log_step(f"[YAML] Starting YAML generation (session='{session}')…")

        local_files = _sync_s3_videos_to_local(session)

        if not local_files:
            msg = "No videos found. Upload videos first."
            log_error("[YAML]", Exception(msg))
            return {"error": msg}

        analyses_map = load_analysis_results_session(session)
        labels_map = load_labels(session)

        files_for_prompt: List[str] = []
        analyses_for_prompt: List[str] = []

        for fname in local_files:
            analysis = analyses_map.get(fname) or f"Hotel/travel clip: {fname}"
            label = labels_map.get(fname)

            if label:
                desc = f"{analysis}\nINTENT: {label}\n"
            else:
                desc = analysis

            files_for_prompt.append(fname)
            analyses_for_prompt.append(desc)

        prompt = build_yaml_prompt(files_for_prompt, analyses_for_prompt)

        if client:
            log_step("[YAML] Calling LLM for config.yml")
            resp = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            yaml_text = (resp.choices[0].message.content or "").strip()
            yaml_text = yaml_text.replace("```yaml", "").replace("```", "").strip()
            cfg = yaml.safe_load(yaml_text)
        else:
            msg = "OpenAI key missing"
            log_error("[YAML]", Exception(msg))
            return {"error": msg}

        if not isinstance(cfg, dict):
            raise ValueError("LLM did not return valid YAML")

        # Clean filenames (remove spaces, unicode, weird chars, etc.)
        cfg = sanitize_yaml_filenames(cfg)

        # Defaults
        render = cfg.setdefault("render", {})
        if "layout_mode" not in render:
            render["layout_mode"] = "tiktok"

        cta = cfg.setdefault("cta", {})
        cta["duration"] = cta.get("duration", 3.0)

        save_config(session, cfg)

        log_success("[YAML]", "Generated and saved config.yml")
        return cfg

    except Exception as e:
        log_error("[YAML]", e)
        return {"error": str(e)}

def api_clip_preview(session: str, filename: str) -> dict:
    session = sanitize_session(session)

    video_path = os.path.join(video_folder, session, filename)

    # 🔥 Ensure local file exists (fixes broken previews)
    if not os.path.exists(video_path):
        from tiktok_assistant import download_s3_video
        key = f"{RAW_PREFIX}{session}/{filename}"
        tmp = download_s3_video(key)
        if not tmp:
            return {"error": "video not found"}
        os.makedirs(os.path.dirname(video_path), exist_ok=True)
        shutil.copy2(tmp, video_path)

    preview_dir = os.path.join("preview_frames", session)
    os.makedirs(preview_dir, exist_ok=True)

    frame_path = os.path.join(preview_dir, filename + ".jpg")

    # ✅ Always regenerate (accurate previews)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss", "00:00:02.2",
            "-i", video_path,
            "-vframes", "1",
            frame_path
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    with open(frame_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()

    return {
        "image": f"data:image/jpeg;base64,{encoded}"
    }



# -------------------------------
# Config retrieval + saving (global)
# -------------------------------
def api_get_config(session: str = "default") -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    if not cfg:
        return {"yaml": "", "config": {}, "error": "config not found"}

    yaml_text = yaml.safe_dump(cfg, sort_keys=False)
    return {"yaml": yaml_text, "config": cfg}




def api_save_yaml(yaml_text: str, session: str = "default") -> Dict[str, Any]:
    try:
        cfg = yaml.safe_load(yaml_text) or {}
        cfg = sanitize_yaml_filenames(cfg)

        session = sanitize_session(session)
        save_config(session, cfg)

        log_success("[SAVE_YAML]", f"config.yml saved for session '{session}'")
        return {"status": "ok"}

    except Exception as e:
        log_error("[SAVE_YAML]", e)
        return {"status": "error", "error": str(e)}

def estimate_video_length(session: str) -> int:
    analyses = load_analysis_results_session(session) or {}
    clip_count = len(analyses)

    if clip_count == 0:
        return 0

    return max(8, clip_count * 4)


def infer_video_goal(labels: dict) -> str:
    if not labels:
        return "General highlight"

    text = " ".join(labels.values()).lower()

    scores = {
        "Hotel / Travel Highlight": 0,
        "Food & Lifestyle": 0,
        "Event Recap": 0,
        "Relaxation / Vibes": 0,
    }

    for k in ["hotel", "resort", "room", "lobby", "suite", "check-in"]:
        if k in text:
            scores["Hotel / Travel Highlight"] += 1

    for k in ["food", "dinner", "restaurant", "cocktail", "bar", "drink", "brunch"]:
        if k in text:
            scores["Food & Lifestyle"] += 1

    for k in ["concert", "festival", "dj", "show", "party", "stage"]:
        if k in text:
            scores["Event Recap"] += 1

    for k in ["beach", "pool", "sunset", "ocean", "spa", "rooftop", "vibes"]:
        if k in text:
            scores["Relaxation / Vibes"] += 1

    best = max(scores, key=scores.get)

    if scores[best] == 0:
        return "General highlight"

    return best


def api_ai_setup_summary(session: str) -> dict:
    session = sanitize_session(session)

    analyses = load_analysis_results_session(session)
    clip_count = len(analyses)
    has_analysis = clip_count > 0

    labels = load_labels(session)
    label_count = len(labels)
    weak = sum(1 for l in labels.values() if is_weak_label(l))

    hook_data = api_hook_score(session)
    score = hook_data.get("score", 70)

    if score >= 85:
        hook_conf = "clear"
    elif score >= 70:
        hook_conf = "moderate"
    else:
        hook_conf = "weak"

    est = estimate_video_length(session)

    estimated_length = (
        None if not has_analysis
        else f"{max(est - 2, 6)}–{est + 2}s"
    )

    return {
        "has_analysis": has_analysis,
        "clips": clip_count,
        "labels": {
            "total": label_count,
            "weak": weak,
            "quality": (
                "none" if label_count == 0
                else "strong" if weak == 0
                else "mixed"
            )
        },
        "hook_confidence": hook_conf,
        "recommended_goal": infer_video_goal(labels),
        "estimated_length": estimated_length
    }

# -------------------------------
# Captions (editor tab — global)
# -------------------------------

def normalize_location_repetition(captions: list[str]) -> list[str]:
    if not captions:
        return captions

    first = captions[0]

    # Try to detect location phrase from first caption
    match = re.search(r"\bat\s+(.+)$", first, re.IGNORECASE)
    if not match:
        return captions

    location = match.group(1).strip()
    location_lower = location.lower()

    cleaned = [first]  # keep first intact

    for c in captions[1:]:
        if location_lower in c.lower():
            c = re.sub(rf"\s*at\s+{re.escape(location)}", "", c, flags=re.IGNORECASE)
            cleaned.append(c.strip().capitalize())
        else:
            cleaned.append(c)

    return cleaned

def normalize_variant_text(text: str, expected_blocks: int) -> str:
    """
    Ensures ONE caption block per clip.
    Removes extra generations separated by --- or excess blocks.
    """
    if not text:
        return text

    # Split on hard separators first
    if "\n---\n" in text or "\n\n---\n\n" in text:
        text = re.split(r"\n\s*---\s*\n", text)[0]

    # Split into caption blocks
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]

    # Hard cap to expected number of clips
    if len(blocks) > expected_blocks:
        blocks = blocks[:expected_blocks]

    return "\n\n".join(blocks)

def score_story_flow_from_text(text: str, intent: str = "discovery") -> dict:
    """
    Stateless story flow scoring for raw variant text.
    Cached to avoid repeat LLM calls for identical text.
    """

    if not text:
        return {"score": 0, "reasons": ["Not enough captions to evaluate flow."]}

    cache_key = f"{intent}::{text}"

    cached = FLOW_SCORE_CACHE.get(cache_key)
    if cached:
        return cached

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]

    # Ignore first block (hook)
    middle = blocks[1:]

    if len(middle) < 2:
        result = {"score": 0, "reasons": ["Not enough captions to evaluate flow."]}
        FLOW_SCORE_CACHE[cache_key] = result
        return result

    if not client:
        result = {"score": 70, "reasons": ["AI unavailable — default score."]}
        FLOW_SCORE_CACHE[cache_key] = result
        return result

    intent_guidance = ""

    if intent == "discovery":
        intent_guidance = """
            Reward:
            - Escalating energy
            - Curiosity progression
            - Momentum between captions

            Penalize:
            - Flat pacing
            - Repetition
            - Slow exposition
            """

    elif intent == "informational":
        intent_guidance = """
    Reward:
    - Logical sequencing
    - Clear informational build
    - Structured progression

    Penalize:
    - Disorganized order
    - Jumping between ideas
    """

    elif intent == "aesthetic":
        intent_guidance = """
    Reward:
    - Tone consistency
    - Smooth emotional transitions
    - Polished rhythm
    - Atmospheric progression

    Penalize:
    - Abrupt tonal shifts
    - Jarring progression
    """

    elif intent == "personal":
        intent_guidance = """
    Reward:
    - Emotional pull
    - Human continuity
    - Experience that feels personal and lived-in

    Penalize:
    - Monotony
    - Emotional flatness
    - Captions that feel detached
    """

    prompt = f"""
    Score the narrative flow of these captions from 1–100.

    Evaluate based on the following intent-specific guidance:

    {intent_guidance}

    Captions:
    {json.dumps(middle, indent=2)}

    Return JSON only:
    {{
    "score": number,
    "reasons": ["reason1", "reason2"]
    }}
    """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )

        content = (resp.choices[0].message.content or "").strip()

        # ✅ Use your safe_json_extract here (if it returns dict or None)
        data = safe_json_extract(content)
        if not data or "score" not in data:
            result = {"score": 70, "reasons": ["Flow evaluation failed."]}
            FLOW_SCORE_CACHE[cache_key] = result
            return result

        result = {
            "score": int(data.get("score", 70)),
            "reasons": data.get("reasons", []),
        }

        FLOW_SCORE_CACHE[cache_key] = result
        return result

    except Exception:
        result = {"score": 70, "reasons": ["Flow evaluation failed."]}
        FLOW_SCORE_CACHE[cache_key] = result
        return result

def score_caption_rhythm(text: str) -> int:
    if not text:
        return 0

    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return 0

    lengths = [len(b.split()) for b in blocks]
    avg = sum(lengths) / len(lengths)

    variance_penalty = sum(abs(x - avg) for x in lengths) / len(lengths)

    score = 100

    if avg < 3:
        score -= 20
    if avg > 14:
        score -= 20

    score -= min(35, round(variance_penalty * 4))

    return max(0, min(100, round(score)))


def score_cta_presence(text: str) -> int:
    if not text:
        return 0

    blocks = [b.strip().lower() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        return 0

    last_block = blocks[-1]

    has_cta = any(
        phrase in last_block
        for phrase in [
            "follow", "book", "save", "visit",
            "check it out", "dont miss", "don’t miss"
        ]
    )

    return 100 if has_cta else 0

def score_context_relevance_bonus(hook: str, context: str) -> int:
    """
    Rewards hooks that align with the selected content context.
    """

    if not hook or not context or context == "auto":
        return 0

    text = hook.lower()

    CONTEXT_KEYWORDS = {

        "cruise": ["cruise", "deck", "ocean", "sailing", "port", "ship"],

        "hotel": ["hotel", "stay", "suite", "lobby", "rooftop"],

        "restaurant": ["chef", "dish", "restaurant", "plate", "dining"],

        "nightlife": ["party", "dance", "club", "night", "dj"],

        "fitness": ["gym", "workout", "training", "lift", "fitness"],

        "disney": ["magic", "park", "ride", "castle", "disney"],

        "luxury": ["luxury", "exclusive", "elite", "vip"]
    }

    words = CONTEXT_KEYWORDS.get(context, [])

    matches = sum(1 for w in words if w in text)

    if matches >= 2:
        return 6
    elif matches == 1:
        return 3

    return 0

def score_primary_experience_bonus(text: str, primary_experience: str) -> int:
    if not text or not primary_experience or primary_experience == "mixed":
        return 0

    lower = text.lower()

    keywords = {
        "relaxation": ["relax", "unwind", "calm", "quiet", "sunset", "ocean", "pool", "spa", "peaceful"],
        "luxury": ["luxury", "exclusive", "vip", "suite", "elevated", "premium", "rooftop", "gourmet"],
        "energy": ["party", "night", "dance", "crowd", "electric", "hype", "dj", "celebration"],
        "exploration": ["discover", "explore", "city", "wander", "tour", "adventure", "hidden", "destination"],
        "fitness": ["gym", "workout", "training", "strength", "lift", "performance", "push"],
        "romance": ["romantic", "date", "together", "shared", "intimate", "love", "sunset dinner"],
    }

    matches = sum(1 for kw in keywords.get(primary_experience, []) if kw in lower)

    if matches >= 2:
        return 6
    if matches == 1:
        return 3
    return 0

def score_generated_hook(
    clean: str,
    intent: str,
    video_subjects=None,
    context="auto",
    first_clip_text: str = "",
    primary_experience: str = "mixed",
) -> dict:
    lower = clean.lower()

    WEAK_HOOK_PATTERNS = [
        "wait until you see",
        "you won't believe",
        "you won’t believe",
        "hidden gem",
        "this place",
        "this spot",
        "you won't guess",
        "you won’t guess",
    ]

    VAGUE_HOOK_PATTERNS = [
        "this drink",
        "this view",
        "this place",
        "this spot",
        "this is how",
        "feel the",
        "while sipping",
        "while drinking",
        "taste this",
        "watch this",
    ]

    first_clip_bonus = score_first_clip_alignment_bonus(clean, first_clip_text)

    penalty = 0
    vague_penalty = 0

    if any(p in lower for p in WEAK_HOOK_PATTERNS):
        penalty = 8

    if any(p in lower for p in VAGUE_HOOK_PATTERNS):
        vague_penalty = 4

    score_data = score_hook_text(clean, intent)
    base_score = max(score_data["score"] - penalty - vague_penalty, 0)

    curiosity_bonus = score_hook_curiosity_bonus(clean, intent)
    subject_bonus = score_hook_subject_bonus(clean, video_subjects)
    visual_anchor_bonus = score_hook_visual_anchor_bonus(clean)
    context_bonus = score_context_relevance_bonus(clean, context)
    primary_experience_bonus = score_primary_experience_bonus(clean, primary_experience)

    score = min(
        base_score +
        curiosity_bonus +
        subject_bonus +
        visual_anchor_bonus +
        context_bonus +
        first_clip_bonus +
        primary_experience_bonus,
        100
    )

    return {
        "score": score,
        "base_score": base_score,
        "curiosity_bonus": curiosity_bonus,
        "subject_bonus": subject_bonus,
        "visual_anchor_bonus": visual_anchor_bonus,
        "context_bonus": context_bonus,
        "first_clip_bonus": first_clip_bonus,
        "primary_experience_bonus": primary_experience_bonus,
        "vague_penalty": vague_penalty,
    }

def infer_clip_role_v2(text: str) -> str:
    """
    Generic clip role inference that scales better across video types.
    Returns one of:
    intro, activity, social, highlight, payoff, outro
    """
    if not text:
        return "highlight"

    lower = text.lower()

    role_signals = {
        "intro": [
            "arrival", "arrive", "enter", "entrance", "outside", "exterior",
            "lobby", "check-in", "opening", "welcome", "front", "street", "walk-up"
        ],
        "activity": [
            "gym", "workout", "exercise", "training", "run", "swim", "cook", "cooking",
            "mixing", "bartender", "driving", "tour", "exploring", "shopping",
            "working", "making", "preparing", "using", "demo", "testing"
        ],
        "social": [
            "cocktail", "drink", "bar", "wine", "dinner", "brunch", "restaurant",
            "friends", "party", "celebration", "cheers", "meal", "table"
        ],
        "highlight": [
            "room", "suite", "dish", "product", "feature", "interior", "details",
            "close-up", "plating", "showcase", "reveal", "design", "setup"
        ],
        "payoff": [
            "view", "skyline", "sunset", "rooftop", "ocean", "beach", "mountain",
            "panorama", "result", "final look", "finished", "completed", "transformation"
        ],
        "outro": [
            "goodnight", "last look", "final shot", "ending", "end", "night view",
            "wrap-up", "goodbye", "closing"
        ],
    }

    scores = {role: 0 for role in role_signals}

    for role, keywords in role_signals.items():
        for kw in keywords:
            if kw in lower:
                scores[role] += 1

    # light phrase-based boosts
    if any(p in lower for p in ["start", "begin", "first stop", "first up"]):
        scores["intro"] += 2

    if any(p in lower for p in ["then", "after that", "next", "later"]):
        scores["activity"] += 1
        scores["social"] += 1

    if any(p in lower for p in ["end the night", "finish the day", "wind down", "unwind"]):
        scores["payoff"] += 2
        scores["outro"] += 1

    # choose strongest role
    best_role = max(scores, key=scores.get)

    # if no strong signal, default to highlight
    if scores[best_role] == 0:
        return "highlight"

    return best_role

def get_role_priority(role: str) -> int:
    priorities = {
        "intro": 0,
        "activity": 1,
        "social": 2,
        "highlight": 3,
        "payoff": 4,
        "outro": 5,
    }
    return priorities.get(role, 3)

def suggest_storyboard_order(cfg: dict) -> list[dict]:
    """
    Returns clips in a more natural narrative order.
    Does not modify cfg.
    """
    clips = []

    if cfg.get("first_clip"):
        clips.append(cfg["first_clip"])

    clips.extend(cfg.get("middle_clips", []))

    if cfg.get("last_clip"):
        clips.append(cfg["last_clip"])

    enriched = []
    for idx, clip in enumerate(clips):
        source_text = (
            clip.get("text")
            or clip.get("label")
            or clip.get("file")
            or ""
        ).strip()

        role = infer_clip_role_v2(source_text)

        enriched.append({
            **clip,
            "_role": role,
            "_priority": get_role_priority(role),
            "_original_index": idx,
        })

    enriched.sort(key=lambda c: (c["_priority"], c["_original_index"]))

    cleaned = []
    for clip in enriched:
        clip = dict(clip)
        clip.pop("_role", None)
        clip.pop("_priority", None)
        clip.pop("_original_index", None)
        cleaned.append(clip)

    return cleaned


def api_suggest_storyboard_order(session: str) -> dict:
    session = sanitize_session(session)
    cfg = _load_config(session)

    if not cfg:
        return {"suggested_order": []}

    suggested = suggest_storyboard_order(cfg)

    return {
        "suggested_order": suggested
    }

def format_session_context_label(label: str) -> str:
    return (label or "general_lifestyle").replace("_", " ")

def infer_session_context_rules(session: str) -> dict:
    """
    Rule-based fallback context inference.
    """
    session = sanitize_session(session)

    labels = load_labels(session) or {}
    analyses = load_analysis_results_session(session) or {}

    text_parts = []

    # session name itself can carry strong context
    text_parts.append(session.replace("_", " "))

    for fname in labels.keys():
        text_parts.append(fname.replace("_", " ").replace("-", " "))

    for label in labels.values():
        text_parts.append(label)

    for desc in analyses.values():
        text_parts.append(desc)

    blob = " ".join(text_parts).lower()

    context_rules = {
        "cruise_trip": [
            "cruise", "ship", "deck", "port", "cabin", "sea day", "ocean view from ship"
        ],
        "hotel_stay": [
            "hotel", "lobby", "suite", "room", "check-in", "rooftop lounge", "hotel gym"
        ],
        "beach_day": [
            "beach", "sand", "ocean", "shore", "waves", "cigar on the beach"
        ],
        "nightlife_outing": [
            "dance floor", "club", "dj", "bar", "party", "crowd", "nightlife"
        ],
        "dining_experience": [
            "restaurant", "dish", "plate", "chef", "dinner", "cocktail", "bartender", "brunch"
        ],
        "travel_outing": [
            "travel", "vacation", "getaway", "trip", "city view", "rooftop", "ocean", "exploring"
        ],
    }

    scores = {ctx: 0 for ctx in context_rules}
    matched_signals = {ctx: [] for ctx in context_rules}

    for ctx, keywords in context_rules.items():
        for kw in keywords:
            if kw in blob:
                scores[ctx] += 1
                matched_signals[ctx].append(kw)

    best_label = max(scores, key=scores.get)
    best_score = scores[best_label]

    if best_score >= 4:
        confidence = "high"
    elif best_score >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    if best_score == 0:
        return {
            "label": "general_lifestyle",
            "confidence": "low",
            "signals": []
        }

    return {
        "label": best_label,
        "confidence": confidence,
        "signals": matched_signals[best_label][:5]
    }

def build_session_context_evidence(session: str) -> dict:
    session = sanitize_session(session)

    labels = load_labels(session) or {}
    analyses = load_analysis_results_session(session) or {}
    cfg = _load_config(session) or {}

    clip_texts = []

    if cfg.get("first_clip", {}).get("text"):
        clip_texts.append(cfg["first_clip"]["text"])

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            clip_texts.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        clip_texts.append(cfg["last_clip"]["text"])

    return {
        "session_name": session.replace("_", " "),
        "labels": list(labels.values()),
        "analyses": list(analyses.values()),
        "clip_texts": clip_texts,
        "filenames": list(labels.keys()),
    }

PRIMARY_EXPERIENCE_RULES = {
    "relaxation": [
        "beach", "ocean", "spa", "sunset", "calm", "pool", "unwind", "relax"
    ],
    "luxury": [
        "suite", "rooftop", "fine dining", "gourmet", "elegant", "luxury", "vip", "exclusive"
    ],
    "energy": [
        "party", "club", "dj", "dance", "nightlife", "crowd", "celebration"
    ],
    "exploration": [
        "city", "tour", "walk", "street", "travel", "discover", "explore"
    ],
    "fitness": [
        "gym", "workout", "training", "lift", "fitness", "exercise"
    ],
    "romance": [
        "date", "romantic", "couple", "love", "anniversary", "sunset dinner"
    ],
}

def infer_primary_experience_from_evidence(evidence: dict) -> str:
    text_parts = []
    text_parts.extend(evidence.get("labels", []))
    text_parts.extend(evidence.get("analyses", []))
    text_parts.extend(evidence.get("clip_texts", []))
    text_parts.extend(evidence.get("filenames", []))

    blob = " ".join(text_parts).lower()

    scores = {k: 0 for k in PRIMARY_EXPERIENCE_RULES}

    for exp, keywords in PRIMARY_EXPERIENCE_RULES.items():
        for kw in keywords:
            if kw in blob:
                scores[exp] += 1

    best = max(scores, key=scores.get)

    return best if scores[best] > 0 else "mixed"

def with_primary_experience(result: dict, primary_experience: str) -> dict:
    result = result or {}
    result.setdefault("primary_experience", primary_experience)
    result.setdefault("subcontexts", [])
    return result

def infer_session_context(session: str) -> dict:
    """
    Hybrid session context inference:
    - LLM decides macro context when enough evidence exists
    - rules-based fallback if AI fails or evidence is sparse
    """
    session = sanitize_session(session)

    evidence = build_session_context_evidence(session)
    primary_experience = infer_primary_experience_from_evidence(evidence)

    label_count = len(evidence["labels"])
    analysis_count = len(evidence["analyses"])
    clip_count = len(evidence["clip_texts"])

    # cheap fallback when there is very little evidence
    if label_count + analysis_count + clip_count < 2:
        result = infer_session_context_rules(session)
        result.setdefault("primary_experience", primary_experience)
        result.setdefault("subcontexts", [])
        return result

    # fallback if AI unavailable
    if not client:
        result = infer_session_context_rules(session)
        result.setdefault("primary_experience", primary_experience)
        result.setdefault("subcontexts", [])
        return result

    prompt = f"""
Classify the OVERALL reel context.

Session name:
{evidence["session_name"]}

Clip labels:
{json.dumps(evidence["labels"], indent=2)}

Clip analyses:
{json.dumps(evidence["analyses"], indent=2)}

Storyboard clip texts:
{json.dumps(evidence["clip_texts"], indent=2)}

Filenames:
{json.dumps(evidence["filenames"], indent=2)}

Rules:
- Identify the MAIN macro context of the reel, not just one isolated scene
- Prefer the broader experience when clips show multiple parts of the same outing or trip
- Example: a cruise reel with dining, beach, and party clips is still a cruise_trip
- Also identify up to 3 supporting subcontexts
- Be conservative and only use evidence that is present
- If uncertain, choose the most likely broad lifestyle/travel context

Allowed labels:
- cruise_trip
- hotel_stay
- beach_day
- nightlife_outing
- dining_experience
- travel_outing
- fitness_workout
- city_trip
- resort_day
- theme_park_trip
- concert_event
- day_in_the_life
- luxury_experience
- general_lifestyle

Return JSON only:
{{
  "label": "one_allowed_label",
  "confidence": "low|medium|high",
  "signals": ["signal1", "signal2", "signal3"],
  "subcontexts": ["sub1", "sub2", "sub3"],
  "primary_experience": "short phrase for the dominant experience"
}}
"""

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": "Return ONLY valid JSON. No markdown. No commentary."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        content = (resp.choices[0].message.content or "").strip()
        data = safe_json_extract(content)

        allowed = {
            "cruise_trip",
            "hotel_stay",
            "beach_day",
            "nightlife_outing",
            "dining_experience",
            "travel_outing",
            "fitness_workout",
            "city_trip",
            "resort_day",
            "theme_park_trip",
            "concert_event",
            "day_in_the_life",
            "luxury_experience",
            "general_lifestyle",
        }

        label = data.get("label", "")
        confidence = data.get("confidence", "low")
        signals = data.get("signals", []) or []
        subcontexts = data.get("subcontexts", []) or []

        llm_primary_experience = data.get("primary_experience", "").strip()
        if not llm_primary_experience:
            llm_primary_experience = primary_experience

        if label not in allowed:
            return with_primary_experience(infer_session_context_rules(session), primary_experience)

        if confidence not in {"low", "medium", "high"}:
            confidence = "low"

        return {
            "label": label,
            "confidence": confidence,
            "signals": signals[:5],
            "subcontexts": subcontexts[:3],
            "primary_experience": llm_primary_experience,
        }

    except Exception as e:
        logger.warning(f"[SESSION_CONTEXT] LLM inference failed: {e}")
        result = infer_session_context_rules(session)
        result.setdefault("primary_experience", primary_experience)
        result.setdefault("subcontexts", [])
        return result


def api_session_context(session: str) -> dict:
    session = sanitize_session(session)
    return infer_session_context(session)

def api_generate_variants(
    session: str,
    modes: dict,
    selected_hook: str | None,
    content_mode: str = "caption",
) -> Dict[str, Any]:
    
    session = sanitize_session(session)
    cfg = _load_config(session) or {}
    session_context = infer_session_context(session)
    content_context = cfg.get("content_context", "auto")
    primary_experience = session_context.get("primary_experience", "mixed")

    first_clip_text = cfg.get("first_clip", {}).get("text", "") or ""

    # --------------------------------------------------
    # Collect captions
    # --------------------------------------------------
    captions = []

    if selected_hook:
        captions.append(selected_hook)
    elif cfg.get("first_clip", {}).get("text"):
        captions.append(cfg["first_clip"]["text"])

    hook_locked = bool(selected_hook)

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            captions.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        captions.append(cfg["last_clip"]["text"])

    captions = normalize_location_repetition(captions)
    base = "\n\n".join(captions).strip()

    if not base:
        return {
            "variants": [{
                "text": "⚠ No captions found in YAML.",
                "tone": "Error"
            }]
        }

    # --------------------------------------------------
    # Tone labels
    # --------------------------------------------------
    STYLE_TONE_LABELS = {
        "rewrite": "Standard · Clean Rewrite",
        "hook": "Hook-Optimized · Scroll Stopper",
        "punchy": "Punchy · TikTok / Reels",
        "story": "Storytelling · Voiceover",
        "influencer": "Influencer · Creator Style",
        "minimal": "Minimal · Luxury Aesthetic",
    }

    # --------------------------------------------------
    # Strict per-style rule blocks
    # --------------------------------------------------
    STYLE_RULES = {
        "rewrite": """
Clean rewrite. Improve clarity and flow.
Keep captions natural and concise.
""",
        "hook": """
ONLY improve the first caption.
Make it scroll-stopping.
Do NOT rewrite remaining captions except minor polish.
""",
        "punchy": """
Energetic TikTok creator tone.
Shorter sentences.
Stronger verbs.
High engagement energy.
""",
        "story": """
Smooth storytelling progression.
Natural emotional build.
Feels like spoken voiceover.
""",
        "influencer": """
Confident creator voice.
Personal, direct, charismatic.
Natural but elevated tone.
""",
        "minimal": """
Minimal luxury aesthetic.
CRITICAL RULES:
- 3–7 words per NON-HOOK caption
- No emojis
- No hashtags
- No full sentences (except hook)
- No brand repetition
- Editorial, high-end tone
"""
    }

    enabled_styles = [
        style for style, enabled in modes.items()
        if enabled and style in STYLE_RULES
    ]

    if not enabled_styles:
        return {"variants": []}

    if not client:
        return {"variants": [], "error": "ai_unavailable"}

    try:

        # --------------------------------------------------
        # Build strict unified prompt
        # --------------------------------------------------
        style_sections = ""
        for style in enabled_styles:
            style_sections += f"""
                STYLE: {style}
                RULES:
                {STYLE_RULES[style]}
                """

        system_prompt = (
            CAPTION_ONLY_GUARDRAIL +
            " Rewrite captions in blocks separated by ONE blank line. "
            "Keep EXACT same number of caption blocks as input."
        )

        system_prompt += f" Each variant MUST contain exactly {len(captions)} caption blocks."

        if hook_locked:
            system_prompt += (
                " The FIRST caption is LOCKED. "
                "It MUST be used verbatim in every variant. "
                "Do NOT change it."
            )

        progression_guidance = """
            STORY FLOW RULES:

            - Captions should feel like one cohesive experience rather than unrelated highlights.
            - Arrange moments in the most natural experiential order.

            Preferred progression patterns include:
            arrival / setup -> activity -> social moment -> wind-down / view
            activity -> celebration -> relaxation
            day -> evening -> night
            energy -> highlight -> unwind

            - When multiple experiences appear (for example gym, cocktails, rooftop), arrange them in the most natural order.
            - Preserve the implied chronological flow when possible.
            - Avoid jumping back and forth between topics unless the transition feels intentional.
            - Avoid repeating the same subject twice unless the experience escalates.
            - Use the subjects mentioned in the captions to determine the most natural sequence.

            Goal: captions should feel like one continuous outing or hotel stay.
            """
        content_context_guidance = ""

        if content_context != "auto":
            content_context_guidance = f"""
            CONTENT CONTEXT:
            - User-selected content context: {content_context}

            Use this as a strong creative anchor for the captions.
            Match the tone, sequencing, and storytelling to this context when supported by the clips.

            Examples:
            - cruise -> trip moments, ocean views, port stops, onboard dining, nightlife
            - hotel -> stay experience, room/lobby/amenities, rooftop, relaxation
            - fitness -> workout progression, effort, energy, recovery
            - disney -> park atmosphere, rides, wonder, magic, nighttime finale
            - nightlife -> energy, lights, crowd, drinks, celebration
            - restaurant -> dining experience, chef craft, plating, ambiance
            """

        context_guidance = f"""
            SESSION CONTEXT:
            - Overall reel context: {format_session_context_label(session_context.get("label"))}
            - Primary experience: {session_context.get("primary_experience", "mixed")}
            - Confidence: {session_context.get("confidence")}
            - Signals: {", ".join(session_context.get("signals", [])) or "none"}

            Use this context to make the captions feel like one connected outing or experience.
            Let the primary experience shape the dominant mood and sequencing.
            Do not force context that is not supported by the clips.
            """
        
        caption_scene = """CAPTION SCENE RULE:
            When describing food clips, prioritize the dining experience over specific ingredients unless the dish itself is the focus of the reel.
        """

        content_mode = (content_mode or "caption").strip().lower()

        content_mode_guidance = ""

        if content_mode == "voiceover":
            content_mode_guidance = """
            CONTENT MODE: voiceover

            Rewrite captions so they feel more like natural spoken narration.
            Prioritize:
            - conversational phrasing
            - smoother transitions
            - creator-style voice
            - complete thoughts
            - less punchy headline energy

            Avoid:
            - abrupt hook-only phrasing
            - overly compressed caption fragments
            - robotic or salesy language
            """
        else:
            content_mode_guidance = """
            CONTENT MODE: caption

            Rewrite captions for on-screen short-form text.
            Prioritize:
            - punchy wording
            - short visual lines
            - scroll-stopping phrasing
            - concise blocks
            - aesthetic and TikTok-friendly rhythm

            Avoid:
            - long spoken-style sentences
            - overly narrative phrasing
            - voiceover-style exposition
            """

        user_prompt = f"""
            Generate caption variants using the style definitions below.

            {content_mode_guidance}

            {style_sections}

            {progression_guidance}

            {content_context_guidance}

            {context_guidance}

            {caption_scene}

            - Do NOT return hook-only outputs.
            - Every variant must include all caption blocks, not just the first line.
            - Preserve full sequence length.

            Return STRICT JSON:

            {{
            "variants": [
                {{
                "style": "style_name",
                "text": "caption blocks separated by blank lines"
                }}
            ]
            }}

            Captions:
            {base}
            """

        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.6,
        )

        content = resp.choices[0].message.content.strip()
        data = safe_json_extract(content)

        expected_blocks = len(captions)
        logger.info(f"[VARIANTS RAW] {content[:2000]}")
        logger.info(f"[VARIANTS PARSED COUNT] {len(data.get('variants', [])) if isinstance(data, dict) else 0}")

        logger.warning(f"[VARIANTS] expected_blocks={expected_blocks}")
        logger.warning(f"[VARIANTS] raw model response: {content}")
        logger.warning(f"[VARIANTS] parsed data: {data}")

        variants = []

        # --------------------------------------------------
        # Build variants
        # --------------------------------------------------
        

        for idx, item in enumerate(data.get("variants", [])):
            raw_text = item.get("text", "")
            style_key = item.get("style", "")

            logger.warning(f"[VARIANTS] item {idx} style={style_key}")
            logger.warning(f"[VARIANTS] item {idx} raw_text={raw_text!r}")

            normalized = normalize_variant_text(
                raw_text,
                expected_blocks=expected_blocks
            )

            blocks = [b.strip() for b in re.split(r"\n\s*\n", normalized) if b.strip()]

            if len(blocks) == 1 and "\n" in normalized:
                blocks = [b.strip() for b in normalized.splitlines() if b.strip()]
            logger.warning(f"[VARIANTS] item {idx} block_count={len(blocks)} blocks={blocks}")

            if hook_locked:
                # Case 1: model returned body only -> prepend locked hook
                if len(blocks) == expected_blocks - 1:
                    blocks = [selected_hook] + blocks

                # Case 2: model returned full set -> force first block to locked hook
                elif len(blocks) >= expected_blocks:
                    blocks = blocks[:expected_blocks]
                    blocks[0] = selected_hook

                # Case 3: incomplete / bad output -> skip it
                else:
                    logger.warning(
                        f"[VARIANTS] Skipping incomplete variant {idx}: "
                        f"expected {expected_blocks} blocks with locked hook, got {len(blocks)}"
                    )
                    continue

            else:
                if len(blocks) != expected_blocks:
                    logger.warning(
                        f"[VARIANTS] Skipping incomplete variant {idx}: "
                        f"expected {expected_blocks} blocks, got {len(blocks)}"
                    )
                    continue

            normalized = "\n\n".join(blocks)

            variants.append({
                "id": idx,
                "text": normalized,
                "tone": STYLE_TONE_LABELS.get(style_key, style_key),
            })

        if not variants:
            return {"variants": []}

        # --------------------------------------------------
        # Attach scoring (unchanged)
        # --------------------------------------------------
        intent = cfg.get("intent", "discovery")

        for v in variants:
            text = v.get("text", "")
            blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
            first_block = blocks[0] if blocks else ""

            video_subjects = get_weighted_video_subjects(session)
            content_context = cfg.get("content_context", "auto")
            hook_score = score_generated_hook(
                first_block,
                intent,
                video_subjects=video_subjects,
                context=content_context,
                first_clip_text=first_clip_text,
                primary_experience=primary_experience,
            ).get("score", 0)

            flow_result = score_story_flow_from_text(text)
            flow_score = flow_result.get("score", 0)
            rhythm_score = score_caption_rhythm(text)
            cta_score = score_cta_presence(text)

            v["hook_score"] = hook_score
            v["story_flow"] = flow_score
            v["flow_score"] = flow_score
            v["rhythm_score"] = rhythm_score
            v["cta_score"] = cta_score
            v["uses_selected_hook"] = hook_locked
            v["smart_score"] = compute_variant_smart_score(v, intent, primary_experience)

        best = choose_best_variant(variants, intent, primary_experience)

        if best:
            for v in variants:
                v["recommended"] = (v.get("id") == best["id"])
                v["confidence"] = best["confidence"]

                if v["recommended"]:
                    v["recommend_reason"] = best["reason"]
                    save_session_pref(session, "last_best_tone", v.get("tone"))
                    save_session_pref(session, "last_intent", intent)

        # Sort recommended first, then by hook, then by flow
        variants.sort(
            key=lambda v: (
                1 if v.get("recommended") else 0,
                v.get("hook_score", 0),
                v.get("flow_score", v.get("story_flow", 0))
            ),
            reverse=True
        )

        return {"variants": variants[:7]}

    except RateLimitError:
        log_error("[VARIANTS]", Exception("quota exceeded"))
        return {"variants": [], "error": "quota_exceeded"}

    except Exception as e:
        log_error("[VARIANTS]", e)
        return {"variants": [], "error": "generation_failed"}

def api_save_captions(text: str, session: str) -> Dict[str, Any]:
    try:
        session = sanitize_session(session)
        cfg = _load_config(session)

        if not cfg:
            return {"status": "error", "error": "config not found"}

        blocks = [
            b.strip()
            for b in re.split(r"\n\s*\n", text)
            if b.strip()
        ]

        idx = 0

        if cfg.get("first_clip") and idx < len(blocks):
            cfg["first_clip"]["text"] = blocks[idx]
            idx += 1

        for clip in cfg.get("middle_clips", []):
            if idx < len(blocks):
                clip["text"] = blocks[idx]
                idx += 1

        if cfg.get("last_clip") and idx < len(blocks):
            cfg["last_clip"]["text"] = blocks[idx]

        save_config(session, cfg)

        log_success("[CAPTIONS]", f"Saved {len(blocks)} caption block(s)")

        return {
            "status": "ok",
            "count": len(blocks),
            "text": text,
            "config": cfg,
        }

    except Exception as e:
        log_error("[CAPTIONS]", e)
        return {"status": "error", "error": str(e)}


# -------------------------------
# EXPORT 
# -------------------------------
def run_export_task(task_id: str, session: str, optimized: bool):
    try:
        session = sanitize_session(session)

        # ----------------------------------------------------
        # Load config from SINGLE SOURCE OF TRUTH
        # ----------------------------------------------------
        cfg = _load_config(session)
        if not cfg:
            msg = f"config not found for session '{session}'"
            export_tasks[task_id]["status"] = "error"
            export_tasks[task_id]["error"] = msg
            return

        # 🚨 CANCEL CHECK (1)
        if export_tasks[task_id].get("cancel_requested"):
            export_tasks[task_id]["status"] = "cancelled"
            return

        # ----------------------------------------------------
        # Render the video (long step)
        # ----------------------------------------------------
        out_path = edit_video(session_id=session, optimized=optimized)
        if not out_path:
            raise RuntimeError("edit_video() returned no output path")

        filename = os.path.basename(out_path)

        # 🚨 CANCEL CHECK (2)
        if export_tasks[task_id].get("cancel_requested"):
            export_tasks[task_id]["status"] = "cancelled"
            return

        # ----------------------------------------------------
        # Upload to S3
        # ----------------------------------------------------
        prefix = EXPORT_PREFIX.rstrip("/")
        export_key = clean_s3_key(f"{prefix}/{session}/{filename}")

        s3.upload_file(out_path, S3_BUCKET_NAME, export_key)
        log_step(f"[EXPORT] Uploaded to s3://{S3_BUCKET_NAME}/{export_key}")

        url = generate_signed_download_url(export_key)

        # ----------------------------------------------------
        # Update Task Result
        # ----------------------------------------------------
        export_tasks[task_id]["status"] = "done"
        export_tasks[task_id]["download_url"] = url
        export_tasks[task_id]["filename"] = filename
        export_tasks[task_id]["s3_key"] = export_key

    except Exception as e:
        log_error("[EXPORT]", e)
        export_tasks[task_id]["status"] = "error"
        export_tasks[task_id]["error"] = str(e)

# -------------------------------
# TTS / CTA Settings (global)
# -------------------------------

def api_set_tts(session: str, enabled: bool, voice: str | None) -> Dict[str, Any]:

    cfg = _load_config(session)
    r = cfg.setdefault("render", {})
    r["tts_enabled"] = bool(enabled)
    if voice:
        r["tts_voice"] = voice
    save_config(session, cfg)
    return {"status": "ok", "render": r}

def api_set_cta(session: str, enabled: bool, text: str | None, voiceover: bool | None, duration: float | None = None):
    session = sanitize_session(session)
    cfg = _load_config(session)

    c = cfg.setdefault("cta", {})
    c["enabled"] = bool(enabled)

    if text is not None:
        c["text"] = text

    if voiceover is not None:
        c["voiceover"] = bool(voiceover)

    if duration is not None:
        try:
            c["duration"] = float(duration)
        except:
            c["duration"] = 3.0
    else:
        c.setdefault("duration", 3.0)

    save_config(session, cfg)
    return {"status": "ok", "cta": c}



def rewrite_captions(cfg: dict, style: str) -> list[str]:
    """
    Rewrites all captions in config.yml according to overlay style.
    Returns list of rewritten captions in same order.
    Does NOT mutate config.
    """

    captions = []

    if cfg.get("first_clip", {}).get("text"):
        captions.append(cfg["first_clip"]["text"])

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            captions.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        captions.append(cfg["last_clip"]["text"])

    if not captions or not client:
        return captions

    prompt = f"""
Rewrite these captions to match the style "{style}".

Rules:
- Do NOT change the number of captions
- Do NOT add or remove captions
- Keep meaning the same
- Improve tone, energy, pacing, and emotional impact
- Use emojis only if appropriate to the style
- Keep captions concise and TikTok-friendly

Captions:
{json.dumps(captions, indent=2)}

Return JSON ONLY:
{{
  "rewrites": ["caption 1", "caption 2", "..."]
}}
"""

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
            {"role": "system", "content": "Return ONLY valid JSON. No markdown. No extra text."},
            {"role": "user", "content": prompt}
            ],
            temperature=0.5,
        )

        content = resp.choices[0].message.content.strip()
        data = safe_json_extract(content)

        rewrites = data.get("rewrites", [])

        if len(rewrites) != len(captions):
            return captions  # fail safe

        return rewrites

    except Exception as e:
        logger.error(f"[REWRITE_CAPTIONS] {e}")
        return captions


# -------------------------------
# Overlay + Timings + fg_scale
# -------------------------------
def api_apply_overlay(
    session_id: str,
    style: str,
    rewrite: bool,
    overlay_text_override: str | None = None
) -> Dict[str, Any]:

    try:
        session_id = sanitize_session(session_id)
        cfg = _load_config(session_id)

        # Always apply visual overlay settings
        apply_overlay(
            session_id,
            style,
            rewrite=False   # 🔥 NEVER rewrite here
        )

        # --------------------------------------
        # If rewrite requested → return PROPOSAL
        # --------------------------------------
        if rewrite:
            if not cfg.get("first_clip", {}).get("text"):
                return {"status": "error", "error": "No captions to rewrite"}

            proposed = rewrite_captions(cfg, style)

            return {
                "status": "proposed",
                "style": style,
                "proposed": "\n\n".join(proposed)
            }

        # Visual-only path
        return {
            "status": "ok",
            "style": style,
            "rewrite": False,
            "session": session_id,
        }

    except Exception as e:
        log_error("[OVERLAY]", e)
        return {"status": "error", "error": str(e)}
    

# ================================
# OVERLAY PREVIEW (IMAGE MOCK)
# ================================
def api_overlay_preview(session: str, style: str) -> dict:
    
    session = sanitize_session(session)
    cfg = _load_config(session)
    first = cfg.get("first_clip",{}).get("text","")



    style = (style or "ai_recommended").lower()
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["ai_recommended"])

    fontsize = preset.get("fontsize", 64)
    y_expr = preset.get("y_expr", "(h*0.50)")
    h = 1920  # for eval

    try:
        y_frac = float(preset.get("y_frac", 0.50))
        y = h * max(0.0, min(y_frac, 1.0))
    except:
        y = h * 0.50

    img = Image.new("RGB", (1080,1920), (0,0,0))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)

    # wrap text 20 chars per line
    wrapped = "\n".join(first[i:i+20] for i in range(0, len(first), 20))

    text_w, text_h = draw.multiline_textsize(wrapped, font=font, spacing=12)
    x = (1080-text_w)/2

    draw.text((x,y), wrapped, fill="white", font=font, spacing=12,
              stroke_width=4, stroke_fill="black")

    buff = BytesIO()
    img.save(buff, format="PNG")
    encoded = base64.b64encode(buff.getvalue()).decode()

    return {"image": "data:image/png;base64,"+encoded}

# ================================
# Preview Render (no rewrite applied)
# ================================
def generate_overlay_preview(session_id: str, style: str) -> str:
    """
    Returns base64 PNG preview frame for caption overlay visual testing
    """

    from io import BytesIO
    import base64
    from PIL import Image, ImageDraw, ImageFont

    cfg = _load_config(session_id)
    print(f"[PREVIEW] loaded config keys: {list(cfg.keys()) if cfg else 'NO CONFIG FOUND'}")

    text = cfg.get("first_clip", {}).get("text", "") or "No captions found"

    print(f"[PREVIEW] first clip text: {text[:60]}")

    # style fallback
    preset = STYLE_PRESETS.get(style, STYLE_PRESETS["ai_recommended"])
    fontsize = preset.get("fontsize", 72)

    img = Image.new("RGB", (1080,1920), (10,10,10))
    draw = ImageDraw.Draw(img)

    # Try multiple font locations for Render flexibility
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fontsize)
    except:
        font = ImageFont.load_default()

    # quick wrapping
    wrapped = "\n".join(text[i:i+22] for i in range(0,len(text),22))
    # Cross-compatible text measurement
    try:
        # New Pillow API
        bbox = draw.multiline_textbbox((0,0), wrapped, font=font, spacing=8)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
    except:
        # Older versions fallback
        w, h = draw.textsize(wrapped, font=font)


    x = (1080 - w)//2
    y = 1450  # where captions normally render

    draw.text((x,y), wrapped, fill="white", font=font, stroke_width=4, stroke_fill="black")

    # export PNG → base64
    buff = BytesIO()
    img.save(buff, format="PNG")

    return base64.b64encode(buff.getvalue()).decode()



def api_apply_timings(session_id: str, smart: bool = False) -> Dict[str, Any]:
    try:

        session_id = sanitize_session(session_id)
        pacing = "cinematic" if smart else "standard"

        # Apply timings for THIS session's config
        apply_smart_timings(session_id, pacing)

        log_success("[TIMINGS]",
                    f"Applied timings '{pacing}' for session '{session_id}'")

        return {"status": "ok", "pacing": pacing, "session": session_id}

    except Exception as e:
        log_error("[TIMINGS]", e)
        return {"status": "error", "error": str(e)}



def api_set_layout(session: str, mode: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    r = cfg.setdefault("render", {})
    r["layout_mode"] = mode

    save_config(session, cfg)

    return {"status": "ok", "layout_mode": mode}


def api_fgscale(session: str, fgscale_mode: str, fgscale: float | None) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    r = cfg.setdefault("render", {})
    r["fgscale_mode"] = fgscale_mode
    r["fgscale"] = fgscale

    save_config(session, cfg)

    return {"status": "ok", "render": r}


# -------------------------------
# Chat
# -------------------------------
def api_chat(message: str, session: str = "default") -> Dict[str, Any]:
    session = sanitize_session(session)

    if not client:
        reply = f"(no OpenAI key) You said: {message}"
        log_error("[CHAT]", Exception("No OpenAI key"))
        return {"reply": reply}

    # Load context
    analyses = load_analysis_results_session(session)
    cfg = _load_config(session)

    labels = load_labels(session)


    # Build the smart contextual prompt
    prompt = f"""
            You are the user's TikTok video-editing assistant.
            They are editing a hotel/travel reel using multiple vertical clips.

            ### VIDEO CLIPS + AI ANALYSIS
            {json.dumps(analyses, indent=2)}

            ### USER-PROVIDED CLIP LABELS (INTENT)
            {json.dumps(labels, indent=2)}


            ### CURRENT YAML CONFIG (do NOT modify unless asked)
            {yaml.safe_dump(cfg, sort_keys=False)}

            ### YOUR JOB
            - Answer questions as an expert TikTok travel creator.
            - Suggest better hooks, captions, CTAs, pacing ideas, and storytelling.
            - Provide advice on improving scenes or captions based on the clip analyses.
            - DO NOT modify YAML unless explicitly asked like:
                "change the caption", "rewrite my CTA", "shorten clip 2"
            - When asked to change something, ONLY describe what to change; do not output YAML.

            User says:
            {message}
            """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        reply = (resp.choices[0].message.content or "").strip()
        log_success("[CHAT]", "Replied successfully")
        return {"reply": reply}

    except Exception as e:
        log_error("[CHAT]", e)
        return {"reply": f"Error: {e}"}


