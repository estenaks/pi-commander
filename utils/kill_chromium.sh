#!/usr/bin/env bash
set -euo pipefail

# Kill any Chromium processes owned by the current user.
# Useful for debugging kiosk scripts / cron runs.

echo "[$(date -Is)] Killing Chromium for user: $(whoami)"

# Try graceful shutdown first
pkill -u "$(id -u)" -TERM -f 'chromium|chromium-browser' || true
sleep 2

# Force kill anything still running
pkill -u "$(id -u)" -KILL -f 'chromium|chromium-browser' || true

# Show what remains (should be empty)
pgrep -a -u "$(id -u)" -f 'chromium|chromium-browser' || echo "No Chromium processes remaining."