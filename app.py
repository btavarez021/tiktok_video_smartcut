# app.py — unified, session-aware, fully cleaned version

import os
import yaml
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from werkzeug.utils import secure_filename
import time
# Import backend API helpers
from assistant_log import status_log
from config_store import load_config, save_config
from assistant_api import (
    load_analysis_results_session,
    delete_session,
    list_sessions,
    list_uploads,
    move_upload_s3,
    delete_upload_s3,
    api_set_layout,
    api_analyze,
    api_generate_yaml,
    api_get_config,
    api_save_yaml,
    api_set_tts,
    api_set_cta,
    api_apply_overlay,
    api_save_captions,
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
    api_story_flow_improve,
    generate_overlay_preview,
    load_labels,
    api_set_label,
    api_clip_preview, 
    repair_label,
    api_generate_variants,
    reorder_storyboard,
    record_variant_feedback,
    AGG_PATH,
    api_ai_setup_summary,
    api_analyze_status,
    api_generate_variants_start,
    api_generate_variants_status,
    api_generate_yaml_start,
    api_generate_yaml_status,
    boost_hook,
    auto_optimize_hook,
    api_suggest_storyboard_order,
    infer_session_context,
    rename_session
    )
from tiktok_assistant import apply_filename_captions
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX
import threading
import json
from werkzeug.datastructures import ImmutableMultiDict

app = Flask(__name__, static_folder="static", template_folder="templates")
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1GB; adjust if you want


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

@app.route("/api/session/rename", methods=["POST"])
def rename_session_route():
    data = request.get_json(silent=True) or {}
    old_session = data.get("old_session", "")
    new_session = data.get("new_session", "")

    result = rename_session(old_session, new_session)

    status_code = 200 if result.get("ok") else 400
    return jsonify(result), status_code


@app.route("/api/session/<session>", methods=["DELETE"])
def api_delete_session_route(session):
    session = sanitize_session(session)
    if session not in list_sessions():
        return jsonify({"success": False, "error": "Session does not exist"}), 404

    delete_session(session)
    return jsonify({"success": True})

@app.route("/api/session/<session>", methods=["POST"])
def api_create_session_route(session):
    session = sanitize_session(session)

    # Create empty S3 raw folder (no files yet)
    key = f"{RAW_PREFIX}{session}/.keep"

    try:
        s3.put_object(
            Bucket=S3_BUCKET_NAME,
            Key=key,
            Body=b"",
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": True, "session": session})

@app.route("/api/session_context", methods=["GET"])
def session_context_route():
    session = request.args.get("session", "default")
    return jsonify(api_session_context(session))

@app.route("/api/session/context", methods=["POST"])
def api_set_content_context():

    data = request.json or {}

    session = sanitize_session(data.get("session"))
    context = data.get("context", "auto")

    cfg = load_config(session) or {}

    cfg["content_context"] = context

    save_config(session, cfg)

    return {"ok": True}

@app.route("/api/generate_yaml/start", methods=["POST"])
def generate_yaml_start():
    data = request.get_json()
    return jsonify(
        api_generate_yaml_start(data["session"])
    )


@app.route("/api/generate_yaml/status")
def generate_yaml_status():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_generate_yaml_status(session))

def api_session_context(session: str) -> dict:
    session = sanitize_session(session)
    return infer_session_context(session)

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
    data = request.get_json(silent=True) or {}
    src = data.get("src")
    dest = data.get("dest")
    if not src or not dest:
        return jsonify({"success": False, "error": "Missing src or dest"}), 400
    return jsonify(move_upload_s3(src=src, dest=dest))


@app.route("/api/uploads/delete", methods=["DELETE"])
def api_delete_upload_route():
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    if not key:
        return jsonify({"success": False, "error": "Missing key"}), 400
    return jsonify(delete_upload_s3(key=key))


@app.route("/api/variant_feedback", methods=["POST"])
def variant_feedback():
    payload = request.get_json(force=True) or {}

    # Normalize camelCase → snake_case (frontend safety)
    if "variantId" in payload and "variant_id" not in payload:
        payload["variant_id"] = payload.pop("variantId")

    return jsonify(record_variant_feedback(payload)), 200

@app.route("/api/feedback_aggregates", methods=["GET"])
def feedback_aggregates():
    if not os.path.exists(AGG_PATH):
        return jsonify({}), 200
    with open(AGG_PATH, "r", encoding="utf-8") as f:
        return jsonify(json.load(f)), 200

@app.route("/api/edit_strategy")
def route_edit_strategy():
    session = sanitize_session(request.args.get("session", "default"))
    from assistant_api import api_edit_strategy
    return jsonify(api_edit_strategy(session))

@app.route("/api/storyboard/suggest_order", methods=["POST"])
def suggest_storyboard_order_route():
    data = request.get_json(silent=True) or {}
    session = data.get("session", "default")
    return jsonify(api_suggest_storyboard_order(session))

# =====================================================================
# CLIP LABELS (GET + POST)
# =====================================================================

@app.route("/api/labels", methods=["GET"])
def api_get_labels_route():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify({"labels": load_labels(session)})


@app.route("/api/labels", methods=["POST"])
def api_set_label_route():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    filename = data.get("file")
    label = data.get("label")

    if not filename:
        return jsonify({"status": "error", "error": "missing_file"}), 400

    result = api_set_label(session, filename, label)
    return jsonify(result)

@app.route("/repair_label", methods=["POST"])
def repair_label_route():
    data = request.json
    file = data["file"]
    label = data["label"]
    session = sanitize_session(data["session"])

    fixed = repair_label(
        filename=file,
        label=label,
        session=session
    )

    return jsonify({"fixed_label": fixed})

@app.route("/api/hooks", methods=["POST"])
def route_generate_hooks():
    try:
        data = request.get_json(force=True) or {}
        session = sanitize_session(data.get("session", "default"))
        intent = data.get("intent")

        from assistant_api import api_generate_hooks
        result = api_generate_hooks(session, intent=intent)

        return jsonify(result)

    except Exception as e:
        print("HOOK ROUTE ERROR:", e)
        return jsonify({
            "hooks": [],
            "error": str(e)
        }), 500


# -----------------------------------------
# Hook Score
#-------------------------------------------

@app.route("/api/hook_score", methods=["GET", "POST"])
def route_hook_score():

    # -----------------------------
    # POST → score raw editor text
    # -----------------------------
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        session = sanitize_session(data.get("session", "default"))
        text = (data.get("text") or "").strip()
        intent = data.get("intent", "discovery")

        from assistant_api import score_hook_from_text
        return jsonify(score_hook_from_text(text, session, intent))

    # -----------------------------
    # GET → score from YAML
    # -----------------------------
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_hook_score(session))

@app.route("/api/hook_improve", methods=["POST"])
def route_hook_improve():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", request.args.get("session", "default")))
    return jsonify(api_improve_hook(session))

@app.route("/api/hook_autoboost", methods=["POST"])
def route_hook_autoboost():
    data = request.get_json(force=True) or {}

    hook = data.get("hook", "")
    intent = data.get("intent", "discovery")

    try:
        result = auto_optimize_hook(hook, intent)
        return jsonify({"status": "ok", **result})
    except Exception as e:
        print("auto boost error:", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/hook_boost", methods=["POST"])
def api_hook_boost():
    data = request.get_json(force=True) or {}

    session = data.get("session", "default")
    hook = data.get("hook", "")
    intent = data.get("intent", "discovery")

    try:
        text = boost_hook(hook, intent)
        return jsonify({"status": "ok", "text": text})

    except Exception as e:
        print("hook_boost error:", e)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/story_flow_score", methods=["GET", "POST"])
def route_story_flow_score():

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        session = sanitize_session(data.get("session", "default"))
        captions = data.get("captions")

        return jsonify(api_story_flow_score(session, captions))

    else:
        session = sanitize_session(request.args.get("session", "default"))
        return jsonify(api_story_flow_score(session))

@app.route("/api/story_flow_improve", methods=["POST"])
def route_story_flow_improve():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    return jsonify(api_story_flow_improve(session))

@app.route("/api/save_config", methods=["POST"])
def save_config_api():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    cfg = data.get("config") or {}

    from config_store import save_config

    save_config(session, cfg)
    return jsonify({"status": "ok"})



@app.route("/api/reorder_clips", methods=["POST"])
def api_reorder_clips():
    data = request.json
    session = sanitize_session(data.get("session", "default"))
    new_order = data["order"]

    if not new_order:
        return jsonify({"status": "error", "error":"missing order"}), 400
    try:
        cfg = reorder_storyboard(session, new_order)
        return jsonify({"status": "ok", "config": cfg})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 400

@app.route("/api/analyze_status")
def analyze_status_route():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_analyze_status(session))

@app.route("/api/variants/start", methods=["POST"])
def route_variants_start():
    data = request.get_json() or {}
    return jsonify(
        api_generate_variants_start(
            data.get("session", "default"),
            data.get("modes", {}),
            data.get("selected_hook")
        )
    )

@app.route("/api/variants/status")
def route_variants_status():
    return jsonify(
        api_generate_variants_status(
            request.args.get("session", "default")
        )
    )

# ============================================================================
# ANALYSIS CACHE + ANALYSIS RUNNERS
# ============================================================================
@app.route("/api/analyses_cache", methods=["GET"])
def api_analyses_cache():
    session = request.args.get("session", "default")
    session = sanitize_session(session)
    results = load_analysis_results_session(session)
    return jsonify(results)

@app.route("/api/analyze", methods=["POST"])
def route_analyze():
    session = sanitize_session(request.args.get("session", "default"))
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
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_get_config(session))


@app.route("/api/save_yaml", methods=["POST"])
def route_save_yaml_route():
    data = request.get_json(silent=True) or {}
    yaml_text = data.get("yaml", "")

    session = sanitize_session(
        request.args.get("session", data.get("session", "default"))
    )

    return jsonify(api_save_yaml(yaml_text, session=session))



@app.route("/api/captions/from_filenames", methods=["POST"])
def captions_from_filenames():
    data = request.get_json() or {}

    session = sanitize_session(data.get("session"))
    if not session:
        return jsonify({"error": "Missing session"}), 400

    apply_filename_captions(session)
    return jsonify({"status": "ok"})




# ============================================================================
# CAPTIONS
# ============================================================================

@app.route("/api/variants", methods=["POST"])
def route_variants():
    data = request.get_json() or {}

    session = sanitize_session(data.get("session", "default"))
    modes = data.get("modes", {})
    selected_hook = data.get("selected_hook")

    return jsonify(api_generate_variants(session, modes, selected_hook))

@app.route("/api/save_captions", methods=["POST"])
def route_save_captions():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    text = data.get("text", "")
    return jsonify(api_save_captions(text, session))

@app.route("/api/apply_variant", methods=["POST"])
def api_apply_variant():
    data = request.get_json() or {}

    session = sanitize_session(data.get("session", "default"))
    text = data.get("text", "")

    if not text.strip():
        return jsonify({"status": "error", "error": "Empty variant text"}), 400

    save_result = api_save_captions(text, session)

    hook_score = api_hook_score(session)
    flow_score = api_story_flow_score(session)

    return jsonify({
        "status": "ok",
        "save": save_result,
        "hook_score": hook_score,
        "story_flow": flow_score
    })


@app.route("/api/captions_mode", methods=["POST"])
def route_captions_mode():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    mode = data.get("mode", "all")
    from assistant_api import api_set_captions_mode
    return jsonify(api_set_captions_mode(session, mode))

@app.route("/api/clip_preview", methods=["POST"])
def clip_preview():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    filename = data.get("filename")
    if not filename:
        return jsonify({"error": "Missing filename"}), 400
    return jsonify(api_clip_preview(session, filename))



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
    data = request.get_json(force=True) or {}

    session = sanitize_session(data.get("session", "default"))
    enabled = bool(data.get("enabled"))
    file = data.get("file") or ""
    volume = float(data.get("volume", 0.25))

    cfg = load_config(session) or {}
    r = cfg.setdefault("render", {})

    # keep your current schema or rename, but don’t mix
    r["music_enabled"] = enabled
    r["music_file"] = file
    r["music_volume"] = volume

    save_config(session, cfg)

    return jsonify({"status": "ok", "render": r})


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
    session_id = sanitize_session(data.get("session", "default"))
    rewrite = bool(data.get("rewrite", False))

    # ✅ NEW: optional emoji-safe overlay text
    overlay_text_override = data.get("overlay_text_override")

    return jsonify(
        api_apply_overlay(
            session_id,
            style,
            rewrite,
            overlay_text_override
        )
    )

@app.route("/api/overlay_preview", methods=["POST"])
def overlay_preview():
    data = request.get_json() or {}
    session = sanitize_session(data.get("session", "default"))
    style   = data.get("style", "ai_recommended").lower()

    print(f"\n=== OVERLAY PREVIEW CALL === session={session} style={style}")

    try:
        img_b64 = generate_overlay_preview(session, style)
        print("=== PREVIEW SUCCESS ===")
        return jsonify({"image": f"data:image/png;base64,{img_b64}"})

    except Exception as e:
        import traceback
        print("\n=== PREVIEW ERROR TRACE ===")
        traceback.print_exc()        # <—— Shows real error in Render logs
        return jsonify({"error": str(e)}), 500



@app.route("/api/timings", methods=["POST"])
def route_timings():
    data = request.get_json() or {}

    smart = bool(data.get("smart", False))
    session_id = sanitize_session(data.get("session", "default"))
    
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


# ============================================================================
# CHAT
# ============================================================================
@app.route("/api/chat", methods=["POST"])
def route_chat():
    data = request.get_json(silent=True) or {}
    session = sanitize_session(data.get("session", "default"))
    return jsonify(api_chat(data.get("message", ""), session=session))


@app.route("/api/ai_setup_summary", methods=["GET"])
def ai_setup_summary():
    session = sanitize_session(request.args.get("session", "default"))
    return jsonify(api_ai_setup_summary(session))

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
