# tiktok_assistant.py  — Assistant-only (FIX-C everywhere)

import os
import re
import json
import yaml
import base64
import random
# from dotenv import load_dotenv
from openai import OpenAI
from moviepy.editor import VideoFileClip  # only for durations + frame grabs
from tiktok_template import normalize_video_ffmpeg
import logging

# =============================================================
# Configure Logging
# =============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 
log_dir = os.path.join(BASE_DIR, "logs/assistant") # Define the directory path
log_file_path = os.path.join(log_dir, "tiktok_assistant.log")

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

# ==============================
# CONFIG
# ==============================
MOCK_MODE = False
INSTANT_APPLY = True  # toggle with: /instant on | /instant off
VISION_MODEL = "gpt-4o"     # for scene description from frames
TEXT_MODEL   = "gpt-4.1"    # for captions/hashtags/hooks etc.

output_yaml  = "config.yml"
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
video_folder = os.path.join(BASE_DIR, "tik_tok_downloads")


# ==============================
# SETUP
# ==============================
# load_dotenv()
api_key = os.getenv("open_ai_api_key")
client = OpenAI(api_key=api_key) if not MOCK_MODE else None

# Load config (create minimal if missing)
if not os.path.exists(output_yaml):
    with open(output_yaml, "w") as _f:
        yaml.safe_dump({
            "first_clip":   {"file": "", "text": "", "duration": 5, "start_time": 0, "text_color": "white", "scale": 1.0},
            "middle_clips": [],
            "last_clip":    {"file": "", "text": "", "duration": 5, "start_time": 0, "text_color": "yellow", "scale": 1.0},
            "music":        {"style": "luxury modern hotel aesthetic", "bpm": 70, "mood": "calm, elegant, sunset rooftop energy", "volume": 0.25}
        }, _f, sort_keys=False)

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
_save_yaml()

# Discover videos in folder (just names)
video_files = sorted([
    f for f in os.listdir(video_folder)
    if f.lower().endswith((".mp4", ".mov", ".avi"))
])


if len(video_files) < 2:
    logging.error("⚠️ Need at least 2 videos in tik_tok_downloads/")
# Cache for quick AI descriptions
video_analyses_cache = {}

# ==============================
# UTILITIES
# ==============================
def resolve_path(filename: str | None):
    if not filename:
        return None
    full = os.path.join(video_folder, filename)
    return full if os.path.exists(full) else None

def extract_frames(video_path):
    """Grab 3 frames (5%, 50%, 90%) for VLM analysis."""
    with VideoFileClip(video_path) as clip:
        duration = clip.duration
        timestamps = [max(0.0, duration * 0.05), duration * 0.50, max(0.0, duration * 0.90 - 0.01)]
        frames = [clip.get_frame(t) for t in timestamps]
    return frames

def encode_frame(frame):
    from PIL import Image
    from io import BytesIO
    img = Image.fromarray(frame)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def analyze_video(video_path):
    """Return 1 aesthetic, persuasive sentence describing scene."""
    try:
        frames = extract_frames(video_path)
        images_payload = [{
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{encode_frame(f)}"}
        } for f in frames]

        prompt = [
            {
                "type": "text",
                "text": (
                    "You create viral hotel TikTok content. Describe this scene in ONE short, "
                    "emotionally compelling sentence that makes someone want to book now. "
                    "Aesthetic, vivid, persuasive. No hashtags or emojis."
                )
            }
        ] + images_payload

        if MOCK_MODE:
            return "Golden-hour rooftop with chic cocktails and skyline calm."
        resp = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"Beautiful hotel moment; book your stay. ({e.__class__.__name__})"

def yaml_safe(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)

# ==============================
# FIX-C HELPERS (durations-only engine)
# ==============================
def _real_len(fname: str) -> float:
    p = resolve_path(fname)
    if not p:
        return 0.0
    with VideoFileClip(p) as c:
        return float(c.duration or 0.0)

def _clamp_duration(fname: str, desired: float, min_seconds: float = 2.0) -> float:
    """Never exceed real clip length; never drop below min_seconds."""
    real = _real_len(fname)
    return round(max(min_seconds, min(desired, real)), 2)

def _even_spread(durations: list[float], target_total: float | None):
    """Rescale a set of durations to meet target_total (if provided)."""
    if not target_total:
        return durations
    s = sum(durations)
    if s <= 0:
        return durations
    scale = target_total / s
    return [round(max(1.0, d * scale), 2) for d in durations]

def _debug_print_timeline(cfg: dict):
    logging.debug("\n🧭 FIX-C plan (all local trims, start_time=0):")
    logging.debug(f"FIRST   {cfg['first_clip'].get('file','?')}  dur={cfg['first_clip'].get('duration',0)}s")
    for i, c in enumerate(cfg.get("middle_clips", []), 1):
        logging.debug(f"MIDDLE{i} {c.get('file','?')}  dur={c.get('duration',0)}s")
    logging.debug(f"LAST    {cfg['last_clip'].get('file','?')}  dur={cfg['last_clip'].get('duration',0)}s\n")

# ==============================
# FIX-C: generate/apply timings
# ==============================
def generate_smart_timings(target_total: int | None = None, pacing: str = "default"):
    """
    Returns durations only. All start_time stay 0 (local trims).
    """
    # pacing windows
    if pacing == "punchy":
        first_len = (3, 5); mid_len = (2, 4); last_len = (3, 5)
    elif pacing == "cinematic":
        first_len = (6, 8); mid_len = (5, 8); last_len = (5, 7)
    else:
        first_len = (5, 7); mid_len = (4, 6); last_len = (4, 6)

    mids = config.get("middle_clips", [])
    n_mids = len(mids)

    first_guess = random.randint(*first_len)
    mid_guesses = [random.randint(*mid_len) for _ in range(n_mids)]
    last_guess  = random.randint(*last_len)

    guesses = [first_guess, *mid_guesses, last_guess]
    guesses = _even_spread(guesses, target_total)
    first_guess, *mid_guesses, last_guess = guesses

    # clamp to real lengths and safeguard minimums
    first_dur = _clamp_duration(config["first_clip"]["file"], first_guess)
    mid_durs  = [_clamp_duration(mids[i]["file"], mid_guesses[i]) for i in range(n_mids)]
    last_dur  = _clamp_duration(config["last_clip"]["file"], last_guess)

    return {
        "first_duration": first_dur,
        "middle_durations": mid_durs,
        "last_duration": last_dur,
    }

def apply_smart_timings(target_total: int | None = None, pacing: str = "default"):
    data = generate_smart_timings(target_total, pacing)

    # first
    config["first_clip"]["duration"]   = float(data["first_duration"])
    config["first_clip"]["start_time"] = 0.0

    # middle
    for clip, dur in zip(config.get("middle_clips", []), data["middle_durations"]):
        clip["duration"]   = float(dur)
        clip["start_time"] = 0.0

    # last
    config["last_clip"]["duration"]   = float(data["last_duration"])
    config["last_clip"]["start_time"] = 0.0

    if INSTANT_APPLY:
        _save_yaml()
    _debug_print_timeline(config)
    print("✅ FIX-C timings applied (durations only; all start_time=0).")
    logging.info("✅ FIX-C timings applied (durations only; all start_time=0).")

# ==============================
# Overlay / Text rewriting
# ==============================
STYLE_ALIASES = {
    "punchy":       ["punchy", "hook", "short", "tiktok"],
    "descriptive":  ["descriptive", "detailed", "rich"],
    "cinematic":    ["cinematic", "emotional", "poetic"],
    "influencer":   ["influencer", "social", "personal", "authentic"],
    "travel_blog":  ["travel_blog", "travel", "review", "informative"],
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

    # ✅ NEW STYLES
    if key == "influencer":
        return "Rewrite in a friendly influencer tone, speaking directly to the viewer, as if sharing a personal recommendation (12–18 words). No hashtags or emojis."

    if key == "travel_blog":
        return "Rewrite as an informative hotel-review style caption (14-20 words). Friendly, helpful, observational. No hashtags or emojis."

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
            temperature=0.8
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or (seed or "")
    except Exception:
        return seed or ""

def save_from_raw_yaml(text: str):
    global config
    config = yaml.safe_load(text) or {}
    _save_yaml()

def apply_overlay(style: str, target: str = "all", filename: str | None = None):
    style_key = _style_key(style)

    # ensure we have hints
    if not video_analyses_cache:
        for v in video_files:
            p = resolve_path(v)
            video_analyses_cache[v] = analyze_video(p) if p else ""

    def rewrite_entry(entry_file: str, entry_dict: dict):
        seed = entry_dict.get("text", "") or ""
        hint = video_analyses_cache.get(entry_file, "")
        entry_dict["text"] = _rewrite_caption(seed, hint, style_key)

    # first
    if "first_clip" in config:
        if target == "all" or (target == "single" and (config["first_clip"].get("file","").lower() == (filename or "").lower())):
            rewrite_entry(config["first_clip"]["file"], config["first_clip"])

    # middle
    for c in config.get("middle_clips", []):
        if target == "all" or (target == "single" and (c.get("file","").lower() == (filename or "").lower())):
            rewrite_entry(c["file"], c)

    # last
    if "last_clip" in config:
        if target == "all" or (target == "single" and (config["last_clip"].get("file","").lower() == (filename or "").lower())):
            rewrite_entry(config["last_clip"]["file"], config["last_clip"])

    if INSTANT_APPLY:
        _save_yaml()

# ==============================
# Smart zoom helper (advice → scale)
# ==============================
def smart_zoom_value(video_path: str):
    with VideoFileClip(video_path) as clip:
        w, h = clip.w, clip.h
    ratio = w / h
    if ratio < 0.7:            # portrait already ideal
        return 1.0
    if 0.7 <= ratio <= 1.1:    # square slight push
        return 1.1
    return 1.25                # landscape needs more zoom

# ==============================
# Fuzzy scale interpreter
# ==============================
SCALE_KEYWORDS = {
    "slightly zoom out": 0.95,
    "zoom out slightly": 0.95,
    "zoom out a little": 0.95,
    "zoom out a bit": 0.9,
    "zoom out": 0.9,
    "zoom out a lot": 0.8,
    "zoom way out": 0.75,

    "slightly zoom in": 1.05,
    "zoom in slightly": 1.05,
    "zoom in a little": 1.05,
    "zoom in a bit": 1.1,
    "zoom in": 1.1,
    "zoom in a lot": 1.25,
    "zoom way in": 1.3,
}
SIZE_PHRASES = {
    "too big": 0.9,
    "too close": 0.9,
    "feels too close": 0.9,
    "needs breathing room": 0.9,

    "too small": 1.1,
    "needs to fill the frame": 1.1,
    "make it bigger": 1.1,
    "dramatic": 1.05,
}
def fuzzy_scale_interpret(user_text: str):
    text = user_text.strip()
    lower = text.lower()

    m = re.search(r"(img_\d+\.(?:mov|mp4|avi))", lower, flags=re.IGNORECASE)
    filename = m.group(1).lower() if m else None
    if not filename:
        return None

    n = re.search(r"\b(\d\.\d+|\d+)\b", lower)
    if n:
        try:
            return filename, float(n.group(1))
        except Exception:
            pass

    if " zoom in" in lower or lower.endswith(" in"):
        return filename, 1.1
    if " zoom out" in lower or lower.endswith(" out"):
        return filename, 0.9

    for phrase, value in SCALE_KEYWORDS.items():
        if phrase in lower:
            return filename, value
    for phrase, value in SIZE_PHRASES.items():
        if phrase in lower:
            return filename, value
    return None

def update_scale_in_config(filename: str, scale_value: float) -> bool:
    updated = False
    fn = (filename or "").lower()

    if config.get("first_clip", {}).get("file", "").lower() == fn:
        config["first_clip"]["scale"] = float(scale_value)
        updated = True

    for c in config.get("middle_clips", []):
        if (c.get("file","").lower() == fn):
            c["scale"] = float(scale_value)
            updated = True

    if config.get("last_clip", {}).get("file", "").lower() == fn:
        config["last_clip"]["scale"] = float(scale_value)
        updated = True

    if updated and INSTANT_APPLY:
        _save_yaml()
    return updated

# ==============================
# /yaml builder (correct CTA + TTS)
# ==============================
def build_yaml_prompt(video_files, analyses):
    cfg_first   = config.get("first_clip", {}) or {}
    cfg_middle  = config.get("middle_clips", []) or []
    cfg_last    = config.get("last_clip", {}) or {}
    cfg_music   = config.get("music", {}) or {}
    cfg_render  = config.get("render", {}) or {}
    cfg_cta     = config.get("cta", {}) or {}

    # ----- DURATIONS -----
    d_first  = cfg_first.get("duration", 6.0)
    d_last   = cfg_last.get("duration", 5.0)
    mids     = video_files[1:-1]

    # ----- MIDDLE CLIPS -----
    middle_yaml = ""
    for idx, v in enumerate(mids):
        prev = next((c for c in cfg_middle if c.get("file","").lower()==v.lower()), {})
        mid_txt   = prev.get("text", analyses[idx+1] if idx+1 < len(analyses) else "")
        mid_dur   = prev.get("duration", 5.0)
        mid_col   = prev.get("text_color", "white")
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

    # ----- FINAL YAML -----
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
  text_color: "{cfg_last.get("text_color","yellow")}"
  scale: {cfg_last.get("scale",1.0)}
  voice: "{cfg_last.get("voice","alloy")}"

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

# ==============================
# MUSIC (text suggestions only)
# ==============================
def run_music_command():
    if not video_analyses_cache:
        logging.warning("\n⚠️ Run /analyze first.\n")
        return
    analyses_text = "\n".join(f"{v}: {d}" for v, d in video_analyses_cache.items())
    prompt = f"""
Here are the hotel scenes:

{analyses_text}

Recommend PERFECT TikTok background music to maximize bookings.
Include:
- genre
- vibe/mood
- BPM
- energy level
- why it converts for hotel travel
- 3 alt genres
- volume suggestion
- fade in/out suggestion
"""
    if MOCK_MODE:
        logging.info("\nAssistant:\nLofi house, 110 BPM, warm/night…\n")
        return
    response = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9
    )
    logging.info("\nAssistant:\n", response.choices[0].message.content, "\n")

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
                logging.debug(f"⚠️ Error reading {f}: {e}")
    logging.debug("-" * 50)

# ==============================
# MAIN LOOP
# ==============================
def main():
    print("""\n
=============================================================================
   TIKTOK CREATOR ASSISTANT — QUICK GUIDE (assistant-only, FIX-C timings)
=============================================================================

IMPORTANT: Always run /analyze first so I understand your videos.

COMMANDS:
--------------------------------------------------
/analyze      → Analyze all videos in the 'tik_tok_downloads' folder
/yaml         → Generate new config.yml automatically
/hooks        → Generate viral TikTok opening hooks
/captions     → Suggest caption ideas
/hashtags     → Create 15 optimized hashtags
/overlay      → [descriptive | punchy| cinematic] (Puts captions on your videos depending on mode you choose)
/story        → Generate a 12-second TikTok story script
/ideas        → Suggest creative video ideas
/cta          → Suggest booking call-to-actions
/music        → Recommend background music (genre/mood/BPM)
/timings      → Auto-calculate FIX-C timing durations for all clips
/timings smart [seconds | punchy | cinematic]
              → Let AI balance durations and pacing dynamically
/scale        → Adjust zoom for one clip or all
                 e.g. /scale img_3780.mov 1.2
/fgscale      → Adjust global foreground scale (blur-border intensity)
                 e.g. /fgscale 0.85  or  "make the foreground smaller"
/instant on|off
              → Enable or disable instant YAML saving after edits
/export       → Render and export final TikTok video
                 Uses config.yml settings for timing, text, scale, and blur
/mock on|off  → Off = Uses AI Models and interacts with you, On = Better for Testing/Offline 

VOICEOVER (TTS) COMMANDS:
--------------------------------------------------
/tts on       → Enable AI voiceover narration
/tts off      → Disable voiceover (default: text only mode)
/tts          → Check current TTS status
/voice alloy  → Change TTS voice (options: alloy, verse, coral, sage, amber, onyx)

/ 💬 Natural language toggles also work:
   "enable voiceover"     → turns on AI narration
   "disable narration"    → turns it off
   "check voiceover"      → shows whether it’s on or off
   "use verse voice"      → switches to a new voice

💡 Examples:
--------------------------------------------------
'zoom out on all videos'              → reduces zoom for every clip
'zoom in on IMG_3753.mov'             → zooms only that clip
'make the foreground smaller'         → increases blur border (reduces fgscale)
'less blur border'                    → decreases blur border (raises fgscale)
'change text color to yellow on all'  → updates overlay text color
'enable voiceover narration'          → adds AI-generated voice
'turn off voiceover'                  → disables narration

🗂 Folder setup:
--------------------------------------------------
🎞️  Place all videos in:      ./tik_tok_downloads/
🧠  Config file location:      ./config.yml
🧩  Normalized cache folder:   ./normalized_cache/
🎥  Exported final video:      ./output_tiktok_final.mp4
🎵  Optional music files:      ./music/
🪄  Google Drive downloader:   Downloads into ./tik_tok_downloads/

NOTES:
--------------------------------------------------
- Run /analyze before generating YAML or timings.
- Text from config.yml is used both for overlays and voiceovers.
- Blurred background and cinematic framing apply automatically.
- FIX-C engine ensures all start_times = 0 with locally trimmed durations.
- You can toggle TTS narration at any time without re-rendering YAML.

Type exit or quit to leave.
-----------------------------------------------------------------------------
""")
    global MOCK_MODE
    global INSTANT_APPLY

    while True:
        user_message = input("Say something: ").strip()
        msg = user_message.lower().strip()

        try:
            # Exit
            if msg in ["exit", "quit", "q"]:
                print("Goodbye!")
                logging.info("Goodbye!")
                break

            # Analyze
            if msg == "/analyze":
                print("\n🔍 Analyzing videos...\n")
                logging.info("\n🔍 Analyzing videos...\n")

                debug_video_dimensions(video_folder)
                os.makedirs("normalized_cache", exist_ok=True)

                # Track which videos will be (re)analyzed
                reanalyzed = []
                skipped = []

                for v in video_files:
                    input_path = os.path.join(video_folder, v)
                    normalized_path = os.path.join("normalized_cache", v)

                    # Normalize only once if not already done
                    if not os.path.exists(normalized_path):
                        logging.info(f"⚙️ Normalizing {v} for analysis...")
                        print(f"⚙️ Normalizing {v} for analysis...")
                        normalize_video_ffmpeg(input_path, normalized_path)
                    else:
                        print(f"✅ Using cached normalized file for {v}")
                        logging.info(f"✅ Using cached normalized file for {v}")

                    # --- Smart skip logic ---
                    file_mod_time = os.path.getmtime(input_path)
                    cache_key = f"{v}|{file_mod_time}"
                    prev_key = getattr(analyze_video, "_last_key", None)

                    if prev_key == cache_key and v in video_analyses_cache:
                        print(f"⏩ Skipping {v} (unchanged since last analysis)")
                        logging.info(f"⏩ Skipping {v} (unchanged since last analysis)")
                        skipped.append(v)
                        continue

                    # Analyze new/changed file
                    desc = analyze_video(normalized_path)
                    video_analyses_cache[v] = desc
                    analyze_video._last_key = cache_key  # store last analyzed file+timestamp
                    reanalyzed.append(v)
                    print(f"{v}: {desc}")

                print("\n--------------------------------------------------")
                if reanalyzed:
                    print(f"✅ Re-analyzed {len(reanalyzed)} videos: {', '.join(reanalyzed)}")
                if skipped:
                    print(f"⏩ Skipped {len(skipped)} unchanged videos: {', '.join(skipped)}")
                print("--------------------------------------------------\n")

                print("✅ Analysis complete. You can now run /yaml, /overlay, /timings.\n")
                continue


            # YAML
            if msg == "/yaml":
                if not video_analyses_cache:
                    print("\n⚠️ Run /analyze first.\n")
                    continue
                analyses = [video_analyses_cache.get(v, "") for v in video_files if v]
                yaml_prompt = build_yaml_prompt(video_files, analyses)

                if MOCK_MODE:
                    yaml_text = yaml_prompt  # pretend the model echoed YAML
                else:
                    response = client.chat.completions.create(
                        model=TEXT_MODEL,
                        messages=[{"role": "user", "content": yaml_prompt}],
                        temperature=0.2
                    )
                    yaml_text = (response.choices[0].message.content or "").strip()

                # scrub code fences if any
                yaml_text = yaml_text.replace("```yaml", "").replace("```", "").strip()

                if not validate_yaml(yaml_text):
                    print("\n❌ Invalid YAML from model. Try again.\n")
                    logging.warning("Invalid YAML returned from model — likely missing fields or bad indentation.")
                    continue

                # Load, force lowercase filenames, preserve start_time=0 as designed
                cfg = yaml.safe_load(yaml_text) or {}
                cfg = lowercase_filenames(cfg)
                with open(output_yaml, "w") as f:
                    yaml.safe_dump(cfg, f, sort_keys=False)

                _reload_config()
                logging.info(f"✅ YAML saved successfully: {output_yaml}")
                print("\n✅ YAML saved:", output_yaml, "\n")
                print(yaml.safe_dump(config, sort_keys=False))
                continue
            
            if msg.startswith("/voice"):
                parts = msg.split()
                if len(parts) == 2:
                    new_voice = parts[1].lower()
                    config.setdefault("render", {})["tts_voice"] = new_voice
                    _save_yaml()
                    print(f"🎤 Default voice changed to '{new_voice}'")
                    logging.info(f"Voice changed to {new_voice} via command.")
                else:
                    print("Usage: /voice alloy|verse|coral|sage|amber|onyx")
                continue

            if msg.startswith("/fgscale"):
                try:
                    val = float(msg.split()[1])
                    if val < 0.5 or val > 2.0:
                        print("fg_scale_default must be between 0.5 and 2.0.")
                        continue
                    config.setdefault("render", {})["fg_scale_default"] = val
                    _save_yaml()
                    print(f"✅ Foreground default scale updated → {val}")
                except:
                    print("❌ Usage: /fgscale <number> (e.g. /fgscale 0.88)")
                continue

                # =========================================================
                # Export final TikTok video directly from the assistant
                # =========================================================
            if msg == "/export" or "export video" in msg:
                print("\n🎬 Exporting your TikTok video...\n")
                logging.info("User triggered video export process.")

                try:
                    from tiktok_template import edit_video  # adjust if your file is named differently
                    output_path = os.path.join(BASE_DIR, "output_tiktok_final.mp4")

                    edit_video(output_file=output_path)

                    print(f"\n✅ Export complete! Video saved at: {output_path}\n")
                    logging.info(f"Export complete. File saved to {output_path}")

                except Exception as e:
                    print(f"\n❌ Export failed: {e}\n")
                    logging.error(f"Export failed: {e}", exc_info=True)  # ⬅️ upgraded to error
                continue

            
            #Natural Langage for exporting video
            if any(p in msg for p in ["export video", "render final", "create final video", "make the tiktok"]):
                print("🎥 Starting final render using your current config...")
                from tiktok_template import edit_video
                output_path = os.path.join(BASE_DIR, "output_tiktok_final.mp4")
                edit_video(output_file=output_path)
                print(f"✅ Done! Video exported to {output_path}")
                continue

            # --- Natural language control for foreground (blur border) scale ---
            if any(word in msg for word in ["foreground", "border", "blur border", "inset"]):
                val = None
                current_val = config.get("render", {}).get("fg_scale_default", 1.0)

                match = re.search(r"\b(\d\.\d+|\d+)\b", msg)
                if match:
                    val = float(match.group(1))
                else:
                    # Interpret natural phrases specifically for blur-border effect
                    if "more blur" in msg or "smaller" in msg or "inset" in msg or "reduce foreground" in msg:
                        val = max(current_val - 0.05, 0.7)
                    elif "less blur" in msg or "larger" in msg or "expand" in msg or "bring forward" in msg:
                        val = min(current_val + 0.05, 1.1)

                if val:
                    config.setdefault("render", {})["fg_scale_default"] = val
                    _save_yaml()
                    print(f"✅ Foreground scale updated → {current_val:.2f} → {val:.2f}")
                else:
                    print("🤔 Sorry, I couldn’t understand. Try phrases like 'increase blur border' or '/fgscale 0.88'.")
                continue

            # Timings (FIX-C everywhere)
            if msg == "/timings":
                apply_smart_timings()
                continue

            if msg.startswith("/timings smart"):
                parts = msg.split()
                if len(parts) == 2:
                    apply_smart_timings()
                    continue
                if len(parts) == 3 and parts[2].isdigit():
                    apply_smart_timings(target_total=int(parts[2]))
                    continue
                if "punchy" in msg:
                    apply_smart_timings(pacing="punchy")
                    continue
                if "cinematic" in msg:
                    apply_smart_timings(pacing="cinematic")
                    continue
                print("❌ Usage: /timings smart [seconds] [punchy|cinematic]")
                continue

            # Viral text commands (simple, using scene analyses)
            viral_commands = {
                "/hooks":    "Give 10 high-retention TikTok hooks.",
                "/captions": "Give 10 strong TikTok captions.",
                "/hashtags": "Give 15 viral hashtags optimized for hotel travel.",
                "/story":    "Write a 12-second TikTok storyline using these scenes.",
                "/ideas":    "Give 10 TikTok ideas inspired by these videos.",
                "/cta":      "Give 10 strong booking call-to-actions.",
            }
            if msg in viral_commands:
                if not video_analyses_cache:
                    print("\n⚠️ Run /analyze first.\n")
                    continue
                analyses_text = "\n".join(f"{v}: {d}" for v, d in video_analyses_cache.items())
                prompt = f"Here is what the videos contain:\n{analyses_text}\n\nTask:\n{viral_commands[msg]}"
                if MOCK_MODE:
                    print("\nAssistant:\n(Mocked text output)\n")
                else:
                    resp = client.chat.completions.create(
                        model=TEXT_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.9
                    )
                    print("\nAssistant:\n", resp.choices[0].message.content, "\n")
                continue

            # Music
            if msg == "/music":
                run_music_command()
                continue

            # ---------------------------------------------------------
            # /mock on | off  (Turn API mocking ON or OFF)
            # ---------------------------------------------------------
            if msg.startswith("/mock"):
                if "on" in msg:
                    MOCK_MODE = True
                    print("🧪 MOCK MODE ENABLED — no real API calls will be made.")
                    logging.info("MOCK MODE ENABLED")
                elif "off" in msg:
                    MOCK_MODE = False
                    print("🔌 MOCK MODE DISABLED — using real API calls.")
                    logging.info("MOCK MODE DISABLED")
                else:
                    print("Usage: /mock on  or  /mock off")
                continue
            # /tts on|off|status

            # ---------------------------------------------------------
            # /cta voiceover on/off  (toggle CTA TTS)
            # ---------------------------------------------------------
            if msg.startswith("/cta voiceover"):
                parts = msg.split()
                if len(parts) == 3:
                    action = parts[2].lower()
                    if action == "on":
                        config.setdefault("cta", {})["voiceover"] = True
                        _save_yaml()
                        print("🎙️ CTA voiceover ENABLED.")
                    elif action == "off":
                        config.setdefault("cta", {})["voiceover"] = False
                        _save_yaml()
                        print("🔇 CTA voiceover DISABLED.")
                    else:
                        print("Usage: /cta voiceover on|off")
                else:
                    print("Usage: /cta voiceover on|off")
                continue


            # /cta on|off|status|text ...
            if msg.startswith("/cta"):
                parts = msg.split(maxsplit=2)

                # /cta on | /cta off | /cta status
                if len(parts) == 2 and parts[1].lower() in ["on", "off", "status"]:
                    action = parts[1].lower()
                    if action == "on":
                        config.setdefault("cta", {})["enabled"] = True
                        _save_yaml()
                        print("📣 CTA ENABLED.")
                    elif action == "off":
                        config.setdefault("cta", {})["enabled"] = False
                        _save_yaml()
                        print("🚫 CTA DISABLED.")
                    else:
                        cta_state = config.get("cta", {}).get("enabled", False)
                        print(f"CTA is currently {'ON' if cta_state else 'OFF'}.")
                    continue

                # /cta text Book now and save 10%...
                if len(parts) >= 3 and parts[1].lower() == "text":
                    cta_text = parts[2].strip()
                    config.setdefault("cta", {})["text"] = cta_text
                    config["cta"]["enabled"] = True
                    _save_yaml()
                    print(f"📣 CTA text set to: {cta_text}")
                    continue

                print("Usage: /cta on|off|status  or  /cta text <your CTA text>")
                continue
            
            # Natural language CTA voiceover
            if "cta voiceover" in msg:
                if "on" in msg or "enable" in msg:
                    config.setdefault("cta", {})["voiceover"] = True
                    _save_yaml()
                    print("🎙️ CTA voiceover ENABLED.")
                    continue
                if "off" in msg or "disable" in msg:
                    config.setdefault("cta", {})["voiceover"] = False
                    _save_yaml()
                    print("🔇 CTA voiceover DISABLED.")
                    continue


            # Natural language CTA:
            if "add cta" in msg or "cta:" in msg or "call to action" in msg:
                # crude extraction: everything after "cta"
                if "cta" in msg:
                    after = user_message.split("cta", 1)[1].strip()
                else:
                    after = ""
                config.setdefault("cta", {})["enabled"] = True
                if after:
                    config["cta"]["text"] = after
                _save_yaml()
                print("📣 CTA updated from natural language.")
                continue

            if "remove cta" in msg or "cta off" in msg:
                config.setdefault("cta", {})["enabled"] = False
                _save_yaml()
                print("🚫 CTA disabled.")
                continue


            if msg.startswith("/tts"):
                if "on" in msg:
                    config.setdefault("render", {})["tts_enabled"] = True
                    _save_yaml()
                    print("🗣️ TTS (voiceover) enabled — future renders will include AI narration.")
                    logging.info("TTS enabled.")
                elif "off" in msg:
                    config.setdefault("render", {})["tts_enabled"] = False
                    _save_yaml()
                    print("🔇 TTS (voiceover) disabled — text only mode.")
                    logging.info("TTS disabled.")
                else:
                    state = config.get("render", {}).get("tts_enabled", False)
                    print(f"TTS is currently {'ON' if state else 'OFF'} (use /tts on|off)")
                continue

            # /voice alloy|verse|...
            if msg.startswith("/voice"):
                parts = msg.split()
                if len(parts) == 2:
                    new_voice = parts[1].lower()
                    config.setdefault("render", {})["tts_voice"] = new_voice
                    _save_yaml()
                    print(f"🎤 Default voice changed to '{new_voice}'")
                    logging.info(f"TTS voice set to {new_voice}")
                else:
                    print("Usage: /voice alloy|verse|coral|sage|amber|onyx")
                continue

            # Natural language: voiceover on/off
            if any(phrase in msg for phrase in [
                "enable voiceover", "turn on voiceover", "voiceover on",
                "enable narration", "turn on narration"
            ]):
                config.setdefault("render", {})["tts_enabled"] = True
                _save_yaml()
                print("🗣️ Voiceover ENABLED.")
                continue

            if any(phrase in msg for phrase in [
                "disable voiceover", "turn off voiceover", "voiceover off",
                "disable narration", "turn off narration", "no voiceover"
            ]):
                config.setdefault("render", {})["tts_enabled"] = False
                _save_yaml()
                print("🔇 Voiceover DISABLED.")
                continue


            # Overlay rewriting
            if msg.startswith("/overlay"):
                parts = user_message.strip().split()
                scope = "all"
                style = "punchy"
                file_arg = None

                if len(parts) == 2:
                    style = parts[1]
                elif len(parts) >= 3:
                    if parts[1].lower() == "all":
                        scope = "all"
                        style = parts[2]
                    else:
                        scope = "single"
                        file_arg = parts[1]
                        style = parts[2]

                if scope == "single" and not file_arg:
                    print("❌ Usage: /overlay <filename.mov> <style>")
                    continue

                apply_overlay(style=style, target=("single" if scope == "single" else "all"), filename=file_arg)
                if scope == "single":
                    print(f"✅ Overlay updated for {file_arg} → {style}")
                else:
                    print(f"✅ Overlay updated for ALL videos → {style}")
                continue

            # Scale command
            if user_message.startswith("/scale"):
                parts = user_message.split()
                if len(parts) < 3:
                    print("Usage: /scale <filename.mov> <value|in|out>")
                    continue
                filename = parts[1].strip().lower()
                direction = parts[2].strip().lower()
                if direction == "in":
                    s = 1.1
                elif direction == "out":
                    s = 0.9
                else:
                    try:
                        s = float(direction)
                    except Exception:
                        print("❌ Invalid scale. Use a number, 'in', or 'out'.")
                        continue

                if update_scale_in_config(filename, s):
                    print(f"✅ Updated scale of {filename} → {s}")
                else:
                    print(f"❌ Could not find {filename} in YAML.")
                continue

            # Global numeric scale (all videos) via natural language
            number_match = re.search(r"\b(\d\.\d+|\d+)\b", msg)
            if ("all videos" in msg or "everything" in msg) and number_match:
                scale_value = float(number_match.group(1))
                if "first_clip" in config:
                    config["first_clip"]["scale"] = scale_value
                for c in config.get("middle_clips", []):
                    c["scale"] = scale_value
                if "last_clip" in config:
                    config["last_clip"]["scale"] = scale_value
                if INSTANT_APPLY:
                    _save_yaml()
                print(f"✅ Updated ALL videos to scale {scale_value}")
                continue

            # Natural language: change text color (supports any color + file/all targeting)
            if "text" in msg and "color" in msg:

                # Try to capture the color name (word after 'to')
                color_match = re.search(r"\bto\s+([a-z]+)\b", msg)
                color = color_match.group(1).lower() if color_match else None

                if not color:
                    print("❌ Could not detect color. Try: 'change text color to yellow on all videos'")
                    continue

                # Apply globally (all videos)
                if "all" in msg or "every" in msg:
                    print(f"🎨 Changing all text colors to {color}...")
                    if "first_clip" in config:
                        config["first_clip"]["text_color"] = color
                    for c in config.get("middle_clips", []):
                        c["text_color"] = color
                    if "last_clip" in config:
                        config["last_clip"]["text_color"] = color
                    if INSTANT_APPLY:
                        _save_yaml()
                    print(f"✅ Text color updated to {color} on all videos.")
                    continue

                # Apply to a specific video
                file_match = re.search(r"(img_\d+\.(?:mov|mp4|avi))", msg, re.IGNORECASE)
                if file_match:
                    filename = file_match.group(1).lower()
                    updated = False
                    for section in ["first_clip", "last_clip"]:
                        if config.get(section, {}).get("file", "").lower() == filename:
                            config[section]["text_color"] = color
                            updated = True
                    for c in config.get("middle_clips", []):
                        if c.get("file", "").lower() == filename:
                            c["text_color"] = color
                            updated = True
                    if updated:
                        if INSTANT_APPLY:
                            _save_yaml()
                        print(f"✅ Text color updated to {color} for {filename}.")
                    else:
                        print(f"❌ Could not find {filename} in YAML.")
                    continue

                print("❌ Specify either 'on all videos' or a filename (e.g. IMG_3782.mov).")
                continue      


            # Natural language: change duration
            if "duration" in msg and any(v.lower() in msg for v in [".mov", ".mp4", ".avi"]):
                filename_match = re.search(r"(img_\d+\.(?:mov|mp4|avi))", msg, re.IGNORECASE)
                value_match = re.search(r"(?:to|=)\s*(\d+(?:\.\d+)?)", msg)  # only match number after 'to' or '='

                if filename_match and value_match:
                    filename = filename_match.group(1).lower()
                    duration_val = float(value_match.group(1))
                    updated = False

                    # First clip
                    if config.get("first_clip", {}).get("file", "").lower() == filename:
                        config["first_clip"]["duration"] = duration_val
                        updated = True

                    # Middle clips
                    for c in config.get("middle_clips", []):
                        if c.get("file", "").lower() == filename:
                            c["duration"] = duration_val
                            updated = True

                    # Last clip
                    if config.get("last_clip", {}).get("file", "").lower() == filename:
                        config["last_clip"]["duration"] = duration_val
                        updated = True

                    if updated:
                        if INSTANT_APPLY:
                            _save_yaml()
                        logging.info(f"✅ Updated duration of {filename} → {duration_val}s")
                    else:
                        logging.warning(f"❌ Could not find {filename} in YAML.")
                else:
                    logging.info("❌ Usage: 'change duration of IMG_3785.mov to 5.0'")
                continue
            
            # Natural language TTS control
            if "voiceover" in msg or "narration" in msg or "tts" in msg:
                if any(word in msg for word in ["enable", "turn on", "activate", "start"]):
                    config.setdefault("render", {})["tts_enabled"] = True
                    _save_yaml()
                    logging.info("🗣️ Voiceover enabled — future videos will include AI narration.")
                    continue
                elif any(word in msg for word in ["disable", "turn off", "remove", "stop"]):
                    config.setdefault("render", {})["tts_enabled"] = False
                    _save_yaml()
                    logging.info("🔇 Voiceover disabled — text only mode is now active.")
                    continue
                elif "status" in msg or "check" in msg:
                    tts_state = config.get("render", {}).get("tts_enabled", False)
                    logging.info(f"🎛️ Voiceover is currently {'ON' if tts_state else 'OFF'}.")
                    continue


            # Fuzzy natural-language scaling
            interpreted = fuzzy_scale_interpret(user_message)
            if interpreted:
                filename, scale_value = interpreted
                if update_scale_in_config(filename, scale_value):
                    print(f"✅ Updated scale of {filename} → {scale_value}")
                    logging.info(f"✅ Updated scale of {filename} → {scale_value}")
                else:
                    print(f"❌ Could not find {filename} in YAML.")
                    logging.warning(f"❌ Could not find {filename} in YAML.")
                continue

            # Instant apply toggle
            if msg.startswith("/instant"):
                if "on" in msg:
                    INSTANT_APPLY = True
                    print("⚡ Instant Apply: ON")
                    logging.info("⚡ Instant Apply: ON")
                elif "off" in msg:
                    INSTANT_APPLY = False
                    print("⏸️ Instant Apply: OFF")
                    logging.info("⏸️ Instant Apply: OFF")
                else:
                    logging.info(f"Instant Apply is {'ON' if INSTANT_APPLY else 'OFF'} (use /instant on|off)")
                continue

            if msg.startswith("/tts"):
                if "on" in msg:
                    config.setdefault("render", {})["tts_enabled"] = True
                    _save_yaml()
                    print("🗣️ TTS (voiceover) enabled — future renders will include AI narration.")
                    logging.info("🗣️ TTS (voiceover) enabled — future renders will include AI narration.")
                elif "off" in msg:
                    config.setdefault("render", {})["tts_enabled"] = False
                    _save_yaml()
                    print("🔇 TTS (voiceover) disabled — text only mode.")
                    logging.info("🔇 TTS (voiceover) disabled — text only mode.")
                else:
                    state = config.get("render", {}).get("tts_enabled", False)
                    print(f"TTS is currently {'ON' if state else 'OFF'} (use /tts on|off)")
                    logging.info(f"TTS is currently {'ON' if state else 'OFF'} (use /tts on|off)")
                continue


            # ---------------------------------------------------------
            # Natural language fallback (chat mode)
            # ---------------------------------------------------------
            try:
                if not MOCK_MODE:
                    prompt = (
                        f"You are my TikTok creative assistant.\n"
                        f"Cached analysis:\n{video_analyses_cache}\n\n"
                        f"User message:\n{user_message}"
                    )

                    resp = client.chat.completions.create(
                        model=TEXT_MODEL,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.7
                    )

                    reply = resp.choices[0].message.content.strip()

                    print(f"\n🧠 Assistant:\n{reply}\n")

                    short_reply = reply[:200].replace("\n", " ") + ("..." if len(reply) > 200 else "")
                    logging.info(f"Assistant responded: {short_reply}")

                else:
                    # MOCK RESPONSE
                    reply = "(Mocked response — MOCK_MODE is ON)"
                    print(f"\n🧠 Assistant:\n{reply}\n")
                    logging.info("Assistant returned mocked response.")

            except Exception as e:
                print(f"\n⚠️ Sorry, I didn’t understand that. Error: {e}")
                print("Please try again with a different command or format.\n")
                logging.warning(f"Chat handling error: {e}")
                continue


        # --------------------------------------------------------
        # ✅ Global error handler for ANY command or chat failure
        # --------------------------------------------------------
        except Exception as e:
            print(f"\n⚠️ Sorry, I didn’t understand that. Error: {e}")
            print("Please try again with a different command or format.\n")
            logging.warning(f"Global command error: {e}")
            continue

if __name__ == "__main__":
    main()
