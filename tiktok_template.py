import os
import yaml
import difflib
from moviepy.editor import (
    VideoFileClip, AudioFileClip,
    TextClip, CompositeVideoClip,CompositeAudioClip,
    concatenate_videoclips, ImageClip

)
from openai import OpenAI
import srt
from moviepy.config import change_settings
from moviepy.video.fx import all as vfx
from moviepy.audio.fx import audio_loop
import logging # Add this line
import subprocess
import datetime
from dotenv import load_dotenv
import tempfile


# =============================================================
# Configure Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
assistant_log_dir  = os.path.join(BASE_DIR, "logs") # Define the directory path
assistant_log_file_path  = os.path.join(assistant_log_dir , "tiktok_editor.log")
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key)


if not os.path.exists(assistant_log_dir ):
    os.makedirs(assistant_log_dir , exist_ok=True)

logging.basicConfig(
    level=logging.INFO, # Set to logging.DEBUG for more verbose output
    filename=assistant_log_file_path,
    filemode='a', 
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# =============================================================
# RENDER MODE (toggle between "high" and "fast")
# =============================================================
RENDER_MODE = "high"   # change to "fast" for preview mode


# =============================================================
# CONFIG
# =============================================================
if os.name == "nt":
    change_settings({"IMAGEMAGICK_BINARY": r"C:\Program Files\ImageMagick-7.1.2-Q16-HDRI\magick.exe"})
else:
    change_settings({"IMAGEMAGICK_BINARY": "/opt/homebrew/bin/magick"})


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
music_folder = os.path.join(BASE_DIR, "music")

logger.info(f"Resolved video folder: {video_folder}")

config_path = os.path.abspath("config.yml")
logger.info(f"Using config file: {config_path}")

if not os.path.exists(config_path):
    logging.error(f"❌ Config file not found at: {config_path}")
    raise FileNotFoundError(f"❌ Config file not found at: {config_path}")

with open(config_path, "r") as f:
    config = yaml.safe_load(f)

if not isinstance(config, dict):
    logging.error("❌ Config file is not valid YAML or is empty.")
    raise ValueError("❌ Config file is not valid YAML or is empty.")

logger.debug("Loaded config:", config)

# Load video files
video_files = sorted([
    os.path.join(video_folder, f)
    for f in os.listdir(video_folder)
    if f.lower().endswith((".mp4", ".mov", ".avi"))
])


# =============================================================
# HELPERS
# =============================================================

import subprocess

def normalize_video_ffmpeg(input_path, output_path):
    """
    Normalize video to 1080x1920 without stretching.
    Keeps original aspect ratio and crops/pads as needed.
    """
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", "scale=1080:-2:force_original_aspect_ratio=increase,crop=1080:1920",
            "-c:a", "copy", output_path
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return output_path
    except Exception as e:
        logging.warning(f"❌ FFmpeg normalization failed for {input_path}: {e}")
        return input_path


def resolve_path(filename):
    """Return absolute path to a video file inside tik_tok_downloads."""
    if not filename:
        return None

    full = os.path.join(video_folder, filename)

    if not os.path.exists(full):
        logging.warning(f"❌ File does NOT exist: {full}")
        return None

    return full

def fake_blur(clip, amount=4):
    # shrink >> enlarge creates a smooth blur effect
    small = clip.resize(1.0 / amount)
    return small.resize(clip.size)


# Updated force_even_size to just do its job without extra comments/confusion
def force_even_size(clip):
    w, h = clip.size
    new_w = w if w % 2 == 0 else w - 1
    new_h = h if h % 2 == 0 else h - 1
    return clip.resize((new_w, new_h))

def make_clip(path, duration=None, start_time=0, text="", text_color="white",
              scale=1.0, voice=None):
    import datetime, srt

    if not os.path.exists(path):
        raise ValueError(f"File not found: {path}")

    # 1) Normalize via ffmpeg to 1080x1920
    norm_path = os.path.join("normalized_cache", os.path.basename(path))
    os.makedirs("normalized_cache", exist_ok=True)
    if not os.path.exists(norm_path):
        logging.info(f"Normalizing {path} → {norm_path}")
        normalize_video_ffmpeg(path, norm_path)

    clip = VideoFileClip(norm_path).set_audio(None)

    # Fix iPhone rotation if present
    if hasattr(clip, "rotation") and clip.rotation in [90, 270]:
        clip = clip.rotate(-clip.rotation)

    # Trim base clip
    duration = duration or clip.duration
    duration = float(duration)
    base = clip.subclip(start_time, min(start_time + duration, clip.duration))

    # 2) Background + foreground
    target_w, target_h = 1080, 1920

    # Background
    bg = base.resize(height=target_h)
    if bg.w < target_w:
        bg = bg.resize(width=target_w)
    bg = bg.crop(x_center=bg.w/2, y_center=bg.h/2, width=target_w, height=target_h)
    bg = fake_blur(bg, amount=12).set_opacity(0.75)

    # Foreground
    fg = base
    w, h = fg.size
    if w / h > 1.2:
        fg = fg.resize(height=target_h)
        if fg.w > target_w:
            fg = fg.crop(x_center=fg.w/2, width=target_w, height=target_h)
    else:
        fg = fg.resize(width=target_w)
        if fg.h > target_h:
            fg = fg.crop(y_center=fg.h/2, height=target_h)

    # Global + per-clip scale
    global_scale = float(config.get("render", {}).get("fg_scale_default", 1.0))
    effective_scale = float(scale) * global_scale
    if effective_scale != 1.0:
        fg = fg.resize(effective_scale)

    fg = fg.set_position("center")

    final = CompositeVideoClip([bg, fg]).set_duration(base.duration)

    # 3) Text overlay
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

    # 4) Optional TTS voiceover
    tts_enabled = config.get("render", {}).get("tts_enabled", False)
    if tts_enabled and text:
        try:
            # clip-level voice override > global voice
            voice_name = voice or config.get("render", {}).get("tts_voice", "alloy")
            tts_path = os.path.join("normalized_cache", f"{os.path.basename(path)}_voice.mp3")

            logging.info(f"Generating TTS ({voice_name}) for: {os.path.basename(path)}")

            speech = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice_name,
                input=text
            )
            speech.stream_to_file(tts_path)

            voiceover = AudioFileClip(tts_path)

            # --- Ensure clip stays visible until TTS finishes ---
            # Freeze last frame instead of showing black screen
            if voiceover.duration > final.duration:
                freeze_time = final.duration - 0.05  # a frame before the end
                extend_by = voiceover.duration - final.duration + 0.05
                final = final.fx(vfx.freeze, t=freeze_time, freeze_duration=extend_by)

            final = final.set_duration(voiceover.duration)
            final = final.set_audio(voiceover)


        except Exception as e:
            logging.error(f"Voiceover failed for {path}: {e}")

    return force_even_size(final)


def debug_video_dimensions(video_folder):
    """Print dimensions, aspect ratio, and orientation for each clip."""
    logging.debug("\n🎥 Debug: Video Dimensions Overview\n" + "-" * 50)
    for f in sorted(os.listdir(video_folder)):
        if f.lower().endswith((".mp4", ".mov", ".avi")):
            path = os.path.join(video_folder, f)
            try:
                clip = VideoFileClip(path)
                rotation = getattr(clip, "rotation", 0)
                w, h = clip.size
                ratio = round(w / h, 3)
                orientation = "Portrait" if h > w else "Landscape"
                if 0.8 <= ratio <= 1.2:
                    orientation = "Square"

                logging.debug(f"{f:25} | {w}x{h} | ratio={ratio} | rot={rotation}° | {orientation}")

                clip.close()
            except Exception as e:
                logging.error(f"⚠️ Error reading {f}: {e}")
    logging.debug("-" * 50)

# =============================================================
# AUTO-SELECT MUSIC BASED ON STYLE + MOOD
# =============================================================
os.makedirs(music_folder, exist_ok=True)
def auto_select_music(music_meta):
    """Pick the best matching song from /music folder."""
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

    style = music_meta.get("style", "").lower()
    mood = music_meta.get("mood", "").lower()

    scores = {}

    for f in music_files:
        name = f.lower()
        score = 0

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

tempfile.tempdir = os.path.join(BASE_DIR, "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

def edit_video(output_file="output_tiktok_final_cinematic.mp4"):
    global config

    # Reload config before every render
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f) or {}
        logging.info("🔁 Reloaded config.yml before rendering.")
    except Exception as e:
        logging.error(f"Failed to reload config.yml: {e}")

    clips = []

    # -----------------------------
    # Helper to build each clip
    # -----------------------------
    def build_clip(cfg: dict):
        file = resolve_path(cfg.get("file"))
        if not file:
            raise ValueError(f"Invalid file: {cfg.get('file')}")
        clean_cfg = cfg.copy()
        clean_cfg.pop("file", None)
        return make_clip(file, **clean_cfg)

    # -----------------------------
    # FIRST / MIDDLE / LAST CLIPS
    # -----------------------------
    first_cfg = config.get("first_clip", {})
    if first_cfg:
        clips.append(build_clip(first_cfg))

    for mc in config.get("middle_clips", []):
        clips.append(build_clip(mc))

    last_cfg = config.get("last_clip", {})
    if last_cfg:
        clips.append(build_clip(last_cfg))

    if not clips:
        raise RuntimeError("No clips built — check your config.yml (first/middle/last).")

    # -----------------------------
    # Normalize sizes to 1080x1920
    # -----------------------------
    processed = []
    for i, c in enumerate(clips, start=1):
        w, h = c.size
        if (w, h) != (1080, 1920):
            logging.debug(f"Resizing clip {i} from {w}x{h} → 1080x1920")
            c = c.resize((1080, 1920))
        processed.append(force_even_size(c))

    # ============================================================
    # OPTIONAL GLOBAL CTA CLIP (added at the very end)
    # ============================================================
    cta_cfg = config.get("cta", {})
    if cta_cfg.get("enabled", False):
        logging.info("📣 Adding CTA clip...")

        cta_text      = cta_cfg.get("text", "")
        cta_color     = cta_cfg.get("text_color", "white")
        cta_duration  = float(cta_cfg.get("duration", 3.0))
        cta_position  = cta_cfg.get("position", "bottom")
        cta_voiceover = bool(cta_cfg.get("voiceover", False))

        # Use last frame of the last processed clip
        last_clip = processed[-1]
        last_frame = last_clip.get_frame(last_clip.duration - 1e-3)
        base = ImageClip(last_frame).set_duration(cta_duration)

        # Blurred background
        bg = base.resize(height=1920)
        if bg.w < 1080:
            bg = bg.resize(width=1080)
        bg = bg.crop(x_center=bg.w / 2, y_center=bg.h / 2, width=1080, height=1920)
        bg = fake_blur(bg, amount=12).set_opacity(0.75)

        # CTA text
        txt = TextClip(
            cta_text,
            fontsize=60,
            color=cta_color,
            method="caption",
            size=(int(1080 * 0.9), None),
        )

        y_pos = {
            "top": 100,
            "center": (1920 - txt.h) // 2,
            "bottom": 1920 - txt.h - 150,
        }.get(cta_position, 1920 - txt.h - 150)

        txt = txt.set_position(("center", y_pos))

        cta_clip = CompositeVideoClip([bg, txt]).set_duration(cta_duration)

        # ---- Optional CTA Voiceover ----
        if cta_voiceover and cta_text:
            try:
                tts_path = os.path.join("normalized_cache", "cta_voice.mp3")
                speech = client.audio.speech.create(
                    model="gpt-4o-mini-tts",
                    voice=config.get("render", {}).get("tts_voice", "alloy"),
                    input=cta_text,
                )
                speech.stream_to_file(tts_path)

                cta_vo = AudioFileClip(tts_path)

                # If TTS is longer than the base CTA duration, freeze last frame
                if cta_vo.duration > cta_clip.duration:
                    extra = cta_vo.duration - cta_clip.duration + 0.05
                    cta_clip = cta_clip.fx(
                        vfx.freeze,
                        t=cta_clip.duration - 0.05,
                        freeze_duration=extra
                    )
                    cta_clip = cta_clip.set_duration(cta_vo.duration)
                else:
                    # If CTA is longer, just trim visually to TTS
                    cta_clip = cta_clip.set_duration(cta_vo.duration)

                cta_clip = cta_clip.set_audio(cta_vo)
                logging.info("CTA voiceover added successfully.")
            except Exception as e:
                logging.error(f"CTA voiceover failed: {e}")

        # Append CTA as last clip
        processed.append(force_even_size(cta_clip))

    # -----------------------------
    # CONCATENATE all clips (video + audio already inside each)
    # -----------------------------
    logging.info("🔧 Concatenating clips...")
    try:
        final = concatenate_videoclips(processed, method="compose")
    except Exception:
        final = concatenate_videoclips(processed, method="chain")

    # -----------------------------
    # ADD BACKGROUND MUSIC
    # -----------------------------
    music_cfg = config.get("music", {})
    music_path = auto_select_music(music_cfg)

    if music_path:
        logging.info(f"🎵 Music selected: {music_path}")
        music = AudioFileClip(music_path)

        if music.duration < final.duration:
            music = audio_loop(music, duration=final.duration)
        else:
            music = music.subclip(0, final.duration)

        vol = music_cfg.get("volume", 0.25)
        if config.get("render", {}).get("tts_enabled", False) and final.audio:
            vol *= 0.4  # duck music under voiceover

        music = music.volumex(vol).audio_fadein(1).audio_fadeout(1)

        if final.audio:
            mixed = CompositeAudioClip([final.audio, music])
            final = final.set_audio(mixed)
        else:
            final = final.set_audio(music)

    # -----------------------------
    # EXPORT
    # -----------------------------
    final.write_videofile(
        output_file,
        fps=24,
        codec="libx264",
        audio_codec="aac",
        preset="medium",
        bitrate="5000k",
        threads=4,
        ffmpeg_params=["-pix_fmt", "yuv420p", "-vf", "scale=1080:1920"],
    )

    logging.info(f"✅ Export done → {output_file}")

# =============================================================
# RUN
# =============================================================
if __name__ == "__main__":
    try:
        edit_video()
    except Exception as e:
        logger.exception(f"❌ An error occurred: {e}")
