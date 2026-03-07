#!/usr/bin/env bash
set -euo pipefail

SERVICE=epd-commander
UNIT=/etc/systemd/system/${SERVICE}.service
REPO=/home/pi/pi-commander
sudo touch "${UNIT}"
sudo cp "${REPO}/utils/${SERVICE}.service" "${UNIT}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE}.service"
sudo systemctl start  "${SERVICE}.service"

echo "Done. Status:"
sudo systemctl status "${SERVICE}.service" --no-pager