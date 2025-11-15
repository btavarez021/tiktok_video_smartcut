# assistant_api.py
import os
import yaml

from tiktok_template import (
    config_path,
    config,
    client,
    normalize_video_ffmpeg,
    edit_video,
)

from tiktok_assistant import (
    debug_video_dimensions,
    analyze_video,
    video_folder,
    build_yaml_prompt,
    apply_smart_timings,
    apply_overlay,
    video_analyses_cache,
    TEXT_MODEL,
)


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


# -------------------------------------------------
# /api/analyze
# -------------------------------------------------
def api_analyze():
    """
    Normalize & analyze all videos in tik_tok_downloads/, fill video_analyses_cache,
    and return {filename: description}.
    """
    os.makedirs("normalized_cache", exist_ok=True)

    results = {}
    files = sorted(
        f
        for f in os.listdir(video_folder)
        if f.lower().endswith((".mp4", ".mov", ".avi"))
    )

    if not files:
        return results

    debug_video_dimensions(video_folder)

    for v in files:
        input_path = os.path.join(video_folder, v)
        normalized_path = os.path.join("normalized_cache", v)

        if not os.path.exists(normalized_path):
            normalize_video_ffmpeg(input_path, normalized_path)

        desc = analyze_video(normalized_path)
        video_analyses_cache[v] = desc
        results[v] = desc

    return results


# -------------------------------------------------
# /api/generate_yaml
# -------------------------------------------------
def api_generate_yaml():
    """
    Use build_yaml_prompt + LLM to generate YAML, save to config.yml, and return dict.
    """
    if not video_analyses_cache:
        # If user forgot to analyze, do a quick analyze here
        api_analyze()

    video_files = list(video_analyses_cache.keys())
    analyses = [video_analyses_cache.get(v, "") for v in video_files]

    yaml_prompt = build_yaml_prompt(video_files, analyses)

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
    return cfg


# -------------------------------------------------
# /api/config  (for YAML preview panel)
# -------------------------------------------------
def api_get_config():
    """
    Return both raw YAML string and parsed config dict.
    """
    load_config()
    yaml_str = yaml.safe_dump(config, sort_keys=False) if config else "# Empty config.yml"
    return {"yaml": yaml_str, "config": config}


# -------------------------------------------------
# /api/export
# -------------------------------------------------
def api_export():
    output_file = "output_tiktok_final.mp4"
    edit_video(output_file=output_file)
    return output_file


# -------------------------------------------------
# /api/tts
# -------------------------------------------------
def api_set_tts(enabled: bool, voice: str | None = None):
    load_config()
    render_cfg = config.setdefault("render", {})
    render_cfg["tts_enabled"] = bool(enabled)
    if voice:
        render_cfg["tts_voice"] = voice
    save_config()
    return render_cfg


# -------------------------------------------------
# /api/cta
# -------------------------------------------------
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


# -------------------------------------------------
# /api/overlay
# -------------------------------------------------
def api_apply_overlay(style: str):
    """
    Apply overlay style (punchy / cinematic / descriptive) to all clips.
    """
    apply_overlay(style=style, target="all", filename=None)
    load_config()
    return {"status": "ok"}


# -------------------------------------------------
# /api/timings
# -------------------------------------------------
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


# -------------------------------------------------
# /api/fgscale
# -------------------------------------------------
def api_fgscale(value: float):
    load_config()
    render_cfg = config.setdefault("render", {})
    render_cfg["fg_scale_default"] = float(value)
    save_config()
    return render_cfg


# -------------------------------------------------
# /api/chat  — LLM Creative Assistant
# -------------------------------------------------
def api_chat(message: str):
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
