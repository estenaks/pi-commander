#!/usr/bin/env bash
set -euo pipefail

URL="http://127.0.0.1/face?rotate=1"
# ^ if display is landscape this shows image in portrait
URL="http://127.0.0.1/config"
# ^ overwrite since using epaper display

# If you're on the desktop (X11), these help Chromium start from cron/autostart
export DISPLAY=:0
export XDG_RUNTIME_DIR="/run/user/$(id -u)"

exec /usr/bin/chromium-browser \
  --new-window \
  --disable-session-crashed-bubble \
  --disable-infobars \
  --start-fullscreen \
  --disable-gpu \
  --disable-software-rasterizer \
  --use-gl=swiftshader \
  "$URL" >/dev/null 2>&1