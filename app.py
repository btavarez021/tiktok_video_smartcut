from flask import Flask, request, jsonify, render_template
import os
import time
import logging

from assistant_log import clear_status_log, log_step, status_log

from assistant_api import (
    api_analyze,
    api_generate_yaml,
    api_get_config,
    api_save_yaml,
    api_apply_overlay,
    api_apply_timings,
    api_set_tts,
    api_set_cta,
    api_fgscale,
    api_chat,
    load_config,
    api_export,
)

from tiktok_assistant import (
    s3,
    S3_BUCKET_NAME,
    S3_PUBLIC_BASE,
    move_all_raw_to_processed,
)

app = Flask(__name__)

EXPORT_PREFIX = "exports/"

# ============================================
# ROOT
# ============================================
@app.route("/")
def home():
    return render_template("index.html")


# ============================================
# UPLOAD → S3 raw_uploads/
# ============================================
RAW_PREFIX = "raw_uploads/"

@app.route("/api/upload", methods=["POST"])
def upload_route():
    file = request.files["file"]
    filename = file.filename
    key = f"{RAW_PREFIX}{filename}"

    s3.upload_fileobj(file, S3_BUCKET_NAME, key)
    log_step(f"Uploaded {filename} to S3 at {key}")

    return jsonify({"status": "uploaded", "file": filename})


# ============================================
# ANALYZE VIDEOS
# ============================================
@app.route("/api/analyze", methods=["POST"])
def analyze_route():
    clear_status_log()
    log_step("🔍 Starting analysis…")
    out = api_analyze()
    return jsonify(out)


# ============================================
# YAML GENERATION
# ============================================
@app.route("/api/generate_yaml", methods=["POST"])
def yaml_route():
    clear_status_log()
    out = api_generate_yaml()
    return jsonify(out)


# ============================================
# CONFIG + SAVE
# ============================================
@app.route("/api/config", methods=["POST"])
def config_route():
    return jsonify(api_get_config())


@app.route("/api/save_yaml", methods=["POST"])
def save_yaml_route():
    yaml_text = (request.json or {}).get("yaml", "")
    out = api_save_yaml(yaml_text)
    return jsonify(out)


# ============================================
# OVERLAY (caption styles)
# ============================================
@app.route("/api/overlay", methods=["POST"])
def overlay_route():
    style = (request.json or {}).get("style", "punchy")
    result = api_apply_overlay(style)
    return jsonify(result)


# ============================================
# TIMINGS
# ============================================
@app.route("/api/timings", methods=["POST"])
def timings_route():
    smart = bool((request.json or {}).get("smart", False))
    out = api_apply_timings(smart)
    return jsonify(out)


# ============================================
# TTS + CTA + FG SCALE
# ============================================
@app.route("/api/tts", methods=["POST"])
def tts_route():
    data = request.json or {}
    out = api_set_tts(data.get("enabled"), data.get("voice"))
    return jsonify(out)


@app.route("/api/cta", methods=["POST"])
def cta_route():
    data = request.json or {}
    out = api_set_cta(data.get("enabled"), data.get("text"), data.get("voiceover"))
    return jsonify(out)


@app.route("/api/fgscale", methods=["POST"])
def fgscale_route():
    value = float((request.json or {}).get("value", 1.0))
    out = api_fgscale(value)
    return jsonify(out)


# ============================================
# EXPORT (render → upload to S3 → move raw→processed)
# ============================================
@app.route("/api/export", methods=["POST"])
def export_route():
    clear_status_log()
    data = request.json or {}
    optimized = bool(data.get("optimized", False))

    load_config()
    local_filename = api_export(optimized)
    local_path = os.path.abspath(local_filename)

    if not os.path.exists(local_path):
        return jsonify({"error": "render_failed"}), 500

    ts = int(time.time())
    final_key = f"{EXPORT_PREFIX}final_{ts}.mp4"
    s3.upload_file(local_path, S3_BUCKET_NAME, final_key)

    url = f"{S3_PUBLIC_BASE}/{final_key}"

    move_all_raw_to_processed()

    return jsonify({"status": "ok", "file_url": url})


# ============================================
# CHAT
# ============================================
@app.route("/api/chat", methods=["POST"])
def chat_route():
    msg = (request.json or {}).get("message", "")
    return jsonify(api_chat(msg))


# ============================================
# LIVE LOG
# ============================================
@app.route("/api/status", methods=["GET"])
def status_route():
    return jsonify({"log": status_log})


if __name__ == "__main__":
    app.run(debug=True, port=5000)