import os
import yaml
import json
import logging
import traceback

from assistant_log import log_step, status_log, clear_status_log

# Import functions from tiktok_assistant
from tiktok_assistant import (
    video_analyses_cache,
    ANALYSIS_CACHE_DIR,
    list_videos_from_s3,
    download_s3_video,
    normalize_video,
    analyze_video,
    save_analysis_result,
    build_yaml_prompt,
    apply_overlay,
    apply_smart_timings,
)

# Import renderer + config helpers from tiktok_template
from tiktok_template import (
    client,
    config_path,
    load_config,
    save_config,
    edit_video,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ======================================================
#  EXPORT MODE (standard vs optimized)
# ======================================================
EXPORT_MODE_FILE = "export_mode.txt"

def set_export_mode(mode: str) -> dict:
    """Save export mode."""
    if mode not in ("standard", "optimized"):
        mode = "standard"
    with open(EXPORT_MODE_FILE, "w") as f:
        f.write(mode)
    return {"mode": mode}

def get_export_mode() -> dict:
    """Read export mode from disk."""
    if not os.path.exists(EXPORT_MODE_FILE):
        return {"mode": "standard"}
    try:
        with open(EXPORT_MODE_FILE, "r") as f:
            mode = f.read().strip()
        if mode not in ("standard", "optimized"):
            mode = "standard"
        return {"mode": mode}
    except:
        return {"mode": "standard"}


# ======================================================
#  ANALYSIS CACHE LOADING (disk)
# ======================================================
def load_all_analysis_results():
    """
    Loads all cached analysis results (.json) from video_analysis_cache/.
    Returns: {filename: description}
    """
    results = {}

    if not os.path.exists(ANALYSIS_CACHE_DIR):
        return results

    for fname in os.listdir(ANALYSIS_CACHE_DIR):
        if not fname.endswith(".json"):
            continue

        fpath = os.path.join(ANALYSIS_CACHE_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                filename = data.get("filename") or fname.replace(".json", "")
                desc = data.get("description") or ""
                results[filename] = desc
        except Exception as e:
            logger.warning(f"Could not load cache file {fname}: {e}")

    return results



# ======================================================
#  STEP-BASED ANALYZE API
# ======================================================
_pending_keys = []     # internal: list of S3 keys to analyze
_current_index = 0     # internal pointer

def api_analyze_start():
    """
    Fetch S3 video list & prepare step-by-step state.
    """
    global _pending_keys, _current_index

    _pending_keys = list_videos_from_s3()
    _current_index = 0

    if not _pending_keys:
        return {"status": "empty"}

    log_step(f"Starting step-based analysis: {len(_pending_keys)} videos")
    return {
        "status": "ready",
        "total": len(_pending_keys),
        "next": 0,
    }


def api_analyze_step():
    """
    Performs analysis for ONE video.
    Frontend calls repeatedly until index == total.
    """
    global _pending_keys, _current_index

    if not _pending_keys:
        return {"status": "no_pending"}

    if _current_index >= len(_pending_keys):
        return {"status": "done"}

    key = _pending_keys[_current_index]
    base = os.path.basename(key).lower()

    log_step(f"Processing {key}…")

    try:
        # Download temp file
        tmp_local = download_s3_video(key)
        if not tmp_local:
            log_step(f"❌ Failed to download {key}")
            _current_index += 1
            return {
                "status": "error",
                "index": _current_index,
            }

        # Normalize
        normalized_path = os.path.join("temp_normalized", base)
        os.makedirs("temp_normalized", exist_ok=True)
        log_step(f"Normalizing {base} → {normalized_path}")
        normalize_video(tmp_local, normalized_path)

        try:
            os.remove(tmp_local)
        except:
            pass

        # LLM analysis
        log_step(f"Analyzing {base} with LLM…")
        desc = analyze_video(normalized_path)

        save_analysis_result(base, desc)
        log_step(f"Analysis saved for {base}")

    except Exception as e:
        err = f"❌ ERROR processing {key}: {e}"
        logger.exception(err)
        log_step(err)

    _current_index += 1
    return {
        "status": "ok",
        "index": _current_index,
        "total": len(_pending_keys),
        "filename": base,
    }



# ======================================================
#  ONE-SHOT ANALYZE (optional)
# ======================================================
def api_analyze():
    """
    Simple: analyze all videos in one loop.
    No step features; used mainly for debugging.
    """
    log_step("Starting full analysis…")

    s3_keys = list_videos_from_s3()
    if not s3_keys:
        log_step("No videos found.")
        return {}

    results = {}

    for key in s3_keys:
        base = os.path.basename(key).lower()
        log_step(f"Processing {base}…")

        try:
            tmp_local = download_s3_video(key)
            if not tmp_local:
                continue

            normalized = os.path.join("temp_normalized", base)
            os.makedirs("temp_normalized", exist_ok=True)
            normalize_video(tmp_local, normalized)

            desc = analyze_video(normalized)
            save_analysis_result(base, desc)
            results[base] = desc

            try:
                os.remove(tmp_local)
            except:
                pass

        except Exception as e:
            log_step(f"❌ Error: {e}")
            continue

    log_step("All videos analyzed.")
    return results



# ======================================================
#  YAML GENERATION
# ======================================================
def api_generate_yaml():
    """
    Load all analyses (disk + memory) and produce storyboard YAML.
    """
    disk_results = load_all_analysis_results()
    merged = {**disk_results, **video_analyses_cache}

    if not merged:
        log_step("No analyses available. Running quick analyze...")
        api_analyze()
        disk_results = load_all_analysis_results()
        merged = {**disk_results, **video_analyses_cache}

    video_files = list(merged.keys())
    analyses = [merged[v] for v in video_files]

    prompt = build_yaml_prompt(video_files, analyses)
    log_step("Calling LLM for YAML…")

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        raw_yaml = (resp.choices[0].message.content or "").strip()
        raw_yaml = raw_yaml.replace("```yaml", "").replace("```", "")

        cfg = yaml.safe_load(raw_yaml) or {}

        with open(config_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False)

        load_config()
        log_step("YAML written to config.yml")
        return cfg

    except Exception as e:
        logger.exception("YAML LLM error")
        return {"error": str(e)}



# ======================================================
#  SAVE YAML MANUALLY
# ======================================================
def api_save_yaml(text: str):
    cfg = yaml.safe_load(text) or {}
    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    load_config()
    return {"status": "ok"}



# ======================================================
#  GET CONFIG
# ======================================================
def api_get_config():
    load_config()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            raw = f.read()
    else:
        raw = "# No config.yml"
    return {"yaml": raw, "config": load_config()}



# ======================================================
#  EXPORT FINAL VIDEO
# ======================================================
def api_export(optimized=False):
    clear_status_log()

    mode = "optimized" if optimized else "standard"
    log_step(f"Rendering export in {mode.upper()} mode…")

    filename = (
        "output_final_optimized.mp4"
        if optimized
        else "output_final.mp4"
    )

    try:
        edit_video(filename, optimized=optimized)
    except Exception as e:
        log_step(f"❌ Export failed: {e}")
        raise

    if not os.path.exists(filename):
        log_step(f"❌ Export file missing: {filename}")
        raise FileNotFoundError(filename)

    log_step(f"Export finished → {filename}")
    return filename



# ======================================================
#  TTS, CTA, OVERLAY, CAPTIONS, TIMINGS, FG SCALE
# ======================================================
def api_set_tts(enabled: bool, voice: str = None):
    cfg = load_config()
    render_cfg = cfg.setdefault("render", {})
    render_cfg["tts_enabled"] = bool(enabled)
    if voice:
        render_cfg["tts_voice"] = voice
    save_config()
    return render_cfg


def api_set_cta(enabled: bool, text: str = None, voiceover: bool = None):
    cfg = load_config()
    cta_cfg = cfg.setdefault("cta", {})
    cta_cfg["enabled"] = bool(enabled)
    if text is not None:
        cta_cfg["text"] = text
    if voiceover is not None:
        cta_cfg["voiceover"] = bool(voiceover)
    save_config()
    return cta_cfg


def api_apply_overlay(style: str):
    apply_overlay(style, target="all")
    load_config()
    return {"status": "ok"}


def api_save_captions(text: str):
    cfg = load_config()
    parts = [p.strip() for p in text.split("\n\n") if p.strip()]

    idx = 0
    if "first_clip" in cfg:
        cfg["first_clip"]["text"] = parts[idx] if idx < len(parts) else ""
        idx += 1

    for item in cfg.get("middle_clips", []):
        item["text"] = parts[idx] if idx < len(parts) else ""
        idx += 1

    if "last_clip" in cfg:
        cfg["last_clip"]["text"] = parts[idx] if idx < len(parts) else ""

    save_config()
    return {"status": "ok"}


def api_apply_timings(smart=False):
    apply_smart_timings("cinematic" if smart else "standard")
    load_config()
    return load_config()


def api_fgscale(value: float):
    cfg = load_config()
    cfg.setdefault("render", {})["fg_scale_default"] = float(value)
    save_config()
    return cfg["render"]


# ======================================================
#  CHAT ENDPOINT
# ======================================================
def api_chat(message: str):
    if not client:
        return {"reply": "LLM not configured."}

    prompt = (
        "You are the TikTok Creative Assistant.\n"
        "Use video analyses to craft hooks, captions, CTAs.\n\n"
        f"Video Analyses:\n{video_analyses_cache}\n\n"
        f"User Request:\n{message}"
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
    )

    return {"reply": resp.choices[0].message.content}
