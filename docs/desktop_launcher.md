# Desktop launcher

Issue #16 adds a native pywebview shell around Project Akira's existing WebUI.
It does not replace FastAPI or duplicate the frontend.

## Run

Install dependencies and launch:

```powershell
pip install -r requirements.txt
python desktop.py
```

The launcher:

1. Finds an available localhost port.
2. Starts Uvicorn and the existing FastAPI app in a background thread.
3. Waits for `/api/health` to respond.
4. Opens the Chat page in a native pywebview window.
5. Gracefully stops FastAPI when the final window closes.

`python server.py` and all `assistant.py` CLI modes remain available.

## Development options

```powershell
python desktop.py --debug
python desktop.py --maximized
python desktop.py --width 1400 --height 900
python desktop.py --port 8123 --server-log-level info
```

The normal launcher selects a free port automatically, so it can run while a
separate development server is already using port 8000.

## Local browser storage

pywebview private mode is disabled so WebUI preferences such as the spoken-reply
toggle persist across launches. On Windows the browser profile is stored under:

```text
%LOCALAPPDATA%\Project Akira\WebView
```

No model, prompt, transcript, or audio data is sent to pywebview. The window
loads only Project Akira's loopback FastAPI address.

## Scope

This checkpoint creates only the main application window. Later v0.3 issues add:

- A separate avatar window
- Remembered sizes and positions
- A system tray icon
- Launch at Windows startup
- PyInstaller packaging
- A Windows installer
