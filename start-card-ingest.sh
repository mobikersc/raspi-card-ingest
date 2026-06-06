#!/usr/bin/env bash
set -euo pipefail

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/home/mobiker/.Xauthority}"

sleep 2
xset s off -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true

# This TFT uses fbcp; lxpanel steals vertical space on 480x320.
pkill lxpanel 2>/dev/null || true

# ADS7846 rotation/axis mapping for the tested TFT orientation.
xinput set-prop 'ADS7846 Touchscreen' 'Coordinate Transformation Matrix' \
  0 -1 1 \
  -1 0 1 \
  0 0 1 2>/dev/null || true

cd /home/mobiker/raspi-card-ingest
exec /usr/bin/python3 /home/mobiker/raspi-card-ingest/card_ingest_gui.py

