import os
import tempfile
import shutil

from assistant_log import log_step

from s3_config import (
    s3,
    S3_BUCKET_NAME,
    RAW_PREFIX,
    clean_s3_key,
)

from tiktok_template import video_folder
from tiktok_assistant import (
    normalize_video,
    sanitize_yaml_filenames,
)

def upload_raw_file(file):
    """
    Safe upload handler for Render:
    - Save upload to temp file
    - Normalize into temp MP4
    - Upload normalized temp MP4 directly to S3
    - (Optional) Save normalized version locally only if needed
    """

    log_step("[UPLOAD] Starting upload handler")

    # -----------------------------
    # 1. Save upload → temp file
    # -----------------------------
    try:
        tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".upload").name
        file.save(tmp_in)
        log_step(f"[UPLOAD] Temp uploaded: {tmp_in}")
    except Exception as e:
        log_step(f"[UPLOAD ERROR] Failed saving temp input: {e}")
        raise

    # -----------------------------
    # 2. Normalize → temp file
    # -----------------------------
    try:
        raw_name = sanitize_yaml_filenames(file.filename.lstrip("/"))
        base, _ = os.path.splitext(raw_name)
        normalized_name = f"{base}.mp4"

        tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        log_step(f"[UPLOAD] Normalizing upload: raw='{file.filename}' → out='{tmp_out}'")
        normalize_video(tmp_in, tmp_out)

        # Optional metadata logging
        log_step(f"[UPLOAD] Normalization complete. Output: {tmp_out}")
    except Exception as e:
        log_step(f"[UPLOAD ERROR] normalize_video() failed: {e}")
        raise

    # -----------------------------
    # 3. Upload normalized temp file to S3
    # -----------------------------
    key = clean_s3_key(f"{RAW_PREFIX}/{normalized_name}")
    s3_uri = f"s3://{S3_BUCKET_NAME}/{key}"

    try:
        log_step(f"[UPLOAD] Uploading normalized → {s3_uri}")
        s3.upload_file(tmp_out, S3_BUCKET_NAME, key)
        log_step(f"[UPLOAD] Upload complete: {s3_uri}")
    except Exception as e:
        log_step(f"[UPLOAD ERROR] S3 upload failed: {e}")
        raise

    # -----------------------------
    # 4. (optional) Save normalized locally
    # -----------------------------
    try:
        os.makedirs(video_folder, exist_ok=True)
        local_copy = os.path.join(video_folder, normalized_name)

        import shutil
        shutil.copy2(tmp_out, local_copy)

        log_step(f"[UPLOAD] Copied normalized file → {local_copy}")
    except Exception as e:
        # Still non-fatal
        log_step(f"[UPLOAD WARNING] Local copy failed: {e}")

    log_step(f"[UPLOAD] Finished upload handler for {normalized_name}")
    return key
