# upload_utils.py
import os
import tempfile
from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX, clean_s3_key
from tiktok_assistant import sanitize_yaml_filenames
from tiktok_template import video_folder, normalize_video


def upload_raw_file(file):
    """
    Upload handler:
    - Save temp file
    - Normalize to MP4
    - Upload normalized version to S3 (raw_uploads/)
    - Save normalized version in local tik_tok_downloads/
    """

    # 1. Save uploaded file to a temp path
    tmp = tempfile.NamedTemporaryFile(delete=False).name
    file.save(tmp)
    log_step(f"[UPLOAD] Saved temp upload: {tmp}")

    # 2. Ensure local folder exists
    os.makedirs(video_folder, exist_ok=True)

    # Sanitize filename (remove slashes)
    raw_name = sanitize_yaml_filenames(file.filename.lstrip("/"))

    base, _ = os.path.splitext(raw_name)
    normalized_name = f"{base}.mp4"

    # Local normalized output path
    local_path = os.path.join(video_folder, normalized_name)

    # 3. Normalize to MP4 (ensures moov atom)
    normalize_video(tmp, local_path)

    # 4. Build S3 key
    key = f"{RAW_PREFIX}/{normalized_name}"
    key = clean_s3_key(key)

    log_step(f"[UPLOAD] Uploading normalized → s3://{S3_BUCKET_NAME}/{key}")

    # 5. Upload to S3
    s3.upload_file(local_path, S3_BUCKET_NAME, key)

    return key
