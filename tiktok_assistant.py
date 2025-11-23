# tiktok_assistant.py
import os
import re
import json
import yaml
import base64
import random
import logging
import tempfile
import subprocess

import boto3
from openai import OpenAI
from moviepy.editor import VideoFileClip

from cache_store import load_cache, save_cache

# =============================================================
# Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
log_dir = os.path.join(BASE_DIR, "logs", "assistant")
os.makedirs(log_dir, exist_ok=True)
log_file_path = os.path.join(log_dir, "tiktok_assistant.log")

logging.basicConfig(
    level=logging.INFO,
    filename=log_file_path,
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# =============================================================
# OpenAI / Models
# =============================================================
MOCK_MODE = False
VISION_MODEL = "gpt-4o"
TEXT_MODEL = "gpt-4.1"

api_key = os.getenv("open_ai_api_key")
client = OpenAI(api_key=api_key) if not MOCK_MODE else None

# =============================================================
# S3 CONFIG
# =============================================================
S3_BUCKET = os.getenv("S3_BUCKET_NAME")
S3_PREFIX_RAW = "raw_uploads/"

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)

def list_videos_from_s3(prefix: str = S3_PREFIX_RAW):
    """
    Return list of S3 keys under prefix that look like video files.
    """
    if not S3_BUCKET:
        logging.error("S3_BUCKET_NAME env var is not set.")
        return []

    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    files = []
    for obj in resp.get("Contents", []):
        key = obj["Key"]
        if key.lower().endswith((".mp4", ".mov", ".avi")):
            files.append(key)
    return files

def download_s3_video(key: str) -> str | None:
    """
    Download an S3 object to a temporary local file and return its path.
    """
    ext = os.path.splitext(key)[1]
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        s3.download_fileobj(S3_BUCKET, key, tmp)
        tmp.close()
        return tmp.name
    except Exception as e:
        logging.error(f"Failed to download {key} from S3: {e}")
        return None

# =============================================================
# Config / YAML helpers
# =============================================================
output_yaml = "config.yml"

if not os.path.exists(output_yaml):
    with open(output_yaml, "w") as _f:
        yaml.safe_dump(
            {
                "first_clip": {
                    "file": "",
                    "text": "",
                    "duration": 5,
                    "start_time": 0,
                    "text_color": "white",
                    "scale": 1.0,
                },
                "middle_clips": [],
                "last_clip": {
                    "file": "",
                    "text": "",
                    "duration": 5,
                    "start_time": 0,
                    "text_color": "yellow",
                    "scale": 1.0,
                },
                "music": {
                    "style": "luxury modern hotel aesthetic",
                    "bpm": 70,
                    "mood": "calm, elegant, sunset rooftop energy",
                    "volume": 0.25,
                },
            },
            _f,
            sort_keys=False,
        )

with open(output_yaml, "r") as f:
    config = yaml.safe_load(f) or {}

def _save_yaml():
    with open(output_yaml, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)

def _reload_config():
    global config
    with open(output_yaml, "r") as f:
        config = yaml.safe_load(f) or {}

def lowercase_filenames(cfg: dict):
    if "first_clip" in cfg and cfg["first_clip"].get("file"):
        cfg["first_clip"]["file"] = cfg["first_clip"]["file"].lower()
    for c in cfg.get("middle_clips", []):
        if c.get("file"):
            c["file"] = c["file"].lower()
    if "last_clip" in cfg and cfg["last_clip"].get("file"):
        cfg["last_clip"]["file"] = cfg["last_clip"]["file"].lower()
    return cfg

config = lowercase_filenames(config)
_ = _save_yaml()

def save_from_raw_yaml(text: str):
    global config
    config = yaml.safe_load(text) or {}
    _save_yaml()

# =============================================================
# Analysis cache & files
# =============================================================
NORMALIZED_CACHE_FILE = "normalized_cache.json"
ANALYSIS_CACHE_FILE = "analysis_cache.json"

normalized_cache = set(load_cache(NORMALIZED_CACHE_FILE) or [])
analysis_cache = set(load_cache(ANALYSIS_CACHE_FILE) or [])

ANALYSIS_DIR = "analysis"
os.makedirs(ANALYSIS_DIR, exist_ok=True)

video_analyses_cache: dict[str, str] = {}

def save_analysis_result(key: str, result: str):
    """
    Save per-clip analysis to disk and in-memory cache.
    """
    path = os.path.join(ANALYSIS_DIR, f"{key.replace('/', '_')}.txt")
    with open(path, "w") as f:
        f.write(result)
    video_analyses_cache[key] = result
    analysis_cache.add(key)
    save_cache(ANALYSIS_CACHE_FILE, list(analysis_cache))

# =============================================================
# Utilities
# =============================================================
def extract_frames(video_path: str):
    with VideoFileClip(video_path) as clip:
        duration = clip.duration
        timestamps = [
            max(0.0, duration * 0.05),
            duration * 0.50,
            max(0.0, duration * 0.90 - 0.01),
        ]
        frames = [clip.get_frame(t) for t in timestamps]
    return frames

def encode_frame(frame):
    from PIL import Image
    from io import BytesIO

    img = Image.fromarray(frame)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def analyze_video(video_path: str) -> str:
    """
    Return one short, aesthetic, persuasive sentence describing the video.
    """
    try:
        frames = extract_frames(video_path)
        images_payload = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{encode_frame(f)}"
                },
            }
            for f in frames
        ]

        prompt = [
            {
                "type": "text",
                "text": (
                    "You create viral hotel TikTok content. Describe this scene in ONE short, "
                    "emotionally compelling sentence that makes someone want to book now. "
                    "Aesthetic, vivid, persuasive. No hashtags or emojis."
                ),
            }
        ] + images_payload

        if MOCK_MODE:
            return "Golden-hour rooftop with chic cocktails and skyline calm."

        resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logging.error(f"analyze_video error: {e}")
        return f"Beautiful hotel moment; book your stay. ({e.__class__.__name__})"

def yaml_safe(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)

# Stub for old CLI debug — safe no-op in cloud
def debug_video_dimensions(folder: str):
    logging.debug("debug_video_dimensions called (no-op in cloud mode).")

# =============================================================
# Timing helpers (FIX-C style)
# =============================================================
def _real_len_s3_key(key: str) -> float:
    """
    Helper: compute real duration of S3 video key.
    Used only for duration clamping.
    """
    tmp = download_s3_video(key)
    if not tmp:
        return 0.0
    with VideoFileClip(tmp) as c:
        return float(c.duration or 0.0)

def _clamp_duration(key: str, desired: float, min_seconds: float = 2.0) -> float:
    real = _real_len_s3_key(key)
    return round(max(min_seconds, min(desired, real)), 2)

def _even_spread(durations: list[float], target_total: float | None):
    if not target_total:
        return durations
    s = sum(durations)
    if s <= 0:
        return durations
    scale = target_total / s
    return [round(max(1.0, d * scale), 2) for d in durations]

def _debug_print_timeline(cfg: dict):
    logging.debug("FIX-C durations:")
    logging.debug(f"FIRST  {cfg.get('first_clip',{}).get('file','?')} "
                  f"dur={cfg.get('first_clip',{}).get('duration',0)}s")
    for i, c in enumerate(cfg.get("middle_clips", []), 1):
        logging.debug(f"MID{i} {c.get('file','?')} dur={c.get('duration',0)}s")
    logging.debug(f"LAST   {cfg.get('last_clip',{}).get('file','?')} "
                  f"dur={cfg.get('last_clip',{}).get('duration',0)}s")

def generate_smart_timings(target_total: int | None = None, pacing: str = "default"):
    if pacing == "punchy":
        first_len = (3, 5)
        mid_len = (2, 4)
        last_len = (3, 5)
    elif pacing == "cinematic":
        first_len = (6, 8)
        mid_len = (5, 8)
        last_len = (5, 7)
    else:
        first_len = (5, 7)
        mid_len = (4, 6)
        last_len = (4, 6)

    mids = config.get("middle_clips", [])
    n_mids = len(mids)

    first_guess = random.randint(*first_len)
    mid_guesses = [random.randint(*mid_len) for _ in range(n_mids)]
    last_guess = random.randint(*last_len)

    guesses = [first_guess, *mid_guesses, last_guess]
    guesses = _even_spread(guesses, target_total)
    first_guess, *mid_guesses, last_guess = guesses

    first_key = config.get("first_clip", {}).get("file", "")
    last_key = config.get("last_clip", {}).get("file", "")

    first_dur = _clamp_duration(first_key, first_guess) if first_key else float(first_guess)
    mid_durs = []
    for i, c in enumerate(mids):
        k = c.get("file", "")
        g = mid_guesses[i]
        mid_durs.append(_clamp_duration(k, g) if k else float(g))
    last_dur = _clamp_duration(last_key, last_guess) if last_key else float(last_guess)

    return {
        "first_duration": first_dur,
        "middle_durations": mid_durs,
        "last_duration": last_dur,
    }

def apply_smart_timings(target_total: int | None = None, pacing: str = "default"):
    data = generate_smart_timings(target_total, pacing)

    if "first_clip" in config:
        config["first_clip"]["duration"] = float(data["first_duration"])
        config["first_clip"]["start_time"] = 0.0

    for clip, dur in zip(config.get("middle_clips", []), data["middle_durations"]):
        clip["duration"] = float(dur)
        clip["start_time"] = 0.0

    if "last_clip" in config:
        config["last_clip"]["duration"] = float(data["last_duration"])
        config["last_clip"]["start_time"] = 0.0

    _save_yaml()
    _debug_print_timeline(config)
    logging.info("✅ FIX-C timings applied (durations only; start_time=0).")

# =============================================================
# Overlay / captions
# =============================================================
STYLE_ALIASES = {
    "punchy": ["punchy", "hook", "short", "tiktok"],
    "descriptive": ["descriptive", "detailed", "rich"],
    "cinematic": ["cinematic", "emotional", "poetic"],
    "influencer": ["influencer", "social", "personal", "authentic"],
    "travel_blog": ["travel_blog", "travel", "review", "informative"],
}

def _style_key(s: str) -> str:
    s = (s or "").strip().lower()
    for key, aliases in STYLE_ALIASES.items():
        if s == key or s in aliases:
            return key
    return "punchy"

def _style_prompt(key: str) -> str:
    if key == "punchy":
        return "Rewrite as a short, high-retention TikTok hook (8–12 words). No hashtags or emojis."
    if key == "descriptive":
        return "Rewrite as vivid, descriptive copy (12–18 words). No hashtags or emojis."
    if key == "cinematic":
        return "Rewrite as cinematic, emotional copy (15–22 words). No hashtags or emojis."
    if key == "influencer":
        return ("Rewrite in a friendly influencer tone, speaking directly to the viewer, "
                "as if sharing a personal recommendation (12–18 words). No hashtags or emojis.")
    if key == "travel_blog":
        return ("Rewrite as an informative hotel-review style caption (14–20 words). "
                "Friendly, helpful, observational. No hashtags or emojis.")
    return "Rewrite succinctly for TikTok viewers. No hashtags or emojis."

def _rewrite_caption(seed: str, hint: str, style_key: str) -> str:
    try:
        if MOCK_MODE:
            return seed or hint or "A dreamy hotel moment above the city."
        prompt = (
            f"Seed:\n{seed or '(empty)'}\n\n"
            f"Scene hint:\n{hint or '(none)'}\n\n"
            f"Instruction:\n{_style_prompt(style_key)}\n"
            "Return only the rewritten sentence."
        )
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or (seed or "")
    except Exception as e:
        logging.error(f"_rewrite_caption error: {e}")
        return seed or ""

def apply_overlay(style: str, target: str = "all", filename: str | None = None):
    """
    Rewrite overlay text in config.yml based on style.
    """
    style_key = _style_key(style)

    # ensure we have hints
    if not video_analyses_cache:
        keys = list_videos_from_s3()
        for k in keys:
            tmp = download_s3_video(k)
            video_analyses_cache[k] = analyze_video(tmp) if tmp else ""

    def rewrite_entry(entry_file: str, entry_dict: dict):
        seed = entry_dict.get("text", "") or ""
        hint = video_analyses_cache.get(entry_file, "")
        entry_dict["text"] = _rewrite_caption(seed, hint, style_key)

    # first
    if "first_clip" in config:
        if target == "all" or (
            target == "single"
            and (config["first_clip"].get("file", "").lower() == (filename or "").lower())
        ):
            rewrite_entry(config["first_clip"]["file"], config["first_clip"])

    # middle
    for c in config.get("middle_clips", []):
        if target == "all" or (
            target == "single"
            and (c.get("file", "").lower() == (filename or "").lower())
        ):
            rewrite_entry(c["file"], c)

    # last
    if "last_clip" in config:
        if target == "all" or (
            target == "single"
            and (config["last_clip"].get("file", "").lower() == (filename or "").lower())
        ):
            rewrite_entry(config["last_clip"]["file"], config["last_clip"])

    _save_yaml()
    logging.info(f"Overlay captions updated for target={target}, style={style}")

# =============================================================
# YAML prompt builder
# =============================================================
def build_yaml_prompt(video_files: list[str], analyses: list[str]) -> str:
    cfg_first = config.get("first_clip", {}) or {}
    cfg_middle = config.get("middle_clips", []) or []
    cfg_last = config.get("last_clip", {}) or {}
    cfg_music = config.get("music", {}) or {}
    cfg_render = config.get("render", {}) or {}
    cfg_cta = config.get("cta", {}) or {}

    d_first = cfg_first.get("duration", 6.0)
    d_last = cfg_last.get("duration", 5.0)
    mids = video_files[1:-1]

    middle_yaml = ""
    for idx, v in enumerate(mids):
        prev = next(
            (c for c in cfg_middle if c.get("file", "").lower() == v.lower()), {}
        )
        mid_txt = prev.get(
            "text", analyses[idx + 1] if idx + 1 < len(analyses) else ""
        )
        mid_dur = prev.get("duration", 5.0)
        mid_col = prev.get("text_color", "white")
        mid_scale = prev.get("scale", 1.0)
        mid_voice = prev.get("voice", "alloy")

        middle_yaml += f"""
  - file: "{v}"
    text: {yaml_safe(mid_txt)}
    duration: {mid_dur}
    start_time: 0
    text_color: "{mid_col}"
    scale: {mid_scale}
    voice: "{mid_voice}"
"""

    return f"""
Generate ONLY raw YAML.

first_clip:
  file: "{video_files[0]}"
  text: {yaml_safe(cfg_first.get("text", analyses[0] if analyses else ""))}
  duration: {d_first}
  start_time: 0
  text_color: "{cfg_first.get("text_color", "white")}"
  scale: {cfg_first.get("scale", 1.0)}
  voice: "{cfg_first.get("voice", "alloy")}"

middle_clips:{middle_yaml}

last_clip:
  file: "{video_files[-1]}"
  text: {yaml_safe(cfg_last.get("text", analyses[-1] if analyses else ""))}
  duration: {d_last}
  start_time: 0
  text_color: "{cfg_last.get("text_color", "yellow")}"
  scale: {cfg_last.get("scale", 1.0)}
  voice: "{cfg_last.get("voice", "alloy")}"

music:
  style: "{cfg_music.get("style","luxury modern hotel aesthetic")}"
  bpm: {cfg_music.get("bpm",70)}
  mood: "{cfg_music.get("mood","calm, elegant, sunset rooftop energy")}"
  volume: {cfg_music.get("volume",0.25)}

render:
  fg_scale_default: {cfg_render.get("fg_scale_default",1.0)}
  tts_voice: "{cfg_render.get("tts_voice","alloy")}"
  tts_enabled: {str(cfg_render.get("tts_enabled",False)).lower()}

cta:
  enabled: {str(cfg_cta.get("enabled", False)).lower()}
  text: {yaml_safe(cfg_cta.get("text",""))}
  text_color: "{cfg_cta.get("text_color","white")}"
  duration: {cfg_cta.get("duration",3.0)}
  scale: {cfg_cta.get("scale",1.0)}
  position: "{cfg_cta.get("position","bottom")}"
  voiceover: {str(cfg_cta.get("voiceover", False)).lower()}

RULES:
- Output ONLY YAML.
- No markdown.
- No commentary.
"""

def validate_yaml(yaml_text: str) -> bool:
    try:
        data = yaml.safe_load(yaml_text)
        return (
            isinstance(data, dict)
            and "first_clip" in data
            and "middle_clips" in data
            and "last_clip" in data
            and "music" in data
            and "render" in data
        )
    except Exception:
        return False

# =============================================================
# Normalization helper (used by assistant_api)
# =============================================================
def normalize_video(input_path: str, output_path: str):
    """
    Simple FFmpeg-based normalization wrapper.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-vf",
        "scale=1080:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
