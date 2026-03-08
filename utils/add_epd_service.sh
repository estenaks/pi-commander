#!/usr/bin/env bash
set -euo pipefail

SERVICE=epd-commander
UNIT=/etc/systemd/system/${SERVICE}.service
REPO=/home/pi/pi-commander
EPAPER=${REPO}/epaper
VENV=${EPAPER}/.venv

# ── venv ──────────────────────────────────────────────────────────────────────
echo "Creating venv at ${VENV}…"
python3 -m venv --system-site-packages "${VENV}"
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install -r "${EPAPER}/requirements.txt"
echo "venv ready."

# ── systemd service ───────────────────────────────────────────────────────────
sudo cp "${REPO}/utils/${SERVICE}.service" "${UNIT}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}.service"
sudo systemctl restart "${SERVICE}.service"

echo "Done. Status:"
sudo systemctl status "${SERVICE}.service" --no-pager