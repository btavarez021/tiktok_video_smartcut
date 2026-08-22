# tiktok_template.py — MOV/MP4 SAFE, LOW-MEMORY, NO CIRCULAR IMPORTS
from config_store import load_config, normalize_config
import os
import logging
import subprocess
import tempfile
from typing import Optional, List, Dict, Any
import yaml
import numpy as np
from PIL import Image, ImageFilter
import imageio_ffmpeg
from assistant_log import log_step
from s3_config import s3, S3_BUCKET_NAME, RAW_PREFIX
from google import genai
from google.genai import types
import wave

# Pillow compatibility shim
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

logger = logging.getLogger(__name__)
import re

# -----------------------------------------
# Emoji stripping (FFmpeg drawtext safe)
# -----------------------------------------
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE
)

# ============================================================
# 1. CONFIG LOADING / NORMALIZATION
# ============================================================
def load_render_config(session_id: str) -> Dict[str, Any]:
    cfg = normalize_config(load_config(session_id))
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    render = cfg.setdefault("render", {})
    captions_mode = render.setdefault("captions_mode", "all")
    render.setdefault("narration_mode", captions_mode)

    return cfg


# -----------------------------------------
# Paths / Globals
# -----------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")
os.makedirs(video_folder, exist_ok=True)

MUSIC_DIR = os.path.join(BASE_DIR, "music")
os.makedirs(MUSIC_DIR, exist_ok=True)

TARGET_W = 1080
TARGET_H = 1920

# -----------------------------------------
# Simple Gaussian blur via Pillow
# -----------------------------------------
def blur_frame(frame, radius: int = 18):
    """Blur a single RGB frame using Pillow (kept for future use)."""
    try:
        img = Image.fromarray(frame)
        img = img.filter(ImageFilter.GaussianBlur(radius=radius))
        return np.array(img)
    except Exception as e:
        logger.warning(f"[BLUR] Frame blur failed: {e}")
        return frame

# ============================================================
# 2. CLIP COLLECTION / ORDERING
# ============================================================

def build_render_clips(
    session_id: str,
    cfg: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Build the ordered render clip list from config.yml.

    Expected config shape:
    - first_clip
    - middle_clips
    - last_clip
    """

    if "first_clip" not in cfg or "last_clip" not in cfg:
        raise RuntimeError("config.yml must contain first_clip and last_clip")

    clips = [
        build_clip_entry(session_id, cfg["first_clip"])
    ]

    for m in cfg.get("middle_clips", []):
        clips.append(
            build_clip_entry(session_id, m)
        )

    clips.append(
        build_clip_entry(
            session_id,
            cfg["last_clip"],
            is_last=True
        )
    )

    return clips

def flatten_clips(cfg):
    clips = []

    if "first_clip" in cfg:
        clips.append(cfg["first_clip"])

    for c in cfg.get("middle_clips", []):
        clips.append(c)

    if "last_clip" in cfg:
        clips.append(cfg["last_clip"])

    return clips


def rebuild_clips(cfg, clips):
    if not clips:
        return cfg

    cfg["first_clip"] = clips[0]

    if len(clips) > 2:
        cfg["middle_clips"] = clips[1:-1]
        cfg["last_clip"] = clips[-1]
    elif len(clips) == 2:
        cfg["middle_clips"] = []
        cfg["last_clip"] = clips[1]
    else:
        cfg["middle_clips"] = []
        cfg.pop("last_clip", None)

    return cfg


def reorder_clips(cfg, new_order):
    """
    new_order = list of clip IDs in the desired order
    """
    clips = flatten_clips(cfg)

    clip_map = {c["id"]: c for c in clips}

    reordered = []
    for cid in new_order:
        if cid not in clip_map:
            raise ValueError(f"Unknown clip id: {cid}")
        reordered.append(clip_map[cid])

    return rebuild_clips(cfg, reordered)

# -------------------------------
# Build a normalized render clip
# -------------------------------
def build_clip_entry(
    session_id: str,
    c: Dict[str, Any],
    is_last: bool = False
) -> Dict[str, Any]:

    raw_file = c["file"]
    filename = os.path.basename(raw_file)
    local_file = ensure_local_video(session_id, filename)

    return {
        "file": local_file,
        "start": float(c.get("start_time", 0)),
        "duration": float(c.get("duration", 3)),
        "text": (c.get("text") or "").strip(),
        "is_last": is_last,
    }
    
# ============================================================
# 3. MEDIA RESTORE FROM S3
# ============================================================

def ensure_local_video(session_id: str, filename: str) -> str:
    """
    Ensures the video exists locally in:
        tik_tok_downloads/<session_id>/<filename>

    If missing, download from:
        s3://bucket/raw_uploads/<session>/<filename>

    Returns absolute local path.
    """

    # Local folder for this session
    session_dir = os.path.join(video_folder, session_id)
    os.makedirs(session_dir, exist_ok=True)

    local_path = os.path.join(session_dir, filename)

    # If already cached locally, use it
    if os.path.exists(local_path):
        return local_path

    # Normalize for safety
    prefix = RAW_PREFIX.rstrip("/")  # "raw_uploads"
    s3_key = f"{prefix}/{session_id}/{filename}"

    log_step(f"[SYNC] Downloading missing clip: s3://{S3_BUCKET_NAME}/{s3_key}")

    try:
        s3.download_file(S3_BUCKET_NAME, s3_key, local_path)
        log_step(f"[SYNC] Restored local clip → {local_path}")
    except Exception as e:
        raise RuntimeError(f"[SYNC ERROR] Cannot restore {filename} from S3: {e}")

    return local_path

# ============================================================
# 4. CAPTION STYLING / DRAW TEXT
# ============================================================

def should_show_caption(
    caption_mode: str,
    clip_index: int
) -> bool:
    """
    Decide whether a clip caption should be rendered.
    CTA captions are handled separately.
    """

    caption_mode = (caption_mode or "all").lower()

    if caption_mode == "all":
        return True

    if caption_mode == "first_only":
        return clip_index == 0

    if caption_mode == "none":
        return False

    # Safe fallback
    return True

def strip_emojis(text: str) -> str:
    return _EMOJI_RE.sub("", text or "").strip()

def _get_layout_mode(cfg: Dict[str, Any]) -> str:
    """
    Decide how to style captions / overlay:
      - "tiktok"  → smaller font, multi-line, TikTok friendly
      - "classic" → closer to your original single-line style
    """
    render = cfg.get("render") or {}
    mode = (render.get("layout_mode") or render.get("video_mode") or "tiktok").lower()
    if mode not in ("tiktok", "classic"):
        mode = "tiktok"
    return mode

# -------------------------------
# Simple, robust caption wrapper
# -------------------------------
def _wrap_caption(text: str, max_chars_per_line: int) -> str:
    """
    Wrap text by character count so drawtext never runs super-wide.
    This avoids captions stretching off-screen.
    """
    if not text:
        return ""

    words = text.split()
    lines = []
    current = ""

    for w in words:
        if not current:
            current = w
        elif len(current) + 1 + len(w) <= max_chars_per_line:
            current += " " + w
        else:
            lines.append(current)
            current = w

    if current:
        lines.append(current)

    return "\n".join(lines)

# -------------------------------
# Safe escape helper for drawtext
# -------------------------------
def escape_drawtext(text: str) -> str:
    if not text:
        return ""
    
    # 1) Temporarily protect real newlines
    t = text.replace("\n", "<<<NL>>>")
    
    # 2) Escape only characters FFmpeg needs escaped
    t = t.replace("\\", "\\\\")     # ESCAPE backslashes
    
    # Use smart quote to bypass FFmpeg's complex single quote escaping nightmare
    t = t.replace("'", "\u2019")    
    t = t.replace("%", "\\\\%") 
    
    # 3) Restore as literal \n (NOT double escaped)
    t = t.replace("<<<NL>>>", "\n")
    
    return t

def build_caption_filter(
    input_label: str,
    chunks: list,
    tts_dur: float,
    clip_dur: float,
    fontfile: str,
    fontsize: int,
    line_spacing: int,
    box_opacity: str,
    boxborderw: int,
    y_expr: str,
    output_label: str = "outv",
    enable: str | None = None,
) -> str:
    if not chunks:
        return f";[{input_label}]copy[{output_label}]"

    # Calculate time per chunk based on TTS duration
    total_time = tts_dur if tts_dur > 0.5 else min(clip_dur * 0.8, 3.0)
    time_per_chunk = total_time / len(chunks)

    filter_chain = []
    current_input = input_label

    for i, chunk in enumerate(chunks):
        start_time = i * time_per_chunk
        
        # The last chunk stays on screen until slightly after the speaking ends
        # to ensure it clears before the next clip or transition begins.
        if i == len(chunks) - 1:
            end_time = total_time + 0.3
        else:
            end_time = (i + 1) * time_per_chunk
        
        chunk_enable = f"between(t,{start_time},{end_time})"
        if enable:
            chunk_enable = f"({enable})*({chunk_enable})"

        next_label = output_label if i == len(chunks) - 1 else f"k_{input_label}_{i}"
        chunk_safe = escape_drawtext(chunk)
        
        drawtext = (
            f";[{current_input}]drawtext=text='{chunk_safe}':"
            f"fontfile={fontfile}:fontcolor=white:fontsize={fontsize}:"
            f"line_spacing={line_spacing}:shadowcolor=0x000000:shadowx=3:shadowy=3:"
            f"text_shaping=1:box=1:boxcolor=0x000000{box_opacity}:boxborderw={boxborderw}:"
            f"x=(w-text_w)/2:y={y_expr}:fix_bounds=1:borderw=0:bordercolor=0x000000:"
            f"enable='{chunk_enable}'"
            f"[{next_label}]"
        )
        filter_chain.append(drawtext)
        current_input = next_label

    return "".join(filter_chain)

def build_cta_filter(
    input_label: str,
    text_safe: str,
    fontfile: str,
    fontsize: int,
    line_spacing: int,
    boxborderw: int,
    y_expr: str,
    enable: str,
    output_label: str = "outv",
) -> str:
    return (
        f";[{input_label}]drawtext=text='{text_safe}':"
        f"fontfile={fontfile}:fontcolor=white:fontsize={fontsize}:"
        f"line_spacing={line_spacing}:shadowcolor=0x000000AA:shadowx=3:shadowy=3:"
        f"text_shaping=1:box=1:boxcolor=0x000000CC:boxborderw={boxborderw}:"
        f"x=(w-text_w)/2:y={y_expr}:fix_bounds=1:borderw=0:"
        f"enable='{enable}'"
        f"[{output_label}]"
    )

# -----------------------------------------
# Enhanced Caption Style Presets 🚀
# -----------------------------------------
STYLE_PRESETS = {
    "punchy": {
        "fontsize": 78,             # big & loud
        "line_spacing": 4,
        "box_opacity": "CC",        # stronger contrast
        "y_expr": "(h * 0.58)",     # lower to avoid blocking center
        "accent_color": "yellow",   # for future highlight pass
        "emoji_boost": True         # 🔥 if emojis present = spacing tweaked
    },

    "cinematic": {
        "fontsize": 54,
        "line_spacing": 18,
        "box_opacity": "DD",        # soft, elegant opacity
        "y_expr": "(h * 0.65)",     # lower for cinematic look
        "font_color": "white",
        "shadow_strength": 0.85     # deeper shadow for film look
    },

    "influencer": {
        "fontsize": 66,
        "line_spacing": 10,
        "box_opacity": "AA",
        "y_expr": "(h * 0.60)",     # lower for influencer style
        "bubble": True,             # future bubble background mode
        "emoji_boost": True
    },

    "travel_blog": {
        "fontsize": 62,
        "line_spacing": 14,
        "box_opacity": "BB",
        "y_expr": "(h * 0.62)",
        "serif_hint": False,
        "tone": "warm"
    },

    "descriptive": {
        "fontsize": 58,
        "line_spacing": 10,
        "box_opacity": "66",        # subtle background
        "y_expr": "(h * 0.60)",
        "tone": "neutral",
        "emoji_boost": False
    },

    "ai_recommended": {
        "fontsize": 68,             # Balanced modern hero style
        "line_spacing": 12,
        "box_opacity": "BB",
        "y_expr": "(h * 0.58)",     # lowered to clear center screen
        "accent_color": "teal",
        "smart_balance": True       # perfect for 90% of cases
    },
}


# ============================================================
# 5. TTS / MUSIC / AUDIO MIX
# ============================================================

GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"

def _gemini_tts_to_wav(client, text: str, voice: str, out_path: str) -> None:
    """
    Generate speech audio via Gemini TTS and write it out as a WAV file.
    """
    resp = client.models.generate_content(
        model=GEMINI_TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
                )
            ),
        ),
    )
    pcm_data = resp.candidates[0].content.parts[0].inline_data.data
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(pcm_data)


def _build_per_clip_tts(cfg, clips, cta_cfg):
    """
    Build TTS for each clip individually.
    Returns list of (path, duration) tuples, and CTA narration tuple.
    """

    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        log_step("[TTS] No API key available — skipping all TTS.")
        return [], None

    render = cfg.get("render", {}) or {}
    tts_cfg = cfg.get("tts", {}) or {}

    tts_enabled = (
        render.get("tts_enabled")
        or tts_cfg.get("enabled")
    )

    if not tts_enabled:
        log_step("[TTS] TTS disabled → skipping narration.")
        return [], None

    voice = (
        render.get("tts_voice")
        or tts_cfg.get("voice")
        or "Kore"
    )

    client = genai.Client(api_key=key)

    tts_files = []

    # -----------------------------------------
    # Generate narration for each clip (A1)
    # -----------------------------------------
    for idx, clip in enumerate(clips):
        text = clip.get("text", "").strip()

        # ---------------------------------------------
        # 🔥 CAPTIONS_MODE controls narration too
        # ---------------------------------------------
        render_cfg = cfg.get("render", {})
        caption_mode = (render_cfg.get("captions_mode") or "all").lower()
        narration_mode = (render_cfg.get("narration_mode") or "all").lower()


        # Skip TTS for clips without captions
        # Narration rules (independent of captions)
        if narration_mode == "none":
            log_step(f"[TTS] Narration disabled (narration_mode=none) on clip {idx+1}")
            tts_files.append(None)
            continue

        if narration_mode == "first_only" and idx > 0:
            log_step(f"[TTS] Narration first_only → skipping clip {idx+1}")
            tts_files.append(None)
            continue


        
        if not text:
            tts_files.append(None)
            continue

        log_step(f"[TTS] Generating narration for clip {idx+1}: '{text}'")

        tmp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name

        try:
            _gemini_tts_to_wav(client, text, voice, tmp_wav)
        except Exception as e:
            log_step(f"[TTS ERROR] clip {idx+1}: {e}")
            tts_files.append(None)
            continue

        # Convert → AAC (FFmpeg)
        tmp_m4a = tmp_wav.replace(".wav", ".m4a")
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_wav, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Measure duration
        try:
            dur = float(subprocess.check_output(
                ["ffprobe", "-v", "error",
                 "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1",
                 tmp_m4a]
            ).decode().strip())
        except:
            dur = None

        if os.path.exists(tmp_m4a):
            tts_files.append((tmp_m4a, dur))
        else:
            tts_files.append(None)

    # -----------------------------------------
    # CTA Narration (C1)
    # -----------------------------------------
    cta_tuple = None

    if cta_cfg.get("enabled") and cta_cfg.get("voiceover") and cta_cfg.get("text"):
        text = cta_cfg["text"]
        log_step(f"[TTS] Generating CTA narration: '{text}'")

        tmp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name
        try:
            _gemini_tts_to_wav(client, text, voice, tmp_wav)
        except Exception as e:
            log_step(f"[TTS ERROR CTA] {e}")
            cta_tuple = None
        else:
            tmp_m4a = tmp_wav.replace(".wav", ".m4a")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_wav, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            try:
                dur = float(subprocess.check_output(
                    ["ffprobe", "-v", "error",
                     "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1",
                     tmp_m4a]
                ).decode().strip())
            except:
                dur = None

            if os.path.exists(tmp_m4a):
                cta_tuple = (tmp_m4a, dur)

    return tts_files, cta_tuple

def build_audio_timeline(
    clips,
    tts_tracks,
    cta_tts_track,
    music_audio,
    music_volume,
    cta_enabled,
    raw_cta_text,
    cta_segment_len,
    last_clip_cta_start_rel,
):
    """
    Build scheduled audio tracks for final mix.

    Returns:
        audio_inputs
    """

    audio_inputs = []

    if music_audio:
        audio_inputs.append({
            "path": music_audio,
            "start": 0.0,
            "volume": music_volume,
        })

    FIRST_TTS_DELAY = 0.05
    last_tts_end = 0.0

    clip_start_times = []
    current_time = 0.0

    for clip in clips:
        clip_start_times.append(current_time)
        current_time += clip["duration"]

    # Per-clip narration
    for idx, clip in enumerate(clips):
        tts_entry = tts_tracks[idx] if idx < len(tts_tracks) else None

        if not tts_entry or not isinstance(tts_entry, tuple):
            continue

        tts_path, tts_dur = tts_entry

        if not tts_path or not tts_dur:
            continue

        delay = FIRST_TTS_DELAY if idx == 0 else 0.0

        start_ts = clip_start_times[idx] + delay

        log_step(
            f"[AUDIO TIMELINE] clip={idx + 1} "
            f"clip_start={clip_start_times[idx]:.2f} "
            f"tts_start={start_ts:.2f} "
            f"tts_dur={float(tts_dur):.2f} "
            f"tts_end={(start_ts + float(tts_dur)):.2f}"
        )

        audio_inputs.append({
            "path": tts_path,
            "start": start_ts,
            "volume": 1.0,
        })

        last_tts_end = max(
            last_tts_end,
            start_ts + float(tts_dur)
        )

    # CTA narration
    if (
        cta_tts_track
        and cta_enabled
        and raw_cta_text
        and cta_segment_len > 0.0
        and last_clip_cta_start_rel is not None
    ):
        if isinstance(cta_tts_track, tuple):
            cta_path, _ = cta_tts_track
        else:
            cta_path = cta_tts_track

        if cta_path:
            last_clip_start_abs = clip_start_times[-1]

            cta_start_abs = (
                last_clip_start_abs
                + last_clip_cta_start_rel
            )

            cta_start_abs = max(
                cta_start_abs,
                last_tts_end + 0.05
            )

            log_step(
                f"[CTA AUDIO] "
                f"cta_start={cta_start_abs:.2f} "
                f"last_tts_end={last_tts_end:.2f} "
                f"last_clip_cta_start_rel={last_clip_cta_start_rel:.2f}"
            )

            audio_inputs.append({
                "path": cta_path,
                "start": cta_start_abs,
                "volume": 1.0,
            })

    return audio_inputs

# -----------------------------------------
# Background music (YAML: music: {enabled, file, volume})
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    """
    Memory-safe background music loader.
    Returns a temp .m4a file path or None.
    """

    music_cfg = cfg.get("music", {}) or {}
    if not music_cfg.get("enabled"):
        log_step("[MUSIC] Disabled in config.")
        return None

    music_file = (music_cfg.get("file") or "").strip()
    if not music_file:
        log_step("[MUSIC] No music file specified.")
        return None

    volume = float(music_cfg.get("volume", 0.25))

    music_path = os.path.join(MUSIC_DIR, music_file)
    if not os.path.exists(music_path):
        log_step(f"[MUSIC] NOT FOUND in MUSIC_DIR: {music_path}")
        return None

    log_step(f"[MUSIC] Using file: {music_path}")

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    fade_start = max(float(total_duration) - 2.0, 0.0)

    cmd = [
        "ffmpeg", "-y",
        "-i", music_path,
        "-filter_complex",
        f"apad,atrim=0:{total_duration},"
        f"afade=t=out:st={fade_start}:d=2.0,"
        f"volume={volume}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path,
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[MUSIC-FFMPEG] stderr:\n{proc.stderr}")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log_step("[MUSIC] Output audio invalid, disabling music.")
        return None

    return out_path

def _build_base_audio(video_path, total_duration):
    """
    Extract original audio from the stitched video, memory-safe.
    Returns a .m4a file path or None.

    NOTE: Currently NOT used in the final mix to keep the chain simple:
    we mix only TTS + music to avoid corrupt/empty sources.
    """

    if not os.path.exists(video_path):
        log_step(f"[AUDIO] Base video missing: {video_path}")
        return None

    out_path = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",
        "-af", f"apad,atrim=0:{total_duration}",
        "-c:a", "aac",
        "-b:a", "192k",
        out_path,
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[AUDIO-BASE-FFMPEG] stderr:\n{proc.stderr}")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1024:
        log_step("[AUDIO] Base audio invalid, skipping.")
        return None

    return out_path

# ============================================================
# 6. VIDEO RENDER HELPERS
# ============================================================
def get_video_duration(filename: str):
    """
    Returns duration in seconds as float, or None if ffprobe fails.
    """
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filename,
            ]
        ).decode().strip()
        return float(out)
    except Exception as e:
        log_step(f"[DURATION] ffprobe failed for {filename}: {e}")
        return None
    
def compute_auto_zoom(video_path: str) -> float:
    """
    Compute a smart foreground scale factor to remove thick borders
    while preventing over-zooming. Safe for MOV/MP4.
    """
    try:
        # Get actual resolution using ffprobe
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            video_path
        ]).decode().strip()

        w, h = map(int, out.split("x"))
    except:
        # fallback safety
        return 1.10

    target_w = 1080
    target_h = 1920

    # Aspect ratios
    clip_aspect = w / h
    target_aspect = target_w / target_h

    # For pillarboxed clips (too tall)
    if clip_aspect < target_aspect:
        zoom = target_w / w      # zoom until width matches
    # For letterboxed clips (too wide)
    else:
        zoom = target_h / h      # zoom until height matches

    # Add slight zoom so borders fully disappear
    zoom *= 1.05

    # clamp to safe range
    zoom = min(max(zoom, 1.05), 1.20)
    return zoom
    
def build_base_video_filter(fg_scale: float, dynamic_zoom: bool = False) -> str:
    """
    Build the base vertical video filter.

    Creates:
    - blurred 1080-wide background
    - scaled foreground
    - centered overlay
    - output label [v1]
    """

    fg_scale = min(max(float(fg_scale), 1.0), 1.25)

    if dynamic_zoom:
        return (
            f"[0:v]scale=1080:-2,setsar=1,boxblur=30:1[bg];"
            f"[0:v]scale=iw*{fg_scale}:ih*{fg_scale},setsar=1[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2,"
            f"zoompan=z='min(1.0+0.02*time,1.15)':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':d=1:fps=30:s=1080x1920[v1]"
        )
    else:
        return (
            f"[0:v]scale=1080:-2,setsar=1,boxblur=30:1[bg];"
            f"[0:v]scale=iw*{fg_scale}:ih*{fg_scale},setsar=1[fg];"
            f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[v1]"
        )
    

def get_transition_settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    render = cfg.get("render", {}) or {}
    transition = render.get("transition", {}) or {}

    transition_type = (transition.get("type") or "none").lower()
    duration = float(transition.get("duration", 0.4) or 0.4)

    if transition_type not in ("none", "fade"):
        transition_type = "none"

    duration = max(0.1, min(duration, 1.0))

    return {
        "type": transition_type,
        "duration": duration,
    }

def concat_videos_fade(trimmed_files: List[str], transition_duration: float = 0.4, optimized: bool = False) -> str:
    log_step(f"[CONCAT] Using fade transitions duration={transition_duration:.2f}")

    if len(trimmed_files) <= 1:
        return trimmed_files[0]

    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

    durations = [get_video_duration(f) or 0.0 for f in trimmed_files]

    cmd = ["ffmpeg", "-y"]
    for f in trimmed_files:
        cmd += ["-i", f]

    filter_parts = []
    for i in range(len(trimmed_files)):
        filter_parts.append(
            f"[{i}:v]fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
        )

    current = "[v0]"
    elapsed = durations[0]

    for i in range(1, len(trimmed_files)):
        offset = max(elapsed - transition_duration, 0.0)

        out_label = f"[xf{i}]"
        filter_parts.append(
            f"{current}[v{i}]xfade=transition=fade:"
            f"duration={transition_duration}:"
            f"offset={offset}"
            f"{out_label}"
        )

        current = out_label
        elapsed += durations[i] - transition_duration

    filter_complex = ";".join(filter_parts)

    cmd += [
        "-filter_complex", filter_complex,
        "-map", current,
        "-c:v", "libx264",
        "-preset", "superfast" if optimized else "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        concat_output,
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[FADE-CONCAT-FFMPEG] stderr:\n{proc.stderr}")

    dur = get_video_duration(concat_output)
    log_step(f"[FADE CONCAT RESULT] duration={dur:.2f}s")

    return concat_output

def concat_videos_standard(trimmed_files: List[str], optimized: bool = False) -> str:
    log_step("[CONCAT] Using timestamp-safe concat filter")

    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

    cmd = ["ffmpeg", "-y"]

    for f in trimmed_files:
        cmd += ["-i", f]

    filter_parts = []
    input_labels = []

    for i in range(len(trimmed_files)):
        filter_parts.append(
            f"[{i}:v]fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
        )
        input_labels.append(f"[v{i}]")

    filter_complex = (
        ";".join(filter_parts)
        + ";"
        + "".join(input_labels)
        + f"concat=n={len(trimmed_files)}:v=1:a=0[outv]"
    )

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-c:v", "libx264",
        "-preset", "superfast" if optimized else "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        concat_output,
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if proc.stderr:
        log_step(f"[CONCAT-FFMPEG] stderr:\n{proc.stderr}")

    dur = get_video_duration(concat_output)
    log_step(f"[CONCAT RESULT] duration={dur:.2f}s")

    return concat_output
    
# ============================================================
# 7. FINAL EXPORT / MUX
# ============================================================

def edit_video(session_id: str, output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Build final TikTok-style video using a low-memory FFmpeg-only pipeline.
    """

    cfg = load_render_config(session_id)

    transition_settings = get_transition_settings(cfg)

    log_step(
        f"[EXPORT] Building video timeline "
        f"(transition={transition_settings['type']}, "
        f"duration={transition_settings['duration']:.2f})"
    )
    
    layout_mode = _get_layout_mode(cfg)
    log_step(f"[EXPORT] Building low-memory FFmpeg timeline… (layout_mode={layout_mode})")

    # CLEAN UP legacy wrong music keys from older UI
    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)


    clips = build_render_clips(session_id, cfg)

    log_step(
    "[CONFIG DURATIONS] "
    + ", ".join(str(c["duration"]) for c in clips)
        )

    render_cfg = cfg.setdefault("render", {})

    overlay_style = (render_cfg.get("overlay_style") or "ai_recommended").lower()
    log_step(f"[OVERLAY] Visual style = {overlay_style}")


    # -----------------------------------------
    # GLOBAL CAPTION LAYOUT (used by BOTH clip captions + CTA captions)
    # -----------------------------------------
    preset = STYLE_PRESETS.get(overlay_style, STYLE_PRESETS["ai_recommended"])

    if layout_mode == "tiktok":
        max_chars = 16
        fontsize = preset["fontsize"]
        line_spacing = preset["line_spacing"]
        boxborderw = 24
        box_opacity = preset["box_opacity"]
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        y_expr = preset["y_expr"]
    else:
        max_chars = 34
        fontsize = 52
        line_spacing = 8
        boxborderw = 20
        box_opacity = "AA"
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        y_expr = "h-(text_h*2.0)-200"



    # Remove accidental duplicates by file
    all_files = [c["file"] for c in clips]
    if len(set(all_files)) < len(all_files):
        log_step("[SAFETY] Removing duplicate clip entries from YAML…")
        unique: List[Dict[str, Any]] = []
        seen = set()
        for c in clips:
            if c["file"] not in seen:
                unique.append(c)
                seen.add(c["file"])
        clips = unique

    if not clips:
        raise RuntimeError("No clips defined in config.yml")

    # --------------------------
    # AUTO / MANUAL FG SCALE LOGIC
    # --------------------------

    fg_mode = str(render_cfg.get("fgscale_mode", "auto")).lower()

    if fg_mode == "auto":
        example_clip = clips[0]["file"]
        auto_zoom = compute_auto_zoom(example_clip)
        render_cfg["fgscale"] = auto_zoom
    else:
        if render_cfg.get("fgscale") is None:
            render_cfg["fgscale"] = 1.10
        log_step(f"[FGSCALE] Manual mode → using fgscale={render_cfg.get('fgscale')}")

    # ------------------------------------------------------------------
    # 0. TTS + CLIP DURATION EXTENSION (per-clip + CTA)
    # ------------------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    tts_tracks, cta_tts_track = _build_per_clip_tts(cfg, clips, cta_cfg)

    # -----------------------------------------
    # CTA CONFIG — we draw CTA on *last clip*
    # -----------------------------------------
    cta_enabled = bool(cta_cfg.get("enabled", False))
    raw_cta_text = (cta_cfg.get("text") or "").strip()

    # Slightly narrower captions for TikTok
    if layout_mode == "tiktok":
        cta_max_chars = 16
    else:
        cta_max_chars = 32

    clean_cta = strip_emojis(raw_cta_text) if raw_cta_text else ""
    wrapped_cta = _wrap_caption(clean_cta, max_chars_per_line=cta_max_chars) if clean_cta else ""
    cta_text_safe = escape_drawtext(wrapped_cta) if wrapped_cta else ""


    log_step(f"[CTA-DEBUG] raw_cta_text: {repr(raw_cta_text)}")
    log_step(f"[CTA-DEBUG] wrapped_cta: {repr(wrapped_cta)}")
    log_step(f"[CTA-DEBUG] cta_text_safe: {repr(cta_text_safe)}")

    cta_config_dur = float(cta_cfg.get("duration", 3.0))

    # CTA voice (if generated)
    if cta_tts_track and isinstance(cta_tts_track, tuple):
        _, cta_voice_dur = cta_tts_track
        cta_voice_dur = cta_voice_dur or 0.0
    else:
        cta_voice_dur = 0.0

    # "Logical" CTA length before we clamp visual part
    cta_segment_len = 0.0
    if cta_enabled and raw_cta_text:
        cta_segment_len = max(float(cta_config_dur or 1.5), float(cta_voice_dur or 0), 1.5)

    # Ensure each clip is long enough to contain its narration
    for i, clip in enumerate(clips):
        is_last = (i == len(clips) - 1)
        tts_entry = tts_tracks[i] if i < len(tts_tracks) else None
        
        tts_dur = 0.0
        if tts_entry and isinstance(tts_entry, tuple):
            tts_path, dur = tts_entry
            if tts_path and dur:
                tts_dur = float(dur)

        needed = tts_dur + 1.0  # small safety padding
        
        # If it's the last clip and we have a CTA, extend enough for both TTS + CTA
        if is_last and cta_segment_len > 0.0:
            needed = tts_dur + 0.1 + cta_segment_len
            
        if needed > clip["duration"]:
            log_step(
                f"[A1a] Extending clip {i+1} "
                f"duration from {clip['duration']:.2f}s → {needed:.2f}s"
            )
            clip["duration"] = needed

    base_video_duration = sum(clip["duration"] for clip in clips)

    # We'll decide CTA *visual* window on the last clip only.
    last_clip_cta_start_rel: Optional[float] = None
    last_clip_cta_visual_len: float = 0.0
    if cta_enabled and raw_cta_text and cta_segment_len > 0.0:
        last_clip = clips[-1]
        clip_dur = float(last_clip["duration"])

        # Find when the last clip's TTS ends
        last_clip_tts_dur = 0.0
        if len(tts_tracks) > 0 and tts_tracks[-1] and isinstance(tts_tracks[-1], tuple):
            _, dur = tts_tracks[-1]
            if dur:
                last_clip_tts_dur = float(dur)

        # Give CTA enough time to be seen/read
        last_clip_cta_visual_len = min(
            cta_segment_len,
            clip_dur,
            3.0
        )
        
        # Ensure CTA starts after TTS
        min_start_time = max(clip_dur * 0.75, last_clip_tts_dur + 0.1)
        last_clip_cta_start_rel = max(clip_dur - last_clip_cta_visual_len, min_start_time)
        
        # Guardrail just in case
        if last_clip_cta_start_rel >= clip_dur:
            last_clip_cta_start_rel = max(0.0, clip_dur - 1.0)
        

        log_step(
            f"[CTA-LAST-CLIP-SETUP] clip_dur={clip_dur:.2f}, "
            f"visual_len={last_clip_cta_visual_len:.2f}, "
            f"start_rel={last_clip_cta_start_rel:.2f}"
        )

    # -------------------------------
    # 1. TRIM EACH CLIP (with captions + CTA on last)
    # -------------------------------
    trimmed_files: List[str] = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    with open(trimlist, "w") as lf:
        for clip_index, clip in enumerate(clips):
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

            render_cfg = cfg.get("render", {})
            fg_scale = float(render_cfg.get("fgscale", 1.10))
            auto_zoom = render_cfg.get("auto_zoom", False)
            vf = build_base_video_filter(fg_scale, dynamic_zoom=auto_zoom)

            is_last = clip.get("is_last", False)

            # =========================
            # CAPTION MODE (global)
            # =========================
            caption_mode = (render_cfg.get("captions_mode") or "all").lower()
            log_step(f"[CAPTIONS] mode={caption_mode}")


            # ---------------------------------------------
            # CAPTION RULES (NEW)
            # ---------------------------------------------
            # Show captions only if:
            # - captions_mode="all"
            # - captions_mode="first_only" and clip_index == 0
            # - captions_mode="none" → never show clip captions
            # CTA logic still runs on last clip normally

            allow_caption = should_show_caption(
                caption_mode,
                clip_index
            )

            # -------------- NON-LAST + caption/NO-caption -------------
            if not is_last or not (cta_enabled and raw_cta_text and last_clip_cta_start_rel is not None and cta_text_safe):
                if allow_caption and clip["text"]:
                    clean_text = strip_emojis(clip["text"])
                    
                    word_count = len(clean_text.split())
                    dynamic_fontsize = fontsize
                    dynamic_max_chars = max_chars
                    
                    if word_count > 15:
                        dynamic_fontsize = int(fontsize * 0.75)
                        dynamic_max_chars = int(max_chars * 1.3)
                    elif word_count > 8:
                        dynamic_fontsize = int(fontsize * 0.85)
                        dynamic_max_chars = int(max_chars * 1.15)
                        
                    words = clean_text.split()
                    
                    if overlay_style in ["cinematic", "descriptive", "travel_blog"]:
                        chunk_size = 999  # show full block for cinematic, travel_blog, etc.
                    else:
                        chunk_size = 3    # karaoke for punchy, influencer, ai_recommended
                        
                    # If we are doing full blocks, we should wrap the text so it fits on screen
                    if chunk_size == 999:
                        wrapped = _wrap_caption(clean_text, max_chars_per_line=dynamic_max_chars)
                        chunks = [wrapped]
                    else:
                        chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]
                    
                    vf += build_caption_filter(
                        input_label="v1",
                        chunks=chunks,
                        tts_dur=tts_dur,
                        clip_dur=float(clip["duration"]),
                        fontfile=fontfile,
                        fontsize=dynamic_fontsize,
                        line_spacing=line_spacing,
                        box_opacity=box_opacity,
                        boxborderw=boxborderw,
                        y_expr=y_expr,
                        output_label="outv",
                    )
                else:
                    vf += ";[v1]copy[outv]"


            # ----- LAST CLIP: caption first, then CTA at the end -----
            else:
                clip_dur = float(clip["duration"])
                cta_start = last_clip_cta_start_rel

                # ---------------------------------------------------------
                # (1) CAPTION PHASE — draw until CTA start
                # ---------------------------------------------------------
                if allow_caption and clip["text"]:
                    clean_text = strip_emojis(clip["text"])
                    
                    word_count = len(clean_text.split())
                    dynamic_fontsize = fontsize
                    dynamic_max_chars = max_chars
                    
                    if word_count > 15:
                        dynamic_fontsize = int(fontsize * 0.75)
                        dynamic_max_chars = int(max_chars * 1.3)
                    elif word_count > 8:
                        dynamic_fontsize = int(fontsize * 0.85)
                        dynamic_max_chars = int(max_chars * 1.15)
                        
                    words = clean_text.split()
                    
                    if overlay_style in ["cinematic", "descriptive", "travel_blog"]:
                        chunk_size = 999
                    else:
                        chunk_size = 3
                        
                    if chunk_size == 999:
                        wrapped = _wrap_caption(clean_text, max_chars_per_line=dynamic_max_chars)
                        chunks = [wrapped]
                    else:
                        chunks = [" ".join(words[i:i+chunk_size]) for i in range(0, len(words), chunk_size)]

                    vf += build_caption_filter(
                        input_label="v1",
                        chunks=chunks,
                        tts_dur=tts_dur,
                        clip_dur=float(clip["duration"]),
                        fontfile=fontfile,
                        fontsize=dynamic_fontsize,
                        line_spacing=line_spacing,
                        box_opacity=box_opacity,
                        boxborderw=boxborderw,
                        y_expr=y_expr,
                        output_label="v2",
                        enable=f"lt(t,{cta_start})",
                    )
                else:
                    vf += ";[v1]copy[v2]"


                # ---------------------------------------------------------
                # (2) BLUR UNDER CTA — but NEVER make video black
                #     boxblur with enable=... passes input when false
                # ---------------------------------------------------------
                vf += (
                    f";[v2]split[v2a][v2b];"
                    f"[v2a]boxblur=12:1[v2blur];"
                    f"[v2b][v2blur]overlay=0:0:enable='gte(t,{cta_start})'[v3]"

                )

                # ---------------------------------------------------------
                # (3) CTA TEXT — only after CTA start
                # ---------------------------------------------------------
                if layout_mode == "tiktok":
                    cta_y_expr = "(h * 0.70)"
                else:
                    cta_y_expr = "(h * 0.72)"   # safe for classic layout – always visible


                vf += build_cta_filter(
                    input_label="v3",
                    text_safe=cta_text_safe,
                    fontfile=fontfile,
                    fontsize=fontsize,
                    line_spacing=line_spacing,
                    boxborderw=boxborderw,
                    y_expr=cta_y_expr,
                    enable=f"gte(t,{cta_start})",
                    output_label="outv",
                )

                log_step(
                    f"[CTA-LAST-CLIP-SIMPLE] caption→CTA, start={cta_start:.2f}"
                )

            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", clip["file"],
                "-t", str(clip["duration"]),
                "-filter_complex", vf,
                "-map", "[outv]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "20",
                "-an",
                trimmed_path,
            ]

            log_step(f"[TRIM] {clip['file']} -> {trimmed_path}")
            proc = subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc.stderr:
                log_step(f"[TRIM-FFMPEG] stderr for {clip['file']}:\n{proc.stderr}")

            if not os.path.exists(trimmed_path):
                raise RuntimeError(f"[TRIM ERROR] Output not created for {clip['file']}")

            actual_trim_duration = get_video_duration(trimmed_path)

            log_step(
                f"[TRIM RESULT] clip={clip_index + 1} "
                f"requested={clip['duration']:.2f} "
                f"actual={actual_trim_duration:.2f}"
            )

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")

    log_step(f"[CONCAT INPUTS] count={len(trimmed_files)}")
    for i, f in enumerate(trimmed_files):
        log_step(f"[CONCAT INPUT] {i+1}: duration={get_video_duration(f):.2f} path={f}")
        
    # -------------------------------
    # 2. CONCAT CLIPS
    # -------------------------------

    if transition_settings["type"] == "fade" and len(trimmed_files) > 1:
        final_video_source = concat_videos_fade(
            trimmed_files=trimmed_files,
            transition_duration=transition_settings["duration"],
            optimized=optimized,
        )
    else:
        final_video_source = concat_videos_standard(
            trimmed_files=trimmed_files,
            optimized=optimized,
        )

    log_step(
        f"[TRANSITION] type={transition_settings['type']} "
        f"duration={transition_settings['duration']:.2f}"
    )

    # ✅ always compute duration
    total_video_duration = get_video_duration(final_video_source) or float(base_video_duration)
    log_step(f"[DURATION] total_video_duration={total_video_duration:.2f}s")


    # ------------------------------------------------------------------
    # 4. AUDIO PIPELINE — CLEAN, NO OVERLAP, ACCURATE TTS TIMELINE
    # ------------------------------------------------------------------
    log_step("[AUDIO] Building audio timeline…")

    # Background music
    music_cfg = cfg.get("music", {}) or {}
    music_audio = None

    if music_cfg.get("enabled"):
        music_audio = _build_music_audio(cfg, total_video_duration)

    if music_audio:
        log_step(f"[AUDIO-MUSIC] Adding background music: {music_audio}")
    else:
        log_step("[AUDIO-MUSIC] No music added.")

    audio_timeline_clips = []

    for idx, clip in enumerate(clips):
        measured_duration = None

        if idx < len(trimmed_files):
            measured_duration = get_video_duration(trimmed_files[idx])

        audio_timeline_clips.append({
            **clip,
            "duration": measured_duration or clip["duration"],
        })

    log_step(
        "[AUDIO TIMELINE] measured clip durations="
        + ", ".join(f"{c['duration']:.2f}" for c in audio_timeline_clips)
    )

    audio_inputs = build_audio_timeline(
        clips=clips,
        tts_tracks=tts_tracks,
        cta_tts_track=cta_tts_track,
        music_audio=music_audio,
        music_volume=float(music_cfg.get("volume", 0.25)),
        cta_enabled=cta_enabled,
        raw_cta_text=raw_cta_text,
        cta_segment_len=cta_segment_len,
        last_clip_cta_start_rel=last_clip_cta_start_rel,
    )

    # ------------------------------------------------------------------
    # MIX ALL AUDIO
    # ------------------------------------------------------------------
    final_audio = None
    if audio_inputs:
        narration_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name

        cmd = ["ffmpeg", "-y"]

        for inp in audio_inputs:
            cmd += ["-i", inp["path"]]

        filter_parts = []
        
        has_music = (music_audio is not None)
        voiceover_labels = []

        for idx, inp in enumerate(audio_inputs):
            delay_ms = int(round(inp["start"] * 1000))
            filter_parts.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={inp['volume']}[a{idx}]"
            )
            
            if has_music and idx == 0:
                pass  # It's the music track
            else:
                voiceover_labels.append(f"[a{idx}]")

        if voiceover_labels:
            if len(voiceover_labels) > 1:
                filter_parts.append(
                    "".join(voiceover_labels) + f"amix=inputs={len(voiceover_labels)}:normalize=0[v_mix]"
                )
            else:
                filter_parts.append(f"{voiceover_labels[0]}anull[v_mix]")

        if has_music and voiceover_labels:
            # Ducking: Sidechain compress the music [a0] using the voiceover [v_mix]
            # Split the voiceover track so it can be used for both the sidechain and the final mix
            filter_parts.append("[v_mix]asplit=2[v_mix_main][v_mix_sc]")
            # Apply sidechain compression to music
            filter_parts.append("[a0][v_mix_sc]sidechaincompress=threshold=0.08:ratio=4:attack=200:release=1000[ducked_music]")
            # Mix the ducked music with the main voiceover
            filter_parts.append("[ducked_music][v_mix_main]amix=inputs=2:normalize=0[outa]")
        elif has_music and not voiceover_labels:
            filter_parts.append("[a0]anull[outa]")
        elif not has_music and voiceover_labels:
            filter_parts.append("[v_mix]anull[outa]")

        full_filter = "; ".join(filter_parts)

        cmd += [
            "-filter_complex", full_filter,
            "-map", "[outa]",
            "-c:a", "aac",
            narration_out,
        ]

        log_step("[AUDIO] Mixing audio tracks with ducking if needed…")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        if proc.stderr:
            log_step(f"[AUDIO-FFMPEG] stderr:\n{proc.stderr}")

        if os.path.exists(narration_out) and os.path.getsize(narration_out) > 1024:
            final_audio = narration_out
        else:
            log_step("[AUDIO] Mix invalid, skipping narration.")
            final_audio = None

    # -------------------------------
    # 6. FINAL MUX
    # -------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))

    actual_final_video_duration = get_video_duration(final_video_source)
    if actual_final_video_duration is None:
        log_step("[MUX-WARNING] Could not probe video duration, using fallback = total_video_duration")
        actual_final_video_duration = total_video_duration
    else:
        total_video_duration = actual_final_video_duration

    use_shortest = False

    mux_cmd = ["ffmpeg", "-y"]
    mux_cmd += ["-i", final_video_source]

    if final_audio:
        mux_cmd += ["-i", final_audio]
        mux_cmd += [
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
        ]
        if use_shortest:
            mux_cmd.append("-shortest")
        mux_cmd.append(final_output)
    else:
        mux_cmd += [
            "-c:v", "copy",
            final_output,
        ]

    log_step("[MUX] Running final mux command…")
    mux_proc = subprocess.run(mux_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if mux_proc.stderr:
        log_step(f"[MUX-FFMPEG] stderr:\n{mux_proc.stderr}")

    if not os.path.exists(final_output) or os.path.getsize(final_output) < 200_000:
        raise RuntimeError(f"[MUX ERROR] Final output invalid or missing! ({final_output})")

    log_step(f"[EXPORT] Video rendered OK → {final_output}")
    return final_output
