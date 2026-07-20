# System tray

Issue #19 adds a native system-tray icon to the Project Akira desktop launcher.
The tray keeps the FastAPI backend, active conversation, microphone controls,
and avatar state available when the normal windows are hidden.

## Default behavior

Run the desktop application normally:

```powershell
python desktop.py
```

Closing the main WebUI or avatar window hides that window instead of ending the
process. The tray menu can then:

- Reopen the main Project Akira window
- Reopen the avatar window
- Start microphone listening
- Stop microphone listening
- Quit Project Akira completely

Double-clicking the tray icon performs the default **Open Project Akira** action.
The first time the main window is hidden, Project Akira shows a notification to
make it clear that the application is still running.

## Settings

```json
{
  "general": {
    "system_tray_enabled": true,
    "close_to_tray": true
  }
}
```

`system_tray_enabled` controls whether the icon is created. `close_to_tray`
controls whether native close buttons hide windows or close them normally.
The Settings page displays these fields under General/Advanced automatically.

## Command-line overrides

```powershell
python desktop.py --tray
python desktop.py --no-tray
python desktop.py --close-to-tray
python desktop.py --exit-on-close
```

The tray is implemented with `pystray`, and its small generated icon uses
Pillow. No external icon file is required.

## Shutdown

Always use **Quit Project Akira** from the tray when the windows are hidden.
That action:

1. Stops the tray event loop.
2. Allows pywebview close events to complete.
3. Saves final window geometry.
4. Shuts down FastAPI and the AI/audio services cleanly.
