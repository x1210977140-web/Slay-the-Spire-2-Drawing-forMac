# Slay the Spire 2 Painter (macOS)

A complete macOS replacement for the Windows drawing helper.

It keeps the same workflow:
- Convert image or text into lineart
- Preview and optionally crop lineart
- Enter full-screen "digital amber" selection mode
- Auto draw contours with right-button continuous strokes
- Press global `P` at any time to emergency stop

## Features

- Tkinter desktop UI with realtime lineart preview
- Three lineart sources:
  - External image -> edge lineart
  - Text -> adaptive bounding-box lineart
  - Existing saved lineart file
- Secondary crop window for current lineart
- Config memory in `output_lines/config.json`
  - Window topmost
  - Detail slider
  - Speed slider
- macOS Quartz-based low-level input simulation
- Global keyboard event-tap emergency stop (`P`)

## Requirements

- macOS 12+
- Python 3.10+
- Xcode Command Line Tools (recommended)

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Run from source

```bash
python3 spire_painter_mac.py
```

## Required macOS permissions

This app needs all of the following:

1. Accessibility
- Needed to inject mouse events for drawing.
- System Settings -> Privacy & Security -> Accessibility

2. Input Monitoring
- Needed for global `P` key emergency stop.
- System Settings -> Privacy & Security -> Input Monitoring

3. Screen Recording
- Needed for full-screen capture in digital amber mode.
- System Settings -> Privacy & Security -> Screen Recording

If permissions were granted while the app was running, fully quit and reopen the app.

## Build app bundle (Universal2)

```bash
bash build_mac.sh
```

Output:

- `dist/SlaytheSpire2DrawingMac.app`

Notes:
- This project does not include code signing or notarization setup.
- Unsigned apps may show Gatekeeper warnings on first launch.

## Troubleshooting

1. Global `P` does not work
- Recheck Input Monitoring + Accessibility permissions.
- Remove and re-add terminal/python entry in both permission lists if needed.

2. Drawing starts but nothing appears in target app
- Some apps/games may ignore synthetic events.
- Reduce speed slider (2-4 recommended) and retry.

3. Screen dark overlay cannot start
- Screen capture likely blocked by Screen Recording permission.

4. Text lineart uses unexpected font
- Selected font was not found; fallback font was applied automatically.

## Project files

- `spire_painter_mac.py`: full macOS implementation
- `requirements.txt`: runtime dependencies
- `build_mac.sh`: universal2 app bundle build script
- `output_lines/`: generated lineart and config storage
