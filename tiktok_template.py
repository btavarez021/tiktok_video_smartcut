import os
import logging
import subprocess
import gc
from typing import List, Dict, Any, Optional

# Pillow compatibility fix for MoviePy
from PIL import Image, ImageFilter

if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

import numpy as np
import yaml

from utils_video import enforce_mp4

from moviepy.editor import (
    VideoFileClip,
    TextClip,
    CompositeVideoClip,
    AudioFileClip,
    CompositeAudioClip,
    concatenate_videoclips,
    concatenate_audioclips,
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

MUSIC_DIR = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_DIR, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920


# -----------------------------------------
# Simple Gaussian blur via Pillow
# -----------------------------------------
def blur_frame(frame, radius: int = 18):
    """Blur a single RGB frame using Pillow (MoviePy 1.0.3 safe)."""
    try:
        img = Image.fromarray(frame)
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        return np.array(img)
    except Exception as e:
        logger.warning(f"[BLUR] Frame blur failed: {e}")
        return frame


# -----------------------------------------
# FFmpeg normalization (helper, if needed)
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

    log_step(f"[FFMPEG] Normalizing video {src} -> {dst}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        if result.stderr:
            lines = result.stderr.split("\n")
            important = [
                ln
                for ln in lines
                if "warning" in ln.lower() or "error" in ln.lower()
            ]
            for ln in important:
                log_step(f"[FFMPEG WARN] {ln}")

    except subprocess.CalledProcessError as e:
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
# Clip loading (case-insensitive, ext-tolerant)
# -----------------------------------------
def _load_clip_from_config(conf: Dict[str, Any]):
    """
    Load a clip described in the YAML config.

    Tolerant to:
    - case differences (IMG_3753.mp4 vs img_3753.mp4)
    - different video extensions (.mov, .m4v, .mp4) as long as basename matches.
    """
    filename = conf.get("file")
    if not filename:
        return None

    # Ensure .mp4 extension, but keep original basename
    filename = enforce_mp4(filename)

    def _find_clip_path(fname: str) -> Optional[str]:
        target_name = fname
        target_base, _ = os.path.splitext(target_name)

        direct_path = os.path.join(video_folder, target_name)
        if os.path.exists(direct_path):
            return direct_path

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
# Build timeline (and keep per-clip segments)
# -----------------------------------------
def _build_timeline_from_config(cfg: Dict[str, Any]):
    """
    Returns:
      - timeline: concatenated vertical video
      - segments: list of {start, end, text}
    """
    render_cfg = cfg.get("render", {}) or {}
    fg_default = float(render_cfg.get("fg_scale_default", 1.0))

    clips: List[VideoFileClip] = []
    segments: List[Dict[str, Any]] = []
    current_start = 0.0

    def _add(sec: dict):
        nonlocal current_start
        if not sec:
            return
        result = _load_clip_from_config(sec)
        if not result:
            return
        c, sc = result
        c = _scale_and_crop_vertical(c, fg_default * sc)

        dur = float(c.duration)
        start = current_start
        end = start + dur

        segments.append(
            {
                "start": start,
                "end": end,
                "text": (sec.get("text") or "").strip(),
            }
        )
        clips.append(c)
        current_start = end

    _add(cfg.get("first_clip"))
    for mc in cfg.get("middle_clips", []):
        _add(mc)
    _add(cfg.get("last_clip"))

    if not clips:
        raise RuntimeError("No clips available from config.yml")

    timeline = concatenate_videoclips(clips, method="compose")
    timeline = timeline.resize((TARGET_W, TARGET_H))

    total = timeline.duration
    if segments:
        segments[-1]["end"] = total

    return timeline, segments


# -----------------------------------------
# Caption collection (for TTS script)
# -----------------------------------------
def _collect_all_captions(cfg: Dict[str, Any]) -> List[str]:
    caps: List[str] = []

    if cfg.get("first_clip", {}).get("text"):
        caps.append(str(cfg["first_clip"]["text"]))

    for mc in cfg.get("middle_clips", []):
        if mc.get("text"):
            caps.append(str(mc["text"]))

    if cfg.get("last_clip", {}).get("text"):
        caps.append(str(cfg["last_clip"]["text"]))

    # Include CTA in voiceover if requested
    cta_cfg = cfg.get("cta", {}) or {}
    if (
        cta_cfg.get("enabled")
        and cta_cfg.get("voiceover")
        and (cta_cfg.get("text") or "").strip()
    ):
        caps.append(str(cta_cfg["text"]).strip())

    return caps


# -----------------------------------------
# Overlay builder (text + box)
# -----------------------------------------
def _try_text_overlay(
    text: str,
    duration: float,
    start: float,
    fontsize: int = 60,
    position: str = "bottom",
    fade: bool = True,
):
    """Create overlay elements (box + text) for a given time window."""
    text = (text or "").strip()
    if not text or duration <= 0:
        return []

    try:
        txt = (
            TextClip(
                text,
                fontsize=fontsize,
                font="DejaVu-Sans-Bold",
                color="white",
                method="caption",
                size=(TARGET_W - 160, None),
            )
            .set_duration(duration)
            .set_start(start)
        )

        if fade and duration > 0.5:
            txt = txt.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.25)

        box_h = txt.h + 60
        box = (
            ColorClip(size=(TARGET_W, box_h), color=(0, 0, 0))
            .set_duration(duration)
            .set_start(start)
            .set_opacity(0.45)
        )

        y = TARGET_H * (0.80 if position == "bottom" else 0.50)

        txt = txt.set_position(("center", y))
        box = box.set_position(("center", y))

        return [box, txt]

    except Exception as e:
        logger.warning("Text overlay failed: %s", e)
        return []


# -----------------------------------------
# Simple audio loop helper (no audio_loop)
# -----------------------------------------
def _loop_audio_to_duration(audio_clip: AudioFileClip, duration: float) -> AudioFileClip:
    if duration <= 0 or not audio_clip:
        return audio_clip

    clips = []
    t = 0.0
    while t < duration:
        remaining = duration - t
        if audio_clip.duration <= remaining + 1e-3:
            part = audio_clip
        else:
            part = audio_clip.subclip(0, remaining)
        clips.append(part)
        t += part.duration
        if len(clips) > 50:
            break

    if not clips:
        return audio_clip

    return concatenate_audioclips(clips)


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg, segments, total_duration):
    """
    Build segmented TTS:
    - One narration segment per clip
    - Optional CTA narration at end
    - Each segment aligns exactly with its video duration
    - No bleed, no overlap
    """
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        logger.warning("No API key -> no TTS.")
        return None

    render = cfg.get("render", {}) or {}
    if not render.get("tts_enabled"):
        return None

    voice = render.get("tts_voice", "alloy")

    client = OpenAI(api_key=key)

    audio_segments = []

    # ------------------------------------------------
    # 1) CLIP-BY-CLIP TTS (first, mids, last)
    # ------------------------------------------------
    for seg in segments:
        text = (seg.get("text") or "").strip()
        start = float(seg["start"])
        duration = float(seg["end"] - seg["start"])
        if not text or duration <= 0:
            continue

        try:
            # Output file
            out_path = os.path.join(
                BASE_DIR, f"tts_seg_{int(start*1000)}.mp3"
            )

            # Generate per-segment TTS
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )

            with open(out_path, "wb") as f:
                f.write(resp.read())

            clip_audio = AudioFileClip(out_path)

            # Trim or loop to EXACT duration
            if clip_audio.duration < duration:
                clip_audio = _loop_audio_to_duration(clip_audio, duration)
            else:
                clip_audio = clip_audio.subclip(0, duration)

            # Set start time to align with video segment
            clip_audio = clip_audio.set_start(start)

            audio_segments.append(clip_audio)

        except Exception as e:
            logger.warning(f"[TTS SEGMENT ERROR] {e}")

    # ------------------------------------------------
    # 2) CTA TTS (if enabled)
    # ------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    if (
        cta_cfg.get("enabled")
        and cta_cfg.get("voiceover")
        and cta_cfg.get("text")
    ):
        cta_text = cta_cfg["text"].strip()
        cta_duration = float(cta_cfg.get("duration", 3.0))
        cta_start = total_duration - cta_duration

        try:
            out_path = os.path.join(BASE_DIR, "tts_cta.mp3")

            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=cta_text,
            )

            with open(out_path, "wb") as f:
                f.write(resp.read())

            cta_audio = AudioFileClip(out_path)

            if cta_audio.duration < cta_duration:
                cta_audio = _loop_audio_to_duration(cta_audio, cta_duration)
            else:
                cta_audio = cta_audio.subclip(0, cta_duration)

            cta_audio = cta_audio.set_start(cta_start)

            audio_segments.append(cta_audio)

        except Exception as e:
            logger.warning(f"[TTS CTA ERROR] {e}")

    # ------------------------------------------------
    # 3) FINAL COMPOSITE
    # ------------------------------------------------
    if not audio_segments:
        return None

    return CompositeAudioClip(audio_segments)


# -----------------------------------------
# Background music (YAML: music: {enabled, file, volume})
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    music_cfg = cfg.get("music", {}) or {}
    if not music_cfg.get("enabled"):
        return None

    music_file = (music_cfg.get("file") or "").strip()
    if not music_file:
        return None

    music_volume = float(music_cfg.get("volume", 0.25))
    music_path = os.path.join(MUSIC_DIR, music_file)

    if not os.path.exists(music_path):
        log_step(f"[MUSIC] NOT FOUND: {music_path}")
        return None

    try:
        log_step(f"[MUSIC] Loaded: {music_path}")
        music = AudioFileClip(music_path).volumex(music_volume)
        if music.duration < total_duration:
            music_full = _loop_audio_to_duration(music, total_duration)
        else:
            music_full = music.subclip(0, total_duration)
        return music_full
    except Exception as e:
        log_step(f"[MUSIC ERROR] Failed to load or loop {music_path}: {e}")
        return None


# -----------------------------------------
# Main render
# -----------------------------------------
def edit_video(output_file="output_tiktok_final.mp4", optimized: bool = False):

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building 1080x1920 timeline…")

    base, segments = _build_timeline_from_config(cfg)
    total = float(base.duration)

    # ----- CTA timing -----
    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = bool(cta_cfg.get("enabled"))
    cta_duration = float(cta_cfg.get("duration", 3.0)) if cta_enabled else 0.0
    cta_start = (
        max(0.0, total - cta_duration)
        if cta_enabled and cta_duration > 0
        else None
    )

    # ----- Apply CTA blur + dim + fade (B2) -----
    if cta_enabled and cta_start is not None:
        try:
            pre = base.subclip(0, cta_start)
            outro = base.subclip(cta_start, total)

            # Blur the outro
            outro_blurred = outro.fl_image(lambda f: blur_frame(f, radius=18))

            # Dim layer over the blurred outro
            dim = ColorClip(size=(TARGET_W, TARGET_H), color=(0, 0, 0)).set_opacity(
                0.20
            )
            dim = dim.set_duration(outro_blurred.duration)

            outro_blurred_dim = CompositeVideoClip(
                [outro_blurred, dim], size=(TARGET_W, TARGET_H)
            )

            # Soft crossfade between pre and blurred-dim outro
            pre = pre.fx(vfx.fadeout, 0.4)
            outro_blurred_dim = outro_blurred_dim.fx(vfx.fadein, 0.4)

            base_video = concatenate_videoclips(
                [pre, outro_blurred_dim], method="compose"
            )
            base_video = base_video.resize((TARGET_W, TARGET_H))
        except Exception as e:
            logger.warning(f"[CTA] Blur+dim failed, using unblurred outro: {e}")
            base_video = base
    else:
        base_video = base

    overlays: List[Any] = []

    # ----- CAPTIONS (per-clip, no bleed into CTA) -----
    if segments:
        log_step("Applying caption overlays per clip…")
        last_idx = len(segments) - 1
        for idx, seg in enumerate(segments):
            text = seg.get("text") or ""
            if not text.strip():
                continue

            seg_start = float(seg["start"])
            seg_end = float(seg["end"])
            seg_dur = max(0.0, seg_end - seg_start)
            if seg_dur <= 0:
                continue

            # For the last segment, stop caption a bit before CTA starts
            if idx == last_idx and cta_enabled and cta_start is not None:
                safe_end = min(seg_end, cta_start - 0.3)
                if safe_end <= seg_start + 0.4:
                    safe_end = min(seg_end, seg_start + 0.7)
                if safe_end <= seg_start:
                    # too tight -> skip last caption entirely
                    continue
                seg_dur = safe_end - seg_start

            layers = _try_text_overlay(
                text=text,
                duration=seg_dur,
                start=seg_start,
                fontsize=54,
                position="bottom",
                fade=True,
            )
            overlays.extend(layers)

    # ----- CTA text overlay (bottom, on blurred outro) -----
    if cta_enabled and cta_start is not None:
        cta_text = (cta_cfg.get("text") or "").strip()
        if cta_text:
            cta_layers = _try_text_overlay(
                text=cta_text,
                duration=cta_duration,
                start=cta_start,
                fontsize=60,
                position="bottom",
                fade=True,
            )
            overlays.extend(cta_layers)

    # ----- AUDIO: base video audio -----
    base_audio = base.audio  # original audio

    # ----- TTS -----
    tts_audio = _build_tts_audio(cfg, segments, total)

    # ----- Background music (music: block) -----
    music_audio = _build_music_audio(cfg, total)

    audio_tracks = []
    if base_audio:
        vol = 0.4 if (tts_audio or music_audio) else 1.0
        audio_tracks.append(base_audio.volumex(vol))
    if tts_audio:
        audio_tracks.append(tts_audio.volumex(1.0))
    if music_audio:
        audio_tracks.append(music_audio)

    final_audio = None
    if audio_tracks:
        audio_tracks = [a for a in audio_tracks if a is not None]
        if audio_tracks:
            final_audio = CompositeAudioClip(audio_tracks)

    # ----- FINAL COMPOSITION -----
    final = CompositeVideoClip([base_video] + overlays, size=(TARGET_W, TARGET_H))

    if final_audio:
        final = final.set_audio(final_audio)

    # ----- RENDER SETTINGS -----
    bitrate = "6000k" if optimized else "4000k"
    preset = "slow" if optimized else "veryfast"

    out = os.path.abspath(os.path.join(BASE_DIR, output_file))
    log_step(f"Writing video -> {out}")

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

    # Cleanup
    try:
        final.close()
        if base_video is not base:
            base_video.close()
        base.close()
    except Exception:
        pass
    gc.collect()

    return out