import os
import json
import yaml
import traceback
import logging

from assistant_log import log_step, status_log, clear_status_log
from tiktok_assistant import (
    list_videos_from_s3,
    download_s3_video,
    analyze_video,
    save_analysis_result,
    load_all_analysis_results,
    apply_overlay,
    apply_smart_timings,
    video_analyses_cache,
)
from tiktok_renderer import render_final_video
from tiktok_template import config_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ==========================================================
# LOAD / SAVE CONFIG
# ==========================================================
config = {}

def load_config():
    global config
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    return config


def save_config():
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)

# ==========================================================
# EXPORT MODE HELPERS — required for UI toggle
# ==========================================================
EXPORT_MODE_FILE = "export_mode.txt"

def set_export_mode(mode: str) -> dict:
    """
    Persist export mode to a tiny local file.
    Valid modes: 'standard' or 'optimized'.
    """
    if mode not in ("standard", "optimized"):
        mode = "standard"
    with open(EXPORT_MODE_FILE, "w") as f:
        f.write(mode)
    return {"mode": mode}


def get_export_mode() -> dict:
    """
    Read the stored export mode from file.
    Defaults to standard.
    """
    if not os.path.exists(EXPORT_MODE_FILE):
        return {"mode": "standard"}
    try:
        with open(EXPORT_MODE_FILE, "r") as f:
            mode = f.read().strip()
        if mode not in ("standard", "optimized"):
            mode = "standard"
        return {"mode": mode}
    except Exception:
        return {"mode": "standard"}
    
# ==========================================================
# STEP-BASED ANALYSIS ENGINE
# ==========================================================

analysis_queue = []   # filenames pending
analysis_results = {} # track results for single session


def api_analyze_start():
    """
    Step 1: Build queue from S3
    """
    global analysis_queue, analysis_results

    analysis_queue = []
    analysis_results = {}

    s3_list = list_videos_from_s3()

    if not s3_list:
        return {"queue": [], "message": "No videos in s3://raw_uploads"}

    # Example: raw_uploads/IMG_3753.mov → IMG_3753.mov
    cleaned = [os.path.basename(k) for k in s3_list]
    analysis_queue.extend(cleaned)

    log_step(f"Step-based analyze started: {len(cleaned)} clips")

    return {
        "queue": cleaned,
        "message": "Ready for step analysis"
    }


def api_analyze_step():
    """
    Step 2: Process ONE clip each call
    """
    global analysis_queue, analysis_results

    if not analysis_queue:
        return {
            "done": True,
            "results": analysis_results,
            "message": "Analysis complete"
        }

    next_file = analysis_queue.pop(0)
    log_step(f"Analyzing (step): {next_file}")

    try:
        tmp_path = download_s3_video(next_file)
        desc = analyze_video(tmp_path)
        save_analysis_result(next_file, desc)
        analysis_results[next_file] = desc
    except Exception as e:
        logger.error(f"Analyze step failed: {e}")
        log_step(f"ERROR: {str(e)}")

    return {
        "done": len(analysis_queue) == 0,
        "current": next_file,
        "remaining": len(analysis_queue),
        "results": analysis_results,
    }


# ==========================================================
# OLD ONE-SHOT ANALYZE (optional)
# ==========================================================
def api_analyze():
    log_step("One-shot analyzer requested")

    s3_list = list_videos_from_s3()
    if not s3_list:
        return {}

    results = {}
    for key in s3_list:
        file = os.path.basename(key)
        log_step(f"Analyzing {file}…")

        try:
            tmp = download_s3_video(file)
            desc = analyze_video(tmp)
            save_analysis_result(file, desc)
            results[file] = desc
        except Exception as e:
            log_step(f"Analyze FAILED: {file} → {e}")
            logger.error(traceback.format_exc())

    return results


# ==========================================================
# YAML GENERATION
# ==========================================================
def api_generate_yaml():
    """
    Uses both disk + memory analyses to build config.yml
    """
    disk = load_all_analysis_results()
    merged = {**disk, **video_analyses_cache}

    if not merged:
        return {"error": "No analyses found. Run analyze first."}

    video_files = list(merged.keys())
    analyses = [merged[k] for k in video_files]

    # Build simple YAML template
    cfg = {
        "first_clip": {
            "file": video_files[0],
            "start_time": 0,
            "duration": 4,
            "text": analyses[0],
            "scale": 1.0,
        },
        "middle_clips": [],
        "last_clip": {
            "file": video_files[-1],
            "start_time": 0,
            "duration": 4,
            "text": analyses[-1],
            "scale": 1.0,
        },
        "music": {
            "style": "chill travel",
            "mood": "uplifting",
            "volume": 0.25,
        },
        "render": {
            "tts_enabled": False,
            "tts_voice": "alloy",
            "fg_scale_default": 1.0
        },
        "cta": {
            "enabled": False,
            "text": "",
            "voiceover": False,
            "duration": 3.0,
            "position": "bottom"
        }
    }

    # Add middle clips if > 2
    if len(video_files) > 2:
        for vf, a in zip(video_files[1:-1], analyses[1:-1]):
            cfg["middle_clips"].append({
                "file": vf,
                "start_time": 0,
                "duration": 4,
                "text": a,
                "scale": 1.0
            })

    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return cfg


# ==========================================================
# YAML SAVE/GET
# ==========================================================
def api_get_config():
    load_config()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            yaml_text = f.read()
    else:
        yaml_text = "# No config.yml found."

    return {
        "yaml": yaml_text,
        "config": config
    }


def api_save_yaml(yaml_text: str):
    try:
        cfg = yaml.safe_load(yaml_text) or {}
        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)
        load_config()
        return {"status": "ok"}
    except Exception as e:
        return {"error": str(e)}


# ==========================================================
# EXPORT (Rendering)
# ==========================================================
def api_export(optimized=False):
    clear_status_log()
    log_step("Starting EXPORT…")

    try:
        path = render_final_video(optimized=optimized)
        return {"filename": path}
    except Exception as e:
        log_step(f"Export FAILED: {str(e)}")
        logger.error(traceback.format_exc())
        return {"error": str(e)}


# ==========================================================
# CTA
# ==========================================================
def api_set_cta(enabled: bool, text: str = None, voiceover: bool = None):
    load_config()
    cfg = config.setdefault("cta", {})

    cfg["enabled"] = bool(enabled)
    if text is not None:
        cfg["text"] = text
    if voiceover is not None:
        cfg["voiceover"] = bool(voiceover)

    save_config()
    return cfg


# ==========================================================
# TTS
# ==========================================================
def api_set_tts(enabled: bool, voice: str = None):
    load_config()
    r = config.setdefault("render", {})
    r["tts_enabled"] = bool(enabled)
    if voice:
        r["tts_voice"] = voice
    save_config()
    return r


# ==========================================================
# Overlay Styles
# ==========================================================
def api_apply_overlay(style: str):
    apply_overlay(style)
    load_config()
    return {"status": "ok"}


# ==========================================================
# Caption Editing
# ==========================================================
def api_save_captions(text: str):
    """
    Replace captions in YAML: separated by blank lines
    """
    load_config()

    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    idx = 0

    if "first_clip" in config and idx < len(parts):
        config["first_clip"]["text"] = parts[idx]
        idx += 1

    for mc in config.get("middle_clips", []):
        if idx < len(parts):
            mc["text"] = parts[idx]
            idx += 1

    if "last_clip" in config and idx < len(parts):
        config["last_clip"]["text"] = parts[idx]

    save_config()
    return {"status": "ok"}


# ==========================================================
# Smart Timings
# ==========================================================
def api_apply_timings(smart: bool = False):
    apply_smart_timings("cinematic" if smart else "standard")
    load_config()
    return config


# ==========================================================
# FG Scale
# ==========================================================
def api_fgscale(value: float):
    load_config()
    r = config.setdefault("render", {})
    r["fg_scale_default"] = float(value)
    save_config()
    return r


# ==========================================================
# Creative Chat
# ==========================================================
def api_chat(message: str):
    return {
        "reply": (
            "Creative chat temporarily disabled during renderer migration.\n"
            "You can ask me to re-enable OpenAI chat when ready."
        )
    }
