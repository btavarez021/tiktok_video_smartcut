# app.py
import os
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS

# Backend logic modules
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
    api_apply_timings,
    api_fgscale,
    api_chat,
    load_all_analysis_results,
    get_export_mode,
    set_export_mode,
    api_s3_upload,
    api_list_s3_raw,
)

# -------------------------------------------------------
# Flask App
# -------------------------------------------------------
app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)

CORS(app)


# -------------------------------------------------------
# Frontend Index Route
# -------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# -------------------------------------------------------
# Upload to S3
# -------------------------------------------------------
@app.route("/api/upload_s3", methods=["POST"])
def route_upload_s3():
    """
    Upload a file to S3 -> raw_uploads/
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    result = api_s3_upload(file)
    return jsonify(result)


@app.route("/api/list_uploads", methods=["GET"])
def route_list_s3_raw():
    """
    List S3 files in raw_uploads/
    """
    files = api_list_s3_raw()
    return jsonify({"files": files})


# -------------------------------------------------------
# Status Log
# -------------------------------------------------------
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"status_log": status_log})


# -------------------------------------------------------
# Analyses Cache
# -------------------------------------------------------
@app.route("/api/analyses_cache", methods=["GET"])
def api_get_analyses_cache():
    results = load_all_analysis_results()
    return jsonify(results)


# -------------------------------------------------------
# ANALYZE — Step-based (recommended)
# -------------------------------------------------------
@app.route("/api/analyze_start", methods=["POST"])
def route_analyze_start():
    result = api_analyze_start()
    return jsonify(result)


@app.route("/api/analyze_step", methods=["POST"])
def route_analyze_step():
    result = api_analyze_step()
    return jsonify(result)


# Optional one-shot analyze (not recommended on Render)
@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    result = api_analyze()
    return jsonify(result)


# -------------------------------------------------------
# YAML / Config APIs
# -------------------------------------------------------
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


# -------------------------------------------------------
# Export API
# -------------------------------------------------------
@app.route("/api/export", methods=["POST"])
def route_export():
    data = request.get_json() or {}
    optimized = bool(data.get("optimized", False))
    filename = api_export(optimized=optimized)
    return jsonify({"filename": filename})


@app.route("/api/download/<path:filename>", methods=["GET"])
def route_download(filename):
    if not os.path.exists(filename):
        return jsonify({"error": f"File '{filename}' not found."}), 404
    return send_file(filename, as_attachment=True)


# -------------------------------------------------------
# TTS & CTA
# -------------------------------------------------------
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
    voiceover = bool(data.get("voiceover", False))
    result = api_set_cta(enabled, text, voiceover)
    return jsonify(result)


# -------------------------------------------------------
# Overlay, Timings, FG Scale
# -------------------------------------------------------
@app.route("/api/overlay", methods=["POST"])
def route_overlay():
    data = request.get_json() or {}
    style = data.get("style", "travel_blog")
    result = api_apply_overlay(style)
    return jsonify(result)


@app.route("/api/save_captions", methods=["POST"])
def route_save_captions():
    data = request.get_json() or {}
    text = data.get("text", "")
    result = api_save_captions(text)
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


# -------------------------------------------------------
# Chat (LLM helper)
# -------------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def route_chat():
    data = request.get_json() or {}
    message = data.get("message", "")
    result = api_chat(message)
    return jsonify(result)


# -------------------------------------------------------
# Export Mode Toggle
# -------------------------------------------------------
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


# -------------------------------------------------------
# LOCAL DEV ENTRYPOINT (Render uses Gunicorn)
# -------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
