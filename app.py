# app.py
import os
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS

from assistant_log import status_log
from assistant_api import (
    api_analyze_start,
    api_analyze_step,
    api_analyze,
    api_generate_yaml,
    api_get_config,
    api_save_yaml,
    api_export,
    api_set_tts,
    api_set_cta,
    api_apply_overlay,
    api_save_captions,
    api_get_captions,
    api_apply_timings,
    api_fgscale,
    api_chat,
    get_export_mode,
    set_export_mode,
    load_all_analysis_results,
)
from tiktok_assistant import upload_raw_file, config_path, yaml

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)


# ---------------------------------
# Root – serve index.html
# ---------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------
# Health
# ---------------------------------
@app.route("/healthz")
def healthz():
    return "ok"


# ---------------------------------
# Status log
# ---------------------------------
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"status_log": status_log[-100:]})


# ---------------------------------
# Upload to S3
# ---------------------------------
@app.route("/api/upload", methods=["POST"])
def route_upload():
    if "files" not in request.files:
        return jsonify({"error": "No files field in form-data."}), 400

    files = request.files.getlist("files")
    uploaded = []

    for f in files:
        if not f.filename:
            continue
        key = upload_raw_file(f)
        uploaded.append(key)

    return jsonify({"uploaded": uploaded})


# ---------------------------------
# Analyses cache (disk + memory)
# ---------------------------------
@app.route("/api/analyses_cache", methods=["GET"])
def api_get_analyses_cache():
    results = load_all_analysis_results()
    return jsonify(results)


# ---------------------------------
# Analyze APIs
# ---------------------------------
@app.route("/api/analyze_start", methods=["POST"])
def route_analyze_start():
    result = api_analyze_start()
    return jsonify(result)


@app.route("/api/analyze_step", methods=["POST"])
def route_analyze_step():
    result = api_analyze_step()
    return jsonify(result)


@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    result = api_analyze()
    return jsonify(result)


# ---------------------------------
# YAML + config APIs
# ---------------------------------
@app.route("/api/generate_yaml", methods=["POST"])
def route_generate_yaml():
    cfg = api_generate_yaml()
    return jsonify(cfg)


@app.route("/api/config", methods=["GET"])
def route_get_config():
    cfg = api_get_config()
    return jsonify(cfg)


@app.route("/api/save_yaml", methods=["POST"])
def route_save_yaml():
    data = request.get_json() or {}
    yaml_text = data.get("yaml", "")
    result = api_save_yaml(yaml_text)
    return jsonify(result)


# ---------------------------------
# Captions
# ---------------------------------
@app.route("/api/get_captions", methods=["GET"])
def route_get_captions():
    return jsonify(api_get_captions())


@app.route("/api/save_captions", methods=["POST"])
def route_save_captions():
    data = request.get_json() or {}
    text = data.get("text", "")
    result = api_save_captions(text)
    return jsonify(result)


# ---------------------------------
# Export & download
# ---------------------------------
@app.route("/api/export", methods=["POST"])
def route_export():
    data = request.get_json() or {}
    optimized = bool(data.get("optimized", False))
    result = api_export(optimized=optimized)
    return jsonify(result)



@app.route("/api/download/<path:filename>", methods=["GET"])
def route_download(filename):
    full_path = os.path.join(os.getcwd(), filename)
    if not os.path.exists(full_path):
        return jsonify({"error": f"File {filename} not found."}), 404
    return send_file(full_path, as_attachment=True)


# ---------------------------------
# TTS & CTA
# ---------------------------------
@app.route("/api/tts", methods=["POST"])
def route_tts():
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", False))
    voice = data.get("voice")
    result = api_set_tts(enabled, voice)
    return jsonify(result)


@app.route("/api/cta", methods=["POST"])
def route_cta():
    data = request.get_json() or {}
    enabled = bool(data.get("enabled", False))
    text = data.get("text")
    voiceover = data.get("voiceover")
    result = api_set_cta(enabled, text, voiceover)
    return jsonify(result)

# ---------------------------------
# Music
# ---------------------------------
@app.route("/api/music_list", methods=["GET"])
def api_music_list():
    music_dir = os.path.join(os.path.dirname(__file__), "music")
    files = [f for f in os.listdir(music_dir) if f.lower().endswith(".mp3")]
    return {"files": files}

@app.route("/api/music", methods=["POST"])
def api_music():
    data = request.get_json(force=True)
    enabled = bool(data.get("enabled"))
    file = data.get("file") or ""
    volume = float(data.get("volume", 0.25))

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    if "render" not in cfg:
        cfg["render"] = {}

    cfg["render"]["music_enabled"] = enabled
    cfg["render"]["music_file"] = file
    cfg["render"]["music_volume"] = volume

    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {"status": "ok"}

@app.route("/api/music_file/<path:filename>")
def route_music_file(filename):
    from flask import send_from_directory
    from tiktok_template import MUSIC_DIR

    return send_from_directory(MUSIC_DIR, filename, as_attachment=False)


# ---------------------------------
# Overlay & timings & fgscale
# ---------------------------------
@app.route("/api/overlay", methods=["POST"])
def route_overlay():
    data = request.get_json() or {}
    style = data.get("style", "travel_blog")
    result = api_apply_overlay(style)
    return jsonify(result)


@app.route("/api/timings", methods=["POST"])
def route_timings():
    data = request.get_json() or {}
    smart = bool(data.get("smart", False))
    result = api_apply_timings(smart=smart)
    return jsonify(result)


@app.route("/api/fgscale", methods=["POST"])
def route_fgscale():
    data = request.get_json() or {}
    value = float(data.get("value", 1.0))
    result = api_fgscale(value)
    return jsonify(result)


# ---------------------------------
# Chat
# ---------------------------------
@app.route("/api/chat", methods=["POST"])
def route_chat():
    data = request.get_json() or {}
    message = data.get("message", "")
    result = api_chat(message)
    return jsonify(result)


# ---------------------------------
# Export mode (for UI toggle)
# ---------------------------------
@app.route("/api/export_mode", methods=["GET"])
def route_export_mode_get():
    mode = get_export_mode()
    return jsonify(mode)


@app.route("/api/export_mode", methods=["POST"])
def route_export_mode_set():
    data = request.get_json() or {}
    mode = data.get("mode", "standard")
    result = set_export_mode(mode)
    return jsonify(result)


if __name__ == "__main__":
    # Local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
