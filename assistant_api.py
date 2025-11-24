# assistant_api.py
import os
import yaml
import shutil
import tempfile

from assistant_log import log_step, status_log, clear_status_log

from tiktok_template import (
    config_path,
    edit_video,
    video_folder,   # local folder used for normalized / downloaded clips
    client,
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
)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)


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
EXPORT_MODE_FILE = "export_mode.txt"


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
# /api/analyze
# ============================================
def api_analyze():
    """
    - Clears local tik_tok_downloads folder
    - Downloads all S3 raw_uploads/*.mp4 into that folder
    - Runs LLM analysis per file
    - Caches results in video_analyses_cache
    - Returns {filename: description}
    """
    log_step("Starting analysis…")

    # Ensure local folder exists & is clean
    os.makedirs(video_folder, exist_ok=True)
    for name in os.listdir(video_folder):
        path = os.path.join(video_folder, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    # 1. Fetch list of videos from S3
    log_step("Fetching videos from S3 (raw_uploads/)…")
    s3_keys = list_videos_from_s3()

    if not s3_keys:
        log_step("No videos found in raw_uploads/.")
        return {}

    log_step(f"Found {len(s3_keys)} video(s) in raw_uploads.")
    os.makedirs("normalized_cache", exist_ok=True)

    results = {}

    for key in s3_keys:
        log_step(f"Processing {key}…")

        # 2. Download to temporary file
        tmp_local_path = download_s3_video(key)
        if not tmp_local_path:
            log_step(f"❌ Failed to download {key}")
            continue

        # 3. Copy into local tik_tok_downloads/ with basename
        raw_base = os.path.basename(key)
        base = raw_base.lower()
        local_path = os.path.join(video_folder, base)
        try:
            shutil.copy(tmp_local_path, local_path)
        except Exception as e:
            log_step(f"❌ Failed to copy {tmp_local_path} → {local_path}: {e}")
            continue

        # 4. (Optional) debug dimensions if you want
        # debug_video_dimensions(video_folder)

        # 5. Analyze with LLM
        log_step(f"Analyzing {base} with LLM…")
        desc = analyze_video(local_path)
        log_step(f"Analysis complete for {base}.")

        # 6. Save analysis result (by basename)
        save_analysis_result(base, desc)
        results[base] = desc

        # Clean up temp file
        try:
            os.remove(tmp_local_path)
        except OSError:
            pass

    log_step("All videos analyzed ✅")
    return results


# ============================================
# /api/generate_yaml
# ============================================
def api_generate_yaml():
    """
    Use build_yaml_prompt + LLM to generate YAML, save to config.yml, and return dict.
    """
    if not video_analyses_cache:
        log_step("No cached analyses; running quick analyze before YAML generation…")
        api_analyze()

    video_files = list(video_analyses_cache.keys())  # basenames
    analyses = [video_analyses_cache.get(v.lower(), "") for v in video_files]

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
        # Fill middle clips if more than 2
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
# /api/config  (for YAML + captions)
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
        # Reraise so the Flask route can return a 500 JSON
        raise

    # Final sanity check that the file exists
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
