# upload_utils.py
import os
import tempfile
from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX, clean_s3_key

from tiktok_template import video_folder     # safe import
from tiktok_assistant import normalize_video, sanitize_yaml_filenames  # safe import


def upload_raw_file(file):
    """
    Safe upload handler for Render:
    - Save upload to temp file
    - Normalize into temp MP4
    - Upload normalized temp MP4 directly to S3
    - (Optional) Save normalized version locally only if needed
    """

    # -----------------------------
    # 1. Save upload → temp file
    # -----------------------------
    tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix=".upload").name
    file.save(tmp_in)
    log_step(f"[UPLOAD] Temp uploaded: {tmp_in}")

    # -----------------------------
    # 2. Normalize → temp file
    # -----------------------------
    raw_name = sanitize_yaml_filenames(file.filename.lstrip("/"))
    base, _ = os.path.splitext(raw_name)
    normalized_name = f"{base}.mp4"

    tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

    log_step(f"[UPLOAD] Normalizing → {tmp_out}")
    normalize_video(tmp_in, tmp_out)

    # -----------------------------
    # 3. Upload normalized temp file to S3
    # -----------------------------
    key = clean_s3_key(f"{RAW_PREFIX}/{normalized_name}")

    log_step(f"[UPLOAD] Uploading normalized → s3://{S3_BUCKET_NAME}/{key}")

    # Upload from temp file (SAFE FOR RENDER)
    s3.upload_file(tmp_out, S3_BUCKET_NAME, key)

    log_step(f"[UPLOAD] Upload complete: {key}")

    # -----------------------------
    # 4. (OPTIONAL) Save normalized locally for analysis/sync
    # -----------------------------
    try:
        os.makedirs(video_folder, exist_ok=True)
        local_copy = os.path.join(video_folder, normalized_name)
        import shutil
        shutil.copy2(tmp_out, local_copy)
        log_step(f"[UPLOAD] Copied to local {local_copy}")
    except Exception as e:
        # Non-fatal — local copy may fail on Render, but S3 upload succeeded
        log_step(f"[UPLOAD WARNING] Local copy failed: {e}")

    return key
