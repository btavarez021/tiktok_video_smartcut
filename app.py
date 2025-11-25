# app.py
import os
from flask import Flask, jsonify, request, send_file, render_template
from flask_cors import CORS

# Internal application modules
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
)

# --------------------------------------
# Flask App Config
# --------------------------------------
app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)
CORS(app)


# ----------------------------------------------------
# Frontend Route (loads templates/index.html)
# ----------------------------------------------------
@app.route("/")
def index():
    """Serve the main frontend UI."""
    return render_template("index.html")


# ----------------------------------------------------
# Health Check (Render uses this)
# ----------------------------------------------------
@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok"})


# ----------------------------------------------------
# Status log viewer
# ----------------------------------------------------
@app.route("/api/status", methods=["GET"])
def api_status():
    return jsonify({"status_log": status_log})


# ----------------------------------------------------
# Analysis Cache: read disk + memory
# ----------------------------------------------------
@app.route("/api/analyses_cache", methods=["GET"])
def api_get_analyses_cache():
    results = load_all_analysis_results()
    return jsonify(results)


# ----------------------------------------------------
# ANALYZE — Step-Based & Legacy One-Shot
# ----------------------------------------------------
@app.route("/api/analyze_start", methods=["POST"])
def route_analyze_start():
    return jsonify(api_analyze_start())


@app.route("/api/analyze_step", methods=["POST"])
def route_analyze_step():
    return jsonify(api_analyze_step())


@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    return jsonify(api_analyze())


# ----------------------------------------------------
# YAML & CONFIG
# ----------------------------------------------------
@app.route("/api/generate_yaml", methods=["POST"])
def route_generate_yaml():
    return jsonify(api_generate_yaml())


@app.route("/api/config", methods=["GET"])
def route_get_config():
    return jsonify(api_get_config())


@app.route("/api/save_yaml", methods=["POST"])
def route_save_yaml():
    data = request.get_json() or {}
    yaml_text = data.get("yaml", "")
    return jsonify(api_save_yaml(yaml_text))


# ----------------------------------------------------
# EXPORT RENDERING
# ----------------------------------------------------
@app.route("/api/export", methods=["POST"])
def route_export():
    data = request.get_json() or {}
    optimized = bool(data.get("optimized", False))
    filename = api_export(optimized=optimized)
    return jsonify({"filename": filename})


@app.route("/api/download/<path:filename>", methods=["GET"])
def route_download(filename):
    if not os.path.exists(filename):
        return jsonify({"error": f"File {filename} not found."}), 404
    return send_file(filename, as_attachment=True)


# ----------------------------------------------------
# TTS + CTA
# ----------------------------------------------------
@app.route("/api/tts", methods=["POST"])
def route_tts():
    data = request.get_json() or {}
    return jsonify(api_set_tts(
        enabled=bool(data.get("enabled", False)),
        voice=data.get("voice")
    ))


@app.route("/api/cta", methods=["POST"])
def route_cta():
    data = request.get_json() or {}
    return jsonify(api_set_cta(
        enabled=bool(data.get("enabled", False)),
        text=data.get("text"),
        voiceover=data.get("voiceover")
    ))


# ----------------------------------------------------
# Overlay / Timings / FG Scale
# ----------------------------------------------------
@app.route("/api/overlay", methods=["POST"])
def route_overlay():
    data = request.get_json() or {}
    return jsonify(api_apply_overlay(data.get("style", "travel_blog")))


@app.route("/api/save_captions", methods=["POST"])
def route_save_captions():
    data = request.get_json() or {}
    return jsonify(api_save_captions(data.get("text", "")))


@app.route("/api/timings", methods=["POST"])
def route_timings():
    data = request.get_json() or {}
    return jsonify(api_apply_timings(smart=bool(data.get("smart", False))))


@app.route("/api/fgscale", methods=["POST"])
def route_fgscale():
    data = request.get_json() or {}
    return jsonify(api_fgscale(float(data.get("value", 1.0))))


# ----------------------------------------------------
# Chatbot / Creative Assistant
# ----------------------------------------------------
@app.route("/api/chat", methods=["POST"])
def route_chat():
    data = request.get_json() or {}
    return jsonify(api_chat(data.get("message", "")))


# ----------------------------------------------------
# Export Mode (Standard / Optimized)
# ----------------------------------------------------
@app.route("/api/export_mode", methods=["GET"])
def route_export_mode_get():
    return jsonify(get_export_mode())


@app.route("/api/export_mode", methods=["POST"])
def route_export_mode_set():
    data = request.get_json() or {}
    return jsonify(set_export_mode(data.get("mode", "standard")))


# ----------------------------------------------------
# Local Development Server
# ----------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)