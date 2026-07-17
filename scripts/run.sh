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

# Activate venv if present
[ -f venv/bin/activate ] && source venv/bin/activate

echo "=== Starting bot (Ctrl+C to stop) ==="

# Run in background. loguru's file sink handles clean logfile output;
# console output goes to the terminal for real-time viewing.
python -m src.main 2>&1 &
BOT_PID=$!
echo $BOT_PID > .bot.pid
wait $BOT_PID
