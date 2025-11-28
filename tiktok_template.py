import os
import logging
import subprocess
import gc
from typing import List, Dict, Any, Optional
from assistant_log import log_step
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
    ImageClip
)
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
# TTS generation
# -----------------------------------------
def _build_tts_audio(cfg, segments, total_duration):
    """
    MEMORY-SAFE TTS BUILDER (FFmpeg-only)

    - Generates one TTS audio file per segment
    - Creates a concat list for all narration segments
    - FFmpeg merges audio with no RAM spikes
    """

    from openai import OpenAI
    import subprocess, tempfile

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
    if not key:
        logger.warning("No API key -> no TTS.")
        return None

    render = cfg.get("render", {}) or {}
    if not render.get("tts_enabled"):
        return None

    voice = render.get("tts_voice", "alloy")
    client = OpenAI(api_key=key)

    # --------------------------------------------
    # TEMP concat file for FFmpeg
    # --------------------------------------------
    concat_list = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name
    entries = []

    # Convert segments structure (from MoviePy pipeline) 
    # into safe trimmed TTS segments for FFmpeg
    for seg in segments or []:
        text = (seg.get("text") or "").strip()
        start = float(seg["start"])
        end = float(seg["end"])
        duration = max(0.0, end - start)

        if not text or duration <= 0:
            continue

        try:
            # Generate raw TTS audio
            tts_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )

            with open(tts_path, "wb") as f:
                f.write(resp.read())

            # Trim/extend TTS audio to exact segment length
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

            trim_cmd = [
                "ffmpeg", "-y",
                "-i", tts_path,
                "-t", str(duration),
                "-af", "apad=pad_dur=10",  # avoid clicks
                "-c:a", "aac",
                trimmed_path
            ]
            subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Add offset entry to concat list
            entries.append(f"file '{trimmed_path}'\n")
            entries.append(f"inpoint 0\n")  
            entries.append(f"outpoint {duration}\n")

        except Exception as e:
            logger.warning(f"[TTS SEGMENT ERROR] {e}")

    # --------------------------------------------
    # CTA voiceover (optional)
    # --------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    if (
        cta_cfg.get("enabled")
        and cta_cfg.get("voiceover")
        and cta_cfg.get("text")
    ):
        cta_text = cta_cfg["text"].strip()
        cta_duration = float(cta_cfg.get("duration", 3.0))

        try:
            tts_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=cta_text,
            )

            with open(tts_path, "wb") as f:
                f.write(resp.read())

            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

            trim_cmd = [
                "ffmpeg", "-y",
                "-i", tts_path,
                "-t", str(cta_duration),
                "-af", "apad=pad_dur=10",
                "-c:a", "aac",
                trimmed_path
            ]
            subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            entries.append(f"file '{trimmed_path}'\n")
            entries.append(f"inpoint 0\n")
            entries.append(f"outpoint {cta_duration}\n")

        except Exception as e:
            logger.warning(f"[TTS CTA ERROR] {e}")

    # --------------------------------------------
    # No narration? Return None
    # --------------------------------------------
    if not entries:
        return None

    # --------------------------------------------
    # Write concat list
    # --------------------------------------------
    with open(concat_list, "w") as f:
        f.writelines(entries)

    # --------------------------------------------
    # Final merged TTS output path
    # --------------------------------------------
    final_tts_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c:a", "aac",
        "-b:a", "192k",
        final_tts_path,
    ]

    logger.info(f"[TTS] Merging {len(entries)//3} TTS segments…")
    subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return final_tts_path


# -----------------------------------------
# Background music (YAML: music: {enabled, file, volume})
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    """
    MEMORY-SAFE MUSIC BUILDER (FFmpeg-only)

    - Loads music file without MoviePy
    - Loops or trims using ffmpeg
    - Ensures exact total_duration alignment
    - Returns a temp file path, not an in-memory audio object
    """
    import subprocess, tempfile

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
        log_step(f"[MUSIC] Using file: {music_path}")

        # ----------------------------------------
        # Output temp file
        # ----------------------------------------
        out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

        # FFmpeg filter chain:
        # - apad: pad the audio if too short
        # - atrim: trim to exact total duration
        # - volume: apply music volume
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-i", music_path,
            "-af", f"apad=pad_dur=20,atrim=0:{total_duration},volume={music_volume}",
            "-c:a", "aac",
            "-b:a", "192k",
            out_path
        ]

        subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        return out_path

    except Exception as e:
        log_step(f"[MUSIC ERROR] {e}")
        return None


def _build_base_audio(base_video_path, total_duration):
    """
    MEMORY-SAFE BASE AUDIO EXTRACTOR

    - Extracts the video's original audio using FFmpeg
    - Never loads audio into RAM
    - Trims or pads to total_duration (important if TTS extended the video)
    - Returns a temp audio file path for FFmpeg mixing
    """
    import subprocess, tempfile

    if not os.path.exists(base_video_path):
        log_step(f"[AUDIO] Base video missing: {base_video_path}")
        return None

    # Output temp file
    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    # FFmpeg filter chain:
    # - apad: extends audio if shorter than video
    # - atrim: trims to exact duration if longer
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", base_video_path,
        "-vn",  # no video, extract audio only
        "-af", f"apad=pad_dur=20,atrim=0:{total_duration}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path
    ]

    subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return out_path



def edit_video(output_file="output_tiktok_final.mp4", optimized: bool = False):
    import subprocess, tempfile, os, json, shutil
    from assistant_log import log_step
    import yaml

    cfg = _load_config()
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    log_step("Building low-memory FFmpeg timeline…")

    # ------------------------------
    # Build clip list from YAML
    # ------------------------------
    def collect(c, is_last=False):
        return {
            "file": os.path.join(video_folder, c["file"]),
            "start": float(c.get("start_time", 0)),
            "duration": float(c.get("duration", 3)),
            "text": (c.get("text") or "").strip(),
            "is_last": is_last,
        }

    clips = [collect(cfg["first_clip"])]
    for m in cfg.get("middle_clips", []):
        clips.append(collect(m))
    clips.append(collect(cfg["last_clip"], is_last=True))


    # ------------------------------
    # 1. Trim each clip safely
    # ------------------------------
    trimmed_files = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    with open(trimlist, "w") as lf:
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

            vf = "scale=1080:-2,setsar=1"
            if clip["text"]:
                txt = clip["text"].replace(":", "\\:")
                vf += f",drawtext=text='{txt}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=h-200"

            trim_cmd = [
                "ffmpeg","-y",
                "-ss", str(clip["start"]),
                "-i", clip["file"],
                "-t", str(clip["duration"]),
                "-vf", vf,
                "-c:v","libx264",
                "-preset","veryfast",
                "-crf","20",
                "-an",
                trimmed_path
            ]

            log_step(f"[TRIM] {clip['file']} -> {trimmed_path}")
            subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")


    # ------------------------------
    # 2. Concat via demuxer
    # ------------------------------
    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

    concat_cmd = [
        "ffmpeg","-y",
        "-f","concat",
        "-safe","0",
        "-i", trimlist,
        "-c:v","libx264",
        "-preset","superfast" if optimized else "veryfast",
        "-crf","22",
        "-pix_fmt","yuv420p",
        concat_output
    ]

    log_step("[CONCAT] Merging all clips…")
    subprocess.run(concat_cmd)


    # ------------------------------
    # 3. CTA blur
    # ------------------------------
    final_video_source = concat_output

    cta_cfg = cfg.get("cta", {}) or {}
    if cta_cfg.get("enabled"):
        cta_text = (cta_cfg.get("text") or "").replace(":", "\\:")
        cta_dur = float(cta_cfg.get("duration", 3.0))

        blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

        vf = (
            f"split[v1][v2]; "
            f"[v1]trim=0:({-cta_dur}),setpts=PTS-STARTPTS[pre]; "
            f"[v2]trim=({-cta_dur}):,setpts=PTS-STARTPTS,boxblur=10[blur]; "
            f"[blur]drawtext=text='{cta_text}':fontcolor=white:fontsize=60:"
            f"x=(w-text_w)/2:y=h-200[blurtext]; "
            f"[pre][blurtext]concat=n=2:v=1:a=0[out]"
        )

        blur_cmd = [
            "ffmpeg","-y",
            "-i", concat_output,
            "-vf", vf,
            "-map","[out]",
            blurred
        ]

        log_step("[CTA] Applying blur+text…")
        subprocess.run(blur_cmd)

        final_video_source = blurred


    # ------------------------------
    # 4. AUDIO (FFmpeg-only)
    # ------------------------------
    total_duration = float(cfg["last_clip"]["start_time"] + cfg["last_clip"]["duration"])

    # Base Audio
    base_audio = _build_base_audio(final_video_source, total_duration)

    # TTS Audio
    tts_audio = _build_tts_audio(cfg)

    # Music
    music_audio = _build_music_audio(cfg, total_duration)

    mix_inputs = []
    mix_filters = []
    idx = 0

    def add(path, vol):
        nonlocal idx
        if not path:
            return
        mix_inputs.extend(["-i", path])
        mix_filters.append(f"[{idx}:a]volume={vol}[a{idx}]")
        idx += 1

    add(base_audio, 0.8)
    add(tts_audio, 1.0)
    add(music_audio, 0.25)

    if idx > 0:
        audio_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

        filter_complex = "; ".join(mix_filters) + "; " + "".join(
            f"[a{i}]" for i in range(idx)
        ) + f"amix=inputs={idx}:normalize=0[outa]"

        audio_cmd = [
            "ffmpeg","-y",
            *mix_inputs,
            "-filter_complex", filter_complex,
            "-map","[outa]",
            "-c:a","aac",
            audio_out
        ]

        log_step("[AUDIO] Mixing…")
        subprocess.run(audio_cmd)
    else:
        audio_out = None


    # ------------------------------
    # 5. Final MUX
    # ------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))

    mux_cmd = ["ffmpeg","-y","-i",final_video_source]

    if audio_out:
        mux_cmd.extend(["-i", audio_out, "-c:v","copy","-c:a","aac", final_output])
    else:
        mux_cmd.extend(["-c:v","copy", final_output])

    log_step("[MUX] Writing final video…")
    subprocess.run(mux_cmd)


    # Ensure file EXISTS
    if not os.path.exists(final_output):
        raise RuntimeError(f"Final output missing! {final_output}")

    log_step(f"[EXPORT] Video rendered: {final_output}")
    return final_output
