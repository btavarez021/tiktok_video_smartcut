# assistant_api.py — MOV/MP4 SAFE VERSION

import os
import logging
import json
from typing import Any, Dict, List, Optional

import yaml
import boto3

from assistant_log import log_step
from tiktok_template import config_path, edit_video, video_folder
from tiktok_assistant import (
    list_videos_from_s3,
    download_s3_video,
    normalize_video,
    analyze_video,
    build_yaml_prompt,
    save_analysis_result,
    sanitize_yaml_filenames,
    video_analyses_cache,
    TEXT_MODEL,
    client,
)

logger = logging.getLogger(__name__)

# -----------------------------------------
# ENV / S3 CONFIG
# -----------------------------------------

S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError(
        "S3_BUCKET_NAME (or AWS_BUCKET_NAME) environment variable is required"
    )

S3_REGION = os.getenv("S3_REGION", "us-east-2")
RAW_PREFIX = os.getenv("S3_RAW_PREFIX", "raw_uploads").rstrip("/") + "/"
EXPORT_PREFIX = os.getenv("S3_EXPORT", "exports").rstrip("/") + "/"

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise RuntimeError(
        "Missing AWS credentials! You MUST set AWS_ACCESS_KEY_ID and "
        "AWS_SECRET_ACCESS_KEY in Render."
    )

s3 = boto3.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

# -----------------------------------------
# Helpers
# -----------------------------------------


def _ensure_video_folder() -> None:
    """Make sure the local normalized video folder exists."""
    os.makedirs(video_folder, exist_ok=True)


def _normalized_local_name_from_key(key: str) -> str:
    """
    Convert an S3 key like 'raw_uploads/IMG_3753.MOV' into
    a local path like '<video_folder>/img_3753.mov'.

    NOTE: we preserve the original extension; we only:
    - strip the prefix
    - lowercase the basename
    """
    basename = os.path.basename(key)
    basename_lower = basename.lower()
    return os.path.join(video_folder, basename_lower)


# -----------------------------------------
# Public API used by app.py routes
# -----------------------------------------


def api_list_raw() -> Dict[str, Any]:
    """
    List raw videos from S3. Used by UI to show what's available.
    """
    files = list_videos_from_s3()
    return {"files": files}


def api_upload(file_storage) -> Dict[str, Any]:
    """
    Upload a single file to S3 raw prefix.
    `file_storage` is a Werkzeug / Flask style file object.
    """
    from tiktok_assistant import upload_raw_file  # avoid circular import at top

    key = upload_raw_file(file_storage)
    return {"key": key}


def api_get_analyses_cache() -> Dict[str, str]:
    """
    Exposed as /api/analyses_cache for the frontend to poll and show
    which files were analyzed and their descriptions.
    """
    # Just return the in-memory cache (already key-normalized in tiktok_assistant)
    return video_analyses_cache


def api_generate_yaml_from_cache() -> Dict[str, Any]:
    """
    Build config.yml using whatever is currently in the analysis cache,
    without re-running analysis.

    This is a convenience function; you may or may not be using it in app.py.
    """
    if not video_analyses_cache:
        raise ValueError("No video analyses available to build YAML")

    # The cache keys are normalized basenames (e.g. 'img_3753.mov')
    video_files: List[str] = list(video_analyses_cache.keys())
    analyses: List[str] = [video_analyses_cache[f] for f in video_files]

    prompt = build_yaml_prompt(video_files, analyses)

    if client is None:
        # Fallback: build a very simple YAML using the analyses directly
        cfg = _fallback_yaml_from_prompt_inputs(video_files, analyses)
    else:
        cfg = _call_llm_for_yaml(prompt)

    cfg = sanitize_yaml_filenames(cfg)

    # Save to config.yml
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {
        "yaml": yaml.safe_dump(cfg, sort_keys=False),
        "parsed": cfg,
    }


def api_analyze_all() -> Dict[str, Any]:
    """
    Main 'analyze' entrypoint used by /api/analyze.

    Steps:
    - List S3 videos under RAW_PREFIX
    - For each, download to temp
    - Normalize to local `video_folder` (keeping extension)
    - Run LLM analysis on each
    - Cache analyses (memory + disk)
    - Ask LLM to build config.yml (YAML)
    - Save config.yml to disk
    - Return yaml + parsed yaml for UI
    """
    _ensure_video_folder()

    raw_keys = list_videos_from_s3()
    if not raw_keys:
        raise ValueError("No raw videos found in S3")

    log_step(f"Found raw S3 videos: {raw_keys}")

    video_files_for_yaml: List[str] = []
    analyses: List[str] = []

    for key in raw_keys:
        # Only handle supported extensions (same check as list_videos_from_s3)
        ext = os.path.splitext(key)[1].lower()
        if ext not in [".mp4", ".mov", ".avi", ".m4v"]:
            continue

        tmp_path = download_s3_video(key)
        if not tmp_path:
            log_step(f"[ANALYZE] Skipping {key}, download failed")
            continue

        # Normalize into local video_folder, preserving extension
        local_out = _normalized_local_name_from_key(key)
        normalize_video(tmp_path, local_out)

        # Analyze (LLM or fallback)
        desc = analyze_video(local_out)
        save_analysis_result(key, desc)

        basename_lower = os.path.basename(local_out)
        video_files_for_yaml.append(basename_lower)
        analyses.append(desc)

        log_step(f"[ANALYZE] {basename_lower} -> {desc}")

    if not video_files_for_yaml:
        raise ValueError("No valid video files to analyze")

    # Build prompt + call LLM for YAML
    prompt = build_yaml_prompt(video_files_for_yaml, analyses)

    if client is None:
        cfg = _fallback_yaml_from_prompt_inputs(video_files_for_yaml, analyses)
    else:
        cfg = _call_llm_for_yaml(prompt)

    cfg = sanitize_yaml_filenames(cfg)

    # Save to config.yml
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    yaml_text = yaml.safe_dump(cfg, sort_keys=False)

    return {
        "yaml": yaml_text,
        "parsed": cfg,
        "files": video_files_for_yaml,
    }


def api_get_config() -> Dict[str, Any]:
    """
    Return the current config.yml contents and parsed structure.
    Used by UI to populate the YAML editor.
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError("config.yml not found")

    with open(config_path, "r", encoding="utf-8") as f:
        yaml_text = f.read()

    cfg = yaml.safe_load(yaml_text) or {}
    cfg = sanitize_yaml_filenames(cfg)

    # Optionally re-save sanitized version (keeps things tidy)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {"yaml": yaml.safe_dump(cfg, sort_keys=False), "parsed": cfg}


def api_save_config(new_yaml_text: str) -> Dict[str, Any]:
    """
    Save YAML coming from the UI "Save YAML" button.
    We:
    - Parse the incoming YAML
    - Sanitize filenames (basename + lowercase, no extension changes)
    - Save back to config.yml
    - Return parsed config
    """
    try:
        cfg = yaml.safe_load(new_yaml_text) or {}
    except Exception as e:
        logger.error(f"YAML parse error: {e}")
        raise ValueError(f"Invalid YAML: {e}")

    if not isinstance(cfg, dict):
        raise ValueError("YAML must represent a mapping at the top level")

    cfg = sanitize_yaml_filenames(cfg)

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    return {"yaml": yaml.safe_dump(cfg, sort_keys=False), "parsed": cfg}


def api_export(optimized: bool = False) -> Dict[str, Any]:
    """
    Export final video based on config.yml and upload result to S3.

    Steps:
    - Load and validate config.yml
    - Call edit_video(optimized=optimized) (this reads config_path internally)
    - Upload resulting video file to S3 under EXPORT_PREFIX
    - Return export key + maybe a simple URL (depending on how you're serving S3)
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError("config.yml not found")

    with open(config_path, "r", encoding="utf-8") as f:
        yaml_text = f.read()

    cfg = yaml.safe_load(yaml_text) or {}
    cfg = sanitize_yaml_filenames(cfg)

    # Basic validation so we don't call edit_video with an empty config
    first_clip = cfg.get("first_clip")
    last_clip = cfg.get("last_clip")
    middle_clips = cfg.get("middle_clips", [])

    if not first_clip and not last_clip and not middle_clips:
        raise ValueError("No clips available from config.yml")

    # Re-save sanitized config before rendering
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    mode = "OPTIMIZED" if optimized else "STANDARD"
    log_step(f"[LOG] Rendering export in {mode} mode...")

    try:
        # NOTE: assuming edit_video reads config_path internally and
        # returns the local path of the rendered file.
        result_path = edit_video(optimized=optimized)
    except Exception as e:
        log_step(f"Export failed while calling edit_video: {e}")
        logger.exception("Export failed while calling edit_video")
        # Re-raise so Flask / app.py can turn it into a 500
        raise

    if not result_path or not os.path.exists(result_path):
        raise RuntimeError("edit_video did not produce an output file")

    out_name = os.path.basename(result_path)
    export_key = f"{EXPORT_PREFIX}{out_name}"

    log_step(f"[EXPORT] Uploading rendered video to s3://{S3_BUCKET_NAME}/{export_key}")
    s3.upload_file(result_path, S3_BUCKET_NAME, export_key)

    # If you have public bucket / CDN, you can construct a URL here.
    # For now, just return the key + filename.
    return {
        "key": export_key,
        "filename": out_name,
    }


# -----------------------------------------
# Internal helpers
# -----------------------------------------


def _call_llm_for_yaml(prompt: str) -> Dict[str, Any]:
    """
    Call OpenAI Chat Completions to get YAML config text,
    then parse to a Python dict.
    """
    if client is None:
        raise RuntimeError("OpenAI client is not configured")

    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    yaml_text = (resp.choices[0].message.content or "").strip()
    yaml_text = yaml_text.replace("```yaml", "").replace("```", "").strip()

    try:
        cfg = yaml.safe_load(yaml_text) or {}
    except Exception as e:
        logger.error(f"YAML from LLM could not be parsed: {e}")
        raise ValueError("LLM returned invalid YAML")

    if not isinstance(cfg, dict):
        raise ValueError("LLM YAML must be a mapping at the top level")

    return cfg


def _fallback_yaml_from_prompt_inputs(
    video_files: List[str],
    analyses: List[str],
) -> Dict[str, Any]:
    """
    Simple non-LLM fallback: first video is first_clip, last video is last_clip,
    anything in the middle is middle_clips. Uses analyses as text.
    """
    if not video_files:
        raise ValueError("No video files provided for fallback YAML")

    cfg: Dict[str, Any] = {}

    # First clip
    cfg["first_clip"] = {
        "file": video_files[0],
        "start_time": 0,
        "duration": 5,
        "text": analyses[0] if analyses else "",
        "scale": 1.0,
    }

    # Middle clips
    middle_list: List[Dict[str, Any]] = []
    if len(video_files) > 2:
        for vf, desc in zip(video_files[1:-1], analyses[1:-1]):
            middle_list.append(
                {
                    "file": vf,
                    "start_time": 0,
                    "duration": 5,
                    "text": desc,
                    "scale": 1.0,
                }
            )
    cfg["middle_clips"] = middle_list

    # Last clip
    last_idx = len(video_files) - 1
    cfg["last_clip"] = {
        "file": video_files[last_idx],
        "start_time": 0,
        "duration": 5,
        "text": analyses[last_idx] if analyses else "",
        "scale": 1.0,
    }

    # Render + CTA defaults
    cfg["render"] = {
        "tts_enabled": False,
        "tts_voice": "alloy",
        "fg_scale_default": 1.0,
        "blur_background": False,
    }

    cfg["cta"] = {
        "enabled": False,
        "text": "",
        "voiceover": False,
        "duration": 3.0,
        "position": "bottom",
    }

    return cfg