from flask import Flask, request, jsonify, render_template
import logging
import os
from tiktok_assistant import s3, S3_BUCKET

from assistant_api import (
    api_analyze,
    api_generate_yaml,
    api_export,
    api_set_tts,
    api_set_cta,
    api_apply_overlay,
    api_apply_timings,
    api_fgscale,
    api_get_config,
    api_chat,
    get_export_mode,
    set_export_mode,
    api_save_yaml,
    api_save_captions,
)
from assistant_log import clear_status_log, log_step, status_log

app = Flask(__name__)

# =============================================================
# Configure Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
assistant_log_dir = os.path.join(BASE_DIR, "logs")
assistant_log_file_path = os.path.join(assistant_log_dir, "tiktok_editor.log")

if not os.path.exists(assistant_log_dir):
    os.makedirs(assistant_log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,  # Set to logging.DEBUG for more verbose output
    filename=assistant_log_file_path,
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================
# ROOT
# ============================================
@app.route("/")
def home():
    return render_template("index.html")


# ============================================
# CORE WORKFLOW
# ============================================

@app.route("/api/upload", methods=["POST"])
def upload_route():
    file = request.files["file"]
    filename = file.filename

    s3.upload_fileobj(file, S3_BUCKET, filename)

    return jsonify({"status": "uploaded", "file": filename})


@app.route("/api/analyze", methods=["POST"])
def analyze_route():
    clear_status_log()
    log_step("🔍 Starting video analysis…")
    out = api_analyze()
    log_step(f"✅ Analysis complete. {len(out)} video(s) processed.")
    return jsonify(out)


@app.route("/api/generate_yaml", methods=["POST"])
def yaml_route():
    clear_status_log()
    log_step("🧠 Generating YAML with LLM…")
    out = api_generate_yaml()
    log_step("✅ YAML generated.")
    return jsonify(out)


@app.route("/api/config", methods=["POST"])
def config_route():
    return jsonify(api_get_config())


@app.route("/api/save_yaml", methods=["POST"])
def save_yaml_route():
    data = request.json or {}
    yaml_text = data.get("yaml", "")
    log_step("💾 Saving edited YAML…")
    out = api_save_yaml(yaml_text)
    log_step("✅ YAML saved.")
    return jsonify(out)


# ============================================
# EXPORT + EXPORT MODE
# ============================================
@app.route("/api/export", methods=["POST"])
def export_route():
    data = request.json or {}
    optimized = data.get("mode") == "optimized"

    clear_status_log()
    log_step(
        "🎬 Starting export render "
        + ("(OPTIMIZED)…" if optimized else "(STANDARD)…")
    )

    out = api_export(optimized=optimized)

    log_step("✅ Export complete.")
    return jsonify({"output": out, "log": status_log})


@app.route("/api/export_mode", methods=["GET", "POST"])
def export_mode_route():
    if request.method == "GET":
        # Returns {"mode": "..."}
        return jsonify(get_export_mode())

    data = request.json or {}
    mode = data.get("mode", "standard")
    log_step(f"⚙ Updating export mode → {mode}")
    out = set_export_mode(mode)
    log_step("✅ Export mode saved.")
    return jsonify(out)


# ============================================
# TTS SETTINGS
# ============================================
@app.route("/api/tts", methods=["POST"])
def tts_route():
    clear_status_log()
    data = request.json or {}
    enabled = data.get("enabled", False)
    voice = data.get("voice")

    log_step(f"🎤 TTS update → enabled={enabled}, voice={voice}")
    out = api_set_tts(enabled, voice)
    log_step("✅ TTS settings applied.")
    return jsonify(out)


# ============================================
# CTA SETTINGS
# ============================================
@app.route("/api/cta", methods=["POST"])
def cta_route():
    clear_status_log()
    data = request.json or {}
    enabled = data.get("enabled", False)
    text = data.get("text")
    voiceover = data.get("voiceover")

    log_step(f"📣 CTA update → enabled={enabled}, voiceover={voiceover}")
    out = api_set_cta(enabled, text, voiceover)
    log_step("✅ CTA saved.")
    return jsonify(out)


# ============================================
# SAVE CAPTIONS (from editor)
# ============================================
@app.route("/api/save_captions", methods=["POST"])
def save_captions_route():
    clear_status_log()
    data = request.json or {}
    text = data.get("text", "")

    log_step("✏ Saving edited captions…")
    out = api_save_captions(text)
    log_step("✅ Captions saved to config.yml")
    return jsonify(out)


# ============================================
# OVERLAY STYLE
# ============================================
@app.route("/api/overlay", methods=["POST"])
def overlay_route():
    clear_status_log()
    data = request.json or {}
    style = data.get("style", "punchy")

    log_step(f"🎨 Applying overlay style: {style}…")
    result = api_apply_overlay(style)
    log_step("✅ Overlay captions updated.")
    return jsonify(result)


# ============================================
# FOREGROUND SCALE
# ============================================
@app.route("/api/fgscale", methods=["POST"])
def fgscale_route():
    clear_status_log()
    data = request.json or {}
    value = float(data.get("value", 1.0))

    log_step(f"🖼 Updating foreground scale → {value}")
    out = api_fgscale(value)
    log_step("✅ Foreground layout updated.")
    return jsonify(out)


# ============================================
# TIMINGS
# ============================================
@app.route("/api/timings", methods=["POST"])
def timings_route():
    clear_status_log()
    data = request.json or {}
    smart = bool(data.get("smart", False))
    mode = "Smart pacing" if smart else "Standard FIX-C"

    log_step(f"⏱ Applying timings → {mode}")
    out = api_apply_timings(smart)
    log_step(f"✅ {mode} applied.")
    return jsonify(out)


# ============================================
# LLM CHAT
# ============================================
@app.route("/api/chat", methods=["POST"])
def chat_route():
    data = request.json or {}
    message = data.get("message", "")
    return jsonify(api_chat(message))


# ============================================
# GLOBAL LIVE LOG (for side log panel)
# ============================================
@app.route("/api/status", methods=["GET"])
def status_route():
    return jsonify({"log": status_log})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
