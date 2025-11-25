# assistant_api.py
import os
import yaml
import json
import logging
import traceback
import tempfile
import shutil

from assistant_log import log_step, status_log, clear_status_log
from tiktok_template import (
    edit_video,
    normalize_video_ffmpeg,
    video_folder,
    config_path,
    client,
)
from tiktok_assistant import (
    analyze_video,
    build_yaml_prompt,
    apply_overlay,
    apply_smart_timings,
    video_analyses_cache,
    save_analysis_result,
    list_videos_from_s3,
    download_s3_video,
    RAW_PREFIX,
    s3,
    S3_BUCKET_NAME,
    ANALYSIS_CACHE_DIR,
)

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIG HELPERS
# ============================================================================
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


# ============================================================================
# EXPORT MODE
# ============================================================================
EXPORT_MODE_FILE = "export_mode.txt"


def set_export_mode(mode: str):
    if mode not in ("standard", "optimized"):
        mode = "standard"
    with open(EXPORT_MODE_FILE, "w") as f:
        f.write(mode)
    return {"mode": mode}


def get_export_mode():
    if not os.path.exists(EXPORT_MODE_FILE):
        return {"mode": "standard"}
    with open(EXPORT_MODE_FILE, "r") as f:
        m = f.read().strip() or "standard"
    if m not in ("standard", "optimized"):
        m = "standard"
    return {"mode": m}


# ============================================================================
# LOAD ANALYSIS RESULTS FROM DISK
# ============================================================================
def load_all_analysis_results():
    results = {}
    if not os.path.exists(ANALYSIS_CACHE_DIR):
        return results

    for filename in os.listdir(ANALYSIS_CACHE_DIR):
        if not filename.endswith(".json"):
            continue

        path = os.path.join(ANALYSIS_CACHE_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            key = obj.get("filename") or filename.replace(".json", "")
            desc = obj.get("description", "")
            results[key] = desc
        except Exception as e:
            logger.warning(f"⚠ Failed to load {filename}: {e}")
    return results


# ============================================================================
# STEP-BASED ANALYZE (Render Safe)
# ============================================================================
_current_analyze_batch = []  # S3 keys remaining


def api_analyze_start():
    """
    Initialize analyzing one-by-one — safe for Render.
    """
    global _current_analyze_batch
    clear_status_log()

    log_step("Scanning S3 raw_uploads/...")

    s3_keys = list_videos_from_s3()
    if not s3_keys:
        log_step("No videos found.")
        _current_analyze_batch = []
        return {"remaining": 0}

    _current_analyze_batch = list(s3_keys)
    log_step(f"Prepared {len(_current_analyze_batch)} videos.")
    return {"remaining": len(_current_analyze_batch)}


def api_analyze_step():
    """
    Processes ONE video per call.
    """
    global _current_analyze_batch

    if not _current_analyze_batch:
        log_step("Analysis complete.")
        return {"remaining": 0}

    key = _current_analyze_batch.pop(0)
    base = os.path.basename(key).lower()
    log_step(f"Analyzing: {base}")

    try:
        # Download temporary file
        tmp = download_s3_video(key)
        if not tmp:
            log_step(f"ERROR: Could not download {key}")
            return {"remaining": len(_current_analyze_batch)}

        # Normalize → video_folder
        local_path = os.path.join(video_folder, base)
        log_step(f"Normalizing → {local_path}")
        normalize_video_ffmpeg(tmp, local_path)

        # Cleanup temp
        try:
            os.remove(tmp)
        except:
            pass

        # Analyze with LLM
        log_step(f"LLM analyzing {base} ...")
        desc = analyze_video(local_path)

        # Save to cache (disk + memory)
        save_analysis_result(base, desc)
        log_step(f"Analysis complete for {base}")

    except Exception as e:
        log_step(f"❌ Error: {e}")
        log_step(traceback.format_exc())

    return {"remaining": len(_current_analyze_batch)}


# ============================================================================
# ONE-SHOT ANALYZE (not recommended on Render)
# ============================================================================
def api_analyze():
    """
    Legacy one-shot analyze. Render timeouts possible.
    """
    clear_status_log()
    log_step("Running one-shot analyze...")

    results = {}
    s3_keys = list_videos_from_s3()
    if not s3_keys:
        return {}

    for key in s3_keys:
        base = os.path.basename(key).lower()
        log_step(f"Processing {base}")

        try:
            tmp = download_s3_video(key)
            local_path = os.path.join(video_folder, base)
            normalize_video_ffmpeg(tmp, local_path)

            try:
                os.remove(tmp)
            except:
                pass

            desc = analyze_video(local_path)
            save_analysis_result(base, desc)
            results[base] = desc

        except Exception as e:
            log_step(f"❌ {e}")
            log_step(traceback.format_exc())

    return results


# ============================================================================
# YAML GENERATION
# ============================================================================
def api_generate_yaml():
    disk = load_all_analysis_results()
    merged = {**disk, **video_analyses_cache}

    if not merged:
        log_step("No analyses — running quick analyze()...")
        api_analyze()
        disk = load_all_analysis_results()
        merged = {**disk, **video_analyses_cache}

    video_files = list(merged.keys())
    analyses = [merged[f] for f in video_files]

    if not video_files:
        return {}

    prompt = build_yaml_prompt(video_files, analyses)
    log_step("Calling LLM for YAML...")

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )

    yaml_text = (resp.choices[0].message.content or "").strip()
    yaml_text = yaml_text.replace("```yaml", "").replace("```", "")

    cfg = yaml.safe_load(yaml_text) or {}

    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    load_config()
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
        yaml_text = "# No config yet"

    return {"yaml": yaml_text, "config": config}


# ============================================================================
# EXPORT VIDEO
# ============================================================================
def api_export(optimized=False):
    clear_status_log()

    filename = (
        "output_tiktok_optimized.mp4"
        if optimized else
        "output_tiktok_standard.mp4"
    )

    log_step(f"Rendering export: {filename}")

    try:
        edit_video(output_file=filename, optimized=optimized)
    except Exception as e:
        log_step(f"❌ Export error: {e}")
        raise

    if not os.path.exists(filename):
        raise FileNotFoundError(f"Export failed: no file {filename}")

    return filename


# ============================================================================
# TTS / CTA / OVERLAY / TIMINGS / FG SCALE
# ============================================================================
def api_set_tts(enabled: bool, voice: str | None):
    load_config()
    cfg = config.setdefault("render", {})
    cfg["tts_enabled"] = bool(enabled)
    if voice:
        cfg["tts_voice"] = voice
    save_config()
    return cfg


def api_set_cta(enabled: bool, text: str | None, voiceover: bool):
    load_config()
    cfg = config.setdefault("cta", {})
    cfg["enabled"] = bool(enabled)
    if text is not None:
        cfg["text"] = text
    cfg["voiceover"] = bool(voiceover)
    save_config()
    return cfg


def api_apply_overlay(style: str):
    apply_overlay(style, target="all", filename=None)
    load_config()
    return {"status": "ok"}


def api_apply_timings(smart=False):
    if smart:
        apply_smart_timings(pacing="cinematic")
    else:
        apply_smart_timings()
    load_config()
    return config


def api_fgscale(value: float):
    load_config()
    cfg = config.setdefault("render", {})
    cfg["fg_scale_default"] = float(value)
    save_config()
    return cfg


# ============================================================================
# CHAT
# ============================================================================
def api_chat(message: str):
    prompt = f"""
You are the TikTok travel creative assistant.
User message:
{message}
"""
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
    )
    return {"reply": resp.choices[0].message.content}


# ============================================================================
# S3 UPLOAD
# ============================================================================
def api_s3_upload(file):
    """
    Upload uploaded file → S3 raw_uploads/
    """
    filename = file.filename.lower()

    key = RAW_PREFIX + filename
    temp = tempfile.NamedTemporaryFile(delete=False)
    file.save(temp.name)

    try:
        s3.upload_file(temp.name, S3_BUCKET_NAME, key)
        return {"uploaded": filename, "s3_key": key}
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        try:
            os.remove(temp.name)
        except:
            pass


def api_list_s3_raw():
    items = list_videos_from_s3()
    return items
