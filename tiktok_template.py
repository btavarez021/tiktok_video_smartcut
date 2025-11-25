# tiktok_template.py
import os
import json
import subprocess
import tempfile
import logging
from typing import Optional, List, Dict

import yaml
from openai import OpenAI

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG PATH
# ============================================================================
config_path = os.path.join(os.path.dirname(__file__), "config.yml")


# ============================================================================
# OPENAI TTS CLIENT
# ============================================================================
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
client: Optional[OpenAI] = OpenAI(api_key=api_key) if api_key else None


# ============================================================================
# VIDEO NORMALIZATION (FFMPEG)
# ============================================================================
def normalize_video_ffmpeg(src: str, dst: str):
    """
    Normalize a video into 1080x1920 vertical format.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
               "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-preset", "veryfast",
        "-c:a", "copy",
        dst
    ]

    try:
        logger.info(f"[FFMPEG] Normalizing {src} → {dst}")
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg normalization failed: {e}")
        raise


# ============================================================================
# LOAD CONFIG
# ============================================================================
def load_cfg() -> Dict:
    if not os.path.exists(config_path):
        return {}

    with open(config_path, "r") as f:
        return yaml.safe_load(f) or {}


# ============================================================================
# TEXT OVERLAY FILTER BUILDER
# ============================================================================
def text_filter(text: str, y_offset: int = 100):
    safe = text.replace(":", r"\:").replace("'", r"\'")

    return (
        f"drawtext=text='{safe}':"
        f"fontcolor=white:fontsize=38:"
        f"box=1:boxcolor=black@0.45:boxborderw=15:"
        f"x=(w-text_w)/2:y=h-{y_offset}"
    )


# ============================================================================
# GENERATE TTS VOICEOVER (OPTIONAL)
# ============================================================================
def generate_tts(text: str, voice: str, tmpdir: str) -> Optional[str]:
    """
    Uses OpenAI TTS to create narration audio.
    """
    if client is None:
        return None

    out_path = os.path.join(tmpdir, "tts_audio.mp3")

    try:
        resp = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text,
        )

        with open(out_path, "wb") as f:
            f.write(resp.read())

        return out_path

    except Exception as e:
        logger.error(f"TTS generation failed: {e}")
        return None


# ============================================================================
# ATTACH MUSIC (OPTIONAL)
# ============================================================================
def add_music(video_file: str, music_volume: float, tmpdir: str) -> str:
    """
    Add low-volume background music from static/music/default.mp3.
    """
    music_src = os.path.join("static", "music", "default.mp3")

    if not os.path.exists(music_src):
        return video_file

    out_path = os.path.join(tmpdir, "with_music.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_file,
        "-i", music_src,
        "-filter_complex",
        f"[1:a]volume={music_volume}[bg];"
        f"[0:a][bg]amix=inputs=2:duration=shortest",
        "-c:v", "copy",
        out_path
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return out_path


# ============================================================================
# BUILD TIMELINE
# ============================================================================
def build_clip(
    file: str,
    start: float,
    duration: float,
    text: str,
    scale: float,
    tmpdir: str,
    fg_scale_default: float = 1.0
):
    """
    Crops clip, overlays text, applies foreground scale.
    """
    out = os.path.join(tmpdir, f"{os.path.basename(file)}_trimmed.mp4")

    filter_chain = []

    if scale != 1.0 or fg_scale_default != 1.0:
        total_scale = scale * fg_scale_default
        filter_chain.append(f"scale=iw*{total_scale}:ih*{total_scale}")

    # text overlay
    filter_chain.append(text_filter(text))

    vf = ",".join(filter_chain)

    cmd = [
        "ffmpeg", "-y",
        "-i", file,
        "-vf", vf,
        "-ss", str(start),
        "-t", str(duration),
        "-preset", "veryfast",
        out,
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return out


# ============================================================================
# CONCATENATE ALL CLIPS
# ============================================================================
def concatenate_clips(clips: List[str], tmpdir: str) -> str:
    txt = os.path.join(tmpdir, "list.txt")

    with open(txt, "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")

    out = os.path.join(tmpdir, "concatenated.mp4")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", txt,
        "-c", "copy",
        out
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return out


# ============================================================================
# FINAL EXPORT
# ============================================================================
def edit_video(output_file="output_tiktok_final.mp4", optimized=False):
    """
    Master function: loads config.yml, builds timeline, overlays text,
    adds TTS, music, cta, and exports final video.
    """
    cfg = load_cfg()
    tmpdir = tempfile.mkdtemp()

    clips = []

    # ----------------------------------------------------------
    # FIRST CLIP
    # ----------------------------------------------------------
    first = cfg.get("first_clip")
    if first:
        clip_path = build_clip(
            file=first["file"],
            start=first.get("start_time", 0),
            duration=first.get("duration", 4),
            text=first.get("text", ""),
            scale=first.get("scale", 1.0),
            tmpdir=tmpdir,
            fg_scale_default=cfg.get("render", {}).get("fg_scale_default", 1.0),
        )
        clips.append(clip_path)

    # ----------------------------------------------------------
    # MIDDLE CLIPS
    # ----------------------------------------------------------
    for m in cfg.get("middle_clips", []):
        clip_path = build_clip(
            file=m["file"],
            start=m.get("start_time", 0),
            duration=m.get("duration", 4),
            text=m.get("text", ""),
            scale=m.get("scale", 1.0),
            tmpdir=tmpdir,
            fg_scale_default=cfg.get("render", {}).get("fg_scale_default", 1.0),
        )
        clips.append(clip_path)

    # ----------------------------------------------------------
    # LAST CLIP
    # ----------------------------------------------------------
    last = cfg.get("last_clip")
    if last:
        clip_path = build_clip(
            file=last["file"],
            start=last.get("start_time", 0),
            duration=last.get("duration", 4),
            text=last.get("text", ""),
            scale=last.get("scale", 1.0),
            tmpdir=tmpdir,
            fg_scale_default=cfg.get("render", {}).get("fg_scale_default", 1.0),
        )
        clips.append(clip_path)

    # ----------------------------------------------------------
    # CONCAT CLIPS
    # ----------------------------------------------------------
    output = concatenate_clips(clips, tmpdir)

    # ----------------------------------------------------------
    # MUSIC
    # ----------------------------------------------------------
    music_cfg = cfg.get("music", {})
    output = add_music(output, music_cfg.get("volume", 0.25), tmpdir)

    # ----------------------------------------------------------
    # CTA OVERLAY (optional)
    # ----------------------------------------------------------
    cta_cfg = cfg.get("cta", {})
    if cta_cfg.get("enabled"):
        out_cta = os.path.join(tmpdir, "cta_out.mp4")
        safe = cta_cfg.get("text", "").replace(":", r"\:")
        filter_text = f"drawtext=text='{safe}':fontcolor=white:fontsize=46:box=1:boxcolor=black@0.45:boxborderw=20:x=(w-text_w)/2:y=h-200"

        cmd = [
            "ffmpeg", "-y",
            "-i", output,
            "-vf", filter_text,
            out_cta
        ]

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        output = out_cta

    # ----------------------------------------------------------
    # TTS (optional)
    # ----------------------------------------------------------
    render_cfg = cfg.get("render", {})
    if render_cfg.get("tts_enabled"):
        tts_audio = generate_tts(
            text=cfg.get("first_clip", {}).get("text", ""),
            voice=render_cfg.get("tts_voice", "alloy"),
            tmpdir=tmpdir,
        )

        if tts_audio:
            out_tts = os.path.join(tmpdir, "tts_mix.mp4")
            cmd = [
                "ffmpeg", "-y",
                "-i", output,
                "-i", tts_audio,
                "-filter_complex", "[1:a]adelay=0|0[s];[0:a][s]amix=inputs=2:duration=shortest",
                "-c:v", "copy",
                out_tts
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            output = out_tts

    # ----------------------------------------------------------
    # FINAL MOVE
    # ----------------------------------------------------------
    final_out = output_file
    os.replace(output, final_out)

    logger.info(f"EXPORT COMPLETE → {final_out}")
    return final_out
