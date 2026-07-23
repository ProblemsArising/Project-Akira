# Project Akira

[![Latest release](https://img.shields.io/github/v/release/ProblemsArising/Project-Akira?display_name=tag&sort=semver)](https://github.com/ProblemsArising/Project-Akira/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D4)](https://github.com/ProblemsArising/Project-Akira/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/github/license/ProblemsArising/Project-Akira)](LICENSE)

**Project Akira is a local-first Windows AI companion with text and voice chat, persistent conversations, customizable personalities, audio controls, and optional avatar integration.**

Akira combines a FastAPI backend, browser-based interface, native desktop windows, local speech transcription, text-to-speech, and configurable language-model backends in one application.

> [!NOTE]
> Project Akira is under active development. Version 0.3 establishes the installable Windows desktop application. The next milestone focuses on a built-in avatar renderer.

## Features

### Desktop application

- Native Windows launcher
- Main interface and separate avatar window
- System tray controls
- Saved window size and position
- Optional launch at Windows startup
- Per-user Windows installer and uninstaller
- Start menu shortcut and optional desktop shortcut

### Chat and customization

- Text and microphone conversation
- Streaming status updates
- SQLite conversation history
- Conversation search, rename, resume, and deletion
- Built-in and custom personality presets
- Persistent settings and memory
- Configurable reply speech

### Models and audio

- LM Studio integration
- OpenAI-compatible API support
- Model discovery, selection, loading, and unloading
- Faster-Whisper speech-to-text
- Microphone voice-activity detection and calibration
- Selectable input and output devices
- Local `pyttsx3` text-to-speech

### Avatar integration

- Optional VMC output
- VSeeFace-compatible avatar control
- Speech-driven mouth movement
- Text-selected facial expressions
- Separate avatar display window

A built-in VRM renderer is planned for v0.4. Until then, full animated-avatar rendering requires an external VMC-compatible application such as VSeeFace.

## Download and install

Download the latest Windows installer from the [Releases page](https://github.com/ProblemsArising/Project-Akira/releases/latest):

```text
ProjectAkira-Setup-<version>.exe
```

Run the installer and launch **Project Akira** from the Start menu or the optional desktop shortcut.

Project Akira installs for the current Windows user and does not require administrator privileges.

> [!WARNING]
> Current release installers are not digitally signed. Windows SmartScreen may show an **Unknown publisher** warning.

## Requirements

### Installed release

- Windows 10 or Windows 11, 64-bit
- A configured language-model backend
  - LM Studio is currently the easiest local option
  - An OpenAI-compatible endpoint may also be used
- Audio input/output devices for voice features
- A VMC-compatible avatar application for external avatar rendering

An NVIDIA GPU is optional. GPU acceleration can improve local model and transcription performance, but available behavior depends on the selected backend and installed drivers.

### Source development

- Python 3.10
- Git
- A working C/C++ runtime for native Python dependencies
- Inno Setup 7 only when building the Windows installer

Python 3.10.11 is the primary development version used by this project.

## First-time setup

1. Install and open LM Studio or configure another compatible model backend.
2. Load a chat model and start its local server.
3. Launch Project Akira.
4. Open **Models** and select the backend and model.
5. Open **Audio** to choose and test microphone and output devices.
6. Adjust personality, speech, avatar, and memory options under **Settings**.
7. Open **Chat** and send a message.

Voice, avatar, and speech output can each be disabled independently.

## Local data and privacy

Packaged releases store user data at:

```text
%LOCALAPPDATA%\Project Akira\Data
```

This directory contains settings, personalities, conversation history, memory, and other runtime data. Uninstalling Project Akira preserves it so data can survive upgrades and reinstalls.

Source checkouts use the repository's `data` directory unless another data directory is explicitly configured.

Project Akira is designed for local use and does not require a cloud service by itself. When connected to a local backend, prompts and replies remain on the local machine. Configuring a remote OpenAI-compatible endpoint sends requests to that endpoint according to its own privacy policy.

## Run from source

Clone the repository and create a Python 3.10 virtual environment:

```powershell
git clone https://github.com/ProblemsArising/Project-Akira.git
cd Project-Akira

py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Launch the desktop application:

```powershell
python desktop.py
```

### Command-line modes

Start the continuous microphone conversation loop:

```powershell
python assistant.py
```

Use interactive text chat:

```powershell
python assistant.py --text
```

Send one message and exit:

```powershell
python assistant.py --message "Hello, Akira"
```

Disable spoken replies in text mode:

```powershell
python assistant.py --text --no-speak
```

Run the backend directly for WebUI development:

```powershell
python server.py --reload
```

The development server defaults to:

```text
http://127.0.0.1:8000
```

## Tests

Run the complete unit-test suite:

```powershell
python -m unittest discover -s tests -v
```

Run a single test module:

```powershell
python -m unittest tests.test_conversation -v
```

## Build the Windows application

Install the build dependencies:

```powershell
python -m pip install -r requirements-build.txt
```

Create a diagnostic build with a visible console:

```powershell
python build_windows.py --console
```

Create the normal windowed build:

```powershell
python build_windows.py
```

The PyInstaller distribution is written to:

```text
dist\ProjectAkira
```

## Build the Windows installer

Install Inno Setup 7, then run:

```powershell
python build_installer.py --version 0.3.0
```

The installer is written to:

```text
dist\installer\ProjectAkira-Setup-0.3.0.exe
```

To package an already tested PyInstaller distribution:

```powershell
python build_installer.py --version 0.3.0 --skip-app-build
```

See the files under [`docs`](docs/) for detailed configuration, audio, packaging, and installer notes.

## Project structure

```text
Project-Akira/
├── ai/                 LLM, personality, and memory components
├── app/                Conversation service, API, desktop, tray, and history
├── audio/              Recording, VAD, transcription, and TTS
├── avatar/             VMC avatar controller and expression logic
├── config/             Persistent runtime settings
├── data/               Source-development runtime data
├── docs/               Configuration and build documentation
├── tests/              Unit and integration tests
├── web/                Chat, settings, history, model, audio, and avatar UI
├── assistant.py        Command-line launcher
├── desktop.py          Native desktop launcher
├── server.py           FastAPI development server
├── build_windows.py    PyInstaller build script
└── build_installer.py  Inno Setup installer build script
```

## Roadmap

| Version | Milestone | Status |
| --- | --- | --- |
| v0.1 | Application foundation | Complete |
| v0.2 | WebUI | Complete |
| v0.3 | Desktop application | Complete |
| v0.4 | Built-in avatar | Complete |
| v0.5 | Built-in LLM | Complete |
| v0.6 | Built-in voice conversion | Next |
| v0.7 | Minecraft integration | Planned |

Development is tracked through [GitHub issues](https://github.com/ProblemsArising/Project-Akira/issues) and [milestones](https://github.com/ProblemsArising/Project-Akira/milestones).

## Current limitations

- The Windows installer is currently unsigned.
- A language model is not bundled with v0.3.
- The built-in avatar renderer is not yet implemented.
- External avatar rendering currently requires VMC-compatible software.
- Windows is the primary supported desktop platform.
- Model, Whisper, and voice performance depends heavily on local hardware.

## Contributing

Issues and pull requests are welcome.

Before submitting a change:

1. Create a focused branch.
2. Add or update tests where practical.
3. Run the complete test suite.
4. Keep commits scoped to one issue or feature.
5. Reference the related issue in the pull request.

Bug reports should include the Project Akira version, Windows version, backend, relevant settings, and the complete error message or traceback.

## License

Project Akira is licensed under the [Apache License 2.0](LICENSE).

See [`NOTICE`](NOTICE) for project attribution information.
