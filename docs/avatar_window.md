# Separate avatar window

Issue #17 adds a second native pywebview window to the desktop launcher.

Run:

```powershell
python desktop.py
```

The main Project Akira WebUI opens in one window and the avatar stage opens in
another. The avatar stage listens to the same `/api/events` WebSocket stream and
reacts to listening, transcription, thinking, speaking, error, and shutdown
states.

## Current scope

The window is the desktop shell for the future embedded VRM renderer. Until the
v0.4 avatar milestone is implemented, the stage displays a lightweight reactive
companion face while the existing VMC/VSeeFace pipeline continues to operate
normally.

## Settings

These existing settings control the stage:

```json
{
  "general": {
    "open_avatar_window": true,
    "avatar_always_on_top": false
  }
}
```

Changes take effect the next time the desktop launcher starts.

## Command-line overrides

```powershell
python desktop.py --avatar-window
python desktop.py --no-avatar-window
python desktop.py --avatar-always-on-top
python desktop.py --avatar-normal-z-order
python desktop.py --avatar-width 460 --avatar-height 760
```

The normal browser server (`python server.py`) also exposes the stage at
`http://127.0.0.1:8000/avatar` for development and testing.
