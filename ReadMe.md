🎬 TikTok Creator Assistant

Turn your raw travel or lifestyle clips into a polished TikTok video — complete with captions, music, and smart pacing — all with one simple command line tool.

You don’t need to code.
You just drop in your videos, run the app, and tell it what you want — like

“Make the rooftop clip zoom in a bit”
or
/timings smart 30

The AI does the rest.

🧠 What It Does

The TikTok Creator Assistant:

Watches your videos and describes them (/analyze)

Suggests great captions, hooks, and hashtags

Balances clip timing automatically (/timings smart)

Adds text overlays

Picks background music

Combines everything into a single, ready-to-post TikTok

💻 Before You Start
1. Make sure you have these installed:

macOS (tested on macOS Ventura and newer)

Python 3.10+

Homebrew (optional but handy)

FFmpeg (for video rendering)

brew install ffmpeg

2. Install the Python packages:

From your project folder:

pip install -r requirements.txt


or manually:

pip install moviepy pyyaml openai pillow python-dotenv

3. Add your OpenAI API key

Create a file called .env in your project folder:

OPENAI_API_KEY=your_api_key_here

🎥 Folder Setup

Your project folder should look like this:

tiktok_project/
│
├── tiktok_assistant.py
├── config.yml
├── .env
├── music/
│   ├── chill_vibes.mp3
│   ├── hotel_luxury.wav
│
└── tik_tok_downloads/
    ├── IMG_3753.mov
    ├── IMG_3780.mov
    ├── IMG_3782.mov
    └── IMG_3785.mov

🎵 Folder Purpose

tik_tok_downloads/ → put all your videos here

music/ → optional, add songs or background loops

config.yml → the brain of the project (AI updates this)

▶️ How to Run

In your terminal:

python tiktok_assistant.py


Once it starts, you’ll see:

Say something:


Now you can type commands like /analyze, /yaml, /timings, etc.

🧩 How the Config File Works

The config.yml file is automatically created and updated.
It looks like this:

first_clip:
  file: img_3753.mov
  text: "Sunlight streams across polished floors..."
  duration: 6.5
  start_time: 0
  text_color: white
  scale: 1.0
middle_clips:
- file: img_3780.mov
  text: "Golden whiskey swirls in crystal..."
  duration: 5.0
  start_time: 0
  text_color: white
  scale: 1.0
last_clip:
  file: img_3785.mov
  text: "Begin your day savoring coffee on a rooftop..."
  duration: 4.5
  start_time: 0
  text_color: yellow
  scale: 1.0
music:
  style: luxury modern hotel aesthetic
  bpm: 70
  mood: calm, elegant, sunset rooftop energy
  volume: 0.25


You normally don’t edit this manually — the AI updates it for you.

💬 Main Commands
🔍 /analyze

Scans every video and writes short scene descriptions.
Always run this first.

🧱 /yaml

Builds or updates the config.yml file using the AI analysis.

🗣️ /overlay [style]

Rewrites your captions in a style you want:

/overlay punchy → short TikTok hooks

/overlay descriptive → vivid, elegant text

/overlay cinematic → emotional and poetic tone

⏱️ /timings

Applies balanced local durations for each clip (no start offsets).
Keeps all clips the same pacing.

🧮 /timings smart

Applies the FIX-C engine, which intelligently picks durations and total runtime.
Options:

/timings smart → automatic, balanced

/timings smart 25 → target total of 25 s

/timings smart punchy → quick and energetic pacing

/timings smart cinematic → slower, atmospheric pacing

🔍 /music

Suggests ideal music genres, BPM, and mood based on your scenes.

🔠 /scale

Zoom in or out for specific clips.

/scale IMG_3780.mov in
/scale IMG_3780.mov out
/scale IMG_3780.mov 1.2


Or natural language:

“The coffee clip is too zoomed in”
“Make the rooftop clip bigger”

🎬 /instant on or /instant off

When Instant Apply is ON, all edits update your config.yml immediately.

🔁 reflow starts

Recalculates all start times sequentially.

✨ Other fun ones

/hooks → 10 viral TikTok openings

/captions → 10 caption ideas

/hashtags → 15 optimized hashtags

/story → 12-second story script

/ideas → new content ideas

/cta → call-to-action lines

🧩 FIX-C vs Regular Timings
Command	Purpose	Behavior
/timings	Basic	Even pacing — all start = 0
/timings smart	Smart (FIX-C)	AI-balanced clip lengths, clamped to real durations

Use /timings smart for the best results — it ensures no clip overruns or feels too long.

🎞️ Rendering Your Final Video

Once your config looks good, the system automatically creates a finished TikTok:

output_tiktok_final.mp4


The app:

Crops and centers your clips to TikTok’s 1080×1920 size

Adds blurred backgrounds

Merges all videos and music

Adds overlay text and exports in HD

💡 Tips for Best Results

Always start with /analyze after adding new videos

Run /yaml to rebuild if you change or add clips

Keep video clips under 20 seconds each

Add background music in the music/ folder for auto-selection

Use punchy overlay style for TikTok, cinematic for Reels

🧰 Troubleshooting
Issue	Fix
❌ “File not found”	Make sure videos are inside tik_tok_downloads/
🐢 Rendering too slow	Set RENDER_MODE = "fast" in the script
🪞 Wrong video zoom	Use /scale FILENAME in/out or “zoom out all videos”
🔄 Wrong timings	Run /timings smart again or /timings smart 25