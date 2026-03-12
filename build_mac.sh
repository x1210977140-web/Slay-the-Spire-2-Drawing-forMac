#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name SlaytheSpire2DrawingMac \
  --target-arch universal2 \
  spire_painter_mac.py

echo
echo "Build complete: dist/SlaytheSpire2DrawingMac.app"
