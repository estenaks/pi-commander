#!/usr/bin/env bash
set -euo pipefail

URL="http://127.0.0.1/face"

# If you're on the desktop (X11), these help Chromium start from cron/autostart
export DISPLAY=:0
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

# Launch Chromium full-screen with reduced UI noise
/usr/bin/chromium-browser \
  --new-window \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --start-fullscreen \
  "$URL"