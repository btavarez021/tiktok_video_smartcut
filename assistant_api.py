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


# ========== TASK REGISTRY ==========
export_tasks = {}  
# Structure:
# export_tasks[task_id] = {
#     "status": "pending" | "processing" | "done" | "error" | "cancelled",
#     "download_url": None,
#     "error": None,
#     "cancel_requested": False,
# }



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
    
# ================================
# Emoji-safe text helper
# ================================
def strip_emojis(text: str) -> str:
    if not text:
        return text
    # Remove emojis & pictographs (Unicode-safe)
    return re.sub(r"[\U00010000-\U0010ffff]", "", text).strip()



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

def load_labels_for_session(session: str) -> dict:
    """
    Load labels.json for a session.
    Returns { filename: label }
    """
    labels_path = os.path.join("sessions", session, "labels.json")

    if not os.path.exists(labels_path):
        return {}

    try:
        with open(labels_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("labels", {})
    except Exception as e:
        logger.error(f"[LABELS] Failed to load labels for {session}: {e}")
        return {}


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
    config_path = get_config_path(session)

    if not os.path.exists(config_path):
        return {"status": "error", "error": "config.yml not found"}

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    hook = extract_hook_text(cfg)
    new_hook = improve_hook_text(hook)

    # Update first_clip.text only
    cfg.setdefault("first_clip", {})
    cfg["first_clip"]["text"] = new_hook

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    # Return new score too
    result = score_hook_text(new_hook)

    return {
        "status": "ok",
        "hook": new_hook,
        "score": result["score"],
        "reasons": result["reasons"],
    }

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
            messages=[{"role": "user", "content": prompt}],
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
    config_path = get_config_path(session)

    if not os.path.exists(config_path):
        return {"updated": False, "reason": "config.yml not found"}

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # --------------------------------------------------
    # Collect ALL captions AFTER the hook
    # (middle clips + last clip)
    # --------------------------------------------------
    targets = []  # list of dict refs we will mutate
    texts = []    # their text values

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            targets.append(clip)
            texts.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        targets.append(cfg["last_clip"])
        texts.append(cfg["last_clip"]["text"])

    # Need at least TWO captions to improve flow
    if len(texts) < 2:
        return {
            "updated": False,
            "reason": "Need at least 2 captions after the hook to improve story flow."
        }

    if not client:
        return {
            "updated": False,
            "reason": "AI unavailable."
        }

    # --------------------------------------------------
    # LLM prompt
    # --------------------------------------------------
    prompt = f"""
                Improve the narrative flow of these captions.

                Rules:
                - Do NOT rewrite the opening hook
                - Do NOT add or remove captions
                - Do NOT add sequence words like "then", "next", or "finally"
                - Improve flow by rephrasing sentences, not by adding connectors
                - Use semantic transitions (energy shift, location change, mood progression)
                - Keep captions concise and natural
                - Avoid trivial synonym swaps; each rewrite should meaningfully improve flow
                - Return JSON ONLY

                Captions:
                {json.dumps(texts, indent=2)}

                Return:
                {{
                "rewrites": ["caption 1", "caption 2", "..."]
                }}
                """

    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )

        content = resp.choices[0].message.content.strip()

        # Safe JSON extraction
        start = content.find("{")
        end = content.rfind("}") + 1
        result = json.loads(content[start:end])

        rewrites = result.get("rewrites", [])

        if len(rewrites) != len(targets):
            return {
                "updated": False,
                "reason": "AI rewrite count mismatch."
            }

        # --------------------------------------------------
        # Apply rewrites IN PLACE
        # --------------------------------------------------
        for i, new_text in enumerate(rewrites):
            targets[i]["text"] = new_text

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        return {
            "updated": True,
            "count": len(rewrites)
        }

    except Exception as e:
        log_error("[STORY_FLOW_IMPROVE]", e)
        return {
            "updated": False,
            "reason": "Failed to improve story flow."
        }

LABELS_FILE = "labels.json"

def apply_filename_captions(session: str):
    session = sanitize_session(session)
    config_path = get_config_path(session)

    if not os.path.exists(config_path):
        return

    # 🔑 Load labels internally
    labels = load_labels(session)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    def caption_for(file: str) -> str:
        # 1️⃣ Label wins
        label = (labels.get(file) or "").strip()
        if label:
            return label

        # 2️⃣ Fallback → filename prettified
        name = os.path.splitext(file)[0]
        name = name.replace("_", " ").replace("-", " ")
        return name.strip().title()

    # -----------------------------------
    # Apply captions
    # -----------------------------------
    if cfg.get("first_clip"):
        f = cfg["first_clip"].get("file")
        if f:
            cfg["first_clip"]["text"] = caption_for(f)

    for clip in cfg.get("middle_clips", []):
        f = clip.get("file")
        if f:
            clip["text"] = caption_for(f)

    if cfg.get("last_clip"):
        f = cfg["last_clip"].get("file")
        if f:
            cfg["last_clip"]["text"] = caption_for(f)

    # -----------------------------------
    # Sync captions.txt with YAML
    # -----------------------------------
    blocks = []

    if cfg.get("first_clip", {}).get("text"):
        blocks.append(cfg["first_clip"]["text"])

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            blocks.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        blocks.append(cfg["last_clip"]["text"])

    captions_text = "\n\n".join(blocks)

    with open(_CAPTIONS_FILE, "w", encoding="utf-8") as f:
        f.write(captions_text)


    # -----------------------------------
    # Save YAML
    # -----------------------------------
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)

    log_step(f"📝 Captions generated from labels / filenames (session={session})")


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

    label = (label or "").strip()
    if label:
        labels[filename] = label
    else:
        labels.pop(filename, None)

    save_labels(session, labels)
    return {"status": "ok"}


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


# -------------------------------
# Analyze APIs (per session)
# -------------------------------
def _analyze_all_videos(session: str) -> Dict[str, Any]:
    session = sanitize_session(session)
    raw_prefix = f"{RAW_PREFIX}{session}/"

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
            desc = analyze_video(tmp)
            basename = os.path.basename(key)
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
                desc = f"{analysis}. User intent: {label}"
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
_CAPTIONS_FILE = os.path.join(os.path.dirname(__file__), "captions.txt")

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


# ============================================================
# Caption Variants Generator  🔥 (Rewrite / Punchy / Story etc.)
# ============================================================
def api_generate_variants(session: str, modes: dict) -> Dict[str, Any]:
    session = sanitize_session(session)
    cfg = _load_config(session)

    # Collect captions from YAML (same approach as save/load)
    captions = []

    if cfg.get("first_clip", {}).get("text"):
        captions.append(cfg["first_clip"]["text"])

    for clip in cfg.get("middle_clips", []):
        if clip.get("text"):
            captions.append(clip["text"])

    if cfg.get("last_clip", {}).get("text"):
        captions.append(cfg["last_clip"]["text"])

    captions = normalize_location_repetition(captions)
    base = "\n\n".join(captions).strip()

    if not base:
        return {"variants": ["⚠ No captions found in YAML. Generate or import captions first."]}

    style_prompts = {
        "rewrite":     "Rewrite captions clean and natural.",
        "hook":        "Improve opening hook only, keep rest similar.",
        "punchy":      "Rewrite punchy, energetic TikTok creator style.",
        "story":       "Rewrite more storytelling, emotional progress. Assume the viewer understands the location after the first caption.",
        "influencer":  "Rewrite as confident influencer talking to camera.",
    }

    variants = [base]  # original included for reference

    # Generate one variant per checked mode
    for style, enabled in modes.items():
        if enabled and style in style_prompts:
            resp = client.chat.completions.create(
                model=TEXT_MODEL,
                messages=[
                    {"role": "system", "content": "Rewrite captions in blocks separated by blank lines. Keep same number of blocks."
                    "Assume shared context accross captions and avoid repeating the same location or proper noun in every block unless it adds meaning."},
                    {"role": "user", "content": f"Original:\n{base}\n\nRewrite style: {style_prompts[style]}"},
                ]
            )
            variants.append(resp.choices[0].message.content.strip())

    # Combo magic ✨ (auto mixes modes for advanced results)
    if modes.get("rewrite") and modes.get("punchy"):
        r = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": f"Rewrite punchy + clear:\n{base}"}]
        )
        variants.append(r.choices[0].message.content.strip())

    if modes.get("rewrite") and modes.get("story"):
        r = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": f"Rewrite storytelling + smooth:\n{base}"}]
        )
        variants.append(r.choices[0].message.content.strip())

    return {"variants": variants[:7]}  # max 7 to keep UI manageable



def api_get_captions() -> Dict[str, Any]:
    if not os.path.exists(_CAPTIONS_FILE):
        return {"text": ""}
    with open(_CAPTIONS_FILE, "r", encoding="utf-8") as f:
        return {"text": f.read()}

def api_save_captions(text: str, session: str) -> Dict[str, Any]:
    try:
        session = sanitize_session(session)
        config_path = get_config_path(session)

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

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

        with open(_CAPTIONS_FILE, "w", encoding="utf-8") as f:
            f.write(text)

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

        if rewrite:
            cfg = _load_config(session_id)
            if not cfg.get("first_clip",{}).get("text"):
                return {"status":"error","error":"No captions to rewrite"}

        session_id = sanitize_session(session_id)

        apply_overlay(
            session_id,
            style,
            rewrite=rewrite
        )


        log_success(
            "[OVERLAY]",
            f"Applied overlay '{style}' (rewrite={rewrite}) for session '{session_id}'"
        )

        return {
            "status": "ok",
            "style": style,
            "rewrite": rewrite,
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


