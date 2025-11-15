#!/bin/bash
# To run this file, open command prompt or terminal and run:
    # 1. chmod +x setup_mac.sh
    # 2. ./setup_mac.sh

echo "🎬 Setting up TikTok Creator Assistant for macOS..."

# --- System tools ---
echo "📦 Checking for Homebrew..."
if ! command -v brew &> /dev/null; then
  echo "🚀 Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  echo "✅ Homebrew already installed."
fi

echo "🎞 Installing FFmpeg and ImageMagick (for video + text rendering)..."
brew install ffmpeg imagemagick python-tk || echo "⚠️ You may need to install manually if this fails."

# --- Python setup ---
echo "🐍 Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Setup complete!"
echo "Run with: source venv/bin/activate && python tiktok_assistant.py"

