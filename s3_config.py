# s3_config.py — shared S3 client + prefixes (NO circular imports)

import os
import boto3

# ------------------------------
# ENV VARS
# ------------------------------
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
if not S3_BUCKET_NAME:
    raise RuntimeError("S3_BUCKET_NAME environment variable is required.")

S3_REGION = os.getenv("S3_REGION", "us-east-2")

RAW_PREFIX = os.getenv("S3_RAW_PREFIX", "raw_uploads").strip("/") + "/"
EXPORT_PREFIX = os.getenv("S3_EXPORT", "exports").strip("/") + "/"
PROCESSED_PREFIX = os.getenv("S3_PROCESSED_PREFIX", "processed").strip("/") + "/"


# ------------------------------
# AWS Credentials
# ------------------------------
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

if not AWS_ACCESS_KEY_ID or not AWS_SECRET_ACCESS_KEY:
    raise RuntimeError(
        "Missing AWS credentials — set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
    )

# ------------------------------
# S3 CLIENT (shared system-wide)
# ------------------------------
s3 = boto3.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

# ------------------------------
# Utility: clean key helper
# ------------------------------
def clean_s3_key(key: str) -> str:
    """
    Remove all leading slashes + collapse // into /
    Prevents AWS from creating phantom "/" folders
    """
    key = key.lstrip("/")
    while "//" in key:
        key = key.replace("//", "/")
    return key

