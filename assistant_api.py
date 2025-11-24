# assistant_api.py
import os
import yaml
import shutil

from assistant_log import log_step, status_log, clear_status_log

from tiktok_template import (
    config_path,
    config,
    client,
    edit_video,
    video_folder,
)

from tiktok_assistant import (
    analyze_video,
    build_yaml_prompt,
    apply_smart_timings,
    apply_overlay,
    video_analyses_cache,
    TEXT_MODEL,
    save_analysis_result,
    list_raw_s3_videos,
    download_s3_video,
)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# ============================================
# CONFIG HELPERS
# ============================================
def load_config():
    """Reload config.yml into global config dict."""
    global config
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    return config


def save_config():
    """Persist global config dict to config.yml."""
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)


# ============================================
# /api/analyze
# ============================================
def api_analyze():
    clear_status_log()
    log_step("🔍 Starting S3 video analysis…")

    os.makedirs(video_folder, exist_ok=True)

    # Clear previous session videos
    for name in os.listdir(video_folder):
        path = os.path.join(video_folder, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except:
            pass

    s3_keys = list_raw_s3_videos()

    if not s3_keys:
        log_step("❌ No videos found in raw_uploads/")
        return {}

    results = {}

    for key in s3_keys:
        base = os.path.basename(key)

        log_step(f"⬇ Downloading {base} from S3…")
        tmp = download_s3_video(key)
        if not tmp:
            log_step(f"❌ Failed to download {base}")
            continue

        local_path = os.path.join(video_folder, base)
        shutil.copy(tmp, local_path)

        log_step(f"🧠 LLM analyzing {base}…")
        desc = analyze_video(local_path)

        save_analysis_result(base, desc)
        results[base] = desc

        log_step(f"✅ Analysis saved for {base}")

    log_step("✅ All S3 videos analyzed")
    return results


# ============================================
# /api/generate_yaml
# ============================================
def api_generate_yaml():
    if not video_analyses_cache:
        log_step("No cached analyses — running analyze first…")
        api_analyze()

    video_files = list(video_analyses_cache.keys())
    analyses = [video_analyses_cache[v] for v in video_files]

    yaml_prompt = build_yaml_prompt(video_files, analyses)
    log_step("🧠 LLM generating YAML storyboard…")

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": yaml_prompt}],
        temperature=0.25,
    )

    yaml_text = (resp.choices[0].message.content or "").strip()
    yaml_text = yaml_text.replace("```yaml", "").replace("```", "").strip()

    cfg = yaml.safe_load(yaml_text) or {}

    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    load_config()
    log_step("✅ YAML written to config.yml")
    return cfg

# -----------------------------------------------
# /api/export  (renders MoviePy locally)
# -----------------------------------------------
def api_export(optimized: bool = False):
    clear_status_log()
    mode_label = "OPTIMIZED" if optimized else "STANDARD"
    log_step(f"Starting export in {mode_label} mode…")

    filename = (
        "output_tiktok_final_optimized.mp4"
        if optimized else
        "output_tiktok_final.mp4"
    )

    log_step("Rendering timeline with music, captions, and voiceover…")
    edit_video(output_file=filename, optimized=optimized)

    log_step(f"Export finished → {filename}")
    return filename

# ============================================
# /api/config
# ============================================
def api_get_config():
    load_config()
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            yaml_text = f.read()
    else:
        yaml_text = "# No config.yml yet"
    return {
        "yaml": yaml_text,
        "config": config,
    }


# ============================================
# /api/save_yaml
# ============================================
def api_save_yaml(yaml_text: str):
    cfg = yaml.safe_load(yaml_text) or {}
    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    load_config()
    return {"status": "ok"}


# ============================================
# /api/save_captions
# ============================================
def api_save_captions(text: str):
    load_config()

    parts = [p.strip() for p in text.split("\n\n") if p.strip()]
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
# /api/overlay
# ============================================
def api_apply_overlay(style: str):
    apply_overlay(style=style)
    load_config()
    return {"status": "ok"}


# ============================================
# /api/timings
# ============================================
def api_apply_timings(smart: bool = False):
    if smart:
        apply_smart_timings(pacing="cinematic")
    else:
        apply_smart_timings()
    load_config()
    return config


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
# /api/fgscale
# ============================================
def api_fgscale(value: float):
    load_config()
    render_cfg = config.setdefault("render", {})
    render_cfg["fg_scale_default"] = float(value)
    save_config()
    return render_cfg


# ============================================
# /api/chat
# ============================================
def api_chat(message: str):
    prompt = (
        "You are the TikTok Creative Assistant.\n\n"
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

