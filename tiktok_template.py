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
# Paths
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MUSIC_FOLDER = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_FOLDER, exist_ok=True)

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

logger = logging.getLogger(__name__)

TARGET_W = 1080
TARGET_H = 1920

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MUSIC_DIR = os.path.join(PROJECT_ROOT, "music")



# -----------------------------------------
# Simple Gaussian blur via Pillow for MoviePy 1.0.3
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

    log_step(f"[FFMPEG] Normalizing video {src} -> {dst}")
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

    This is tolerant to:
    - case differences between YAML filenames and actual files (IMG_3753.mp4 vs img_3753.mp4)
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
# Build timeline (and keep per-clip segments)
# -----------------------------------------
def _build_timeline_from_config(cfg: Dict[str, Any]):
    """
    Returns:
      - timeline: concatenated vertical video
      - segments: list of {start, end, text}
    """
    render_cfg = cfg.get("render", {})
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

    # Small safety: recompute duration
    total = timeline.duration
    if segments:
        # Adjust final segment end to match total if off by a tiny epsilon
        segments[-1]["end"] = total

    return timeline, segments


# -----------------------------------------
# Caption collection (for TTS script)
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
        txt = TextClip(
            text,
            fontsize=fontsize,
            font="DejaVu-Sans-Bold",
            color="white",
            method="caption",
            size=(TARGET_W - 160, None),
        ).set_duration(duration).set_start(start)

        if fade and duration > 0.5:
            txt = txt.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.25)

        box_h = txt.h + 60
        box = (
            ColorClip(size=(TARGET_W, box_h), color=(0, 0, 0))
            .set_duration(duration)
            .set_start(start)
            .set_opacity(0.45)
        )

        # Vertical placement
        y = TARGET_H * (0.80 if position == "bottom" else 0.50)

        txt = txt.set_position(("center", y))
        box = box.set_position(("center", y))

        return [box, txt]

    except Exception as e:
        logger.warning("Text overlay failed: %s", e)
        return []


# -----------------------------------------
# Simple audio loop helper (no audio_loop dependency)
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
        if len(clips) > 50:  # safety
            break

    if not clips:
        return audio_clip

    return concatenate_audioclips(clips)


# -----------------------------------------
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg, total_duration):
    from openai import OpenAI

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        logger.warning("No API key -> no TTS.")
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
            vc_loop = _loop_audio_to_duration(vc, total_duration)
            return vc_loop
        else:
            return vc.subclip(0, total_duration)

    except Exception as e:
        logger.exception("TTS failed: %s", e)
        return None


# -----------------------------------------
# Background music
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    render = cfg.get("render", {})
    if not render.get("music_enabled"):
        return None

    music_file = render.get("music_file", "bg_music.mp3")
    music_path = os.path.join(BASE_DIR, music_file)

    if not os.path.exists(music_path):
        log_step(f"[MUSIC] File not found: {music_path}")
        return None

    try:
        music = AudioFileClip(music_path)
        music_full = _loop_audio_to_duration(music, total_duration)
        vol = float(render.get("music_volume", 0.25))
        return music_full.volumex(vol)
    except Exception as e:
        logger.warning(f"[MUSIC] Failed to load or loop music: {e}")
        return None


# -----------------------------------------
# Main render
# -----------------------------------------
def edit_video(output_file="output_tiktok_final.mp4", optimized=False):

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building 1080x1920 timeline…")

    # Build base clip timeline (first/middle/last) + segment metadata
    base, segments = _build_timeline_from_config(cfg)
    total = float(base.duration)

    # ----- CTA timing -----
    cta_cfg = cfg.get("cta", {}) or {}
    cta_enabled = bool(cta_cfg.get("enabled"))
    cta_duration = float(cta_cfg.get("duration", 3.0)) if cta_enabled else 0.0
    cta_start = max(0.0, total - cta_duration) if cta_enabled and cta_duration > 0 else None

    # ----- Apply CTA blur to the last segment of video (video only) -----
    if cta_enabled and cta_start is not None:
        try:
            pre = base.subclip(0, cta_start)
            outro = base.subclip(cta_start, total)

            # Blur via Pillow
            outro_blurred = outro.fl_image(lambda f: blur_frame(f, radius=18))

            base_video = concatenate_videoclips([pre, outro_blurred], method="compose")
            base_video = base_video.resize((TARGET_W, TARGET_H))
        except Exception as e:
            logger.warning(f"[CTA] Blur failed, using unblurred outro: {e}")
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
                # ensure some buffer so last caption disappears before CTA
                safe_end = min(seg_end, cta_start - 0.3)
                if safe_end <= seg_start + 0.4:
                    # too tight, just keep it short or skip
                    safe_end = min(seg_end, seg_start + 0.7)
                if safe_end <= seg_start:
                    # hopeless, skip last caption to avoid overlap
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
    base_audio = base.audio  # use original audio (not blurred vs unblurred)

    # ----- TTS Voiceover -----
    tts_audio = _build_tts_audio(cfg, total)

    # ----- Background music -----
    music_audio = _build_music_audio(cfg, total)

    audio_tracks = []
    if base_audio:
        # Lower original clip audio a bit if TTS or music exist
        vol = 0.4 if (tts_audio or music_audio) else 1.0
        audio_tracks.append(base_audio.volumex(vol))
    if tts_audio:
        audio_tracks.append(tts_audio.volumex(1.0))
    if music_audio:
        audio_tracks.append(music_audio)

    final_audio = None
    if audio_tracks:
        # Filter out any accidental None
        audio_tracks = [a for a in audio_tracks if a is not None]
        if audio_tracks:
            final_audio = CompositeAudioClip(audio_tracks)

    # ----- OPTIONAL MUSIC BACKGROUND -----
    music_cfg = cfg.get("render", {})
    if music_cfg.get("music_enabled"):
        music_file = music_cfg.get("music_file", "").strip()
        music_vol = float(music_cfg.get("music_volume", 0.20))

        if music_file:
            music_path = os.path.join(MUSIC_FOLDER, music_file)

            if os.path.exists(music_path):
                try:
                    music_clip = AudioFileClip(music_path).volumex(music_vol)

                    # Loop to full video duration
                    if music_clip.duration < total:
                        music_clip = music_clip.audio_loop(duration=total)
                    else:
                        music_clip = music_clip.subclip(0, total)

                    log_step(f"[MUSIC] Added {music_file} at volume {music_vol}")

                    # Mix base audio + music (+ TTS if applied)
                    final_audio = [base.audio.volumex(1.0)] if base.audio else []

                    # If TTS exists, it’s already inside base.audio
                    final_audio.append(music_clip)

                    base = base.set_audio(CompositeAudioClip(final_audio))

                except Exception as e:
                    log_step(f"[MUSIC ERROR] {e}")

    # ----- FINAL COMPOSITION -----
    final = CompositeVideoClip(
        [base_video] + overlays,
        size=(TARGET_W, TARGET_H)
    )

    if final_audio:
        final = final.set_audio(final_audio)

    # ----- RENDER SETTINGS -----
    bitrate = "6000k" if optimized else "4000k"
    preset = "slow" if optimized else "veryfast"

    out = os.path.abspath(os.path.join(BASE_DIR, output_file))
    log_step(f"Writing video -> {out}")

    # -----------------------------------------
    # Background Music Mixing (MoviePy 1.0.3 Safe)
    # -----------------------------------------
    music_cfg = cfg.get("music", {})
    music_audio = None

    # filename from YAML (ex: "mytrack.mp3")
    music_file = music_cfg.get("file")
    music_volume = float(music_cfg.get("volume", 0.25))

    if music_file:
        music_path = os.path.join(MUSIC_DIR, music_file)
        if os.path.exists(music_path):
            log_step(f"[MUSIC] Loaded: {music_path}")
            try:
                from moviepy.editor import AudioFileClip, CompositeAudioClip
                music_audio = AudioFileClip(music_path).volumex(music_volume)
            except Exception as e:
                log_step(f"[MUSIC ERROR] Failed to load {music_path}: {e}")
        else:
            log_step(f"[MUSIC] NOT FOUND: {music_path}")

    # Now mix with the base audio safely
    base_audio = final.audio if hasattr(final, "audio") else None

    if base_audio and music_audio:
        log_step("[MUSIC] Mixing base audio + music")
        final = final.set_audio(CompositeAudioClip([base_audio, music_audio]))

    elif music_audio:
        log_step("[MUSIC] Using music only (no base audio)")
        final = final.set_audio(music_audio)

    # else: keep base audio only


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
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            "-profile:v", "baseline",
        ],
        logger=None,
    )

    log_step("Render complete.")

    # Explicit cleanup
    try:
        final.close()
        if base_video is not base:
            base_video.close()
        base.close()
    except Exception:
        pass
    gc.collect()

    return out