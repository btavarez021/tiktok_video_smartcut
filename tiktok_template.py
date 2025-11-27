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

from assistant_log import log_step
import imageio_ffmpeg

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

# -----------------------------------------
# Paths / Globals
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920


# -----------------------------------------
# FFmpeg normalization (used elsewhere)
# -----------------------------------------
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src,
        "-vf",
        "scale='min(1080,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
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
                ln
                for ln in lines
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
            ln
            for ln in lines
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
    - different video extensions (.mov, .m4v) as long as the basename matches.
    """
    filename = conf.get("file")
    if not filename:
        return None

    # Ensure .mp4 extension, but keep original basename
    filename = enforce_mp4(filename)

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
                if base.lower() == target_base.lower() and ext.lower() in (
                    ".mp4",
                    ".mov",
                    ".m4v",
                ):
                    return os.path.join(video_folder, f)
        except FileNotFoundError:
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
# Build timeline (first + middle[] + last)
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
# Caption collection (for TTS script)
# -----------------------------------------
def _collect_all_captions(cfg: Dict[str, Any]) -> List[str]:
    caps: List[str] = []

    if cfg.get("first_clip", {}).get("text"):
        caps.append(cfg["first_clip"]["text"])

    for mc in cfg.get("middle_clips", []):
        if mc.get("text"):
            caps.append(mc["text"])

    if cfg.get("last_clip", {}).get("text"):
        caps.append(cfg["last_clip"]["text"])

    return caps


# -----------------------------------------
# Text overlay (caption)
# -----------------------------------------
def _try_text_overlay(
    base: VideoFileClip,
    text: str,
    duration: float,
    start: float,
    fontsize: int = 60,
    position: str = "bottom",
):
    """
    Build text overlay elements (box + text) that live on top of `base`.
    Returns [box_clip, text_clip] or None.
    """

    text = (text or "").strip()
    if not text or duration <= 0:
        return None

    try:
        # Text clip
        txt = TextClip(
            text,
            fontsize=fontsize,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(duration).set_start(start)

        # Fade in/out (0.25s each)
        fade_dur = min(0.25, duration / 3.0)
        if fade_dur > 0:
            txt = txt.fx(vfx.fadein, fade_dur)
            txt = txt.fx(vfx.fadeout, fade_dur)

        # Background box
        box_h = txt.h + 60
        box = (
            ColorClip(size=(TARGET_W, box_h), color=(0, 0, 0))
            .set_duration(duration)
            .set_start(start)
            .set_opacity(0.45)
        )

        # Position
        y = TARGET_H * (0.80 if position == "bottom" else 0.50)
        txt = txt.set_position(("center", y))
        box = box.set_position(("center", y))

        return [box, txt]

    except Exception as e:
        logger.warning("Text overlay failed: %s", e)
        return None


# -----------------------------------------
# CTA Outro (blur final segment, keep audio)
# -----------------------------------------
def apply_cta_outro(timeline: VideoFileClip, cfg: Dict[str, Any]) -> VideoFileClip:
    """
    Creates a CTA outro using the LAST X seconds of the video:

    - Take last `duration` seconds of the existing video
    - Blur only that region
    - Add CTA box + text on top
    - Keep ORIGINAL audio for that segment
    - Replace the last X seconds with this blurred CTA segment
      (total duration stays the same)
    """

    cta = cfg.get("cta", {})
    if not cta.get("enabled"):
        return timeline

    text = (cta.get("text", "") or "").strip()
    if not text:
        return timeline

    duration = float(cta.get("duration", 3.0))
    total = timeline.duration

    # Clamp duration
    if total <= 0:
        return timeline
    if duration <= 0 or duration >= total:
        duration = max(1.0, total * 0.3)

    start = total - duration

    # --- Split into main + outro segment ---
    main = timeline.subclip(0, start)
    outro = timeline.subclip(start, total)

    # --- Blur outro (video only, audio preserved) ---
    try:
        outro_blur = outro.fx(vfx.blur, size=25)
    except Exception as e:
        logger.warning(f"[CTA] Blur failed, using original outro: {e}")
        outro_blur = outro

    # --- Build CTA text + box ---
    try:
        txt = TextClip(
            text,
            fontsize=60,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(duration)

        # CTA fade in/out
        fade_dur = min(0.25, duration / 3.0)
        if fade_dur > 0:
            txt = txt.fx(vfx.fadein, fade_dur)
            txt = txt.fx(vfx.fadeout, fade_dur)

        box = (
            ColorClip(size=(TARGET_W, txt.h + 60), color=(0, 0, 0))
            .set_opacity(0.45)
            .set_duration(duration)
        )

        txt = txt.set_position(("center", TARGET_H * 0.82))
        box = box.set_position(("center", TARGET_H * 0.82))

        outro_final = CompositeVideoClip(
            [outro_blur, box, txt], size=(TARGET_W, TARGET_H)
        )

    except Exception as e:
        logger.warning(f"[CTA] Text overlay failed: {e}")
        outro_final = outro_blur

    # NOTE: audio: CompositeVideoClip takes the audio from the first clip (outro_blur),
    # which preserves the original audio from the last segment (your requirement #4).
    final = concatenate_videoclips([main, outro_final], method="compose")
    return final


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg: Dict[str, Any], total_duration: float):
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
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False) -> str:
    """
    Build final vertical reel with:
    - per-clip captions (non-bleeding, with fade in/out)
    - optional TTS voiceover
    - blurred CTA outro that reuses last segment of the video
    """

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building 1080x1920 timeline…")

    # Base video timeline (first + middle[] + last)
    base = _build_timeline_from_config(cfg)
    base = base.set_audio(base.audio)
    total = base.duration

    # -------------------------
    # Prepare CTA timing for caption clamping
    # -------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    if cta_cfg.get("enabled"):
        cta_duration = float(cta_cfg.get("duration", 3.0))
        if cta_duration <= 0 or cta_duration >= total:
            cta_duration = max(1.0, total * 0.3)
        cta_start = total - cta_duration
    else:
        cta_duration = 0.0
        cta_start = total

    overlays: List[VideoFileClip] = []

    # -------------------------
    # Per-clip captions (B: clip-aligned)
    # -------------------------
    try:
        cfg_clips: List[Dict[str, Any]] = []

        if cfg.get("first_clip"):
            cfg_clips.append(cfg["first_clip"])
        for mc in cfg.get("middle_clips", []):
            cfg_clips.append(mc)
        if cfg.get("last_clip"):
            cfg_clips.append(cfg["last_clip"])

        timeline_cursor = 0.0

        for section in cfg_clips:
            result = _load_clip_from_config(section)
            if not result:
                continue

            clip, _scale = result
            dur = clip.duration
            if dur <= 0:
                continue

            text = (section.get("text", "") or "").strip()
            start = timeline_cursor
            end = timeline_cursor + dur

            # Clamp last clip caption so it does NOT bleed into CTA region
            if cta_duration > 0:
                # If this clip starts entirely in CTA region → skip caption
                if start >= cta_start:
                    text = ""
                else:
                    end = min(end, cta_start)

            if text and end > start:
                cap_dur = end - start
                layer = _try_text_overlay(
                    base=base,
                    text=text,
                    duration=cap_dur,
                    start=start,
                    fontsize=54,
                    position="bottom",
                )
                if layer:
                    overlays.extend(layer)

            timeline_cursor += dur

    except Exception as e:
        logger.warning(f"Clip-aligned captions failed: {e}")

    # -------------------------
    # TTS Voiceover over entire video duration
    # -------------------------
    base_audio = base.audio
    tts_audio = _build_tts_audio(cfg, total)

    if tts_audio:
        log_step("Mixing TTS with base audio…")
        try:
            mix_clips = []
            if base_audio:
                mix_clips.append(base_audio.volumex(0.4))
            mix_clips.append(tts_audio.volumex(1.0))

            mix_clips = [c for c in mix_clips if c]
            if mix_clips:
                mix = CompositeAudioClip(mix_clips)
                base = base.set_audio(mix)
        except Exception as e:
            logger.warning(f"TTS mix failed, using base audio only: {e}")
            if base_audio:
                base = base.set_audio(base_audio)
    else:
        if base_audio:
            base = base.set_audio(base_audio)

    # -------------------------
    # CTA OUTRO (blurred last segment with CTA text)
    # -------------------------
    final_video = apply_cta_outro(base, cfg)

    # -------------------------
    # Final Composite: base video + overlays (captions only)
    # -------------------------
    final = CompositeVideoClip(
        [final_video] + overlays,
        size=(TARGET_W, TARGET_H),
    )

    # -------------------------
    # Export settings
    # -------------------------
    bitrate = "6000k" if optimized else "4000k"
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
        threads=2,
        write_logfile=False,
        temp_audiofile=os.path.join(BASE_DIR, "temp-audio.m4a"),
        remove_temp=True,
        ffmpeg_params=[
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
        ],
        logger=None,
    )

    log_step("Render complete.")

    # Clean temp audio if still there
    try:
        temp_audio = os.path.join(BASE_DIR, "temp-audio.m4a")
        if os.path.exists(temp_audio):
            os.remove(temp_audio)
    except Exception:
        pass

    gc.collect()

    return out