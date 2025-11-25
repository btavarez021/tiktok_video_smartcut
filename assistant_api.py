# assistant_api.py
import os
import yaml
import json
import logging
import traceback

from assistant_log import log_step, status_log, clear_status_log

from tiktok_template import (
    config_path,
    edit_video,
    video_folder,
    client,
    normalize_video_ffmpeg,
)

from tiktok_assistant import (
    debug_video_dimensions,
    analyze_video,
    build_yaml_prompt,
    apply_smart_timings,
    apply_overlay,
    video_analyses_cache,
    TEXT_MODEL,
    save_from_raw_yaml,
    list_videos_from_s3,
    download_s3_video,
    save_analysis_result,
    ANALYSIS_CACHE_DIR,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_STATE_FILE = os.path.join(BASE_DIR, "analysis_state.json")

# ============================================
# MODULE-LEVEL CONFIG (mirror of config.yml)
# ============================================
config = {}


def load_config():
    """Reload config.yml into module-level config dict."""
    global config
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    return config


def save_config():
    """Persist module-level config dict to config.yml."""
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)


# ============================================
# EXPORT MODE HELPERS (standard / optimized)
# ============================================
EXPORT_MODE_FILE = os.path.join(BASE_DIR, "export_mode.txt")


def set_export_mode(mode: str) -> dict:
    """Persist export mode to a small text file."""
    if mode not in ("standard", "optimized"):
        mode = "standard"
    with open(EXPORT_MODE_FILE, "w") as f:
        f.write(mode)
    return {"mode": mode}


def get_export_mode() -> dict:
    """Return {'mode': 'standard'|'optimized'} for frontend toggle."""
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
def load_all_analysis_results():
    """
    Loads all cached analysis results stored as .json files in video_analysis_cache/
    Returns { filename: description }
    """
    results = {}

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

    return results


# ============================================
# ANALYSIS JOB STATE HELPERS
# ============================================
def _write_analysis_state(state: dict) -> None:
    os.makedirs(BASE_DIR, exist_ok=True)
    with open(ANALYSIS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _read_analysis_state() -> dict:
    if not os.path.exists(ANALYSIS_STATE_FILE):
        return {
            "status": "idle",
            "s3_keys": [],
            "index": 0,
            "results": {},
            "error": None,
        }
    try:
        with open(ANALYSIS_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to read analysis_state.json: %s", e)
        return {
            "status": "idle",
            "s3_keys": [],
            "index": 0,
            "results": {},
            "error": f"Failed to read state: {e}",
        }


# ============================================
# /api/analyze_start – START JOB (NO HEAVY WORK)
# ============================================
def api_analyze_start():
    """
    - Clears local tik_tok_downloads folder
    - Lists videos from S3
    - Writes analysis_state.json with s3_keys
    - Returns immediately (no heavy processing)
    """
    clear_status_log()
    log_step("Starting analysis…")
    log_step("Preparing local folder and listing S3 videos…")

    # Ensure local folder exists & is clean
    os.makedirs(video_folder, exist_ok=True)
    for name in os.listdir(video_folder):
        path = os.path.join(video_folder, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # Fetch list of videos from S3
    log_step("Fetching videos from S3 (raw_uploads/)…")
    s3_keys = list_videos_from_s3()

    if not s3_keys:
        log_step("No videos found in raw_uploads/.")
        state = {
            "status": "idle",
            "s3_keys": [],
            "index": 0,
            "results": {},
            "error": None,
        }
        _write_analysis_state(state)
        return {"status": "idle", "total": 0}

    log_step(f"Found {len(s3_keys)} video(s) in raw_uploads.")

    # Initialize state
    state = {
        "status": "running",
        "s3_keys": s3_keys,
        "index": 0,
        "results": {},
        "error": None,
    }
    _write_analysis_state(state)

    return {
        "status": "running",
        "total": len(s3_keys),
        "processed": 0,
    }


# ============================================
# /api/analyze_step – PROCESS EXACTLY ONE VIDEO
# ============================================
def api_analyze_step():
    """
    - Loads analysis_state.json
    - If status != running → return state
    - Else process ONE video:
        - download from S3
        - normalize
        - LLM analyze
        - save analysis & state
    """
    state = _read_analysis_state()
    status = state.get("status", "idle")
    s3_keys = state.get("s3_keys", [])
    index = state.get("index", 0)
    results = state.get("results", {}) or {}

    if status != "running":
        # Nothing to do; just return current state
        total = len(s3_keys)
        return {
            "status": status,
            "total": total,
            "processed": min(index, total),
            "results": results,
            "error": state.get("error"),
        }

    total = len(s3_keys)
    if index >= total:
        # Already done
        state["status"] = "done"
        _write_analysis_state(state)
        log_step("All videos analyzed ✅")
        return {
            "status": "done",
            "total": total,
            "processed": total,
            "results": results,
            "error": None,
        }

    # Process one key
    key = s3_keys[index]
    try:
        log_step(f"Processing {key}…")
        tmp_local_path = download_s3_video(key)
        if not tmp_local_path:
            log_step(f"❌ Failed to download {key}")
            # Mark as skipped but continue
            state["index"] = index + 1
            _write_analysis_state(state)
            return {
                "status": "running",
                "total": total,
                "processed": index + 1,
                "results": results,
                "last_file": key,
                "last_desc": None,
                "error": f"Failed to download {key}",
            }

        base = os.path.basename(key).lower()
        local_path = os.path.join(video_folder, base)
        log_step(f"Normalizing {base} → {local_path} …")
        normalize_video_ffmpeg(tmp_local_path, local_path)

        try:
            os.remove(tmp_local_path)
        except Exception:
            pass

        log_step(f"Analyzing {base} with LLM…")
        desc = analyze_video(local_path)
        log_step(f"Analysis complete for {base}.")

        save_analysis_result(base, desc)
        results[base] = desc

        # Advance index
        index += 1
        state["index"] = index
        state["results"] = results

        if index >= total:
            state["status"] = "done"
            log_step("All videos analyzed ✅")
        else:
            state["status"] = "running"

        _write_analysis_state(state)

        return {
            "status": state["status"],
            "total": total,
            "processed": index,
            "results": results,
            "last_file": base,
            "last_desc": desc,
            "error": None,
        }

    except Exception as e:
        err_msg = f"❌ ERROR processing {key}: {e}"
        log_step(err_msg)
        log_step(traceback.format_exc())
        logger.exception(err_msg)

        state["status"] = "error"
        state["error"] = str(e)
        _write_analysis_state(state)

        return {
            "status": "error",
            "total": total,
            "processed": index,
            "results": results,
            "last_file": key,
            "last_desc": None,
            "error": str(e),
        }


# ============================================
# /api/analyze – OPTIONAL: one-shot convenience
# ============================================
def api_analyze():
    """
    Backwards-compatible: perform full analysis in one go (NOT recommended on Render).
    Kept just in case, but frontend should use analyze_start + analyze_step.
    """
    api_analyze_start()
    # Loop through all videos synchronously (for local dev only)
    while True:
        step = api_analyze_step()
        if step["status"] in ("done", "error", "idle"):
            break
    return step.get("results", {})


# ============================================
# /api/generate_yaml
# ============================================
def api_generate_yaml():
    """
    Use build_yaml_prompt + LLM to generate YAML, save to config.yml, and return dict.
    Uses both in-memory and disk cache for analyses.
    """
    disk_results = load_all_analysis_results()
    merged = {**disk_results, **video_analyses_cache}

    if not merged:
        log_step("No cached analyses; running quick analyze before YAML generation…")
        api_analyze()
        disk_results = load_all_analysis_results()
        merged = {**disk_results, **video_analyses_cache}

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


# ============================================
# /api/config (for YAML + captions)
# ============================================
def api_get_config():
    """
    Returns:
    {
      "yaml": "<raw yaml text>",
      "config": <parsed dict>
    }
    """
    load_config()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            yaml_text = f.read()
    else:
        yaml_text = "# No config.yml found yet."
    return {
        "yaml": yaml_text,
        "config": config,
    }


# ============================================
# /api/export (render to local file)
# ============================================
def api_export(optimized: bool = False) -> str:
    """
    Calls tiktok_template.edit_video and returns the local output filename.
    Raises on failure so the Flask route can handle it.
    """
    clear_status_log()
    mode_label = "OPTIMIZED" if optimized else "STANDARD"
    log_step(f"Rendering export in {mode_label} mode…")

    filename = (
        "output_tiktok_final_optimized.mp4"
        if optimized
        else "output_tiktok_final.mp4"
    )

    try:
        log_step("Rendering timeline with music, captions, and voiceover…")
        edit_video(output_file=filename, optimized=optimized)
        log_step(f"Export finished → {filename}")
    except Exception as e:
        msg = f"Export failed while calling edit_video: {e}"
        log_step(f"❌ {msg}")
        logger.exception(msg)
        raise

    if not os.path.exists(filename):
        msg = f"Export failed: file {filename} not found after render."
        log_step(f"❌ {msg}")
        logger.error(msg)
        raise FileNotFoundError(msg)

    return filename


# ============================================
# /api/tts
# ============================================
def api_set_tts(enabled: bool, voice: str | None = None):
    load_config()
    render_cfg = config.setdefault("render", {})
    render_cfg["tts_enabled"] = bool(enabled)
    if voice:
        render_cfg["tts_voice"] = voice
    save_config()
    return render_cfg


# ============================================
# /api/cta
# ============================================
def api_set_cta(enabled: bool, text: str | None = None, voiceover: bool | None = None):
    load_config()
    cta_cfg = config.setdefault("cta", {})
    cta_cfg["enabled"] = bool(enabled)
    if text is not None:
        cta_cfg["text"] = text
    if voiceover is not None:
        cta_cfg["voiceover"] = bool(voiceover)
    save_config()
    return cta_cfg


# ============================================
# /api/overlay
# ============================================
def api_apply_overlay(style: str):
    """
    Apply overlay style (punchy / cinematic / descriptive / etc.) to all clips.
    """
    apply_overlay(style=style, target="all", filename=None)
    load_config()
    return {"status": "ok"}


# ============================================
# /api/save_captions
# ============================================
def api_save_captions(text: str):
    """
    Overwrite captions in config.yml while keeping structure.
    Splits textarea text into first / middle / last using blank lines.
    """
    load_config()

    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not parts:
        return {"status": "no_captions"}

    idx = 0

    if "first_clip" in config and idx < len(parts):
        config["first_clip"]["text"] = parts[idx]
        idx += 1

    for c in config.get("middle_clips", []):
        if idx < len(parts):
            c["text"] = parts[idx]
            idx += 1

    if "last_clip" in config and idx < len(parts):
        config["last_clip"]["text"] = parts[idx]

    save_config()
    return {"status": "ok", "count": len(parts)}


# ============================================
# /api/timings
# ============================================
def api_apply_timings(smart: bool = False):
    """
    Standard FIX-C or smart pacing (cinematic).
    """
    if smart:
        apply_smart_timings(pacing="cinematic")
    else:
        apply_smart_timings()
    load_config()
    return config


# ============================================
# /api/fgscale
# ============================================
def api_fgscale(value: float):
    load_config()
    render_cfg = config.setdefault("render", {})
    render_cfg["fg_scale_default"] = float(value)
    save_config()
    return render_cfg


# ============================================
# /api/chat — LLM Creative Assistant
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