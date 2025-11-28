# upload_utils.py
import os
import tempfile
from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX, clean_s3_key

from tiktok_template import video_folder     # safe import
from tiktok_assistant import normalize_video, sanitize_yaml_filenames  # safe import


def upload_raw_file(file):
    """
    Upload handler:
    - Save temp file
    - Normalize to MP4
    - Upload normalized version to S3
    - Save normalized version locally (tik_tok_downloads)
    """

    # Save upload to a temporary file
    tmp_path = tempfile.NamedTemporaryFile(delete=False).name
    file.save(tmp_path)
    log_step(f"[UPLOAD] Saved temp file: {tmp_path}")

    # Ensure local folder exists
    os.makedirs(video_folder, exist_ok=True)

    # Clean filename
    raw_name = sanitize_yaml_filenames(file.filename.lstrip("/"))
    base, _ = os.path.splitext(raw_name)
    normalized_name = f"{base}.mp4"

    # Local normalized output path
    local_path = os.path.join(video_folder, normalized_name)

    # Normalize to mp4 (ensures moov atom)
    normalize_video(tmp_path, local_path)

    # Build S3 key
    key = f"{RAW_PREFIX}/{normalized_name}"
    key = clean_s3_key(key)

    log_step(f"[UPLOAD] Uploading normalized → s3://{S3_BUCKET_NAME}/{key}")

    # Upload to S3
    s3.upload_file(local_path, S3_BUCKET_NAME, key)

    return key
