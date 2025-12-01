# assistant_api.py — session-aware uploads + YAML + analysis

import os
import json
import glob
import logging
from typing import Dict, Any, List
import yaml
from openai import OpenAI
from flask import request
from assistant_log import log_step, log_error, log_success
from tiktok_template import config_path, edit_video, video_folder
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
    merge_session_config_into,
    load_session_config,
    save_session_config,
    generate_signed_download_url,
    list_videos_from_s3,
    download_s3_video,
    analyze_video,
    build_yaml_prompt,
    save_analysis_result,
    sanitize_yaml_filenames,
)

logger = logging.getLogger(__name__)

# -------------------------------
# OpenAI client
# -------------------------------
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client = OpenAI(api_key=api_key) if api_key else None
TEXT_MODEL = "gpt-4.1-mini"

# -------------------------------
# Analysis cache directory
# -------------------------------
ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)

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


# ==========================================
# 🔥 LEGACY GLOBAL CACHE CLEANUP (RUNS ONCE)
# ==========================================
# Remove old-style "*.json" files that lived directly under video_analysis_cache/
LEGACY_DIR = ANALYSIS_BASE_DIR  # same folder

try:
    # Delete only top-level stale JSONs (NOT subfolders!)
    for f in glob.glob(os.path.join(LEGACY_DIR, "*.json")):
        os.remove(f)
except Exception as e:
    print("[LEGACY CLEANUP] Skipped:", e)

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


# -------------------------------
# Load all analysis results from disk
# -------------------------------
def load_all_analysis_results() -> Dict[str, str]:
    results: Dict[str, str] = {}

    if not os.path.isdir(ANALYSIS_CACHE_DIR):
        return results

    for path in glob.glob(os.path.join(ANALYSIS_CACHE_DIR, "*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            fname = data.get("filename")
            desc = data.get("description")
            if fname and desc:
                results[fname] = desc
        except Exception as e:
            logger.error(f"[LOAD_ANALYSIS] failed for {path}: {e}")

    return results


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
        local_path = os.path.join(video_folder, filename)

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

        files_for_prompt: List[str] = []
        analyses_for_prompt: List[str] = []

        for fname in local_files:
            key_norm = fname.lower()
            desc = analyses_map.get(key_norm, f"Hotel/travel clip: {fname}")
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

        cfg = sanitize_yaml_filenames(cfg)

        render = cfg.setdefault("render", {})
        if "layout_mode" not in render:
            render["layout_mode"] = "tiktok"

        # NOTE: still using a single global config_path for now
        # Apply session overrides (fgscale, etc.)
        cfg = merge_session_config_into(cfg, session)

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
    if not os.path.exists(config_path):
        return {"yaml": "", "config": {}, "error": "config.yml not found"}

    with open(config_path, "r", encoding="utf-8") as f:
        yaml_text = f.read()

    try:
        cfg = yaml.safe_load(yaml_text) or {}

        # 🔥 Re-serialize cleaned config so UI sees the cleaned version
        yaml_text = yaml.safe_dump(cfg, sort_keys=False)

    except Exception:
        cfg = {}

    return {"yaml": yaml_text, "config": cfg}


def api_save_yaml(yaml_text: str) -> Dict[str, Any]:
    try:
        cfg = yaml.safe_load(yaml_text) or {}
        cfg = sanitize_yaml_filenames(cfg)

        # Apply session overrides
        session = request.args.get("session", "default")
        session = sanitize_session(session)
        cfg = merge_session_config_into(cfg, session)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)


        log_success("[SAVE_YAML]", "config.yml saved successfully")
        return {"status": "ok"}

    except Exception as e:
        log_error("[SAVE_YAML]", e)
        return {"status": "error", "error": str(e)}


# -------------------------------
# Captions (editor tab — global)
# -------------------------------
_CAPTIONS_FILE = os.path.join(os.path.dirname(__file__), "captions.txt")


def api_get_captions() -> Dict[str, Any]:
    if not os.path.exists(_CAPTIONS_FILE):
        return {"text": ""}
    with open(_CAPTIONS_FILE, "r", encoding="utf-8") as f:
        return {"text": f.read()}


def api_save_captions(text: str) -> Dict[str, Any]:
    try:
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        idx = 0
        if "first_clip" in cfg and idx < len(blocks):
            cfg["first_clip"]["text"] = blocks[idx]
            idx += 1

        if "middle_clips" in cfg:
            for clip in cfg["middle_clips"]:
                if idx < len(blocks):
                    clip["text"] = blocks[idx]
                    idx += 1

        if "last_clip" in cfg and idx < len(blocks):
            cfg["last_clip"]["text"] = blocks[idx]

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        with open(_CAPTIONS_FILE, "w", encoding="utf-8") as f:
            f.write(text)

        log_success("[CAPTIONS]", "Captions updated")
        return {"status": "ok", "text": text, "config": cfg}

    except Exception as e:
        log_error("[CAPTIONS]", e)
        return {"status": "error", "error": str(e)}

# -------------------------------
# EXPORT (global config, session-aware)
# -------------------------------
def api_export(optimized: bool = False, session: str = "default") -> Dict[str, Any]:
    if not os.path.exists(config_path):
        msg = "config.yml not found"
        log_error("[EXPORT]", Exception(msg))
        return {"status": "error", "error": msg}

    try:
        mode = _EXPORT_MODE
        log_step(f"[EXPORT] Rendering in {mode.upper()} mode (optimized={optimized})")

        # Inject session-specific overrides BEFORE rendering
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            # sanitize session coming from caller
            session = sanitize_session(session)

            # merge per-session config (fgscale, etc.)
            cfg = merge_session_config_into(cfg, session)

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False)

        except Exception as e:
            log_error("[EXPORT][SESSION MERGE]", e)

        # Now render with merged config.yml
        out_path = edit_video(optimized=optimized)
        if not out_path:
            raise RuntimeError("edit_video() returned no output path")

        log_step(f"[EXPORT] Finished rendering: {out_path}")

        filename = os.path.basename(out_path)
        prefix = EXPORT_PREFIX.rstrip("/")
        raw_key = f"{prefix}/{filename}"
        export_key = clean_s3_key(raw_key)

        s3.upload_file(out_path, S3_BUCKET_NAME, export_key)
        log_step(f"[EXPORT] Uploaded to s3://{S3_BUCKET_NAME}/{export_key}")

        url = generate_signed_download_url(export_key)

        log_success("[EXPORT]", f"Completed and uploaded {filename}")
        return {
            "status": "ok",
            "output_path": out_path,
            "download_url": url,
            "s3_key": export_key,
            "local_filename": filename,
        }

    except Exception as e:
        log_error("[EXPORT]", e)
        return {"status": "error", "error": str(e)}



# -------------------------------
# TTS / CTA Settings (global)
# -------------------------------
def _load_config_for_mutation() -> dict:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_config(cfg: dict) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def api_set_tts(enabled: bool, voice: str | None) -> Dict[str, Any]:
    cfg = _load_config_for_mutation()
    r = cfg.setdefault("render", {})
    r["tts_enabled"] = bool(enabled)
    if voice:
        r["tts_voice"] = voice
    _save_config(cfg)
    return {"status": "ok", "render": r}


def api_set_cta(enabled: bool, text: str | None, voiceover: bool | None) -> Dict[str, Any]:
    cfg = _load_config_for_mutation()
    c = cfg.setdefault("cta", {})
    c["enabled"] = bool(enabled)
    if text is not None:
        c["text"] = text
    if voiceover is not None:
        c["voiceover"] = bool(voiceover)
    _save_config(cfg)
    return {"status": "ok", "cta": c}


# -------------------------------
# Overlay + Timings + fg_scale
# -------------------------------
def api_apply_overlay(style: str) -> Dict[str, Any]:
    try:
        from tiktok_assistant import apply_overlay

        apply_overlay(style)
        log_success("[OVERLAY]", f"Applied overlay style '{style}'")
        return {"status": "ok", "style": style}
    except Exception as e:
        log_error("[OVERLAY]", e)
        return {"status": "error", "error": str(e)}


def api_apply_timings(smart: bool = False) -> Dict[str, Any]:
    from tiktok_assistant import apply_smart_timings

    try:
        pacing = "cinematic" if smart else "standard"
        apply_smart_timings(pacing)
        log_success("[TIMINGS]", f"Applied '{smart}'")
        return {"status": "ok", "pacing": pacing}
    except Exception as e:
        log_error("[TIMINGS]", e)
        return {"status": "error", "error": str(e)}


def api_set_layout(mode: str) -> Dict[str, Any]:
    try:
        cfg = _load_config_for_mutation()
        r = cfg.setdefault("render", {})
        r["layout_mode"] = mode
        _save_config(cfg)
        return {"status": "ok", "layout_mode": mode}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def api_fgscale(session: str, fgscale_mode: str, fgscale: float | None) -> Dict[str, Any]:
    """
    Save foreground scale settings for a specific session.

    fgscale_mode: "auto" or "manual"
    fgscale: float value when manual, or None when auto.
    """
    try:
        # Load session config
        cfg = load_session_config(session)
        render_cfg = cfg.get("render", {})

        # Apply changes
        render_cfg["fgscale_mode"] = fgscale_mode
        render_cfg["fgscale"] = fgscale  # can be None

        cfg["render"] = render_cfg

        # Save updated config
        save_session_config(session, cfg)

        log_success("[FGSCALE]", f"({session}) mode={fgscale_mode} value={fgscale}")
        return {"status": "ok", "render": render_cfg}

    except Exception as e:
        return {"status": "error", "error": str(e)}



# -------------------------------
# Chat
# -------------------------------
def api_chat(message: str) -> Dict[str, Any]:
    if not client:
        reply = f"(no OpenAI key) You said: {message}"
        log_error("[CHAT]", Exception("No OpenAI key"))
        return {"reply": reply}

    prompt = f"You are a friendly TikTok travel assistant. User says:\n\n{message}"

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
