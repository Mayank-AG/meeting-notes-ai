#!/bin/bash
# Meeting Notes AI — Start
# Run this every time: ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Check setup was run ──────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "❌  No .env file found. Run ./setup.sh first."
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "❌  Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

# ─── Activate venv ───────────────────────────────────────────
source venv/bin/activate

# ─── Load .env (pick up PORT if set there) ───────────────────
set -a
source .env 2>/dev/null || true
set +a

# ─── Find a free port ────────────────────────────────────────
PORT=${PORT:-8000}
ORIGINAL_PORT=$PORT
while lsof -i ":$PORT" >/dev/null 2>&1; do
    echo "   ⚠️  Port $PORT is in use, trying $((PORT+1))..."
    PORT=$((PORT + 1))
    if [ $PORT -gt 8020 ]; then
        echo "❌  No free port found in range ${ORIGINAL_PORT}–8020."
        echo "    Stop another service using these ports, or set PORT=<number> in .env"
        exit 1
    fi
done
export PORT

# ─── Open browser after short delay ─────────────────────────
(sleep 2 && open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null || true) &

# ─── Start server ────────────────────────────────────────────
echo ""
echo "🎙️  Meeting Notes AI"
echo "===================="
echo "   http://localhost:$PORT"
echo "   Ctrl+C to stop"
echo ""
python3 app.py
