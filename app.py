# app.py — unified, session-aware, fully cleaned version

import os
import yaml
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename
import time
# Import backend API helpers
from assistant_log import status_log
from assistant_api import (
    load_analysis_results_session,
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
    sanitize_session as backend_sanitize_session,
    run_export_task,   
    export_tasks,
    api_hook_score,
    api_improve_hook,   
    api_story_flow_score,
    api_improve_story_flow,
    api_story_flow_improve
)
from tiktok_template import get_config_path
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX
import threading


app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)


# ============================================================================
# SESSION HELPERS — use backend sanitizer everywhere
# ============================================================================
def sanitize_session(s: str) -> str:
    """Use backend sanitizer for consistency across backend + assistant_api."""
    return backend_sanitize_session(s)


# ============================================================================
# ROOT
# ============================================================================
@app.route("/")
def index():
    return render_template("index.html")


# ============================================================================
# HEALTH
# ============================================================================
@app.route("/healthz")
def healthz():
    return "ok"


# ============================================================================
# STATUS LOG
# ============================================================================
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"status_log": status_log[-100:]})


# ============================================================================
# SESSION LIST / DELETE
# ============================================================================
@app.route("/api/sessions", methods=["GET"])
def api_list_sessions_route():
    return jsonify({"sessions": list_sessions()})


@app.route("/api/session/<session>", methods=["DELETE"])
def api_delete_session_route(session):
    session = sanitize_session(session)
    if session not in list_sessions():
        return jsonify({"success": False, "error": "Session does not exist"}), 404

    delete_session(session)
    return jsonify({"success": True})


# ============================================================================
# UPLOAD TO S3 (SESSION-AWARE)
# ============================================================================
@app.route("/api/upload", methods=["POST"])
def upload():
    session = sanitize_session(request.args.get("session", "default"))
    uploaded_files = []

    for file in request.files.getlist("files"):
        filename = secure_filename(file.filename)
        key = f"{RAW_PREFIX}{session}/{filename}"
        s3.upload_fileobj(file, S3_BUCKET_NAME, key)
        uploaded_files.append(filename)

    return jsonify({"uploaded": uploaded_files})


# ============================================================================
# UPLOAD MANAGER
# ============================================================================
@app.route("/api/uploads", methods=["GET"])
def api_list_uploads_route():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(list_uploads(session))


@app.route("/api/uploads/move", methods=["POST"])
def api_move_upload_route():
    data = request.get_json() or {}
    return jsonify(move_upload_s3(src=data["src"], dest=data["dest"]))


@app.route("/api/uploads/delete", methods=["DELETE"])
def api_delete_upload_route():
    data = request.get_json() or {}
    return jsonify(delete_upload_s3(key=data["key"]))

# -----------------------------------------
# Hook Score
#-------------------------------------------

@app.route("/api/hook_score", methods=["GET"])
def route_hook_score():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_hook_score(session))


@app.route("/api/hook_improve", methods=["POST"])
def route_hook_improve():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", request.args.get("session", "default")))
    return jsonify(api_improve_hook(session))

@app.route("/api/story_flow_score", methods=["GET"])
def route_story_flow_score():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_story_flow_score(session))

@app.route("/api/story_flow_improve", methods=["POST"])
def route_story_flow_improve():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    return jsonify(api_story_flow_improve(session))


# ============================================================================
# ANALYSIS CACHE + ANALYSIS RUNNERS
# ============================================================================
@app.route("/api/analyses_cache", methods=["GET"])
def api_analyses_cache():
    session = request.args.get("session", "default")
    session = sanitize_session(session)
    results = load_analysis_results_session(session)
    return results


@app.route("/api/analyze_start", methods=["POST"])
def route_analyze_start():
    body = request.get_json(silent=True) or {}
    session = sanitize_session(body.get("session") or request.args.get("session", "default"))
    return jsonify(api_analyze_start(session=session))


@app.route("/api/analyze_step", methods=["POST"])
def route_analyze_step():
    return jsonify(api_analyze_step())


@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    body = request.get_json(silent=True) or {}
    session = sanitize_session(body.get("session") or request.args.get("session", "default"))
    return jsonify(api_analyze(session=session))


# ============================================================================
# YAML GENERATION + CONFIG
# ============================================================================
@app.route("/api/generate_yaml", methods=["POST"])
def route_generate_yaml():
    body = request.get_json(silent=True) or {}
    session = sanitize_session(body.get("session") or request.args.get("session", "default"))
    return jsonify(api_generate_yaml(session=session))


@app.route("/api/config", methods=["GET"])
def route_get_config():
    return jsonify(api_get_config())


@app.route("/api/save_yaml", methods=["POST"])
def route_save_yaml_route():
    data = request.get_json() or {}
    yaml_text = data.get("yaml", "")

    # Determine session
    session = sanitize_session(
        request.args.get("session", data.get("session", "default"))
    )

    # Patch request.args for api_save_yaml()
    # (so it reads ?session=xxx exactly like before)
    request.args = request.args.copy()
    request.args["session"] = session

    return jsonify(api_save_yaml(yaml_text))



# ============================================================================
# CAPTIONS
# ============================================================================
@app.route("/api/get_captions", methods=["GET"])
def route_get_captions():
    return jsonify(api_get_captions())


@app.route("/api/save_captions", methods=["POST"])
def route_save_captions():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    text = data.get("text", "")
    return jsonify(api_save_captions(text, session))


# ============================================================================
# TTS / CTA
# ============================================================================
@app.route("/api/tts", methods=["POST"])
def route_tts():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    voice = data.get("voice", None)
    if not voice:
        voice = None

    return jsonify(api_set_tts(
    session,
    bool(data.get("enabled", False)),
    data.get("voice")
))



@app.route("/api/cta", methods=["POST"])
def route_cta():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))

    return jsonify(api_set_cta(
    session,
    bool(data.get("enabled", False)),
    data.get("text"),
    data.get("voiceover"),
    data.get("duration")  # NEW
))

@app.route("/api/export/status", methods=["GET"])
def api_export_status():
    task_id = request.args.get("task_id")
    if not task_id or task_id not in export_tasks:
        return jsonify({"error": "Invalid task_id"}), 400

    task = export_tasks[task_id]
    return jsonify(task)


@app.route("/api/export/start", methods=["POST"])
def api_export_start():
    data = request.get_json() or {}

    session_id = sanitize_session(data.get("session", "default"))
    optimized = bool(data.get("optimized", False))

    if not session_id:
        return jsonify({"error": "Missing session"}), 400

    task_id = f"{session_id}-{int(time.time())}"

    export_tasks[task_id] = {
        "status": "pending",
        "download_url": None,
        "filename": None,
        "error": None,
    }

    worker = threading.Thread(
        target=run_export_task,
        args=(task_id, session_id, optimized)
    )
    worker.daemon = True
    worker.start()

    return jsonify({"task_id": task_id, "status": "started"})


@app.route("/api/export/cancel", methods=["POST"])
def api_export_cancel():
    data = request.get_json() or {}
    task_id = data.get("task_id")

    if not task_id or task_id not in export_tasks:
        return jsonify({"error": "Invalid task_id"}), 400

    export_tasks[task_id]["cancel_requested"] = True
    export_tasks[task_id]["status"] = "cancelling"

    return jsonify({"status": "cancelling"})

# ============================================================================
# MUSIC
# ============================================================================
@app.route("/api/music_list", methods=["GET"])
def api_music_list_route():
    music_dir = os.path.join(os.path.dirname(__file__), "music")
    files = [f for f in os.listdir(music_dir) if f.lower().endswith(".mp3")]
    return jsonify({"files": files})


@app.route("/api/music", methods=["POST"])
def api_music():
    data = request.get_json(force=True)

    session = sanitize_session(data.get("session", "default"))
    enabled = bool(data.get("enabled"))
    file = data.get("file") or ""
    volume = float(data.get("volume", 0.25))

    config_path = get_config_path(session)

    cfg = {}
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    r = cfg.setdefault("render", {})
    r["music_enabled"] = enabled
    r["music_file"] = file
    r["music_volume"] = volume

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)


    return jsonify({"status": "ok"})


@app.route("/api/music_file/<path:filename>")
def route_music_file(filename):
    from flask import send_from_directory
    from tiktok_template import MUSIC_DIR
    return send_from_directory(MUSIC_DIR, filename, as_attachment=False)


# ============================================================================
# OVERLAY + TIMINGS + FG SCALE
# ============================================================================
@app.route("/api/overlay", methods=["POST"])
def route_overlay():
    data = request.get_json() or {}

    style = data.get("style", "travel_blog")
    session_id = data.get("session", "default")

    return jsonify(api_apply_overlay(session_id, style))



@app.route("/api/timings", methods=["POST"])
def route_timings():
    data = request.get_json() or {}

    smart = bool(data.get("smart", False))
    session_id = data.get("session", "default")

    return jsonify(api_apply_timings(session_id, smart))


@app.route("/api/layout", methods=["POST"])
def route_set_layout():
    data = request.get_json(force=True)
    session = sanitize_session(data.get("session", "default"))
    mode = data.get("mode", "tiktok")
    return jsonify(api_set_layout(session, mode))



@app.route("/api/fgscale", methods=["POST"])
def route_fgscale_route():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    mode = data.get("fgscale_mode", "manual")
    fg = data.get("fgscale", None)

    # convert fg to float if possible
    if fg is not None:
        try:
            fg = float(fg)
        except:
            return jsonify({"status": "error", "error": "Invalid fgscale value"})

    return jsonify(api_fgscale(session, mode, fg))

@app.route("/api/story_flow_improve", methods=["POST"])
def route_story_flow_improve():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    return jsonify(api_improve_story_flow(session))


# ============================================================================
# CHAT
# ============================================================================
@app.route("/api/chat", methods=["POST"])
def route_chat():
    data = request.get_json() or {}
    return jsonify(api_chat(data.get("message", "")))


# ============================================================================
# EXPORT MODE
# ============================================================================
@app.route("/api/export_mode", methods=["GET"])
def route_export_mode_get():
    return jsonify(get_export_mode())


@app.route("/api/export_mode", methods=["POST"])
def route_export_mode_set():
    data = request.get_json() or {}
    return jsonify(set_export_mode(data.get("mode", "standard")))


# ============================================================================
# RUN LOCAL DEV SERVER
# ============================================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
