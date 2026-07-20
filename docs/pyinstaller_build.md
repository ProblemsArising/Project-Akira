# PyInstaller Windows build

Issue #21 creates a reproducible **one-folder** Windows build. One-folder mode
is intentional: Project Akira includes CUDA, CTranslate2, scientific Python,
pywebview, audio, and WebUI assets. It starts faster and is easier to diagnose
than unpacking those dependencies from a one-file executable on every launch.
The Windows installer in issue #22 will install this folder normally.

## Install build dependencies

Use the same Python 3.10 virtual environment used to run Project Akira:

```powershell
python -m pip install -r requirements-build.txt
```

## Build

```powershell
python build_windows.py
```

The script runs the full unit-test suite first and then creates:

```text
dist/ProjectAkira/
├── ProjectAkira.exe
├── BUILD_INFO.json
└── _internal/
```

Do not move only the EXE. The complete `ProjectAkira` folder is the application.

For a diagnostic build with a visible console:

```powershell
python build_windows.py --console
```

## Test the frozen build

Close the source version, then run:

```powershell
.\dist\ProjectAkira\ProjectAkira.exe
```

Verify chat, Whisper CUDA and CPU modes, TTS, system tray, startup registration,
all WebUI pages, avatar/VMC output, and clean shutdown.

## User data

Source checkouts continue using the repository's `data` folder. Frozen builds
store writable data under:

```text
%LOCALAPPDATA%\Project Akira\Data
```

This includes settings, personalities, memory, chat history, microphone audio,
and calibration samples. Read-only HTML/CSS/JavaScript continues loading from
the PyInstaller bundle. Set `AKIRA_DATA_DIR` to override the packaged data
location for portable testing.

## Models

The build does not include an LLM or Whisper model. LM Studio remains external,
and Faster-Whisper downloads the selected model to the user's normal cache.
Bundling managed models belongs to later milestones.

## Antivirus note

Unsigned PyInstaller applications can receive reputation or heuristic warnings.
Issue #22 creates the installer; production code signing can be added later when
a signing certificate is available.
