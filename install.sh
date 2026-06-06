#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.config/raspi-card-ingest"
DATA_DIR="$HOME/.local/share/raspi-card-ingest"
SERVICE_DIR="$HOME/.config/systemd/user"

mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$SERVICE_DIR"

if [ ! -f "$CONFIG_DIR/config.json" ]; then
  cp "$APP_DIR/config.example.json" "$CONFIG_DIR/config.json"
fi

chmod +x "$APP_DIR/start-card-ingest.sh"

for command in python3 rsync mount umount xset xinput; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "$command nao encontrado. Instale com: sudo apt install python3 python3-tk rsync x11-xserver-utils xinput"
    exit 1
  fi
done

if ! python3 - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  echo "tkinter nao encontrado. Instale com: sudo apt install python3-tk"
  exit 1
fi

cp "$APP_DIR/raspi-card-ingest.service" "$SERVICE_DIR/raspi-card-ingest.service"

echo
echo "Instalado."
echo "Edite: $CONFIG_DIR/config.json"
echo "Ative: systemctl --user daemon-reload && systemctl --user enable --now raspi-card-ingest.service"
echo
echo "Dependencias do sistema recomendadas:"
echo "  sudo apt install python3-tk rsync x11-xserver-utils xinput"
