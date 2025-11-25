# tiktok_template.py
import os
import logging
import subprocess
from typing import List, Dict, Any, Optional

import yaml
from moviepy import editor as mpe

from assistant_log import log_step

# -----------------------------------------
# Paths
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Folder where normalized, local copies of clips live
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

# Main YAML config path
config_path = os.path.join(BASE_DIR, "config.yml")

# -----------------------------------------
# Logging
# -----------------------------------------
logger = logging.getLogger(__name__)


# -----------------------------------------
# FFmpeg normalization
# -----------------------------------------
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    """
    Normalize input video:
    - Fix rotation (ffmpeg auto-applies rotation)
    - Re-encode to H.264 + AAC
    - Reasonable scale (but final scaling to 1080x1920 happens in edit_video)
    """
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
    logger.info("Running ffmpeg: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.stderr:
            log_step(f"[FFMPEG STDERR for {src}] {result.stderr[:600]}")
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
# Internal helpers for rendering
# -----------------------------------------
TARGET_W = 1080
TARGET_H = 1920


def _load_clip_from_config(conf: Dict[str, Any]) -> Optional[mpe.VideoFileClip]:
    """
    Given a clip config dict with keys:
      - file
      - start_time
      - duration
      - scale (optional)
    Load and return a VideoFileClip that:
      - is trimmed to [start_time, start_time+duration]
      - is scaled by 'scale' *and* fg_scale_default
      - is cropped to 1080x1920 (vertical TikTok, Option A)
    """
    filename = conf.get("file")
    if not filename:
        return None

    path = os.path.join(video_folder, os.path.basename(filename))
    if not os.path.exists(path):
        logger.warning("Clip file not found: %s", path)
        return None

    start = float(conf.get("start_time", 0))
    dur = conf.get("duration")
    scale = float(conf.get("scale", 1.0))

    clip = mpe.VideoFileClip(path)

    # Trim
    if dur is not None:
        dur = float(dur)
        end = min(clip.duration, start + dur)
        if end <= start:
            end = min(clip.duration, start + 0.5)
        clip = clip.subclip(start, end)
    else:
        if start > 0 and start < clip.duration:
            clip = clip.subclip(start)

    return clip, scale


def _scale_and_crop_vertical(
    clip: mpe.VideoFileClip,
    fg_scale: float = 1.0,
) -> mpe.VideoFileClip:
    """
    Option A: pure 1080x1920 vertical crop.
    - First fit clip to fill HEIGHT (1920)
    - Then crop horizontally to WIDTH (1080)
    - Apply fg_scale as an extra zoom
    """
    # First, scale by fg_scale
    if fg_scale != 1.0:
        clip = clip.resize(fg_scale)

    # Ensure height matches target H while preserving aspect
    clip = clip.resize(height=TARGET_H)

    # Then center-crop width to 1080
    w, h = clip.size
    if w < TARGET_W:
        # If still narrower, scale to width instead
        clip = clip.resize(width=TARGET_W)
        w, h = clip.size

    x1 = (w - TARGET_W) / 2
    x2 = x1 + TARGET_W

    return clip.crop(x1=x1, x2=x2, y1=0, y2=TARGET_H)


def _build_timeline_from_config(cfg: Dict[str, Any]) -> mpe.VideoFileClip:
    """
    Create a concatenated 1080x1920 vertical timeline from config.yml:
      - first_clip
      - middle_clips[]
      - last_clip
    Applies:
      - fg_scale_default
      - per-clip scale
    """
    render_cfg = cfg.get("render", {})
    fg_default = float(render_cfg.get("fg_scale_default", 1.0))

    clips: List[mpe.VideoFileClip] = []

    def _add_clip(section: Dict[str, Any] | None):
        if not section:
            return
        result = _load_clip_from_config(section)
        if not result:
            return
        c, sc = result
        combined_scale = fg_default * sc
        c = _scale_and_crop_vertical(c, combined_scale)
        clips.append(c)

    # first_clip
    _add_clip(cfg.get("first_clip"))

    # middle_clips
    for mc in cfg.get("middle_clips", []):
        _add_clip(mc)

    # last_clip
    _add_clip(cfg.get("last_clip"))

    if not clips:
        raise RuntimeError("No clips could be loaded from config.yml")

    timeline = mpe.concatenate_videoclips(clips, method="compose")
    # Ensure final size exactly 1080x1920
    timeline = timeline.resize((TARGET_W, TARGET_H))
    return timeline


# -----------------------------------------
# Text overlay helpers (captions & CTA)
# -----------------------------------------
def _try_text_overlay(
    base_clip: mpe.VideoFileClip,
    text: str,
    duration: float,
    start: float,
    fontsize: int = 60,
    position: str = "bottom",
) -> Optional[mpe.VideoFileClip]:
    """
    Try to overlay text on the base clip.
    Uses TextClip; if ImageMagick is not available, falls back to no overlay.
    """
    text = (text or "").strip()
    if not text:
        return None

    try:
        txt_clip = mpe.TextClip(
            text,
            fontsize=fontsize,
            font="Arial-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(duration)

        if position == "bottom":
            txt_clip = txt_clip.set_position(("center", TARGET_H * 0.8))
        else:
            txt_clip = txt_clip.set_position(("center", "center"))

        txt_clip = txt_clip.set_start(start)

        return mpe.CompositeVideoClip([base_clip, txt_clip], size=(TARGET_W, TARGET_H))
    except Exception as e:
        logger.warning("Text overlay failed (%s). Continuing without on-screen text.", e)
        return None


def _collect_all_captions(cfg: Dict[str, Any]) -> List[str]:
    caps: List[str] = []
    fc = cfg.get("first_clip") or {}
    if fc.get("text"):
        caps.append(fc["text"])
    for mc in cfg.get("middle_clips", []):
        if mc.get("text"):
            caps.append(mc["text"])
    lc = cfg.get("last_clip") or {}
    if lc.get("text"):
        caps.append(lc["text"])
    return caps


# -----------------------------------------
# TTS helper (OpenAI)
# -----------------------------------------
def _build_tts_audio(cfg: Dict[str, Any], total_duration: float) -> Optional[mpe.AudioFileClip]:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not api_key:
        logger.warning("No OPENAI_API_KEY set; skipping TTS audio.")
        return None

    render_cfg = cfg.get("render", {})
    if not render_cfg.get("tts_enabled", False):
        return None

    voice = render_cfg.get("tts_voice", "alloy")

    captions = _collect_all_captions(cfg)
    if not captions:
        logger.info("No captions found for TTS; skipping.")
        return None

    # Simple approach: join all captions into one VO script
    script = ". ".join(captions)

    log_step(f"[TTS] Generating voiceover with voice '{voice}'…")

    client = OpenAI(api_key=api_key)

    try:
        tts_path = os.path.join(BASE_DIR, "tts_voiceover.mp3")
        with open(tts_path, "wb") as f:
            response = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=script,
            )
            f.write(response.read())

        tts_audio = mpe.AudioFileClip(tts_path)
        # Loop or trim to match total video duration
        if tts_audio.duration < total_duration:
            tts_audio = mpe.CompositeAudioClip([tts_audio.audio_loop(duration=total_duration)])
        else:
            tts_audio = tts_audio.subclip(0, total_duration)

        return tts_audio
    except Exception as e:
        logger.exception("TTS generation failed: %s", e)
        log_step("TTS generation failed; continuing without voiceover.")
        return None


# -----------------------------------------
# CTA overlay
# -----------------------------------------
def _apply_cta_overlay(
    clip: mpe.VideoFileClip,
    cfg: Dict[str, Any],
) -> mpe.VideoFileClip:
    cta = cfg.get("cta", {}) or {}
    if not cta.get("enabled", False):
        return clip

    text = cta.get("text") or ""
    if not text.strip():
        return clip

    duration = float(cta.get("duration", 3.0))
    pos = cta.get("position", "bottom")
    total_dur = clip.duration

    start = max(0, total_dur - duration)

    over = _try_text_overlay(
        base_clip=clip,
        text=text,
        duration=duration,
        start=start,
        fontsize=52,
        position=pos,
    )
    return over or clip


# -----------------------------------------
# Main render entry point
# -----------------------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False) -> None:
    """
    Main renderer used by api_export().
    - Loads config.yml
    - Builds vertical 1080x1920 timeline from clips in tik_tok_downloads/
    - Applies on-screen captions (best-effort; skipped if TextClip not available)
    - Applies CTA overlay at the end if enabled
    - Builds TTS audio voiceover if enabled
    - Writes final MP4 in standard or optimized quality
    """
    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml is empty or missing.")

    log_step("Building 1080x1920 vertical timeline from config.yml…")
    logger.info("Rendering with config: %s", cfg)

    # 1) Build base timeline from clips
    timeline = _build_timeline_from_config(cfg)
    total_duration = timeline.duration

    # 2) Best-effort on-screen captions
    #    We overlay per-clip captions roughly by time proportion.
    #    If TextClip fails (e.g., ImageMagick unavailable), we just keep timeline.
    captions = _collect_all_captions(cfg)
    if captions:
        log_step("Adding caption overlays (best-effort)…")
        try:
            n = len(captions)
            seg = total_duration / max(n, 1)
            overlays: List[mpe.VideoFileClip] = []
            base = timeline

            # We'll progressively overlay text, reusing base each time
            for i, cap in enumerate(captions):
                start = i * seg
                end = min(total_duration, (i + 1) * seg)
                dur = max(0.5, end - start)
                over = _try_text_overlay(
                    base_clip=base,
                    text=cap,
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

    # 3) CTA overlay at the end
    timeline = _apply_cta_overlay(timeline, cfg)

    # 4) Audio: combine original audio with optional TTS
    base_audio = timeline.audio
    tts_audio = _build_tts_audio(cfg, total_duration)

    if tts_audio is not None:
        log_step("Mixing TTS voiceover with original audio…")
        try:
            mixed = mpe.CompositeAudioClip(
                [
                    base_audio.volumex(0.4) if base_audio else None,
                    tts_audio.volumex(1.0),
                ]
            )
            # Filter out Nones
            mixed.clips = [c for c in mixed.clips if c is not None]
            timeline = timeline.set_audio(mixed)
        except Exception as e:
            logger.warning("Failed mixing TTS audio: %s", e)
    else:
        if base_audio is not None:
            timeline = timeline.set_audio(base_audio)

    # 5) Write final file
    # Standard vs optimized: slightly different preset / bitrate
    if optimized:
        codec = "libx264"
        audio_codec = "aac"
        bitrate = "6000k"
        preset = "slow"
        log_step("Writing optimized export (higher quality, heavier)…")
    else:
        codec = "libx264"
        audio_codec = "aac"
        bitrate = "4000k"
        preset = "veryfast"
        log_step("Writing standard export (fast)…")

    out_path = os.path.join(BASE_DIR, "..", output_file)
    out_path = os.path.abspath(out_path)

    log_step(f"Rendering to {out_path} … this may take a bit.")
    logger.info("Writing video file to %s", out_path)

    timeline.write_videofile(
        out_path,
        codec=codec,
        audio_codec=audio_codec,
        fps=30,
        bitrate=bitrate,
        preset=preset,
        threads=2,
        verbose=False,
        logger=None,
    )

    log_step(f"Render completed → {out_path}")
