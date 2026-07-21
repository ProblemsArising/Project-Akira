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

## Embedded VRM model

The avatar stage can now store and render one local `.vrm` file directly. Use
**Choose VRM** in the avatar window to select a VRM 0.x or VRM 1.0 model. The
file is validated as a binary glTF/VRM before it replaces the current model.

Source checkouts save the selected model under:

```text
data/avatar/
```

Packaged builds save it under:

```text
%LOCALAPPDATA%\Project Akira\Data\avatar\
```

The model remains local and is served only by Project Akira's loopback backend.
The renderer bundles Three.js and `@pixiv/three-vrm`, so loading the avatar does
not require internet access. The existing VMC/VSeeFace output remains available
and is not changed by selecting an embedded model.

Issue #23 only loads and frames the avatar. Mouth visemes, facial expressions,
idle movement, and body poses are added by the following v0.4 issues.
