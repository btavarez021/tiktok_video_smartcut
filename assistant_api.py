# assistant_api.py — session-aware uploads + YAML + analysis

import os
import json
import glob
import logging
import re
from typing import Dict, Any, List
import yaml
from openai import OpenAI
import base64
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import subprocess
from flask import request
from assistant_log import log_step, log_error, log_success
from tiktok_template import edit_video, video_folder,get_config_path, STYLE_PRESETS
from s3_config import (
    s3,
    S3_BUCKET_NAME,
    RAW_PREFIX,
    EXPORT_PREFIX,
    S3_REGION,
    clean_s3_key,
    PROCESSED_PREFIX,
)
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

logger = logging.getLogger(__name__)
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
EVENTS_PATH = os.path.join(DATA_DIR, "feedback_events.jsonl")
AGG_PATH = os.path.join(DATA_DIR, "feedback_aggregates.json")

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
TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------
# Helpers
# -------------------------------
def _load_config(session: str) -> dict:
    """Load the session's config.yml safely."""
    session = sanitize_session(session)
    config_path = get_config_path(session)
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

INTENT_PROFILE = {
    "discovery": {
        "hook_weight": 0.7,
        "flow_weight": 0.3,
        "tone_bias": ["punchy", "influencer"],
    },
    "personal": {
        "hook_weight": 0.4,
        "flow_weight": 0.6,
        "tone_bias": ["story"],
    },
    "aesthetic": {
        "hook_weight": 0.3,
        "flow_weight": 0.7,
        "tone_bias": ["minimal", "cinematic"],
    },
    "informational": {
        "hook_weight": 0.5,
        "flow_weight": 0.5,
        "tone_bias": ["rewrite", "descriptive"],
    }
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
    with open(AGG_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def get_feedback_adjustment(intent: str, tone: str, confidence: str) -> float:
    # Never adjust if we're already confident
    if confidence == "clear":
        return 0.0

    tone = tone or "unknown"
    key = f"{intent}||{tone}"

    aggs = _load_aggregates()
    row = aggs.get(key)
    if not row:
        return 0.0

    views = float(row.get("views", 0) or 0)
    chosen = float(row.get("chosen", 0) or 0)

    # Guardrail: don't trust tiny samples
    if views < 5:
        return 0.0

    ratio = chosen / views  # 0..1
    MAX_BOOST = 5.0

    adj = (ratio - 0.5) * MAX_BOOST

    # scale by confidence
    if confidence == "moderate":
        adj *= 0.5

    # clamp to safety
    if adj > MAX_BOOST:
        adj = MAX_BOOST
    if adj < -MAX_BOOST:
        adj = -MAX_BOOST

    return round(adj, 2)

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
    if len(label) < 6:
        return True

    # Too generic
    weak_words = {
        "video", "clip", "shot", "scene",
        "food", "drink", "hotel", "lobby",
        "cocktail", "view", "room",
        "test", "sample", "file", "upload"
    }

    if label in weak_words:
        return True

    # Single vague noun
    if " " not in label:
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
    config_path = get_config_path(session)

    if mode not in ("all", "first_only", "none"):
        return {"status": "error", "error": "Invalid captions_mode"}

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    r = cfg.setdefault("render", {})
    r.setdefault("captions_mode", "all")  # fallback if missing
    r["captions_mode"] = mode            # update to selected

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    log_step(f"[CAPTIONS_MODE] {session} -> {mode}")
    return {"status": "ok", "captions_mode": mode}


# -----------------------------------------
# Hook Score
#-------------------------------------------

def api_hook_score(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    hook = extract_hook_text(cfg)
    result = score_hook_text(hook)

    return {
        "hook": hook,
        "score": result["score"],
        "reasons": result["reasons"],
    }


def api_improve_hook(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

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

    score = score_hook_text(new_hook)

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

def get_feedback_adjustment(intent, tone, confidence):
    if confidence == "clear":
        return 0

    aggs = _load_aggregates()
    key = f"{intent}||{tone}"

    if key not in aggs:
        return 0

    row = aggs[key]
    views = row.get("views", 0)
    chosen = row.get("chosen", 0)

    if views < 5:  # 🔒 minimum data guardrail
        return 0

    preference_ratio = chosen / views
    MAX_BOOST = 5

    adjustment = (preference_ratio - 0.5) * MAX_BOOST

    if confidence == "moderate":
        adjustment *= 0.5

    return round(adjustment, 2)


def record_variant_feedback(payload: dict):
    """
    v2 = write raw event (jsonl) + update aggregates (json)
    """
    _ensure_data_files()

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

    return {
        "ok": True,
        "stored": event,
        "aggregate_key": f"{event['intent']}||{event['tone']}"
    }


def api_generate_hooks(session: str, intent: str | None = None):

    
    print("[HOOK_LAB] Generating hooks for", session)
    session = sanitize_session(session)
    cfg = _load_config(session)

    scenes = []
    if cfg.get("first_clip", {}).get("text"):
        scenes.append(cfg["first_clip"]["text"])
    for c in cfg.get("middle_clips", []):
        if c.get("text"):
            scenes.append(c["text"])

    if not scenes:
        return {"hooks": []}

    if not client:
        # fallback
        return {
            "hooks": [{"text": scenes[0], "score": 70}]
        }

    prompt = f"""
Generate 8 short viral TikTok hooks based on these scenes.

Rules:
- Hooks must refer to the SAME experience
- Different tones: hype, curiosity, luxury, influencer, cinematic
- Max 12 words
- No emojis
- Do NOT describe all scenes — tease the experience

Scenes:
{json.dumps(scenes, indent=2)}

Return JSON:
{{ "hooks": ["hook1", "hook2", ...] }}
"""

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )

        content = resp.choices[0].message.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        data = json.loads(content[start:end])

        hooks = []
        for text in data.get("hooks", []):
            score = score_hook_text(text)["score"]
            clean = strip_emojis(text).strip()
            lower = clean.lower()

            if any(w in lower for w in ["wait", "watch", "this", "you", "from"]):
                tone = "punchy"
            elif any(w in lower for w in ["calm", "quiet", "slow", "peaceful"]):
                tone = "cinematic"
            else:
                tone = "neutral"

            hooks.append({
                "text": clean,
                "score": score,
                "tone": tone
            })



        # Sort best first
        hooks.sort(key=lambda x: x["score"], reverse=True)

        # 🎯 Intent-based recommendation
        intent = cfg.get("intent", "discovery")
        print("choose_best_hook exists:", "choose_best_hook" in globals())
        best = choose_best_hook(hooks, intent)

        if best:
            for h in hooks:
                if h["text"] == best["text"]:
                    h["recommended"] = True
                    h["reason"] = best["reason"]


        print("[HOOK_LAB] Hooks generated with intent:", intent)

        return {
            "hooks": hooks,
            "intent": intent
        }


    except Exception as e:
        log_error("[HOOK_LAB]", e)
        return {"hooks": []}


def api_variant_feedback():
    data = request.json or {}
    return record_variant_feedback(data)


def api_generate_body_from_hook(session, hook, style):
    cfg = _load_config(session)

    scenes = []
    for c in cfg.get("middle_clips", []):
        if c.get("text"):
            scenes.append(c["text"])
    if cfg.get("last_clip", {}).get("text"):
        scenes.append(cfg["last_clip"]["text"])

    prompt = f"""
You are writing TikTok captions.

Selected Hook:
"{hook}"

Scenes:
{json.dumps(scenes, indent=2)}

Rewrite the captions in "{style}" tone.

Rules:
- Do NOT change the hook
- One caption per scene
- Keep them concise
- Match the hook’s tone

Return JSON:
{{ "body": ["caption1", "caption2", "caption3"] }}
"""



# -----------------------------------------
# Story Flow Score
# -----------------------------------------

def api_story_flow_score(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    captions: List[str] = []

    # Hook
    if cfg.get("first_clip", {}).get("text"):
        captions.append(cfg["first_clip"]["text"])

    # Middle clips
    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            captions.append(clip["text"])

    # CTA / last clip (ignored for scoring, but included for structure)
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
        start = content.find("{")
        end = content.rfind("}") + 1
        result = json.loads(content[start:end])


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

def api_story_flow_improve(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    # Collect captions
    hook = cfg.get("first_clip", {}).get("text", "")

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
Improve the narrative flow of these captions.

Rules:
- Do NOT rewrite the opening hook
- Do NOT add or remove captions
- Improve flow by rephrasing sentences only
- Keep captions concise and natural
- Return JSON only

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
        start = content.find("{")
        end = content.rfind("}") + 1
        result = json.loads(content[start:end])

        rewrites = result.get("rewrites", [])

        if len(rewrites) != len(middle):
            return {"error": "Rewrite count mismatch"}

        full = [hook] + rewrites

        return {
            "proposed": "\n\n".join(full)
        }

    except Exception as e:
        log_error("[STORY_FLOW_IMPROVE]", e)
        return {"error": "Failed to improve story flow"}


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
UPLOAD_ORDER_KEY = RAW_PREFIX + "order.json"


def load_upload_order() -> List[str]:
    try:
        obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=UPLOAD_ORDER_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        return data.get("order", [])
    except Exception:
        return []


def save_upload_order(order: List[str]) -> None:
    try:
        payload = json.dumps({"order": order}, indent=2).encode("utf-8")
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=UPLOAD_ORDER_KEY,
            Body=payload,
            ContentType="application/json",
        )
    except Exception as e:
        logger.error(f"[UPLOAD_ORDER] Failed to save order.json: {e}")


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

def api_set_label(session: str, filename: str, label: str | None) -> Dict[str, Any]:
    session = sanitize_session(session)
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
    try:
        image_b64 = get_clip_preview_base64(session, filename)
    except Exception as e:
        logger.error(f"[REPAIR_LABEL] No preview frame: {e}")
        return normalize_label(label)

    messages = [
        {
            "role": "system",
            "content": "You generate short, visual labels for video clips."
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"""
Fix or create a short label for this video.

Current label: "{label or '(empty)'}"

Rules:
- Max 8 words
- No emojis
- No hashtags
- Do not use hotel name unless visible
- Describe what is on screen
- Useful for captions

Return ONLY the label text.
"""
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_b64
                    }
                }
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



def move_upload_s3(src: str, dest: str) -> Dict[str, Any]:
    """Move a file in S3 by copying then deleting."""
    s3.copy_object(
        Bucket=S3_BUCKET_NAME,
        CopySource=f"{S3_BUCKET_NAME}/{src}",
        Key=dest,
    )
    s3.delete_object(Bucket=S3_BUCKET_NAME, Key=src)
    return {"ok": True}


def delete_upload_s3(key: str) -> Dict[str, Any]:
    """Delete a file from S3."""
    s3.delete_object(Bucket=S3_BUCKET_NAME, Key=key)
    return {"ok": True}

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
    order = load_upload_order()
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

    config_path = get_config_path(session)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    log_step(f"[REORDER] Updated clip order for session '{session}'")
    return cfg


def build_variant_reason(best, variants, intent):
    hook = best.get("hook_score", 0)
    flow = best.get("story_flow", 0)
    tone = (best.get("tone") or "").lower()

    avg_hook = sum(v.get("hook_score", 0) for v in variants) / len(variants)
    avg_flow = sum(v.get("story_flow", 0) for v in variants) / len(variants)

    if best.get("uses_selected_hook"):
        return "Preserves your selected hook while improving structure."
    if intent == "discovery":
        if hook > avg_hook + 8:
            return "Stronger opening hook than other variants"
        if "punchy" in tone:
            return "Punchier tone optimized for discovery"
        return "Best overall hook performance for reach"

    if intent == "personal":
        if flow > avg_flow + 8:
            return "More natural storytelling flow than other options"
        return "Stronger emotional progression for personal content"

    if intent == "aesthetic":
        if "minimal" in tone:
            return "Cleaner, more minimal pacing than other variants"
        return "Calmest visual rhythm for aesthetic content"

    if intent == "informational":
        return "Clearer structure and explanation than alternatives"

    return "Best overall balance across variants"

SESSION_PREFS_DIR = "session_prefs"
os.makedirs(SESSION_PREFS_DIR, exist_ok=True)

def save_session_pref(session, key, value):
    path = os.path.join(SESSION_PREFS_DIR, sanitize_session(session) + ".json")
    data = {}
    if os.path.exists(path):
        data = json.load(open(path))
    data[key] = value
    json.dump(data, open(path, "w"), indent=2)


def choose_best_variant(variants: list, intent: str):
    if not variants:
        return None

    intent_cfg = INTENT_PROFILE.get(intent, INTENT_PROFILE["discovery"])

    def base_score(v):
        hook = v.get("hook_score", 0)
        flow = v.get("story_flow", 0)
        tone = (v.get("tone") or "").lower()

        base = (
            hook * intent_cfg["hook_weight"] +
            flow * intent_cfg["flow_weight"]
        )

        # soft tone bias (never dominant)
        for t in intent_cfg["tone_bias"]:
            if t in tone:
                base += 3

        return base

    # 1) Score everything by your ORIGINAL scoring
    scored = [{**v, "_base": base_score(v)} for v in variants]
    scored.sort(key=lambda v: v["_base"], reverse=True)

    best = scored[0]
    second = scored[1] if len(scored) > 1 else None
    gap = best["_base"] - (second["_base"] if second else 0)

    # 2) Now confidence is REAL
    if gap > 100:
        confidence = "clear"
    elif gap > 50:
        confidence = "moderate"
    else:
        confidence = "close"

    # 3) Apply feedback ONLY when NOT clear
    def final_score(v):
        fb = get_feedback_adjustment(
            intent=intent,
            tone=v.get("tone") or "unknown",
            confidence=confidence
        )
        return v["_base"] + fb

    if confidence != "clear":
        scored.sort(key=final_score, reverse=True)
        best = scored[0]

    return {
        "id": best["id"],
        "reason": build_variant_reason(best, variants, intent),
        "confidence": confidence
    }



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
    


def api_analyze(session: str = "default") -> Dict[str, Any]:
    return _analyze_all_videos(session)


def api_analyze_start(session: str = "default") -> Dict[str, Any]:
    # For now just run the whole pass synchronously
    return _analyze_all_videos(session)


def api_analyze_step() -> Dict[str, Any]:
    # Kept for API compatibility
    return {"status": "done"}


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

        # Save YAML directly — no merge!
        config_path = get_config_path(session)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

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
            "-ss", "00:00:01.5",
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
def api_get_config() -> Dict[str, Any]:
    session_id = sanitize_session(request.args.get("session", "default"))
    config_path = get_config_path(session_id)

    if not os.path.exists(config_path):
        return {"yaml": "", "config": {}, "error": "config.yml not found"}

    # Load YAML text
    with open(config_path, "r", encoding="utf-8") as f:
        yaml_text = f.read()

    try:
        cfg = yaml.safe_load(yaml_text) or {}
    except Exception as e:
        log_error("[GET_CONFIG]", e)
        return {"yaml": yaml_text, "config": {}}

    return {"yaml": yaml_text, "config": cfg}



def api_save_yaml(yaml_text: str) -> Dict[str, Any]:
    try:
        # Parse raw user YAML
        cfg = yaml.safe_load(yaml_text) or {}
        cfg = sanitize_yaml_filenames(cfg)

        session = sanitize_session(request.args.get("session", "default"))
        config_path = get_config_path(session)

        # ❗ Write ONLY what the user edited
        # Do NOT merge session overrides here
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        log_success("[SAVE_YAML]", f"config.yml saved for session '{session}'")
        return {"status": "ok"}

    except Exception as e:
        log_error("[SAVE_YAML]", e)
        return {"status": "error", "error": str(e)}



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

def api_generate_variants(session: str, modes: dict, selected_hook: str | None = None) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    # --------------------------------------------------
    # Collect captions from YAML
    # --------------------------------------------------
    captions = []

    # 🔥 Use selected hook if provided
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
                "text": "⚠ No captions found in YAML. Generate or import captions first.",
                "tone": "Error"
            }]
        }

    # --------------------------------------------------
    # Tone labels (authoritative)
    # --------------------------------------------------
    STYLE_TONE_LABELS = {
        "rewrite": "Standard · Clean Rewrite",
        "hook": "Hook-Optimized · Scroll Stopper",
        "punchy": "Punchy · TikTok / Reels",
        "story": "Storytelling · Voiceover",
        "influencer": "Influencer · Creator Style",
        "minimal": "Minimal · Luxury Aesthetic (brand implied)",
    }

    style_prompts = {
        "rewrite": "Rewrite captions clean and natural.",
        "hook": (
            "Improve ONLY the first caption as a scroll-stopping hook. "
            "Do NOT rewrite the other captions except for capitalization or punctuation fixes."
        ),
        "punchy": "Rewrite punchy, energetic TikTok creator style.",
        "story": (
            "Rewrite with storytelling and emotional progression. "
            "Assume the viewer understands the location after the first caption."
        ),
        "influencer": "Rewrite as a confident influencer speaking to camera.",
        "minimal": "Rewrite in minimal luxury style."
    }

    # If nothing selected, return empty list
    if not any(modes.values()):
        return {"variants": []}

    variants: List[Dict[str, str]] = []

    # --------------------------------------------------
    # Base system guardrail (used everywhere)
    # --------------------------------------------------
    BASE_SYSTEM_PROMPT = (
        CAPTION_ONLY_GUARDRAIL +
        " Rewrite captions in blocks separated by blank lines. "
        "Keep the SAME number of caption blocks as the input. "
        "Do NOT merge captions into one paragraph. "
    )


    # --------------------------------------------------
    # Generate variants per selected mode
    # --------------------------------------------------
    for style, enabled in modes.items():
        if not enabled or style not in style_prompts:
            continue

        system_prompt = BASE_SYSTEM_PROMPT

        if hook_locked:
            system_prompt += (
                " IMPORTANT: The first paragraph is a FIXED hook. "
                "It MUST be used verbatim in every variant. "
                "Do NOT rewrite it. "
                "Do NOT rephrase it. "
                "Do NOT shorten it. "
                "Do NOT change punctuation. "
                "Do NOT add or remove words. "
                "Only rewrite the remaining captions to match the tone."
            )



        if style == "minimal":  
            system_prompt += (
                " Minimal luxury captions. "
                "Assume the hotel name is already established in context. "
                "DO NOT include or repeat the hotel or brand name. "
                "CRITICAL FORMAT RULES: "
                "- Each caption must be its own block separated by ONE blank line. "
                "- 3–7 words per caption for NON-HOOK captions. "
                "- Editorial, high-end luxury tone. "
                "- No emojis. No hashtags. No full sentences. "
            )

            if hook_locked:
                system_prompt += (
                    " The FIRST caption is a locked hook. "
                    "It may be longer and may be a full sentence. "
                    "DO NOT rewrite or remove it."
                )

        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"{style_prompts[style]}\n\n{base}"
                },
            ],
            temperature=0.6,
        )

        variants.append({
            "text": resp.choices[0].message.content.strip(),
            "tone": STYLE_TONE_LABELS.get(style, style),
        })

    # --------------------------------------------------
    # Combo variants (optional enhancement)
    # --------------------------------------------------
    COMBO_SYSTEM_PROMPT = (
        CAPTION_ONLY_GUARDRAIL +
        " Rewrite captions in blocks separated by blank lines. "
        "Keep the SAME number of caption blocks. "
        "Assume shared context across captions and avoid repeating location names."
    )

    if modes.get("rewrite") and modes.get("punchy"):
        r = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": COMBO_SYSTEM_PROMPT},
                {"role": "user", "content": f"Rewrite punchy + clear:\n\n{base}"}
            ],
            temperature=0.6,
        )
        variants.append({
            "text": r.choices[0].message.content.strip(),
            "tone": "Rewrite + Punchy",
        })

    if modes.get("rewrite") and modes.get("story"):
        r = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[
                {"role": "system", "content": COMBO_SYSTEM_PROMPT},
                {"role": "user", "content": f"Rewrite storytelling + smooth:\n\n{base}"}
            ],
            temperature=0.6,
        )
        variants.append({
            "text": r.choices[0].message.content.strip(),
            "tone": "Rewrite + Story",
        })


    # 🎯 Intent-based recommendation
    cfg = _load_config(session)
    intent = cfg.get("intent", "discovery")

    # --------------------------------------------------
    # Attach lightweight scores to variants (REQUIRED)
    # --------------------------------------------------
    for idx, v in enumerate(variants):
        text = v.get("text", "")
        tone = v.get("tone", "").lower()

        is_punchy = "punchy" in tone
        is_story = "story" in tone
        is_minimal = "minimal" in tone
        is_rewrite = "rewrite" in tone

        if hook_locked:
            v["uses_selected_hook"] = True
        else:
            v["uses_selected_hook"] = False


        # Simple heuristics (fast + deterministic)
        hook_score = 0
        flow_score = 0

        # Hook strength
        if any(word in text.lower() for word in ["you", "this", "watch", "wait", "from"]):
            hook_score += 20
        if "!" in text:
            hook_score += 10

        # Flow / structure
        blocks = [b for b in text.split("\n\n") if b.strip()]
        flow_score += min(len(blocks) * 10, 40)


        # Tone bias
        if is_punchy:
            hook_score += 15
        if is_story:
            flow_score += 15
        if is_minimal:
            flow_score += 10

        v["id"] = idx
        v["hook_score"] = hook_score
        v["story_flow"] = flow_score

        import random

        jitter = random.random() * 0.5  # tiny randomness
        v["hook_score"] += jitter
        v["story_flow"] += jitter



    best = choose_best_variant(variants, intent)

    for v in variants:
        v.pop("recommended", None)
        v.pop("recommend_reason", None)

    if best:
        for v in variants:
            if v.get("id") == best["id"]:
                v["recommended"] = True
                v["recommend_reason"] = best["reason"]

                # ✅ SAVE SESSION PREFERENCES HERE
                save_session_pref(session, "last_best_tone", v.get("tone"))
                save_session_pref(session, "last_intent", intent)





    # --------------------------------------------------
    # Cap to UI max (defensive)
    # --------------------------------------------------
    return {
        "variants": variants[:7]
    }


def api_save_captions(text: str, session: str) -> Dict[str, Any]:
    try:
        session = sanitize_session(session)
        config_path = get_config_path(session)

        # ✅ Ensure config directory exists
        os.makedirs(os.path.dirname(config_path), exist_ok=True)

        # ✅ Load existing config OR create a minimal one
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            cfg = {
                "first_clip": {},
                "middle_clips": [],
                "last_clip": {},
                "render": {}
            }

        # 🔥 Robust block split
        blocks = [
            b.strip()
            for b in re.split(r"\n\s*\n", text)
            if b.strip()
        ]

        idx = 0

        # First clip
        if cfg.get("first_clip") and idx < len(blocks):
            cfg["first_clip"]["text"] = blocks[idx]
            idx += 1

        # Middle clips
        for clip in cfg.get("middle_clips", []):
            if idx < len(blocks):
                clip["text"] = blocks[idx]
                idx += 1

        # Last clip
        if cfg.get("last_clip") and idx < len(blocks):
            cfg["last_clip"]["text"] = blocks[idx]

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

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
        config_path = get_config_path(session)

        # 🚨 If config does not exist
        if not os.path.exists(config_path):
            msg = f"config.yml not found for session '{session}'"
            export_tasks[task_id]["status"] = "error"
            export_tasks[task_id]["error"] = msg
            return

        # 🚨 CANCEL CHECK (1)
        if export_tasks[task_id].get("cancel_requested"):
            export_tasks[task_id]["status"] = "cancelled"
            return

        # ----------------------------------------------------
        # Load config (NO MERGING ANYMORE)
        # ----------------------------------------------------
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            log_error("[EXPORT][LOAD_CFG]", e)
            export_tasks[task_id]["status"] = "error"
            export_tasks[task_id]["error"] = "Failed to load config.yml"
            return

        # 🚨 CANCEL CHECK (2)
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

        # 🚨 CANCEL CHECK (3)
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

        # Signed URL
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
    session = sanitize_session(session)
    config_path = get_config_path(session)

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    r = cfg.setdefault("render", {})
    r["tts_enabled"] = bool(enabled)

    if voice:
        r["tts_voice"] = voice

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {"status": "ok", "render": r}



def api_set_cta(session: str, enabled: bool, text: str | None, voiceover: bool | None, duration: float | None = None):
    session = sanitize_session(session)
    config_path = get_config_path(session)

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

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

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

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
        start = content.find("{")
        end = content.rfind("}") + 1
        data = json.loads(content[start:end])

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
        y = eval(y_expr)  # y position inside preset
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
    config_path = get_config_path(session)

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    r = cfg.setdefault("render", {})
    r["layout_mode"] = mode

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {"status": "ok", "layout_mode": mode}


def api_fgscale(session: str, fgscale_mode: str, fgscale: float | None) -> Dict[str, Any]:
    session = sanitize_session(session)
    config_path = get_config_path(session)

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    r = cfg.setdefault("render", {})
    r["fgscale_mode"] = fgscale_mode
    r["fgscale"] = fgscale

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

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


