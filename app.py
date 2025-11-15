from flask import Flask, request, jsonify, render_template
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
)

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


# --- Core workflow ---

@app.route("/api/analyze", methods=["POST"])
def analyze_route():
    return jsonify(api_analyze())


@app.route("/api/generate_yaml", methods=["POST"])
def yaml_route():
    cfg = api_generate_yaml()
    return jsonify(cfg)


@app.route("/api/config", methods=["POST"])
def config_route():
    return jsonify(api_get_config())


@app.route("/api/export", methods=["POST"])
def export_route():
    out = api_export()
    return jsonify({"output": out})


# --- Settings: TTS / CTA / overlay / fgscale / timings ---

@app.route("/api/tts", methods=["POST"])
def tts_route():
    data = request.json or {}
    enabled = data.get("enabled", False)
    voice = data.get("voice")
    return jsonify(api_set_tts(enabled, voice))


@app.route("/api/cta", methods=["POST"])
def cta_route():
    data = request.json or {}
    enabled = data.get("enabled", False)
    text = data.get("text")
    voiceover = data.get("voiceover")
    return jsonify(api_set_cta(enabled, text, voiceover))


@app.route("/api/overlay", methods=["POST"])
def overlay_route():
    data = request.json or {}
    style = data.get("style", "punchy")
    return jsonify(api_apply_overlay(style))


@app.route("/api/fgscale", methods=["POST"])
def fgscale_route():
    data = request.json or {}
    value = float(data.get("value", 1.0))
    return jsonify(api_fgscale(value))


@app.route("/api/timings", methods=["POST"])
def timings_route():
    data = request.json or {}
    smart = bool(data.get("smart", False))
    return jsonify(api_apply_timings(smart))


# --- LLM chat ---

@app.route("/api/chat", methods=["POST"])
def chat_route():
    data = request.json or {}
    message = data.get("message", "")
    return jsonify(api_chat(message))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
