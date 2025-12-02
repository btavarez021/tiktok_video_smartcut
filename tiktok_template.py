# tiktok_template.py — MOV/MP4 SAFE, LOW-MEMORY, NO CIRCULAR IMPORTS

import os
import logging
import subprocess
import tempfile
from typing import Dict, Any
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
    """
    Word-wrap caption into multiple lines so it stays inside frame width.
    Returns a string with literal newlines, which ffmpeg drawtext will render
    as multi-line text.
    """
    text = (text or "").strip()
    if not text:
        return ""

    words = text.split()
    lines = []
    current = ""

    for w in words:
        # +1 for space if current is not empty
        extra = 1 if current else 0
        if len(current) + len(w) + extra > max_chars_per_line:
            if current:
                lines.append(current)
            current = w
        else:
            current = f"{current} {w}".strip()

    if current:
        lines.append(current)

    return "\n".join(lines)


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


# -----------------------------------------
# Core export function: edit_video   (WITH S1 SMOOTH CTA FADE)
# -----------------------------------------
# -----------------------------------------
# Core export function: edit_video
# -----------------------------------------
def edit_video(session_id: str, output_file: str = "output_tiktok_final.mp4", optimized: bool = False):
    """
    Build final TikTok-style video using FFmpeg-only pipeline:
    """
    cfg = load_config_for_session(session_id)
    if not cfg:
        raise RuntimeError("config.yml missing or empty")

    layout_mode = _get_layout_mode(cfg)
    log_step(f"[EXPORT] Building low-memory FFmpeg timeline… (layout_mode={layout_mode})")

    # CLEAN UP legacy wrong music keys from older UI
    if "render" in cfg:
        cfg["render"].pop("music_enabled", None)
        cfg["render"].pop("music_file", None)
        cfg["render"].pop("music_volume", None)

    # -------------------------------
    # Safe escape helper
    # -------------------------------
    def esc(text: str) -> str:
        text = text.replace("%", "\\%")
        if not text:
            return ""
        return (
            text.replace("\\", "\\\\")
                .replace("'", "\\'")
                .replace(":", "\\:")
        )

    # -------------------------------
    # Build clip list
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

    clips = [collect(cfg["first_clip"])]
    for m in cfg.get("middle_clips", []):
        clips.append(collect(m))
    clips.append(collect(cfg["last_clip"], is_last=True))

    # --------------------------
    # Safety: ensure clip list exists
    # --------------------------
    if not clips:
        raise RuntimeError("No clips defined in config.yml")

    # --------------------------
    # AUTO / MANUAL FG SCALE LOGIC (v2)
    # --------------------------
    render_cfg = cfg.setdefault("render", {})
    fg_mode = render_cfg.get("fgscale_mode", "auto").lower()

    if fg_mode == "auto":
        example_clip = clips[0]["file"]
        auto_zoom = compute_auto_zoom(example_clip)
        render_cfg["fgscale"] = auto_zoom
    else:
        # Ensure manual mode has a valid numeric value
        if render_cfg.get("fgscale") is None:
            render_cfg["fgscale"] = 1.10
        log_step(f"[FGSCALE] Manual mode → using fgscale={render_cfg.get('fgscale')}")


    if not clips:
        raise RuntimeError("No clips defined in config.yml")

    # ------------------------------------------------------------------
    # 0. TTS + CLIP DURATION EXTENSION (A1 + A1a)
    # ------------------------------------------------------------------
    cta_cfg = cfg.get("cta", {}) or {}
    tts_tracks, cta_tts_track = _build_per_clip_tts(cfg, clips, cta_cfg)

    # Auto-extend clip durations based on TTS (so trim uses extended durations)
    for i, clip in enumerate(clips):
        tts_entry = tts_tracks[i] if i < len(tts_tracks) else None
        if not tts_entry or not isinstance(tts_entry, tuple):
            continue

        tts_path, tts_dur = tts_entry
        if not tts_path or not tts_dur:
            continue

        needed = tts_dur + 0.35  # small safety padding
        if needed > clip["duration"]:
            log_step(
                f"[A1a] Extending clip {i+1} "
                f"duration from {clip['duration']:.2f}s → {needed:.2f}s"
            )
            clip["duration"] = needed

    # -------------------------------
    # 1. TRIM EACH CLIP (uses extended durations now)
    # -------------------------------
    trimmed_files = []
    trimlist = tempfile.NamedTemporaryFile(delete=False, suffix=".txt").name

    if layout_mode == "tiktok":
        max_chars = 22
        fontsize = 68
        line_spacing = 14
        boxborderw = 55
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        y_expr = "(h * 0.55)"
    else:
        max_chars = 38
        fontsize = 54
        line_spacing = 8
        boxborderw = 35
        fontfile = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        y_expr = "h-(text_h*1.8)-150"

    with open(trimlist, "w") as lf:
        for clip in clips:
            trimmed_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
            vf = "scale=1080:-2,setsar=1"

            # foreground scaling (read from YAML)
            render_cfg = cfg.get("render", {})
            fg_scale = float(render_cfg.get("fgscale", 1.10))  # fallback better than 1.0

            # clamp to safe values
            fg_scale = min(max(fg_scale, 1.0), 1.25)


            # ===== 1. BASE FG + BG chain (required for filter_complex) =====
            vf = (
                f"[0:v]scale=1080:-2,setsar=1,boxblur=30:1[bg];"
                f"[0:v]scale=iw*{fg_scale}:ih*{fg_scale},setsar=1[fg];"
                f"[bg][fg]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2[v1]"
            )

            # ===== 2. CAPTIONS (attach to v1 → outv) =====
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
                    f"fix_bounds=1:borderw=2:bordercolor=0x000000[outv]"
                )
            else:
                # No captions → just forward video
                vf += ";[v1]copy[outv]"

            # ===== 3. CORRECT FFmpeg invocation =====
            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(clip["start"]),
                "-i", clip["file"],
                "-t", str(clip["duration"]),
                "-filter_complex", vf,   # IMPORTANT
                "-map", "[outv]",        # IMPORTANT
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
    # 2. CONCAT
    # -------------------------------
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

    log_step("[CONCAT] Merging all clips…")
    subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    final_video_source = concat_output

    # ------------------------------------------------------------------
    # 3. CTA VIDEO EXTENSION + CTA BLUR/TEXT (Smooth Option A)
    # ------------------------------------------------------------------
    cta_enabled = cta_cfg.get("enabled", False)
    cta_text = esc(cta_cfg.get("text", ""))
    cta_config_dur = float(cta_cfg.get("duration", 3.0))

    # How long is CTA VOICE actually?
    if cta_tts_track and isinstance(cta_tts_track, tuple):
        _, cta_voice_dur = cta_tts_track
        cta_voice_dur = cta_voice_dur or 0.0
    else:
        cta_voice_dur = 0.0

    # CTA duration = max(config, voiceover)
    cta_segment_len = max(cta_config_dur, cta_voice_dur) if (cta_enabled and cta_text) else 0.0

    # Total duration of clips after extension
    expected_total = sum([clip["duration"] for clip in clips])

    # CTA will be applied AFTER clips, but not padded yet
    cta_start_time = None


    # -------------------------------------
    # CTA VISUAL OVERLAY — FINAL CARD MODE
    # -------------------------------------
    if cta_enabled and cta_text and cta_segment_len > 0:

        # Get actual video length
        try:
            vid_total = float(
                subprocess.check_output(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        final_video_source,
                    ]
                ).decode().strip()
            )
        except Exception:
            vid_total = expected_total

        # CTA occupies the final tail: [vid_total - len, vid_total]
        cta_start = max(vid_total - cta_segment_len, 0.0)
        cta_end = vid_total
        cta_start_time = cta_start  # used later for CTA audio

        # Only apply if CTA window is meaningful
        if (cta_end - cta_start) > 0.05:
            blurred = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name

            # Fade length for blur
            fade_len = min(0.3, max(cta_segment_len * 0.5, 0.15))

            # FFmpeg CTA overlay (blur + CTA text)
            filter_complex = (
                f"[0:v]format=rgba,split=2[base][tmp];"
                f"[tmp]boxblur=10:1,"
                f"fade=t=in:st={cta_start}:d={fade_len}:alpha=1[blur];"
                f"[base][blur]overlay=format=auto[vb];"
                f"[vb]drawtext=text='{cta_text}':"
                f"fontfile={fontfile}:"
                f"fontcolor=white:fontsize=60:"
                f"x=(w-text_w)/2:y=h-220:"
                f"enable='between(t,{cta_start},{cta_end})'[vout]"
            )

            blur_cmd = [
                "ffmpeg", "-y",
                "-i", final_video_source,
                "-filter_complex", filter_complex,
                "-map", "[vout]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                blurred,
            ]

            log_step("[CTA] Applying smooth CTA blur + text…")
            blur_proc = subprocess.run(
                blur_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            if os.path.exists(blurred) and os.path.getsize(blurred) > 150 * 1024:
                final_video_source = blurred
            else:
                log_step("[CTA] CTA blur failed → keeping original video.")


    # -------------------------------------
    # FINAL PADDING (AFTER CTA ONLY)
    # -------------------------------------
    # After CTA, ensure video lasts long enough for CTA or voiceover
    try:
        vid_total_after_cta = float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    final_video_source,
                ]
            ).decode().strip()
        )
    except Exception:
        vid_total_after_cta = expected_total

    final_needed = expected_total + cta_segment_len

    # Only pad AFTER CTA, so CTA is always at the REAL END
    if final_needed > vid_total_after_cta + 0.05:
        pad_len = final_needed - vid_total_after_cta
        log_step(f"[FINAL PAD] Adding {pad_len:.2f}s freeze-frame after CTA…")

        extended = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4").name
        subprocess.run([
            "ffmpeg", "-y",
            "-i", final_video_source,
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad_len}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            extended,
        ])

        if os.path.exists(extended) and os.path.getsize(extended) > 150 * 1024:
            final_video_source = extended


    # -------------------------------
    # 4. AUDIO PIPELINE
    # -------------------------------
    log_step("[AUDIO] Building audio timeline…")
    audio_inputs = []
    current_time = 0.0

    # Per-clip TTS aligned to each clip in sequence
    FIRST_TTS_DELAY = 0.25  # adjust if needed: 0.20–0.35 feels perfect

    for idx, clip in enumerate(clips):
        tts_entry = tts_tracks[idx] if idx < len(tts_tracks) else None
        if tts_entry and isinstance(tts_entry, tuple):
            tts_path, tts_dur = tts_entry
            if tts_path:
                delay = FIRST_TTS_DELAY if idx == 0 else 0.0
                audio_inputs.append({
                    "path": tts_path,
                    "start": current_time + delay,
                    "volume": 1.0,
                })
        current_time += clip["duration"]


    # CTA TTS: start exactly after the last clip (same as CTA blur window)
    if cta_tts_track and cta_enabled and cta_text:
        if isinstance(cta_tts_track, tuple):
            cta_path, _ = cta_tts_track
        else:
            cta_path = cta_tts_track

        if cta_path:
            audio_inputs.append({
                "path": cta_path,
                "start": expected_total,   # aligns with CTA blur start
                "volume": 1.0,
            })

    final_audio = None

    if audio_inputs:
        narration_out = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        cmd = ["ffmpeg", "-y"]

        for inp in audio_inputs:
            cmd += ["-i", inp["path"]]

        parts = []
        labels = []

        for idx, inp in enumerate(audio_inputs):
            delay_ms = int(round(inp["start"] * 1000))
            parts.append(
                f"[{idx}:a]adelay={delay_ms}|{delay_ms},"
                f"volume={inp['volume']}[a{idx}]"
            )
            labels.append(f"[a{idx}]")

        filter_complex = (
            "; ".join(parts)
            + "; "
            + "".join(labels)
            + f"amix=inputs={len(audio_inputs)}:normalize=0[outa]"
        )

        cmd += [
            "-filter_complex", filter_complex,
            "-map", "[outa]",
            "-c:a", "aac",
            narration_out,
        ]

        log_step("[AUDIO] Mixing per-clip TTS (and CTA)…")
        mix_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if mix_proc.stderr:
            log_step(f"[AUDIO-FFMPEG] stderr:\n{mix_proc.stderr}")

        if os.path.exists(narration_out) and os.path.getsize(narration_out) > 1024:
            final_audio = narration_out
        else:
            log_step("[AUDIO] Narration mix invalid → disabling narration.")
            final_audio = None

    # -------------------------------
    # Mix music
    # -------------------------------
    try:
        total_duration = float(
            subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    final_video_source,
                ]
            ).decode().strip()
        )
    except Exception:
        total_duration = expected_total + cta_segment_len

    music_audio = _build_music_audio(cfg, total_duration)

    if final_audio and music_audio:
        mixed = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a").name
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", final_audio,
                "-i", music_audio,
                "-filter_complex",
                "[0:a]volume=1.0[a0]; "
                "[1:a]volume=0.25[a1]; "
                "[a0][a1]amix=inputs=2:normalize=0[out]",
                "-map", "[out]",
                "-c:a", "aac",
                mixed,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if os.path.exists(mixed) and os.path.getsize(mixed) > 1024:
            final_audio = mixed
        else:
            log_step("[AUDIO] Music+TTS mix invalid → falling back to narration only.")

    # -------------------------------
    # 5. FINAL MUX
    # -------------------------------
    final_output = os.path.abspath(os.path.join(BASE_DIR, output_file))
    mux_cmd = ["ffmpeg", "-y", "-i", final_video_source]

    if final_audio:
        mux_cmd += [
            "-i", final_audio,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            final_output,
        ]
    else:
        mux_cmd += [
            "-c:v", "copy",
            final_output,
        ]

    mux_proc = subprocess.run(mux_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if mux_proc.stderr:
        log_step(f"[MUX-FFMPEG] stderr:\n{mux_proc.stderr}")

    if not os.path.exists(final_output):
        raise RuntimeError(f"Final output missing! {final_output}")

    log_step(f"[EXPORT] Video rendered: {final_output}")
    return final_output