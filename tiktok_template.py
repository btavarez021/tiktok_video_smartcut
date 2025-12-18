# tiktok_template.py — MOV/MP4 SAFE, LOW-MEMORY, NO CIRCULAR IMPORTS

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

# Pillow compatibility shim
if not hasattr(Image, "ANTIALIAS"):
    from PIL import Image as _Image
    Image.ANTIALIAS = _Image.Resampling.LANCZOS
    Image.BILINEAR = _Image.Resampling.BILINEAR
    Image.BICUBIC = _Image.Resampling.BICUBIC
    Image.NEAREST = _Image.Resampling.NEAREST

os.environ["IMAGEIO_FFMPEG_EXE"] = imageio_ffmpeg.get_ffmpeg_exe()

logger = logging.getLogger(__name__)

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


# -----------------------------------------
# Config helpers
# -----------------------------------------

def get_config_path(session_id: str) -> str:
    folder = os.path.join(BASE_DIR, "configs", session_id)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.yml")

def load_config_for_session(session_id: str):
    path = get_config_path(session_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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


# -----------------------------------------
# Caption wrapping helper
# -----------------------------------------
def _wrap_caption(text: str, max_chars_per_line: int = 28) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    words = text.split()
    lines = []
    current = ""

    for w in words:
        extra = 1 if current else 0
        if len(current) + len(w) + extra > max_chars_per_line:
            if current:
                lines.append(current.rstrip())
            current = w
        else:
            current = f"{current} {w}".strip()

    if current:
        lines.append(current.rstrip())

    # 🔑 KEY CHANGE: use *literal* "\n" sequences, not real newlines
    # This avoids the "citynviews" bug and plays nice with ffmpeg.
    return r"\n".join(lines)



# -----------------------------------------
# TTS generation
# -----------------------------------------
# -----------------------------------------
# NEW: Per-clip TTS builder (A1 + C1)
# -----------------------------------------
def _build_per_clip_tts(cfg, clips, cta_cfg):
    """
    Build TTS for each clip individually.
    Returns list of (path, duration) tuples, and CTA narration tuple.
    """

    from openai import OpenAI
    import tempfile

    key = os.getenv("OPENAI_API_KEY") or os.getenv("open_ai_api_key")
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
        or "alloy"
    )

    client = OpenAI(api_key=key)

    tts_files = []

    # -----------------------------------------
    # Generate narration for each clip (A1)
    # -----------------------------------------
    for idx, clip in enumerate(clips):
        text = clip.get("text", "").strip()
        if not text:
            tts_files.append(None)
            continue

        log_step(f"[TTS] Generating narration for clip {idx+1}: '{text}'")

        tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

        try:
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )
            with open(tmp_mp3, "wb") as f:
                f.write(resp.read())
        except Exception as e:
            log_step(f"[TTS ERROR] clip {idx+1}: {e}")
            tts_files.append(None)
            continue

        # Convert → AAC (FFmpeg)
        tmp_m4a = tmp_mp3.replace(".mp3", ".m4a")
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_mp3, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
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

        tmp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
        try:
            resp = client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=voice,
                input=text,
            )
            with open(tmp_mp3, "wb") as f:
                f.write(resp.read())
        except Exception as e:
            log_step(f"[TTS ERROR CTA] {e}")
            cta_tuple = None
        else:
            tmp_m4a = tmp_mp3.replace(".mp3", ".m4a")
            subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_mp3, "-c:a", "aac", "-b:a", "192k", tmp_m4a],
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


# -----------------------------------------
# Background music (YAML: music: {enabled, file, volume})
# -----------------------------------------
def _build_music_audio(cfg, total_duration):
    """
    Memory-safe background music loader.
    Returns a temp .m4a file path or None.
    """
    import tempfile

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

    cmd = [
        "ffmpeg", "-y",
        "-i", music_path,
        "-filter_complex",
        f"apad,atrim=0:{total_duration},volume={volume}",
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
    import tempfile

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


# -----------------------------------------
# Ensure local video exists (S3 → local sync)
# -----------------------------------------
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


def edit_video(session_id: str, output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Build final TikTok-style video using a low-memory FFmpeg-only pipeline.

    - Per-clip TTS (narration) aligned to each clip duration
    - CTA is shown on the *last clip* (no extra tail file)
    - CTA TTS aligned with the CTA visual segment
    - Background music from YAML (music: { enabled, file, volume })
    """
    cfg = load_config_for_session(session_id)
    if not cfg:
        raise RuntimeError("config.yml missing or empty")
    
    render = cfg.get("render", {})

    log_step("[EXPORT] Using standard concat (no transitions)")


    layout_mode = _get_layout_mode(cfg)
    log_step(f"[EXPORT] Building low-memory FFmpeg timeline… (layout_mode={layout_mode})")

    # CLEAN UP legacy wrong music keys from older UI
    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)

    # -------------------------------
    # Safe escape helper for drawtext
    # -------------------------------
    def esc(text: str) -> str:
        if not text:
            return ""
        
        # 1) Temporarily protect real newlines
        t = text.replace("\n", "<<<NL>>>")
        
        # 2) Escape only characters FFmpeg needs escaped
        t = t.replace("\\", "\\\\")     # ESCAPE backslashes
        t = t.replace("'", "\\'")       # ESCAPE single quotes
        t = t.replace("%", "\\\\%") 
        
        # 3) Restore as literal \n (NOT double escaped)
        t = t.replace("<<<NL>>>", "\n")
        
        return t


    # -------------------------------
    # Small helper: probe video duration with ffprobe
    # -------------------------------
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

    # -------------------------------
    # Build clip list (first, middle*, last)
    # -------------------------------
    def collect(c: Dict[str, Any], is_last: bool = False) -> Dict[str, Any]:
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

    if "first_clip" not in cfg or "last_clip" not in cfg:
        raise RuntimeError("config.yml must contain first_clip and last_clip")

    clips: List[Dict[str, Any]] = [collect(cfg["first_clip"])]
    for m in cfg.get("middle_clips", []):
        clips.append(collect(m))
    clips.append(collect(cfg["last_clip"], is_last=True))

    # -----------------------------------------
    # GLOBAL CAPTION LAYOUT (used by BOTH clip captions + CTA captions)
    # -----------------------------------------
    if layout_mode == "tiktok":
        max_chars = 16               # narrow TikTok wrapping
        fontsize = 64
        line_spacing = 12
        boxborderw = 24
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        y_expr = "(h * 0.48)"
    else:
        max_chars = 34                # classic overlay wider
        fontsize = 52
        line_spacing = 8
        boxborderw = 20
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
    render_cfg = cfg.setdefault("render", {})
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

    # Ensure each clip is long enough to contain its narration
    for i, clip in enumerate(clips):
        tts_entry = tts_tracks[i] if i < len(tts_tracks) else None
        if not tts_entry or not isinstance(tts_entry, tuple):
            continue

        tts_path, tts_dur = tts_entry
        if not tts_path or not tts_dur:
            continue

        needed = float(tts_dur) + 1.0  # small safety padding
        if needed > clip["duration"]:
            log_step(
                f"[A1a] Extending clip {i+1} "
                f"duration from {clip['duration']:.2f}s → {needed:.2f}s"
            )
            clip["duration"] = needed

    base_video_duration = sum(clip["duration"] for clip in clips)

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

    wrapped_cta = _wrap_caption(raw_cta_text, max_chars_per_line=cta_max_chars) if raw_cta_text else ""
    cta_text_safe = esc(wrapped_cta) if wrapped_cta else ""

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

    # We'll decide CTA *visual* window on the last clip only.
    last_clip_cta_start_rel: Optional[float] = None
    last_clip_cta_visual_len: float = 0.0
    if cta_enabled and raw_cta_text and cta_segment_len > 0.0:
        last_clip = clips[-1]
        clip_dur = float(last_clip["duration"])

        # Visual CTA window: last ~1.0–1.2 seconds (never more)
        last_clip_cta_visual_len = min(cta_segment_len, clip_dur, 1.2)
        last_clip_cta_start_rel = max(clip_dur - last_clip_cta_visual_len, clip_dur * 0.75)
        

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
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

            render_cfg = cfg.get("render", {})
            fg_scale = float(render_cfg.get("fgscale", 1.10))
            fg_scale = min(max(fg_scale, 1.0), 1.25)

            # Base FG + BG chain
            vf = (
                f"[0:v]scale=1080:-2,setsar=1,boxblur=30:1[bg];"
                f"[0:v]scale=iw*{fg_scale}:ih*{fg_scale},setsar=1[fg];"
                f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[v1]"
            )

            is_last = clip.get("is_last", False)

            # ----- NON-LAST CLIPS: normal caption -----
            if not is_last or not (cta_enabled and raw_cta_text and last_clip_cta_start_rel is not None and cta_text_safe):
                if clip["text"]:
                    wrapped = _wrap_caption(clip["text"], max_chars_per_line=max_chars)
                    text_safe = esc(wrapped)
                    vf += (
                        f";[v1]drawtext=text='{text_safe}':"
                        f"fontfile={fontfile}:"
                        f"fontcolor=white:fontsize={fontsize}:"
                        f"line_spacing={line_spacing}:"
                        f"shadowcolor=0x000000:shadowx=3:shadowy=3:"
                        f"text_shaping=1:"
                        f"box=1:boxcolor=0x000000AA:boxborderw={boxborderw}:"
                        f"x=(w-text_w)/2:y={y_expr}:"
                        f"fix_bounds=1:borderw=0:bordercolor=0x000000[outv]"
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
                if clip["text"]:
                    wrapped = _wrap_caption(clip["text"], max_chars_per_line=max_chars)
                    text_safe = esc(wrapped)

                    vf += (
                        f";[v1]drawtext=text='{text_safe}':"
                        f"fontfile={fontfile}:fontcolor=white:fontsize={fontsize}:"
                        f"line_spacing={line_spacing}:shadowcolor=0x000000:shadowx=3:shadowy=3:"
                        f"text_shaping=1:box=1:boxcolor=0x000000AA:boxborderw={boxborderw}:"
                        f"x=(w-text_w)/2:y={y_expr}:fix_bounds=1:borderw=0:"
                        f"enable='lt(t,{cta_start})'"
                        f"[v2]"
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


                vf += (
                    f";[v3]drawtext=text='{cta_text_safe}':"
                    f"fontfile={fontfile}:fontcolor=white:fontsize={fontsize}:"
                    f"line_spacing={line_spacing}:shadowcolor=0x000000AA:shadowx=3:shadowy=3:"
                    f"text_shaping=1:box=1:boxcolor=0x000000CC:boxborderw={boxborderw}:"
                    f"x=(w-text_w)/2:y={cta_y_expr}:fix_bounds=1:borderw=0:"
                    f"enable='gte(t,{cta_start})'"
                    f"[outv]"
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

            trimmed_files.append(trimmed_path)
            lf.write(f"file '{trimmed_path}'\n")

    # -------------------------------
    # 2. CONCAT CLIPS
    # -------------------------------

    log_step("[CONCAT] Using standard concat")

    concat_output = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", trimlist,
        "-c:v", "libx264",
        "-preset", "superfast" if optimized else "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        concat_output,
    ]

    proc = subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.stderr:
        log_step(f"[CONCAT-FFMPEG] stderr:\n{proc.stderr}")

    final_video_source = concat_output

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

    audio_inputs = []

    if music_audio:
        audio_inputs.append({
            "path": music_audio,
            "start": 0.0,
            "volume": float(music_cfg.get("volume", 0.25)),
        })

    FIRST_TTS_DELAY = 0.05
    last_tts_end = 0.0

    # Clip start times
    clip_start_times = []
    current_time = 0.0
    for clip in clips:
        clip_start_times.append(current_time)
        current_time += clip["duration"]

    # Per-clip TTS scheduling
    for idx, clip in enumerate(clips):
        tts_entry = tts_tracks[idx] if idx < len(tts_tracks) else None

        if not tts_entry or not isinstance(tts_entry, tuple):
            continue

        tts_path, tts_dur = tts_entry
        if not tts_path or not tts_dur:
            continue

        delay = FIRST_TTS_DELAY if idx == 0 else 0.0
        start_ts = clip_start_times[idx] + delay

        audio_inputs.append({
            "path": tts_path,
            "start": start_ts,
            "volume": 1.0,
        })

        last_tts_end = max(last_tts_end, start_ts + float(tts_dur))

    # CTA TTS scheduling — align with last-clip visual CTA if present
    if cta_tts_track and cta_enabled and raw_cta_text and cta_segment_len > 0.0 and last_clip_cta_start_rel is not None:
        if isinstance(cta_tts_track, tuple):
            cta_path, cta_dur = cta_tts_track
        else:
            cta_path = cta_tts_track
            cta_dur = 0.0

        if cta_path:
            last_clip_start_abs = clip_start_times[-1]
            cta_start_abs = last_clip_start_abs + last_clip_cta_start_rel

            # Just in case, don't start before all other TTS finished
            cta_start_abs = max(cta_start_abs, last_tts_end + 0.05)

            audio_inputs.append({
                "path": cta_path,
                "start": cta_start_abs,
                "volume": 1.0,
            })

            log_step(
                f"[CTA-AUDIO] last_clip_start_abs={last_clip_start_abs:.2f}, "
                f"start_abs={cta_start_abs:.2f}, "
                f"visual_rel={last_clip_cta_start_rel:.2f}"
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
        mix_labels = []

        for idx, inp in enumerate(audio_inputs):
            delay_ms = int(round(inp["start"] * 1000))
            filter_parts.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={inp['volume']}[a{idx}]"
            )
            mix_labels.append(f"[a{idx}]")

        full_filter = (
            "; ".join(filter_parts)
            + "; "
            + "".join(mix_labels)
            + f"amix=inputs={len(audio_inputs)}:normalize=0[outa]"
        )

        cmd += [
            "-filter_complex", full_filter,
            "-map", "[outa]",
            "-c:a", "aac",
            narration_out,
        ]

        log_step("[AUDIO] Mixing audio tracks…")
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
    if final_audio and actual_final_video_duration < (total_video_duration - 0.75):
        log_step(
            f"[MUX-SAFETY] Video ({actual_final_video_duration:.2f}s) shorter than "
            f"expected total ({total_video_duration:.2f}s). Enforcing -shortest."
        )
        use_shortest = True

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
