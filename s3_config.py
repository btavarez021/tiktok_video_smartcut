# s3_config.py
"""
Central place for S3 + bucket constants.
Breaks circular imports between tiktok_assistant and tiktok_template.
"""

from tiktok_assistant import (
    s3,
    S3_BUCKET_NAME,
    RAW_PREFIX,
    EXPORT_PREFIX,
    S3_REGION,
)

# Optional helper used by uploader:
def clean_s3_key(key: str) -> str:
    key = key.lstrip("/")
    while "//" in key:
        key = key.replace("//", "/")
    return key
