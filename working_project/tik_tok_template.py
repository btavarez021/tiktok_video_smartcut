import os
import yaml
import difflib
from moviepy.editor import (
    VideoFileClip, AudioFileClip,
    TextClip, CompositeVideoClip,
    concatenate_videoclips
)
from moviepy.config import change_settings
from moviepy.video.fx import all as vfx
from moviepy.audio.fx import audio_loop
import logging # Add this line
import subprocess


# =============================================================
# Configure Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
log_dir = os.path.join(BASE_DIR, "logs") # Define the directory path
log_file_path = os.path.join(log_dir, "tiktok_editor.log")

if not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO, # Set to logging.DEBUG for more verbose output
    filename=log_file_path,
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
        print(f"❌ FFmpeg normalization failed for {input_path}: {e}")
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


def force_even_size(clip):
    """Ensure final video dimensions are even numbers to prevent encoding errors."""
    w, h = clip.size
    even_w = int(w) if int(w) % 2 == 0 else int(w) - 1
    even_h = int(h) if int(h) % 2 == 0 else int(h) - 1

    if (even_w, even_h) != (w, h):
        clip = clip.resize((even_w, even_h))
        logging.info(f"Adjusted clip size to even dimensions: {even_w}x{even_h}")

    return clip


# Updated force_even_size to just do its job without extra comments/confusion
def force_even_size(clip):
    w, h = clip.size
    new_w = w if w % 2 == 0 else w - 1
    new_h = h if h % 2 == 0 else h - 1
    return clip.resize((new_w, new_h))

def make_clip(path, duration=None, start_time=0, text="", text_color="white", scale=1.0):
    # ... (all existing code up to the final return statement) ...
    if not os.path.exists(path):
        logging.error(f"File not found: {path}")
        raise ValueError(f"File not found: {path}")
    
     #--- Normalize to 1080x1920 before processing ---
    normalized_path = os.path.join("normalized_cache", os.path.basename(path))
    os.makedirs("normalized_cache", exist_ok=True)

    if not os.path.exists(normalized_path):
        print(f"⚙️ Normalizing {os.path.basename(path)} to 1080x1920...")
        path = normalize_video_ffmpeg(path, normalized_path)
    else:
        path = normalized_path

    clip = VideoFileClip(path)

    # --- FIX iPHONE ROTATION ---
    if hasattr(clip, "rotation") and clip.rotation in [90, 270]:
        clip = clip.rotate(-clip.rotation)

    # --- BASIC TRIM LOGIC ---
    start_time = max(0, min(start_time, clip.duration - 0.1))
    end_time = min(start_time + (duration or clip.duration), clip.duration)
    base = clip.subclip(start_time, end_time).set_duration(end_time - start_time)

    # --- TARGET OUTPUT DIMENSIONS ---
    target_w, target_h = 1080, 1920
    final_clip = None # Initialize final_clip variable

    # --- RENDER MODE SWITCH ---
    if RENDER_MODE.lower() == "fast":
        # FAST MODE: Simple resize to fill the canvas, might stretch if source aspect is weird
        fg = base.resize(scale) if scale != 1.0 else base
        fg = fg.resize(width=target_w, height=target_h) # Force standard size
        final_clip = fg

    else:
        # HIGH/CINEMATIC MODE: Blurred background + centered foreground 
        # ... (BG/FG logic as before, which already results in 1080x1920 composite) ...
        bg = base.resize(height=target_h)
        if bg.w < target_w: bg = bg.resize(width=target_w)
        bg = bg.crop(x_center=bg.w / 2, y_center=bg.h / 2, width=target_w, height=target_h)
        # Assuming fake_blur returns a clip of the same size as input
        bg = fake_blur(bg, amount=12).set_opacity(0.75)
        
        # ... (Foreground logic to fit aspect ratio without cropping content) ...
        fg = base
        canvas_aspect = target_w / target_h
        w, h = fg.size
        clip_aspect = w / h
        if clip_aspect > canvas_aspect: fg = fg.resize(width=target_w)
        else: fg = fg.resize(height=target_h)
        if scale != 1.0: 
            fg = fg.resize(scale)
            if fg.w > target_w or fg.h > target_h:
                 logging.warning("User scale caused foreground to exceed canvas dimensions. Clipping.")
                 fg = fg.crop(x_center=fg.w / 2, y_center=fg.h / 2, width=target_w, height=target_h)

        # Read optional global foreground scale
        default_fd_scale = config.get("render", {}).get("fg_scale_default", 1.0)

        #Apply user or global scale
        effective_scale = scale * default_fd_scale if scale else default_fd_scale
        fg = fg.resize(effective_scale)

        fg = fg.set_position(("center", "center"))
        video_composite = CompositeVideoClip([bg, fg]).set_duration(fg.duration)

        # --- ADD TEXT (OPTIONAL) ---
        if text:
            # ... (text logic as before) ...
            txt = TextClip(text, fontsize=50, color=text_color, method="caption", size=(int(video_composite.w * 0.9), None))
            video_height = video_composite.h
            text_height = txt.h
            margin = 50
            y_pos = video_height - text_height - margin
            txt = txt.set_position(("center", y_pos))
            final_clip = CompositeVideoClip([video_composite, txt]).set_duration(video_composite.duration)
        else:
            final_clip = video_composite

    # # --- FINAL SAFETY CHECK (Ensures pixel values are even for FFmpeg compatibility) ---
    return force_even_size(final_clip)


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

                logging.debug(print(f"{f:25} | {w}x{h} | ratio={ratio} | rot={rotation}° | {orientation}"))

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

import tempfile
tempfile.tempdir = os.path.join(BASE_DIR, "temp")
os.makedirs(tempfile.tempdir, exist_ok=True)

# =============================================================
# MAIN EDITOR
# =============================================================
def edit_video(output_file="output_tiktok_final_cinematic.mp4"):
    clips_to_concat = []

    # -------------------------
    # FIRST CLIP
    # -------------------------
    first_cfg = config.get("first_clip", {})
    first_path = resolve_path(first_cfg.get("file")) or video_files[0]

    first_clip = make_clip(
        path=first_path,
        duration=first_cfg.get("duration"),
        start_time=first_cfg.get("start_time", 0),
        text=first_cfg.get("text", ""),
        text_color=first_cfg.get("text_color", "white"),
        scale=first_cfg.get("scale", 1.0)
    )
    clips_to_concat.append(first_clip)

    # -------------------------
    # MIDDLE CLIPS
    # -------------------------
    for mid_cfg in config.get("middle_clips", []):
        mid_path = resolve_path(mid_cfg.get("file"))
        if not mid_path:
            logging.error(f"Invalid file in YAML: {mid_cfg.get('file')}")
            raise ValueError(f"Invalid file in YAML: {mid_cfg.get('file')}")

        clip = make_clip(
            path=mid_path,
            duration=mid_cfg.get("duration"),
            start_time=mid_cfg.get("start_time", 0),
            text=mid_cfg.get("text", ""),
            text_color=mid_cfg.get("text_color", "white"),
            scale=mid_cfg.get("scale", 1.0)
        )
        clips_to_concat.append(clip)

    # -------------------------
    # LAST CLIP
    # -------------------------
    last_cfg = config.get("last_clip", {})
    last_path = resolve_path(last_cfg.get("file")) or video_files[-1]

    last_clip = make_clip(
        path=last_path,
        duration=last_cfg.get("duration"),
        start_time=last_cfg.get("start_time", 0),
        text=last_cfg.get("text", ""),
        text_color=last_cfg.get("text_color", "yellow"),
        scale=last_cfg.get("scale", 1.0)
    )
    clips_to_concat.append(last_clip)

     # --- CRITICAL DEBUGGING & FINAL RESIZING LOOP ---
    target_w, target_h = 1080, 1920
    processed_clips = []
    for i, clip in enumerate(clips_to_concat):
        current_w, current_h = clip.size
        logging.debug(f"Clip {i+1} original size: {current_w}x{current_h}")

        # Force the clip to the exact target size if it isn't already
        if current_w != target_w or current_h != target_h:
            logging.warndebuging(f"Resizing Clip {i+1} from {clip.size} to {(target_w, target_h)}")
            clip = clip.resize(newsize=(target_w, target_h))
            
        # Ensure it meets the even pixel requirement
        clip = force_even_size(clip)
        
        logging.debug(f"Clip {i+1} final size: {clip.size}")
        processed_clips.append(clip)
    # --- END CRITICAL DEBUGGING & FINAL RESIZING LOOP ---

    # -------------------------
    # CONCATENATE ALL CLIPS
    # -------------------------
    logging.info("\n🔧 Rendering final video...\n")
    try:
        final_clip = concatenate_videoclips(processed_clips, method="chain")
    except Exception:
        final_clip = concatenate_videoclips(processed_clips, method="compose")


    # -------------------------
    # MUSIC SELECTION
    # -------------------------

     # MUSIC
    music_cfg = config.get("music", {})
    music_path = auto_select_music(music_cfg)

    if music_path:
        logging.info(f"🎵 Auto-selected music: {music_path}")

        music = AudioFileClip(music_path)

        if music.duration < final_clip.duration:
            music = audio_loop(music, duration=final_clip.duration)  # ✅ FIXED
        else:
            music = music.subclip(0, final_clip.duration)

        music = music.volumex(music_cfg.get("volume", 0.25))
        music = music.audio_fadein(1).audio_fadeout(1)

        final_clip = final_clip.set_audio(music)
    else:
        logging.info("ℹ️ No matching music found.")

    # -------------------------
    # EXPORT VIDEO
    # -------------------------
    final_clip.write_videofile(
    output_file,
    fps=24,
    codec="libx264",
    audio_codec="aac",
    preset="medium",
    bitrate="5000k",
    threads=4,
    verbose=True,
    logger='bar',
    ffmpeg_params=[
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.1",
        "-vf", "scale=1080:1920",
        "-aspect", "9:16"
    ]
)

    logging.info(f"\n✅ Video exported to: {output_file}")


# =============================================================
# RUN
# =============================================================
if __name__ == "__main__":
    try:
        edit_video()
    except Exception as e:
        logger.exception(f"❌ An error occurred: {e}")
