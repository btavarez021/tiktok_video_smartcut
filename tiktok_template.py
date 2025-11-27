import os
import logging
import subprocess
import gc
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
    concatenate_videoclips,
    ColorClip,
    vfx,
)
from moviepy.audio.fx.all import audio_fadeout

from assistant_log import log_step
import imageio_ffmpeg

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

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
            # Only show REAL warnings/errors
            lines = result.stderr.split("\n")
            important = [
                ln for ln in lines
                if "warning" in ln.lower() or "error" in ln.lower()
            ]

            if important:
                for ln in important:
                    log_step(f"[FFMPEG WARN] {ln}")

    except subprocess.CalledProcessError as e:
        # Cleanly extract only meaningful error lines
        err = e.stderr or ""
        lines = err.split("\n")
        important = [
            ln for ln in lines
            if any(word in ln.lower() for word in ["error", "failed", "invalid"])
        ]

        if important:
            for ln in important:
                log_step(f"[FFMPEG ERROR] {ln}")
        else:
            # fallback to one clean line
            log_step("[FFMPEG ERROR] Video normalization failed")

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
    """
    Load a clip described in the YAML config.

    Tolerant to:
    - case differences between YAML filenames and actual files
      (IMG_3753.mp4 vs img_3753.mp4)
    - different video extensions (.mov, .m4v) as long as the basename matches.
    """

    filename = conf.get("file")
    if not filename:
        return None

    # Ensure .mp4 extension, but keep original basename
    filename = enforce_mp4(filename)

    # --- helper: find a matching file in video_folder, case-insensitive ---
    def _find_clip_path(fname: str) -> Optional[str]:
        target_name = fname
        target_base, _ = os.path.splitext(target_name)

        # 1) Direct path first (exact match)
        direct_path = os.path.join(video_folder, target_name)
        if os.path.exists(direct_path):
            return direct_path

        # 2) Case-insensitive search in the folder
        if not os.path.isdir(video_folder):
            os.makedirs(video_folder, exist_ok=True)

        try:
            for f in os.listdir(video_folder):
                base, ext = os.path.splitext(f)
                if base.lower() == target_base.lower() and ext.lower() in (".mp4", ".mov", ".m4v"):
                    return os.path.join(video_folder, f)
        except FileNotFoundError:
            # Folder doesn't exist yet; nothing to find
            return None

        return None

    path = _find_clip_path(filename)

    if not path or not os.path.exists(path):
        msg = f"[LOAD FAILED] File not found (case-insensitive search): {os.path.join(video_folder, filename)}"
        logger.warning(msg)
        log_step(msg)
        return None

    # --- timing & scale ---
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
# Caption collection (for TTS script only)
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
# Per-clip caption overlay (no bleeding)
# -----------------------------------------
def _apply_caption_to_clip(
    clip: VideoFileClip,
    text: str,
    fontsize: int = 54,
    position: str = "bottom",
    fade_duration: float = 0.25,
) -> VideoFileClip:
    """
    Adds a caption to a single clip with its own fade in/out.
    No timeline math, so it cannot bleed into other clips.
    """
    text = (text or "").strip()
    if not text:
        return clip

    try:
        txt = TextClip(
            text,
            fontsize=fontsize,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(clip.duration)

        # Dark box behind text
        box_h = txt.h + 60
        box = ColorClip(size=(TARGET_W, box_h), color=(0, 0, 0))\
            .set_duration(clip.duration)\
            .set_opacity(0.45)

        # vertical placement
        y = TARGET_H * (0.80 if position == "bottom" else 0.50)

        txt = txt.set_position(("center", y))
        box = box.set_position(("center", y))

        # fade in/out on overlays only
        if fade_duration > 0:
            txt = txt.fx(vfx.fadein, fade_duration).fx(vfx.fadeout, fade_duration)
            box = box.fx(vfx.fadein, fade_duration).fx(vfx.fadeout, fade_duration)

        composed = CompositeVideoClip(
            [clip, box, txt],
            size=(TARGET_W, TARGET_H),
        )
        composed = composed.set_duration(clip.duration)
        return composed

    except Exception as e:
        logger.warning(f"[CAPTION] Overlay failed: {e}")
        return clip


# -----------------------------------------
# Build main timeline + clean CTA source
# -----------------------------------------
def _build_timeline_and_cta_source(cfg: Dict[str, Any]):
    """
    Returns:
      - main_timeline: concatenated clips with per-clip captions
      - cta_source:    clean tail of the last clip for CTA blur
    """

    render_cfg = cfg.get("render", {}) or {}
    fg_default = float(render_cfg.get("fg_scale_default", 1.0))

    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = bool(cta_cfg.get("enabled"))
    cta_duration = float(cta_cfg.get("duration", 3.0)) if cta_enabled else 0.0

    segments_main: List[VideoFileClip] = []
    cta_source: Optional[VideoFileClip] = None

    first = cfg.get("first_clip")
    middles = cfg.get("middle_clips", []) or []
    last = cfg.get("last_clip")

    sections: List[Dict[str, Any]] = []
    if first:
        sections.append(first)
    sections.extend(middles)
    if last:
        sections.append(last)

    total_sections = len(sections)

    for idx, sec in enumerate(sections):
        result = _load_clip_from_config(sec)
        if not result:
            continue
        clip, sc = result
        clip = _scale_and_crop_vertical(clip, fg_default * sc)
        text = (sec.get("text") or "").strip()

        is_last = (idx == total_sections - 1)

        if not is_last:
            # Normal clip → full caption over whole clip
            if text:
                clip = _apply_caption_to_clip(clip, text=text)
            segments_main.append(clip)
        else:
            # Last clip – special handling for CTA
            if cta_enabled and clip.duration >= cta_duration + 0.5:
                # Split last clip into (captioned main) + (clean tail for CTA)
                main_end = clip.duration - cta_duration

                last_main = clip.subclip(0, main_end)
                if text:
                    last_main = _apply_caption_to_clip(last_main, text=text)
                segments_main.append(last_main)

                cta_source = clip.subclip(main_end, clip.duration)

            elif cta_enabled and clip.duration >= cta_duration:
                # Not enough extra room; treat entire last clip as CTA source (no caption)
                cta_source = clip

            else:
                # No CTA or clip too short → just caption whole last clip normally
                if text:
                    clip = _apply_caption_to_clip(clip, text=text)
                segments_main.append(clip)
                cta_source = None

    if not segments_main:
        raise RuntimeError("No clips available from config.yml")

    main_timeline = concatenate_videoclips(segments_main, method="compose")
    main_timeline = main_timeline.resize((TARGET_W, TARGET_H))

    if cta_source is not None:
        cta_source = cta_source.resize((TARGET_W, TARGET_H))

    return main_timeline, cta_source


# -----------------------------------------
# TTS generation (no looping)
# -----------------------------------------
def _build_tts_audio(cfg, _total_duration: float):
    """
    Build a single TTS track from all captions.
    NOTE: we DO NOT loop it; it plays once and will be trimmed/faded later.
    """
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        logger.warning("No API key → no TTS.")
        return None

    render = cfg.get("render", {}) or {}
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
        return vc

    except Exception as e:
        logger.exception("TTS failed: %s", e)
        return None


# -----------------------------------------
# CTA outro – blur last seconds with CTA text
# -----------------------------------------
def apply_cta_outro(main: VideoFileClip,
                    cta_source: Optional[VideoFileClip],
                    cfg: Dict[str, Any]) -> VideoFileClip:
    """
    CTA behavior (Option B + clean tail):
    - main: all clips + captions
    - cta_source: clean tail of last clip (no captions)
    - CTA: last N seconds of cta_source, blurred, with CTA text on top,
      keeping only base audio (no TTS).
    """
    cta_cfg = cfg.get("cta", {}) or {}
    if not cta_cfg.get("enabled"):
        return main

    if cta_source is None:
        return main

    text = (cta_cfg.get("text") or "").strip()
    if not text:
        return main

    cta_duration = float(cta_cfg.get("duration", 3.0))
    total_cta = cta_source.duration
    if total_cta <= 0:
        return main

    if cta_duration >= total_cta:
        outro = cta_source
    else:
        start = total_cta - cta_duration
        outro = cta_source.subclip(start, total_cta)

    # blur video only (downscale to save memory, then back up)
    try:
        small = outro.resize(0.70)                    # downscale for speed
        blurred_small = small.fx(vfx.gaussian_blur, sigma=12)
        outro_blur = blurred_small.resize((TARGET_W, TARGET_H))

        outro_blur = outro_blur.set_duration(outro.duration)
        outro_blur = outro_blur.set_audio(outro.audio)  # keep last clip's audio
    except Exception as e:
        logger.warning(f"[CTA] Blur failed, using unblurred outro: {e}")
        outro_blur = outro

    try:
        txt = TextClip(
            text,
            fontsize=60,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None)
        ).set_duration(outro_blur.duration)

        box = ColorClip(size=(TARGET_W, txt.h + 60), color=(0, 0, 0))\
            .set_opacity(0.45)\
            .set_duration(outro_blur.duration)

        y = TARGET_H * 0.80
        txt = txt.set_position(("center", y))
        box = box.set_position(("center", y))

        # subtle fade on CTA overlay
        txt = txt.fx(vfx.fadein, 0.2).fx(vfx.fadeout, 0.2)
        box = box.fx(vfx.fadein, 0.2).fx(vfx.fadeout, 0.2)

        outro_final = CompositeVideoClip(
            [outro_blur, box, txt],
            size=(TARGET_W, TARGET_H),
        ).set_duration(outro_blur.duration)

    except Exception as e:
        logger.warning(f"[CTA] Text overlay failed: {e}")
        outro_final = outro_blur

    final = concatenate_videoclips([main, outro_final], method="compose")
    return final.set_duration(main.duration + outro_final.duration)


# -----------------------------------------
# Main render
# -----------------------------------------
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False) -> str:
    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building 1080x1920 timeline…")

    # 1) Build main timeline + clean CTA source
    main, cta_source = _build_timeline_and_cta_source(cfg)
    main_total = main.duration

    # 2) TTS + base audio mix (TTS fades out at CTA start)
    base_audio = main.audio
    tts_audio = _build_tts_audio(cfg, main_total)

    try:
        mix_clips = []

        if base_audio:
            mix_clips.append(base_audio.volumex(0.4))

        if tts_audio:
            # Trim TTS to main duration (no CTA)
            if tts_audio.duration > main_total:
                tts_audio = tts_audio.subclip(0, main_total)

            # Fade out over 1s at the end of main section
            fade_dur = min(1.0, main_total / 2.0)
            tts_audio = audio_fadeout(tts_audio, fade_dur)

            mix_clips.append(tts_audio.volumex(1.0))

        mix_clips = [c for c in mix_clips if c]

        if mix_clips:
            mix = CompositeAudioClip(mix_clips)
            main = main.set_audio(mix)

    except Exception as e:
        logger.warning(f"Audio mix (TTS) failed, using base audio only: {e}")
        if base_audio:
            main = main.set_audio(base_audio)

    # 3) CTA outro (blurred tail of last clip, with CTA text)
    final = apply_cta_outro(main, cta_source, cfg)

    # 4) Render settings (memory-friendly)
    bitrate = "4000k" if optimized else "3000k"
    preset = "slow" if optimized else "veryfast"

    out = os.path.abspath(os.path.join(BASE_DIR, output_file))
    log_step(f"Writing video → {out}")

    final.write_videofile(
        out,
        codec="libx264",
        audio_codec="aac",
        fps=30,
        bitrate=bitrate,
        preset=preset,
        threads=1,                    # <= safer for Render
        write_logfile=False,
        temp_audiofile=None,          # <= avoid big temp audio
        remove_temp=True,
        ffmpeg_params=[
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-profile:v", "baseline",
        ],
        logger=None,
    )

    log_step("Render complete.")
    gc.collect()

    return out