# app.py — session-aware routes (aligned with app.js)

import os

from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename

from assistant_log import status_log
from assistant_api import (
    delete_session,
    list_sessions,
    list_uploads,
    move_upload_s3,
    delete_upload_s3,
    api_set_layout,
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
    sanitize_session as backend_sanitize_session,
)
from tiktok_template import config_path
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX
import yaml

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)


# ---------------------------------
# Local session sanitizer (UI → backend)
# ---------------------------------
def sanitize_session(s: str) -> str:
    if not s:
        return "default"
    s = s.lower().strip().replace(" ", "_")
    return "".join(c for c in s if c.isalnum() or c == "_") or "default"

@app.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    return jsonify({"sessions": list_sessions()})


@app.route("/api/session/<session>", methods=["DELETE"])
def api_delete_session(session):
    delete_session(session)
    return jsonify({"success": True})



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
# Upload to S3 (per session)
# ---------------------------------
@app.route("/api/upload", methods=["POST"])
def upload():
    session = sanitize_session(request.args.get("session", "default"))

    uploaded_files = []

    for file in request.files.getlist("files"):
        filename = secure_filename(file.filename)
        # Use RAW_PREFIX to stay in sync with s3_config
        key = f"{RAW_PREFIX}{session}/{filename}"
        s3.upload_fileobj(file, S3_BUCKET_NAME, key)
        uploaded_files.append(filename)

    return jsonify({"uploaded": uploaded_files})


# ---------------------------------
# Manage files already uploaded to S3
# ---------------------------------
@app.get("/api/uploads")
def api_list_uploads_route():
    session = sanitize_session(request.args.get("session", "default"))
    return list_uploads(session)


@app.post("/api/uploads/move")
def api_move_upload_route():
    data = request.get_json() or {}
    return move_upload_s3(
        src=data["src"],
        dest=data["dest"],
    )


@app.delete("/api/uploads/delete")
def api_delete_upload_route():
    data = request.get_json() or {}
    return delete_upload_s3(key=data["key"])


# ---------------------------------
# Analyses cache (disk + memory)
# Note: still global; we don't yet partition by session.
# ---------------------------------
@app.route("/api/analyses_cache", methods=["GET"])
def api_get_analyses_cache():
    results = load_all_analysis_results()
    return jsonify(results)


# ---------------------------------
# Analyze APIs (per session)
# ---------------------------------
@app.route("/api/analyze_start", methods=["POST"])
def route_analyze_start():
    # Support both body + query param, but JS sends body.
    body = request.get_json(silent=True) or {}
    session = body.get("session") or request.args.get("session", "default")
    session = sanitize_session(session)
    result = api_analyze_start(session=session)
    return jsonify(result)


@app.route("/api/analyze_step", methods=["POST"])
def route_analyze_step():
    result = api_analyze_step()
    return jsonify(result)


@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    body = request.get_json(silent=True) or {}
    session = body.get("session") or request.args.get("session", "default")
    session = sanitize_session(session)
    result = api_analyze(session=session)
    return jsonify(result)


# ---------------------------------
# YAML + config APIs
# ---------------------------------
@app.route("/api/generate_yaml", methods=["POST"])
def route_generate_yaml():
    body = request.get_json(silent=True) or {}
    session = body.get("session") or request.args.get("session", "default")
    session = sanitize_session(session)
    cfg = api_generate_yaml(session=session)
    return jsonify(cfg)


@app.route("/api/config", methods=["GET"])
def route_get_config():
    # For now, config is global; session query arg is ignored.
    cfg = api_get_config()
    return jsonify(cfg)


@app.route("/api/save_yaml", methods=["POST"])
def route_save_yaml():
    data = request.get_json() or {}
    yaml_text = data.get("yaml", "")
    # session is currently ignored; config is global.
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
    # session is currently ignored; captions file is global.
    result = api_save_captions(text)
    return jsonify(result)


# ---------------------------------
# Export & download
# ---------------------------------
@app.route("/api/export", methods=["POST"])
def route_export():
    data = request.get_json() or {}
    optimized = bool(data.get("optimized", False))
    # session value is accepted from JS but currently not used in api_export.
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
        cfg = yaml.safe_load(f) or {}

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
    # session currently not used in overlay; it just edits global config.yml.
    result = api_apply_overlay(style)
    return jsonify(result)


@app.route("/api/timings", methods=["POST"])
def route_timings():
    data = request.get_json() or {}
    smart = bool(data.get("smart", False))
    # session currently not used; timings edit global config.yml.
    result = api_apply_timings(smart=smart)
    return jsonify(result)


@app.route("/api/layout", methods=["POST"])
def route_set_layout():
    data = request.get_json(force=True)
    mode = data.get("mode", "tiktok")
    result = api_set_layout(mode)
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
