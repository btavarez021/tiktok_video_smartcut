# assistant_api.py  — MOV/MP4 SAFE, SYNCED WITH app.py

import os
import json
import glob
import logging
from typing import Dict, Any, List
import shutil
import yaml
from openai import OpenAI

from assistant_log import log_step
from tiktok_template import config_path, edit_video, video_folder
from tiktok_assistant import (
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
# Analysis cache (disk)
# -------------------------------
ANALYSIS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "video_analysis_cache")
os.makedirs(ANALYSIS_CACHE_DIR, exist_ok=True)

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
# Helper: load all analysis results
# -------------------------------
def load_all_analysis_results() -> Dict[str, str]:
    """
    Read all *.json files in video_analysis_cache and return
    { filename: description }.
    """
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
# Helper: sync S3 videos to local folder
# -------------------------------
def _sync_s3_videos_to_local() -> List[str]:
    os.makedirs(video_folder, exist_ok=True)

    keys = list_videos_from_s3()
    log_step(f"[SYNC] Found {len(keys)} raw S3 videos")
    local_files = []

    for key in keys:
        base, _ = os.path.splitext(os.path.basename(key))
        basename = f"{base.lower()}.mp4"
        local_path = os.path.join(video_folder, basename)

        if os.path.exists(local_path):
            local_files.append(basename)
            continue

        tmp_path = download_s3_video(key)
        if not tmp_path:
            continue

        try:
            from tiktok_template import normalize_video_ffmpeg
            normalize_video_ffmpeg(tmp_path, local_path)
            log_step(f"[SYNC] Normalized {key} -> {local_path}")
            local_files.append(basename)
        except Exception as e:
            log_step(f"[SYNC ERROR] {e}")

    return local_files


# -------------------------------
# Analyze APIs
# -------------------------------
def _analyze_all_videos() -> Dict[str, Any]:
    """
    Analyze all videos currently in S3 (by filename only).
    Saves results into video_analysis_cache/*.json.
    """
    keys = list_videos_from_s3()
    if not keys:
        log_step("[ANALYZE] No videos found in S3")
        return {"status": "no_videos", "count": 0}

    count = 0
    for key in keys:
        tmp = download_s3_video(key)
        if not tmp:
            log_step(f"[ANALYZE] Failed to download {key}")
            continue

        try:
            # analyze the REAL FILE PATH
            desc = analyze_video(tmp)

            # store using the normalized basename (lowercase)
            basename = os.path.basename(key).lower()
            save_analysis_result(basename, desc)

            count += 1

        except Exception as e:
            logger.error(f"[ANALYZE] Failed for {key}: {e}")
            log_step(f"[ANALYZE ERROR] {key}: {e}")
            continue

    log_step(f"[ANALYZE] Completed analysis for {count} videos")
    return {"status": "ok", "count": count}


def api_analyze_start() -> Dict[str, Any]:
    """
    Called when the user hits the Analyze button.
    For simplicity, we just analyze all videos synchronously.
    """
    return _analyze_all_videos()


def api_analyze_step() -> Dict[str, Any]:
    """
    Original project used step-wise analysis.
    Here we report as 'done' and let the UI poll /api/analyses_cache.
    """
    return {"status": "done"}


def api_analyze() -> Dict[str, Any]:
    """
    Full one-shot analyze endpoint.
    """
    return _analyze_all_videos()


# -------------------------------
# YAML + config APIs
# -------------------------------
def api_generate_yaml() -> Dict[str, Any]:
    """
    Use the analysis results + filenames to ask the LLM
    for a config.yml, then save it and return the parsed config.
    """
    # Make sure local video_folder has copies of S3 videos
    local_files = _sync_s3_videos_to_local()
    analyses_map = load_all_analysis_results()

    if not local_files:
        return {"error": "No videos available. Upload videos first."}

    # Build ordered lists for prompt: only include those with an analysis
    files_for_prompt: List[str] = []
    analyses_for_prompt: List[str] = []

    for fname in sorted(local_files):
        key_norm = fname.lower()
        desc = analyses_map.get(key_norm)
        if not desc:
            # If missing, fallback to a dummy description
            desc = f"Hotel/travel clip: {fname}"
        files_for_prompt.append(fname)
        analyses_for_prompt.append(desc)

    prompt = build_yaml_prompt(files_for_prompt, analyses_for_prompt)

    if not client:
        # Fallback: build a simple default YAML if no OpenAI key
        log_step("[YAML] No OpenAI client; generating simple YAML fallback")
        cfg: Dict[str, Any] = _build_simple_yaml_fallback(files_for_prompt, analyses_for_prompt)
    else:
        log_step("[YAML] Calling LLM to generate config.yml")
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        content = (resp.choices[0].message.content or "").strip()
        content = content.replace("```yaml", "").replace("```", "").strip()

        cfg = yaml.safe_load(content)
        if not isinstance(cfg, dict):
            raise ValueError("LLM did not return valid YAML")

    # Normalize filenames (no extension changes; just basename / lowercase)
    cfg = sanitize_yaml_filenames(cfg)

    # Save to config_path
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    log_step(f"[YAML] Saved config.yml to {config_path}")

    return cfg


def _build_simple_yaml_fallback(files: List[str], analyses: List[str]) -> Dict[str, Any]:
    """
    Used when OpenAI is not available: simple deterministic YAML.
    """
    if not files:
        raise ValueError("No files to build fallback YAML")

    first = files[0]
    last = files[-1]
    middle = files[1:-1] if len(files) > 2 else []

    def _find_desc(name: str) -> str:
        idx = files.index(name)
        return analyses[idx]

    cfg: Dict[str, Any] = {
        "first_clip": {
            "file": first.lower(),
            "start_time": 0,
            "duration": 5,
            "text": _find_desc(first),
            "scale": 1.0,
        },
        "middle_clips": [
            {
                "file": m.lower(),
                "start_time": 0,
                "duration": 5,
                "text": _find_desc(m),
                "scale": 1.0,
            }
            for m in middle
        ],
        "last_clip": {
            "file": last.lower(),
            "start_time": 0,
            "duration": 5,
            "text": _find_desc(last),
            "scale": 1.0,
        },
        "render": {
            "tts_enabled": False,
            "tts_voice": "alloy",
            "fg_scale_default": 1.0,
            "blur_background": False,
        },
        "cta": {
            "enabled": False,
            "text": "",
            "voiceover": False,
            "duration": 3.0,
            "position": "bottom",
        },
    }
    return cfg


def api_get_config() -> Dict[str, Any]:
    """
    Return the current config.yml as JSON-compatible dict.
    """
    if not os.path.exists(config_path):
        return {"error": "config.yml not found"}

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    return cfg


def api_save_yaml(yaml_text: str) -> Dict[str, Any]:
    """
    Save YAML coming from the UI text editor.
    """
    try:
        cfg = yaml.safe_load(yaml_text) or {}
        if not isinstance(cfg, dict):
            raise ValueError("YAML must define a mapping at top level")

        cfg = sanitize_yaml_filenames(cfg)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        log_step("[SAVE_YAML] config.yml updated from UI")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"[SAVE_YAML] error: {e}")
        return {"status": "error", "error": str(e)}


# -------------------------------
# Captions
# -------------------------------
_CAPTIONS_FILE = os.path.join(os.path.dirname(__file__), "captions.txt")


def api_get_captions() -> Dict[str, Any]:
    if not os.path.exists(_CAPTIONS_FILE):
        return {"text": ""}
    with open(_CAPTIONS_FILE, "r", encoding="utf-8") as f:
        text = f.read()
    return {"text": text}


def api_save_captions(text: str) -> Dict[str, Any]:
    with open(_CAPTIONS_FILE, "w", encoding="utf-8") as f:
        f.write(text or "")
    log_step("[CAPTIONS] Saved captions text")
    return {"status": "ok"}


# -------------------------------
# Export
# -------------------------------
def api_export(optimized: bool = False) -> Dict[str, Any]:
    """
    Run edit_video(config.yml → final video), then return info
    about the output file. If you want, this is where you'd also
    upload the export to S3.
    """
    if not os.path.exists(config_path):
        return {"status": "error", "error": "config.yml not found"}

    try:
        mode = _EXPORT_MODE  # "standard" or "fast"
        log_step(f"[EXPORT] Rendering export in {mode.upper()} mode... optimized={optimized}")

        out_path = edit_video(optimized=optimized)
        if not out_path:
            raise ValueError("edit_video did not return an output path")

        log_step(f"[EXPORT] Video rendered: {out_path}")

        # If later you want S3 upload, you'd add it here.
        # For now, we just return the filesystem path so /api/download can serve it.
        return {
            "status": "ok",
            "output_path": out_path,
        }
    except Exception as e:
        logger.error(f"[EXPORT] Export failed: {e}")
        return {"status": "error", "error": str(e)}


# -------------------------------
# TTS & CTA
# -------------------------------
def _load_config_for_mutation() -> Dict[str, Any]:
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg


def _save_config(cfg: Dict[str, Any]) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


def api_set_tts(enabled: bool, voice: str | None) -> Dict[str, Any]:
    cfg = _load_config_for_mutation()
    render = cfg.setdefault("render", {})
    render["tts_enabled"] = bool(enabled)
    if voice:
        render["tts_voice"] = voice
    _save_config(cfg)
    log_step(f"[TTS] enabled={enabled}, voice={voice}")
    return {"status": "ok", "render": render}


def api_set_cta(enabled: bool, text: str | None, voiceover: bool | None) -> Dict[str, Any]:
    cfg = _load_config_for_mutation()
    cta = cfg.setdefault("cta", {})
    cta["enabled"] = bool(enabled)
    if text is not None:
        cta["text"] = text
    if voiceover is not None:
        cta["voiceover"] = bool(voiceover)
    _save_config(cfg)
    log_step(f"[CTA] enabled={enabled}, text={text}, voiceover={voiceover}")
    return {"status": "ok", "cta": cta}


# -------------------------------
# Overlay & timings & fgscale
# -------------------------------
def api_apply_overlay(style: str) -> Dict[str, Any]:
    from tiktok_assistant import apply_overlay as _apply_overlay

    _apply_overlay(style)
    return {"status": "ok", "style": style}


def api_apply_timings(smart: bool = False) -> Dict[str, Any]:
    from tiktok_assistant import apply_smart_timings

    pacing = "cinematic" if smart else "standard"
    apply_smart_timings(pacing=pacing)
    return {"status": "ok", "pacing": pacing}


def api_fgscale(value: float) -> Dict[str, Any]:
    cfg = _load_config_for_mutation()
    render = cfg.setdefault("render", {})
    render["fg_scale_default"] = float(value)
    _save_config(cfg)
    log_step(f"[FG_SCALE] set fg_scale_default={value}")
    return {"status": "ok", "render": render}


# -------------------------------
# Chat
# -------------------------------
def api_chat(message: str) -> Dict[str, Any]:
    if not client:
        return {"reply": f"(no OpenAI key configured) You said: {message}"}

    prompt = f"You are a friendly TikTok travel video assistant. User says:\n\n{message}"
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        reply = (resp.choices[0].message.content or "").strip()
        return {"reply": reply}
    except Exception as e:
        logger.error(f"[CHAT] error: {e}")
        return {"reply": f"Error from Chat API: {e}"}