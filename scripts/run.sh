#!/bin/bash
# Quick restart: kill old bot, start new one with console + file logging.
#
# Usage:
#   ./scripts/run.sh          # start with log level from config/bot.toml
#   ./scripts/run.sh DEBUG    # also set BOT_LOG_LEVEL env (if code supports it)
#
# Tip: set level = "DEBUG" in config/bot.toml to see full event tracing.
set -e
cd "$(dirname "$0")/.."

# Kill existing bot process
if [ -f .bot.pid ]; then
    OLD_PID=$(cat .bot.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "=== Killing old bot (PID: $OLD_PID) ==="
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
    rm -f .bot.pid
fi

# Export QR code from NapCat container if present
NAPCAT_CONTAINER="${NAPCAT_CONTAINER:-napcat}"
if docker ps --format "{{.Names}}" 2>/dev/null | grep -qx "$NAPCAT_CONTAINER"; then
    echo "=== Waiting for NapCat QR code ==="
    for _ in $(seq 1 30); do
        if docker exec "$NAPCAT_CONTAINER" test -f /app/napcat/cache/qrcode.png 2>/dev/null; then
            docker cp "$NAPCAT_CONTAINER:/app/napcat/cache/qrcode.png" qrcode.png
            echo "QR code exported to: $(pwd)/qrcode.png"
            break
        fi
        sleep 2
    done
fi

# Activate venv if present
[ -f venv/bin/activate ] && source venv/bin/activate

echo "=== Starting bot (Ctrl+C to stop) ==="

# Run in background. loguru's file sink handles clean logfile output;
# console output goes to the terminal for real-time viewing.
python -m src.main 2>&1 &
BOT_PID=$!
echo $BOT_PID > .bot.pid
wait $BOT_PID
