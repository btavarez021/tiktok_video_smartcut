import os
import subprocess
import yaml
import uuid
import boto3
import tempfile
from gtts import gTTS
from assistant_log import log_step

CONFIG_PATH = "config.yml"
EXPORT_DIR = "exports"

os.makedirs(EXPORT_DIR, exist_ok=True)

# Load bucket info from env
S3_BUCKET = os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION", "us-east-2")

s3 = boto3.client("s3", region_name=S3_REGION)

# -------------------------------------------------------------
# Utility: run ffmpeg
# -------------------------------------------------------------
def ffmpeg(cmd: list):
    log_step("FFmpeg: " + " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc


# -------------------------------------------------------------
# Helper: download video from S3 into /tmp
# -------------------------------------------------------------
def download_video_from_s3(filename: str) -> str:
    """
    filename = "IMG_3753.mov"
    stored at = s3://bucket/raw_uploads/IMG_3753.mov
    """
    key = f"raw_uploads/{filename}"
    _, ext = os.path.splitext(filename)
    tmp_path = f"/tmp/{uuid.uuid4().hex}{ext}"

    log_step(f"Downloading from S3 → {key}")

    with open(tmp_path, "wb") as f:
        s3.download_fileobj(S3_BUCKET, key, f)

    return tmp_path


# -------------------------------------------------------------
# Clip cutter
# -------------------------------------------------------------
def make_clip(input_path, start, duration, text=None):
    out_path = f"/tmp/{uuid.uuid4().hex}.mp4"

    if text:
        vf = (
            "scale=1080:1920,"
            f"drawtext=text='{text}':fontcolor=white:fontsize=48:"
            "x=(w-text_w)/2:y=h*0.12:shadowcolor=000000:shadowx=2:shadowy=2"
        )
    else:
        vf = "scale=1080:1920"

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", str(start),
        "-t", str(duration),
        "-vf", vf,
        "-preset", "medium",
        "-c:a", "aac",
        out_path
    ]
    ffmpeg(cmd)
    return out_path


# -------------------------------------------------------------
# CTA overlay
# -------------------------------------------------------------
def add_cta_overlay(video_path, text):
    out = f"/tmp/{uuid.uuid4().hex}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf",
        f"drawtext=text='{text}':fontcolor=white:fontsize=42:"
        "x=(w-text_w)/2:y=h-h*0.15:shadowcolor=000000:shadowx=2:shadowy=2",
        "-c:a", "copy",
        out,
    ]
    ffmpeg(cmd)
    return out


# -------------------------------------------------------------
# TTS audio via gTTS
# -------------------------------------------------------------
def generate_tts_audio(text: str):
    out = f"/tmp/{uuid.uuid4().hex}.mp3"
    tts = gTTS(text=text, lang="en")
    tts.save(out)
    return out


def merge_audio(video, tts_audio):
    out = f"/tmp/{uuid.uuid4().hex}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", video,
        "-i", tts_audio,
        "-filter_complex",
        "[0:a]volume=0.15[bg];[bg][1:a]amix=inputs=2:dropout_transition=2",
        "-c:v", "copy",
        out
    ]
    ffmpeg(cmd)
    return out


# -------------------------------------------------------------
# Concat clips
# -------------------------------------------------------------
def concat_clips(paths):
    listfile = f"/tmp/{uuid.uuid4().hex}.txt"
    with open(listfile, "w") as f:
        for p in paths:
            f.write(f"file '{p}'\n")

    out = f"/tmp/{uuid.uuid4().hex}.mp4"
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
           "-i", listfile, "-c", "copy", out]
    ffmpeg(cmd)
    return out


# -------------------------------------------------------------
# Optimize output
# -------------------------------------------------------------
def optimize_video(input_path):
    out = f"/tmp/{uuid.uuid4().hex}.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vcodec", "libx264",
        "-crf", "23",
        "-preset", "fast",
        "-c:a", "aac",
        out
    ]
    ffmpeg(cmd)
    return out


# -------------------------------------------------------------
# MAIN RENDER ENTRY POINT
# -------------------------------------------------------------
def render_final_video(optimized=False):
    log_step("Rendering TikTok Reel (S3-based)…")

    if not os.path.exists(CONFIG_PATH):
        raise RuntimeError("config.yml missing")

    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)

    clips = []

    # FIRST CLIP
    fc = cfg["first_clip"]
    fc_input = download_video_from_s3(fc["file"])
    clips.append(make_clip(fc_input, fc["start_time"], fc["duration"], fc["text"]))

    # MIDDLE CLIPS
    for m in cfg.get("middle_clips", []):
        m_input = download_video_from_s3(m["file"])
        clips.append(make_clip(m_input, m["start_time"], m["duration"], m["text"]))

    # LAST CLIP
    lc = cfg["last_clip"]
    lc_input = download_video_from_s3(lc["file"])
    clips.append(make_clip(lc_input, lc["start_time"], lc["duration"], lc["text"]))

    # CONCAT
    final = concat_clips(clips)

    # CTA
    if cfg["cta"]["enabled"]:
        final = add_cta_overlay(final, cfg["cta"]["text"])

    # TTS
    if cfg["render"]["tts_enabled"]:
        full_text = " ".join([
            cfg["first_clip"]["text"],
            *[m["text"] for m in cfg.get("middle_clips", [])],
            cfg["last_clip"]["text"]
        ])
        tts_audio = generate_tts_audio(full_text)
        final = merge_audio(final, tts_audio)

    # Optimize
    if optimized:
        final = optimize_video(final)

    output_name = "output_optimized.mp4" if optimized else "output_standard.mp4"
    output_path = os.path.join(EXPORT_DIR, output_name)

    ffmpeg(["ffmpeg", "-y", "-i", final, "-c", "copy", output_path])

    log_step(f"Render complete → {output_path}")
    return output_path
