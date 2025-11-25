# tiktok_template.py
import os
import yaml
import difflib
import logging
import tempfile
import subprocess
from assistant_log import log_step
from typing import Dict, Any
# --- Pillow ANTIALIAS compatibility patch ---
from PIL import Image
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = getattr(_Image, "Resampling", _Image).LANCZOS
# --------------------------------------------
from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    TextClip,
    CompositeVideoClip,
    CompositeAudioClip,
    concatenate_videoclips,
    ImageClip,
)
from moviepy.config import change_settings
from moviepy.video.fx import all as vfx
from moviepy.audio.fx import audio_loop
from openai import OpenAI

# =============================================================
# Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, "tiktok_editor.log")

logging.basicConfig(
    level=logging.INFO,
    filename=LOG_PATH,
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# =============================================================
# OpenAI client
# =============================================================
api_key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
if not api_key:
    logger.warning("OPENAI_API_KEY / open_ai_api_key not set; LLM features may fail.")
client = OpenAI(api_key=api_key) if api_key else None

# =============================================================
# MoviePy / ImageMagick (for TextClip)
# =============================================================
if os.name == "nt":
    change_settings({
        "IMAGEMAGICK_BINARY": r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"
    })
else:
    change_settings({"IMAGEMAGICK_BINARY": "/opt/homebrew/bin/magick"})

# =============================================================
# Paths & config.yml
# =============================================================
DOWNLOADS_FOLDER_NAME = "tik_tok_downloads"
MUSIC_FOLDER_NAME = "music"

video_folder = os.path.join(BASE_DIR, DOWNLOADS_FOLDER_NAME)
music_folder = os.path.join(BASE_DIR, MUSIC_FOLDER_NAME)
os.makedirs(video_folder, exist_ok=True)
os.makedirs(music_folder, exist_ok=True)

config_path = os.path.join(BASE_DIR, "config.yml")

if not os.path.exists(config_path):
    logger.info("config.yml not found yet; it will be created by LLM.")
    config: Dict[str, Any] = {}
else:
    with open(config_path, "r") as f:
        config = yaml.safe_load(f) or {}

tempfile.tempdir = os.path.join(BASE_DIR, "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

# =============================================================
# FFmpeg helper
# =============================================================
def normalize_video_ffmpeg(src: str, dst: str) -> None:
    """
    Normalize video to 1080x1920 vertical using ffmpeg.
    Logs ALL stdout/stderr so crashes are visible.
    """
    log_step(f"[FFMPEG] Normalizing video {src} → {dst}")

    # FFMPEG command
    cmd = [
        "ffmpeg",
        "-y",                 # overwrite
        "-i", src,            # input file
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "copy",
        dst
    ]

    try:
        # Run process and capture ALL output
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Log stdout/stderr
        if result.stdout:
            log_step(f"[FFMPEG STDOUT for {src}] {result.stdout[:500]}")
        if result.stderr:
            log_step(f"[FFMPEG STDERR for {src}] {result.stderr[:500]}")

        # Check return code
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (code {result.returncode}) while processing {src}"
            )

        log_step(f"[FFMPEG] Completed normalization for {src}")

    except Exception as e:
        # Log full traceback to live log
        import traceback
        log_step(f"[FFMPEG ERROR] Failed to normalize {src}: {e}")
        log_step(traceback.format_exc())
        raise

# =============================================================
# Video helpers (MoviePy-based)
# =============================================================
def resolve_path(filename: str | None) -> str | None:
    """
    Resolves clip filenames in a case-insensitive way.

    - YAML may contain lower-case filenames
    - S3 uploads may retain original case
    - This function ensures we find the correct file
    """
    if not filename:
        return None

    # Exact path first
    full = os.path.join(video_folder, filename)
    if os.path.exists(full):
        return full

    # Case-insensitive fallback
    target_lower = filename.lower()
    for name in os.listdir(video_folder):
        if name.lower() == target_lower:
            fixed_full = os.path.join(video_folder, name)
            logging.info(f"✅ resolve_path matched case-insensitive: {name}")
            return fixed_full

    logging.error(f"❌ resolve_path could not find file for: {filename}")
    return None


def fake_blur(clip, amount: int = 4):
    small = clip.resize(1.0 / amount)
    return small.resize(clip.size)


def force_even_size(clip):
    w, h = clip.size
    new_w = w if w % 2 == 0 else w - 1
    new_h = h if h % 2 == 0 else h - 1
    return clip.resize((new_w, new_h))


def make_clip(
    path: str,
    duration: float | None = None,
    start_time: float = 0,
    text: str = "",
    text_color: str = "white",
    scale: float = 1.0,
    voice: str | None = None,
):
    """
    Build one vertical clip with:
    - blurred background
    - foreground scaled/cropped
    - optional caption text
    - optional per-clip TTS

    IMPORTANT: With Option A, `path` is already a normalized 1080x1920-ish
    file produced during /api/analyze. We do NOT run ffmpeg here anymore.
    """
    if not os.path.exists(path):
        raise ValueError(f"File not found: {path}")

    # ✅ No ffmpeg here – just open the already-normalized file
    clip = VideoFileClip(path)

    # Fix rotation from iPhone, etc.
    if hasattr(clip, "rotation") and clip.rotation in [90, 270]:
        clip = clip.rotate(-clip.rotation)

    # Trim to segment
    if duration is None:
        duration = clip.duration
    duration = float(duration)
    base = clip.subclip(start_time, min(start_time + duration, clip.duration))

    target_w, target_h = 1080, 1920

    # Background blur
    bg = base.resize(height=target_h)
    if bg.w < target_w:
        bg = bg.resize(width=target_w)
    bg = bg.crop(
        x_center=bg.w / 2,
        y_center=bg.h / 2,
        width=target_w,
        height=target_h,
    )
    bg = fake_blur(bg, amount=12).set_opacity(0.75)

    # Foreground
    fg = base
    w, h = fg.size
    if w / h > 1.2:
        fg = fg.resize(height=target_h)
        if fg.w > target_w:
            fg = fg.crop(x_center=fg.w / 2, width=target_w, height=target_h)
    else:
        fg = fg.resize(width=target_w)
        if fg.h > target_h:
            fg = fg.crop(y_center=fg.h / 2, height=target_h)

    global_scale = float(config.get("render", {}).get("fg_scale_default", 1.0))
    effective_scale = float(scale) * global_scale
    if effective_scale != 1.0:
        fg = fg.resize(effective_scale)

    fg = fg.set_position("center")

    final = CompositeVideoClip([bg, fg]).set_duration(base.duration)

    # Caption text
    if text:
        txt = TextClip(
            text,
            fontsize=50,
            color=text_color,
            method="caption",
            size=(int(target_w * 0.9), None),
        )
        txt = txt.set_position(("center", target_h - txt.h - 50))
        final = CompositeVideoClip([final, txt]).set_duration(final.duration)

    # OPTIONAL: per-clip TTS (if enabled)
    tts_enabled = config.get("render", {}).get("tts_enabled", False)
    if tts_enabled and text and client:
        try:
            norm_dir = os.path.join(BASE_DIR, "normalized_cache")
            os.makedirs(norm_dir, exist_ok=True)
            voice_name = voice or config.get("render", {}).get("tts_voice", "alloy")
            tts_path = os.path.join(norm_dir, f"{os.path.basename(path)}_voice.mp3")

            logging.info(f"Generating TTS ({voice_name}) for: {os.path.basename(path)}")

            speech = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice_name,
                input=text,
            )
            speech.stream_to_file(tts_path)

            voiceover = AudioFileClip(tts_path)

            if voiceover.duration > final.duration:
                freeze_time = final.duration - 0.05
                extend_by = voiceover.duration - final.duration + 0.05
                final = final.fx(
                    vfx.freeze,
                    t=freeze_time,
                    freeze_duration=extend_by,
                )

            final = final.set_duration(voiceover.duration)
            final = final.set_audio(voiceover)
        except Exception as e:
            logging.error(f"Voiceover failed for {path}: {e}")

    return force_even_size(final)


def debug_video_dimensions(folder: str):
    logging.debug("\n🎥 Debug: Video Dimensions Overview\n" + "-" * 50)
    for f in sorted(os.listdir(folder)):
        if f.lower().endswith((".mp4", ".mov", ".avi", ".m4v")):
            path = os.path.join(folder, f)
            try:
                clip = VideoFileClip(path)
                rotation = getattr(clip, "rotation", 0)
                w, h = clip.size
                ratio = round(w / h, 3)
                orientation = "Portrait" if h > w else "Landscape"
                if 0.8 <= ratio <= 1.2:
                    orientation = "Square"
                logging.debug(
                    f"{f:25} | {w}x{h} | ratio={ratio} | rot={rotation}° | {orientation}"
                )
                clip.close()
            except Exception as e:
                logging.error(f"⚠️ Error reading {f}: {e}")
    logging.debug("-" * 50)

# =============================================================
# Music
# =============================================================
def auto_select_music(music_meta: Dict[str, Any]) -> str | None:
    if not os.path.exists(music_folder):
        logging.warning("⚠️ No music folder found.")
        return None

    music_files = [
        f for f in os.listdir(music_folder)
        if f.lower().endswith((".mp3", ".wav", ".m4a"))
    ]
    if not music_files:
        logging.warning("⚠️ No audio files found in /music folder.")
        return None

    style = (music_meta.get("style", "") or "").lower()
    mood = (music_meta.get("mood", "") or "").lower()

    scores: Dict[str, float] = {}
    for f in music_files:
        name = f.lower()
        score = 0.0
        for w in style.split():
            if w in name:
                score += 2
        for w in mood.split():
            if w in name:
                score += 1
        score += difflib.SequenceMatcher(None, name, style).ratio()
        score += difflib.SequenceMatcher(None, name, mood).ratio()
        scores[f] = score

    best = max(scores, key=scores.get)
    return os.path.join(music_folder, best)

# =============================================================
# Main render
# =============================================================
def edit_video(output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    global config

    if optimized:
        ffmpeg_flags = "-preset veryfast -crf 28"
    else:
        ffmpeg_flags = "-preset slow -crf 18"

    # Reload config
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    clips = []

    def build_clip(cfg: Dict[str, Any]):
        file = resolve_path(cfg.get("file"))
        if not file:
            raise ValueError(f"Invalid file: {cfg.get('file')}")
        clean = cfg.copy()
        clean.pop("file", None)
        return make_clip(file, **clean)

    first_cfg = config.get("first_clip", {})
    if first_cfg:
        clips.append(build_clip(first_cfg))

    for mc in config.get("middle_clips", []):
        clips.append(build_clip(mc))

    last_cfg = config.get("last_clip", {})
    if last_cfg:
        clips.append(build_clip(last_cfg))

    if not clips:
        raise RuntimeError("No clips built — check your config.yml.")

    processed = []
    for c in clips:
        w, h = c.size
        if (w, h) != (1080, 1920):
            logging.debug(f"Resizing clip from {w}x{h} → 1080x1920")
            c = c.resize((1080, 1920))
        processed.append(force_even_size(c))

    # Optional CTA
    cta_cfg = config.get("cta", {})
    if cta_cfg.get("enabled"):
        logging.info("📣 Adding CTA clip...")
        cta_text = cta_cfg.get("text", "")
        cta_color = cta_cfg.get("text_color", "white")
        cta_duration = float(cta_cfg.get("duration", 3.0))
        cta_position = cta_cfg.get("position", "bottom")
        cta_voiceover = bool(cta_cfg.get("voiceover", False))

        last_clip = processed[-1]
        last_frame = last_clip.get_frame(last_clip.duration - 1e-3)
        base = ImageClip(last_frame).set_duration(cta_duration)

        bg = base.resize(height=1920)
        if bg.w < 1080:
            bg = bg.resize(width=1080)
        bg = bg.crop(x_center=bg.w / 2, y_center=bg.h / 2, width=1080, height=1920)
        bg = fake_blur(bg, amount=12).set_opacity(0.75)

        txt = TextClip(
            cta_text,
            fontsize=60,
            color=cta_color,
            method="caption",
            size=(int(1080 * 0.9), None),
        )
        if cta_position == "top":
            y_pos = 100
        elif cta_position == "center":
            y_pos = (1920 - txt.h) // 2
        else:
            y_pos = 1920 - txt.h - 150

        txt = txt.set_position(("center", y_pos))
        cta_clip = CompositeVideoClip([bg, txt]).set_duration(cta_duration)

        # Optional CTA TTS
        if cta_voiceover and cta_text and client:
            try:
                tts_path = os.path.join(BASE_DIR, "normalized_cache", "cta_voice.mp3")
                speech = client.audio.speech.create(
                    model="gpt-4o-mini-tts",
                    voice=config.get("render", {}).get("tts_voice", "alloy"),
                    input=cta_text,
                )
                speech.stream_to_file(tts_path)
                cta_vo = AudioFileClip(tts_path)

                if cta_vo.duration > cta_clip.duration:
                    extra = cta_vo.duration - cta_clip.duration + 0.05
                    cta_clip = cta_clip.fx(
                        vfx.freeze,
                        t=cta_clip.duration - 0.05,
                        freeze_duration=extra,
                    )
                    cta_clip = cta_clip.set_duration(cta_vo.duration)
                else:
                    cta_clip = cta_clip.set_duration(cta_vo.duration)

                cta_clip = cta_clip.set_audio(cta_vo)
                logging.info("CTA voiceover added.")
            except Exception as e:
                logging.error(f"CTA voiceover failed: {e}")

        processed.append(force_even_size(cta_clip))

    # Concatenate
    logging.info("🔧 Concatenating clips...")
    try:
        final = concatenate_videoclips(processed, method="compose")
    except Exception:
        final = concatenate_videoclips(processed, method="chain")

    # Music
    music_cfg = config.get("music", {})
    music_path = auto_select_music(music_cfg)
    if music_path:
        logging.info(f"🎵 Music selected: {music_path}")
        music = AudioFileClip(music_path)
        if music.duration < final.duration:
            music = audio_loop(music, duration=final.duration)
        else:
            music = music.subclip(0, final.duration)

        vol = float(music_cfg.get("volume", 0.25))
        if config.get("render", {}).get("tts_enabled", False) and final.audio:
            vol *= 0.4
        music = music.volumex(vol).audio_fadein(1).audio_fadeout(1)

        if final.audio:
            mixed = CompositeAudioClip([final.audio, music])
            final = final.set_audio(mixed)
        else:
            final = final.set_audio(music)

    final.write_videofile(
        output_file,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        ffmpeg_params=[
            "-pix_fmt", "yuv420p",
            "-vf", "scale=1080:1920",
        ] + ffmpeg_flags.split(),
    )

    logging.info(f"✅ Export done → {output_file}")


if __name__ == "__main__":
    try:
        edit_video()
    except Exception as e:
        logger.exception(f"❌ An error occurred: {e}")
