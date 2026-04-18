#!/bin/bash
# Meeting Notes AI — First-time setup
# Run once: ./setup.sh

set -e

echo ""
echo "🎙️  Meeting Notes AI — Setup"
echo "============================="
echo ""

# ─── Python check ────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null && python -c "import sys; assert sys.version_info[0]==3" 2>/dev/null; then
    PYTHON=python
else
    echo "❌  Python 3 not found."
    echo "    Install it from https://python.org/downloads (takes 2 min)"
    exit 1
fi
echo "✅  $($PYTHON --version) found"

# ─── Virtual environment ──────────────────────────────────────
if [ ! -d "venv" ]; then
    echo "📦  Creating virtual environment..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

# ─── Dependencies ─────────────────────────────────────────────
echo "📦  Installing dependencies..."
pip install -q -r requirements.txt
echo "✅  Dependencies installed"

# ─── API Keys ─────────────────────────────────────────────────
echo ""
echo "🔑  API Key Setup"
echo "   (Get your Sarvam key at: sarvam.ai → Dashboard → API Keys)"
echo "   (Get your Anthropic key at: console.anthropic.com)"
echo ""

if [ -f ".env" ]; then
    echo "ℹ️   .env already exists. Skipping key entry."
    echo "    Open the app at http://localhost:8000 → ⚙️ Setup to change keys."
else
    read -p "   Sarvam AI API key:    " SARVAM_KEY
    read -p "   Anthropic API key:    " ANTHROPIC_KEY
    echo ""
    echo "   Notion is optional — needed only for pushing notes to Notion."
    read -p "   Notion key (or press Enter to skip): " NOTION_KEY
    read -p "   Notion DB ID (or press Enter to skip): " NOTION_DB_ID

    cat > .env <<EOF
SARVAM_API_KEY=$SARVAM_KEY
ANTHROPIC_API_KEY=$ANTHROPIC_KEY
NOTION_KEY=$NOTION_KEY
NOTION_DB_ID=$NOTION_DB_ID
NOTION_ACTION_ITEMS_DB_ID=
# PORT=8000   # Uncomment and change if port 8000 is already in use on your machine
EOF
    # Protect the file — only readable by you
    chmod 600 .env
    echo "✅  .env created (protected: readable by you only)"
fi

# ─── Done ─────────────────────────────────────────────────────
echo ""
echo "✅  Setup complete!"
echo ""
echo "   To start the app:"
echo "   ./start.sh"
echo ""
