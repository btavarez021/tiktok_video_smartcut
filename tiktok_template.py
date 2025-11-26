import os
import logging
import subprocess
from typing import List, Dict, Any, Optional
# Pillow compatibility fix for MoviePy
from PIL import Image
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

import yaml

from utils_video import enforce_mp4

from moviepy.editor import (
    VideoFileClip,
    TextClip,
    CompositeVideoClip,
    AudioFileClip,
    CompositeAudioClip,
    concatenate_videoclips
)

from assistant_log import log_step

# -----------------------------------------
# Paths
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920

# -----------------------------------------
# FFmpeg normalization
# -----------------------------------------
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", src,
        "-vf", "scale='min(1080,iw)':-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-movflags", "+faststart",
        dst,
    ]

    log_step(f"[FFMPEG] Normalizing video {src} → {dst}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stderr:
            log_step(f"[FFMPEG STDERR] {result.stderr[:600]}")
    except subprocess.CalledProcessError as e:
        log_step(f"[FFMPEG ERROR] {e.stderr[:600] if e.stderr else str(e)}")
        raise


# -----------------------------------------
# Config helpers
# -----------------------------------------
def _load_config() -> Dict[str, Any]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# -----------------------------------------
# Clip loading
# -----------------------------------------
def _load_clip_from_config(conf: Dict[str, Any]):


    filename = conf.get("file")
    if not filename:
        return None

    filename = enforce_mp4(filename)
    path = os.path.join(video_folder, filename)


    if not os.path.exists(path):
        logger.warning(f"[LOAD FAILED] File not found: {path}")
        log_step(f"[LOAD FAILED] File not found: {path}")
        return None
    
    if not os.path.exists(path):
        logger.warning("Clip file not found: %s", path)
        return None

    start = float(conf.get("start_time", 0))
    dur = conf.get("duration")
    scale = float(conf.get("scale", 1.0))

    clip = VideoFileClip(path)

    if dur is not None:
        dur = float(dur)
        end = min(clip.duration, start + dur)
        if end <= start:
            end = min(clip.duration, start + 0.5)
        clip = clip.subclip(start, end)
    else:
        if 0 < start < clip.duration:
            clip = clip.subclip(start)

    return clip, scale


# -----------------------------------------
# Vertical resize + crop
# -----------------------------------------
def _scale_and_crop_vertical(clip: VideoFileClip, fg_scale: float = 1.0):
    if fg_scale != 1.0:
        clip = clip.resize(fg_scale)

    clip = clip.resize(height=TARGET_H)

    w, _ = clip.size
    if w < TARGET_W:
        clip = clip.resize(width=TARGET_W)
        w, _ = clip.size

    x1 = (w - TARGET_W) / 2
    x2 = x1 + TARGET_W

    return clip.crop(x1=x1, x2=x2, y1=0, y2=TARGET_H)


# -----------------------------------------
# Build timeline
# -----------------------------------------
def _build_timeline_from_config(cfg: Dict[str, Any]) -> VideoFileClip:
    render_cfg = cfg.get("render", {})
    fg_default = float(render_cfg.get("fg_scale_default", 1.0))

    clips: List[VideoFileClip] = []

    def _add(sec: dict):
        if not sec:
            return
        result = _load_clip_from_config(sec)
        if not result:
            return
        c, sc = result
        c = _scale_and_crop_vertical(c, fg_default * sc)
        clips.append(c)

    _add(cfg.get("first_clip"))
    for mc in cfg.get("middle_clips", []):
        _add(mc)
    _add(cfg.get("last_clip"))

    if not clips:
        raise RuntimeError("No clips available from config.yml")

    timeline = concatenate_videoclips(clips, method="compose")
    return timeline.resize((TARGET_W, TARGET_H))


# -----------------------------------------
# Caption collection
# -----------------------------------------
def _collect_all_captions(cfg: Dict[str, Any]) -> List[str]:
    caps = []
    if cfg.get("first_clip", {}).get("text"):
        caps.append(cfg["first_clip"]["text"])

    for mc in cfg.get("middle_clips", []):
        if mc.get("text"):
            caps.append(mc["text"])

    if cfg.get("last_clip", {}).get("text"):
        caps.append(cfg["last_clip"]["text"])

    return caps


# -----------------------------------------
# Text overlay (caption or CTA)
# -----------------------------------------
def _try_text_overlay(
    base: VideoFileClip,
    text: str,
    duration: float,
    start: float,
    fontsize: int = 60,
    position: str = "bottom",
):
    text = (text or "").strip()
    if not text:
        return None

    try:
        txt = TextClip(
            text,
            fontsize=fontsize,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(duration)

        if position == "bottom":
            txt = txt.set_position(("center", TARGET_H * 0.80))
        else:
            txt = txt.set_position(("center", "center"))

        txt = txt.set_start(start)

        return CompositeVideoClip([base, txt], size=(TARGET_W, TARGET_H))

    except Exception as e:
        logger.warning("Text overlay failed: %s", e)
        return None


# -----------------------------------------
# CTA overlay
# -----------------------------------------
def _apply_cta_overlay(clip: VideoFileClip, cfg: Dict[str, Any]):
    cta = cfg.get("cta", {})
    if not cta.get("enabled"):
        return clip

    text = cta.get("text", "")
    if not text.strip():
        return clip

    duration = float(cta.get("duration", 3.0))
    total = clip.duration
    start = max(0, total - duration)

    over = _try_text_overlay(
        base=clip,
        text=text,
        duration=duration,
        start=start,
        fontsize=52,
        position=cta.get("position", "bottom"),
    )
    return over or clip


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg, total_duration):
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        logger.warning("No API key → no TTS.")
        return None

    render = cfg.get("render", {})
    if not render.get("tts_enabled"):
        return None

    voice = render.get("tts_voice", "alloy")

    caps = _collect_all_captions(cfg)
    if not caps:
        return None

    script = ". ".join(caps)

    log_step(f"[TTS] Generating audio using voice {voice}")

    client = OpenAI(api_key=key)

    try:
        out = os.path.join(BASE_DIR, "tts_voiceover.mp3")
        with open(out, "wb") as f:
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=script,
            )
            f.write(resp.read())

        vc = AudioFileClip(out)

        if vc.duration < total_duration:
            return CompositeAudioClip([vc.audio_loop(duration=total_duration)])
        else:
            return vc.subclip(0, total_duration)

    except Exception as e:
        logger.exception("TTS failed: %s", e)
        return None


# -----------------------------------------
# Main render
# -----------------------------------------
def edit_video(output_file="output_tiktok_final.mp4", optimized=False):

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building 1080x1920 timeline…")

    timeline = _build_timeline_from_config(cfg)
    total = timeline.duration

    # Captions
    caps = _collect_all_captions(cfg)
    if caps:
        log_step("Applying caption overlays…")
        try:
            n = len(caps)
            seg = total / n
            base = timeline

            for i, c in enumerate(caps):
                start = i * seg
                end = min(total, (i + 1) * seg)
                dur = max(0.5, end - start)

                over = _try_text_overlay(
                    base=base,
                    text=c,
                    duration=dur,
                    start=start,
                    fontsize=54,
                    position="bottom",
                )
                if over:
                    base = over

            timeline = base
        except Exception as e:
            logger.warning("Caption overlay failed: %s", e)

    # CTA
    timeline = _apply_cta_overlay(timeline, cfg)

    # Audio / TTS
    base_audio = timeline.audio
    tts_audio = _build_tts_audio(cfg, total)

    if tts_audio:
        log_step("Mixing TTS with base audio…")
        try:
            mix = CompositeAudioClip([
                base_audio.volumex(0.4) if base_audio else None,
                tts_audio.volumex(1.0),
            ])
            mix.clips = [x for x in mix.clips if x]
            timeline = timeline.set_audio(mix)
        except:
            pass
    else:
        if base_audio:
            timeline = timeline.set_audio(base_audio)

    # Output
    codec = "libx264"
    audio_codec = "aac"
    bitrate = "6000k" if optimized else "4000k"
    preset = "slow" if optimized else "veryfast"

    out = os.path.abspath(os.path.join(BASE_DIR, "..", output_file))

    log_step(f"Writing video → {out}")

    timeline.write_videofile(
        out,
        codec=codec,
        audio_codec=audio_codec,
        fps=30,
        bitrate=bitrate,
        preset=preset,
        threads=2,
        verbose=False,
        logger=None,
    )

    log_step("Render complete.")
