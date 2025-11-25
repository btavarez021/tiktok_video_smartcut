
## 5️⃣ `assistant_api.py`
    
import os
import yaml
import json
import logging
import traceback
from typing import Dict, Any, List

from assistant_log import log_step, clear_status_log
from tiktok_template import config_path, edit_video, video_folder
from tiktok_assistant import (
    analyze_video,
    build_yaml_prompt,
    apply_smart_timings,
    apply_overlay,
    video_analyses_cache,
    TEXT_MODEL,
    list_videos_from_s3,
    download_s3_video,
    save_analysis_result,
    ANALYSIS_CACHE_DIR,
    normalize_video,  
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================
# MODULE-LEVEL CONFIG (mirror of config.yml)
# ============================================
CONFIG: Dict[str, Any] = {}


def load_config():
    global CONFIG
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            CONFIG = yaml.safe_load(f) or {}
    else:
        CONFIG = {}
    return CONFIG


def save_config():
    with open(config_path, "w") as f:
        yaml.safe_dump(CONFIG, f, sort_keys=False)


# ============================================
# EXPORT MODE HELPERS (standard / optimized)
# ============================================
EXPORT_MODE_FILE = "export_mode.txt"


def set_export_mode(mode: str) -> Dict[str, str]:
    if mode not in ("standard", "optimized"):
        mode = "standard"
    with open(EXPORT_MODE_FILE, "w") as f:
        f.write(mode)
    return {"mode": mode}


def get_export_mode() -> Dict[str, str]:
    if not os.path.exists(EXPORT_MODE_FILE):
        return {"mode": "standard"}
    with open(EXPORT_MODE_FILE, "r") as f:
        mode = (f.read().strip() or "standard")
    if mode not in ("standard", "optimized"):
        mode = "standard"
    return {"mode": mode}


# ============================================
# LOAD ALL ANALYSIS RESULTS FROM DISK
# ============================================
def load_all_analysis_results() -> Dict[str, str]:
    """
    Loads all cached analysis results stored as .json files in video_analysis_cache/
    Returns { filename: description }
    """
    results: Dict[str, str] = {}

    if not os.path.exists(ANALYSIS_CACHE_DIR):
        return results

    for name in os.listdir(ANALYSIS_CACHE_DIR):
        if not name.endswith(".json"):
            continue

        file_path = os.path.join(ANALYSIS_CACHE_DIR, name)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            filename = data.get("filename") or name.replace(".json", "")
            desc = data.get("description") or ""
            results[filename] = desc
        except Exception as e:
            logger.warning(f"⚠ Failed loading cached analysis file {name}: {e}")

    # merge with in-memory cache
    results.update(video_analyses_cache)
    return results


# ============================================
# ANALYZE FLOW (step-based)
# ============================================
_ANALYZE_QUEUE: List[str] = []
_ANALYZE_INDEX: int = 0


def _reset_local_video_folder():
    os.makedirs(video_folder, exist_ok=True)
    for name in os.listdir(video_folder):
        path = os.path.join(video_folder, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass


def api_analyze_start():
    """
    - Clears local tik_tok_downloads folder
    - Lists raw_uploads/*.mp4 from S3 and stores in queue
    """
    global _ANALYZE_QUEUE, _ANALYZE_INDEX

    clear_status_log()
    log_step("Starting analysis…")

    _reset_local_video_folder()

    log_step("Fetching videos from S3 (raw_uploads/)…")
    s3_keys = list_videos_from_s3()

    if not s3_keys:
        log_step("No videos found in raw_uploads/.")
        _ANALYZE_QUEUE = []
        _ANALYZE_INDEX = 0
        return {"total": 0, "remaining": 0, "keys": []}

    _ANALYZE_QUEUE = s3_keys
    _ANALYZE_INDEX = 0

    log_step(f"Found {len(s3_keys)} video(s) in raw_uploads.")
    return {"total": len(s3_keys), "remaining": len(s3_keys), "keys": s3_keys}


def api_analyze_step():
    """
    Process ONE video from _ANALYZE_QUEUE.
    Returns {done, total, index, key, description?}
    """
    global _ANALYZE_QUEUE, _ANALYZE_INDEX

    if not _ANALYZE_QUEUE:
        return {"done": True, "total": 0, "index": 0}

    if _ANALYZE_INDEX >= len(_ANALYZE_QUEUE):
        log_step("All videos analyzed ✅")
        return {"done": True, "total": len(_ANALYZE_QUEUE), "index": _ANALYZE_INDEX}

    key = _ANALYZE_QUEUE[_ANALYZE_INDEX]
    log_step(f"Processing {key}…")

    try:
        tmp_local_path = download_s3_video(key)
        if not tmp_local_path:
            log_step(f"❌ Failed to download {key}")
            _ANALYZE_INDEX += 1
            return {
                "done": _ANALYZE_INDEX >= len(_ANALYZE_QUEUE),
                "total": len(_ANALYZE_QUEUE),
                "index": _ANALYZE_INDEX,
                "key": key,
                "error": "download_failed",
            }

        base = os.path.basename(key).lower()
        local_path = os.path.join(video_folder, base)
        log_step(f"Normalizing {base} → {local_path} …")
        normalize_video(tmp_local_path, local_path)

        try:
            os.remove(tmp_local_path)
        except OSError:
            pass

        log_step(f"Analyzing {base} with LLM…")
        desc = analyze_video(local_path)
        log_step(f"Analysis complete for {base}.")

        save_analysis_result(base, desc)
        result = {
            "done": False,
            "total": len(_ANALYZE_QUEUE),
            "index": _ANALYZE_INDEX + 1,
            "key": base,
            "description": desc,
        }
    except Exception as e:
        err_msg = f"❌ ERROR processing {key}: {e}"
        log_step(err_msg)
        log_step(traceback.format_exc())
        logger.exception(err_msg)
        result = {
            "done": False,
            "total": len(_ANALYZE_QUEUE),
            "index": _ANALYZE_INDEX + 1,
            "key": key,
            "error": str(e),
        }

    _ANALYZE_INDEX += 1
    if _ANALYZE_INDEX >= len(_ANALYZE_QUEUE):
        log_step("All videos analyzed ✅")
        result["done"] = True

    return result


def api_analyze():
    """
    Convenience one-shot analyze (loops steps).
    May be slower, but keeps compatibility with /api/analyze.
    """
    start_info = api_analyze_start()
    total = start_info.get("total", 0)
    if total == 0:
        return {"results": {}, "total": 0}

    while True:
        step = api_analyze_step()
        if step.get("done"):
            break

    return load_all_analysis_results()


# ============================================
# YAML GENERATION
# ============================================
def api_generate_yaml():
    """
    Use build_yaml_prompt + LLM to generate YAML, save to config.yml, and return dict.
    Uses both in-memory and disk cache for analyses.
    """
    merged = load_all_analysis_results()

    if not merged:
        log_step("No cached analyses; running quick analyze before YAML generation…")
        api_analyze()
        merged = load_all_analysis_results()

    video_files = list(merged.keys())
    analyses = [merged.get(v, "") for v in video_files]

    if not video_files:
        log_step("No videos available for YAML generation.")
        return {}

    yaml_prompt = build_yaml_prompt(video_files, analyses)
    log_step("Calling LLM to produce YAML storyboard…")

    if client is None:
        # Fallback: build a simple config without LLM
        log_step("No OpenAI client; generating simple fallback YAML.")
        simple_cfg = {
            "first_clip": {
                "file": video_files[0],
                "start_time": 0,
                "duration": 5.0,
                "text": analyses[0] or "Hotel TikTok intro.",
                "scale": 1.0,
            },
            "middle_clips": [],
            "last_clip": {
                "file": video_files[-1],
                "start_time": 0,
                "duration": 5.0,
                "text": "Would you stay here?",
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
                "fg_scale_default": 1.0,
            },
            "cta": {
                "enabled": False,
                "text": "",
                "voiceover": False,
                "duration": 3.0,
                "position": "bottom",
            },
        }
        if len(video_files) > 2:
            for vf, a in zip(video_files[1:-1], analyses[1:-1]):
                simple_cfg["middle_clips"].append(
                    {
                        "file": vf,
                        "start_time": 0,
                        "duration": 5.0,
                        "text": a or "Hotel detail shot.",
                        "scale": 1.0,
                    }
                )

        with open(config_path, "w") as f:
            yaml.safe_dump(simple_cfg, f, sort_keys=False)
        load_config()
        log_step("Fallback YAML written to config.yml ✅")
        return simple_cfg

    # Normal LLM path
    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": yaml_prompt}],
        temperature=0.2,
    )
    yaml_text = (resp.choices[0].message.content or "").strip()
    yaml_text = yaml_text.replace("```yaml", "").replace("```", "").strip()

    cfg = yaml.safe_load(yaml_text) or {}

    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    load_config()
    log_step("YAML written to config.yml ✅")
    return cfg


def api_save_yaml(yaml_text: str):
    cfg = yaml.safe_load(yaml_text) or {}
    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    load_config()
    return {"status": "ok"}


def api_get_config():
    load_config()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            yaml_text = f.read()
    else:
        yaml_text = "# No config.yml found yet."
    return {
        "yaml": yaml_text,
        "config": CONFIG,
    }


# ============================================
# Export (render video)
# ============================================
def api_export(optimized: bool = False) -> str:
    clear_status_log()
    mode_label = "OPTIMIZED" if optimized else "STANDARD"
    log_step(f"Rendering export in {mode_label} mode…")

    filename = "output_tiktok_final_optimized.mp4" if optimized else "output_tiktok_final.mp4"

    try:
        log_step("Rendering timeline with music, captions, and voiceover flags…")
        edit_video(output_file=filename, optimized=optimized)
        log_step(f"Export finished → {filename}")
    except Exception as e:
        msg = f"Export failed while calling edit_video: {e}"
        log_step(f"❌ {msg}")
        logger.exception(msg)
        raise

    full_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(full_path):
        msg = f"Export failed: file {filename} not found after render."
        log_step(f"❌ {msg}")
        logger.error(msg)
        raise FileNotFoundError(msg)

    return filename


# ============================================
# TTS / CTA / FG SCALE
# ============================================
def api_set_tts(enabled: bool, voice: str | None = None):
    load_config()
    render_cfg = CONFIG.setdefault("render", {})
    render_cfg["tts_enabled"] = bool(enabled)
    if voice:
        render_cfg["tts_voice"] = voice
    save_config()
    return render_cfg


def api_set_cta(enabled: bool, text: str | None = None, voiceover: bool | None = None):
    load_config()
    cta_cfg = CONFIG.setdefault("cta", {})
    cta_cfg["enabled"] = bool(enabled)
    if text is not None:
        cta_cfg["text"] = text
    if voiceover is not None:
        cta_cfg["voiceover"] = bool(voiceover)
    save_config()
    return cta_cfg


def api_fgscale(value: float):
    load_config()
    render_cfg = CONFIG.setdefault("render", {})
    render_cfg["fg_scale_default"] = float(value)
    save_config()
    return render_cfg


# ============================================
# Overlay / Timings
# ============================================
def api_apply_overlay(style: str):
    apply_overlay(style=style, target="all", filename=None)
    load_config()
    return {"status": "ok"}


def api_apply_timings(smart: bool = False):
    if smart:
        apply_smart_timings(pacing="cinematic")
    else:
        apply_smart_timings()
    load_config()
    return CONFIG


# ============================================
# Captions
# ============================================
def api_get_captions():
    load_config()
    lines: List[str] = []

    if "first_clip" in CONFIG:
        lines.append(CONFIG["first_clip"].get("text", ""))

    for m in CONFIG.get("middle_clips", []):
        lines.append(m.get("text", ""))

    if "last_clip" in CONFIG:
        lines.append(CONFIG["last_clip"].get("text", ""))

    return {"captions": "\n".join(lines)}


def api_save_captions(text: str):
    load_config()
    captions = [line.strip() for line in text.split("\n") if line.strip()]

    idx = 0
    if "first_clip" in CONFIG and idx < len(captions):
        CONFIG["first_clip"]["text"] = captions[idx]
        idx += 1

    for c in CONFIG.get("middle_clips", []):
        if idx < len(captions):
            c["text"] = captions[idx]
            idx += 1

    if "last_clip" in CONFIG and idx < len(captions):
        CONFIG["last_clip"]["text"] = captions[idx]

    save_config()
    return {"status": "ok", "captions_applied": len(captions)}


# ============================================
# Chat – TikTok creative assistant
# ============================================
def api_chat(message: str):
    if client is None:
        return {"reply": "LLM is not configured (no API key)."}

    prompt = (
        "You are the TikTok Creative Assistant. Use the user's video analyses to "
        "craft hooks, captions, CTAs, and storylines.\n\n"
        f"Video Analyses:\n{video_analyses_cache}\n\n"
        f"User Request:\n{message}"
    )

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
    )

    reply = resp.choices[0].message.content
    return {"reply": reply}
