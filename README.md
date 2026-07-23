# Project Akira

[![Latest release](https://img.shields.io/github/v/release/ProblemsArising/Project-Akira?display_name=tag&sort=semver)](https://github.com/ProblemsArising/Project-Akira/releases/latest)
[![Platform](https://img.shields.io/badge/platform-Windows-0078D4)](https://github.com/ProblemsArising/Project-Akira/releases/latest)
[![Python](https://img.shields.io/badge/python-3.10-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/github/license/ProblemsArising/Project-Akira)](LICENSE)

**Project Akira is a local-first Windows AI companion with text and voice chat, persistent conversations and memory, customizable personalities, a built-in VRM avatar, and multiple local language-model backends.**

Akira combines a FastAPI backend, browser-based interface, native desktop windows, local speech transcription, text-to-speech, managed llama.cpp inference, and optional Discord and VMC integration in one application.

> [!NOTE]
> Version 0.5 completes the **Built-in LLM** milestone. Project Akira can now install and manage its own llama.cpp runtime and GGUF model configuration, so LM Studio remains supported but is no longer required. The next milestone is **v0.6 — Built-in Voice Conversion**.

## Highlights

### Local models and inference

- Built-in managed llama.cpp backend
- Managed llama.cpp runtime installer with NVIDIA CUDA 12, Vulkan, and CPU-only variants
- Automatic runtime recommendation, validation, device detection, repair, and removal
- Direct GGUF model downloads with resume, cancellation, optional SHA-256 verification, and GGUF validation
- Editable Low, Medium, High, and Ultra hardware presets
- Context-size, GPU-layer, and CPU-thread controls with estimated VRAM and system-RAM usage
- Detection of every NVIDIA GPU and combined VRAM
- Backend health checks for Managed llama.cpp, LM Studio, and generic OpenAI-compatible servers
- Separate saved URLs and compatible reasoning controls for each backend
- Automatic shutdown of Akira-managed inference processes when Project Akira exits

### Chat and customization

- Text and microphone conversation
- Persistent SQLite conversation history
- Conversation search, rename, resume, and deletion
- Persistent short-term and long-term memory
- Built-in and custom personality presets
- Streaming activity and status updates
- Configurable spoken replies
- Optional Discord bot integration for direct messages and notifications

### Voice and audio

- Local Faster-Whisper speech-to-text
- Microphone voice-activity detection and calibration
- Selectable input and output devices
- Local `pyttsx3` text-to-speech
- Independent controls for listening, transcription, and spoken replies

### Built-in avatar

- Embedded VRM 0.x and VRM 1.0 renderer
- Persistent local VRM model selection
- Text-estimated mouth visemes and reply-driven expressions
- Procedural breathing, idle movement, and emotion-driven poses
- Optional transparent always-on-top Windows overlay
- Embedded, combined, VMC-only, and disabled output modes
- Existing VMC/VSeeFace compatibility retained

### Windows desktop application

- Native Windows launcher with separate main and avatar windows
- System tray controls and close-to-tray behavior
- Saved window size and position
- Optional launch at Windows startup
- Per-user installer and uninstaller
- Start menu shortcut and optional desktop shortcut

## Download and install

Download the latest Windows installer from the [Releases page](https://github.com/ProblemsArising/Project-Akira/releases/latest):

```text
ProjectAkira-Setup-<version>.exe
```

Run the installer and launch **Project Akira** from the Start menu or the optional desktop shortcut. The installer is per-user and does not require administrator privileges.

> [!WARNING]
> Current release installers are not digitally signed. Windows SmartScreen may show an **Unknown publisher** warning.

## Requirements

### Installed release

- Windows 10 or Windows 11, 64-bit
- Enough disk space, system RAM, and optional VRAM for the selected runtime and model
- A language model provided through one of these backends:
  - **Managed llama.cpp** — recommended for a self-contained local setup
  - **LM Studio** — retained with native model discovery and management
  - **OpenAI-compatible server** — local or remote
- Audio input and output devices only when using voice features
- A `.vrm` model only when using a custom embedded avatar
- A Discord bot token and internet access only when enabling Discord integration

An NVIDIA GPU is optional. Project Akira can install a CUDA runtime for supported NVIDIA systems, a Vulkan runtime for compatible GPUs, or a CPU-only fallback. Actual model capacity and speed depend on the selected GGUF, context size, available memory, drivers, and other running applications.

Language models are not bundled with the installer.

### Source development

- Python 3.10
- Git
- A working C/C++ runtime for native Python dependencies
- Inno Setup 7 only when building the Windows installer

Python 3.10.11 is the primary development version used by this project.

## First-time setup

### Recommended: Managed llama.cpp

1. Launch Project Akira and open **Models**.
2. Select **Managed llama.cpp**.
3. In **Runtime installer**, keep the recommended variant or choose CUDA 12, Vulkan, or CPU-only, then install it.
4. Download a GGUF model from a direct HTTP/HTTPS URL or configure an existing local GGUF path.
5. Select the model and apply a hardware preset.
6. Check **Backend health**. A valid managed backend may show **Idle** until the first message starts it.
7. Open **Chat** and send a message.

The managed runtime is installed separately from Project Akira, validated with `llama-server.exe --list-devices`, and selected automatically after a successful installation. See [Managed llama.cpp runtime](docs/llama_cpp_runtime.md), [Managed llama.cpp backend](docs/llama_cpp_backend.md), and [Hardware presets](docs/hardware_presets.md).

### LM Studio

1. Install and open LM Studio.
2. Load a chat model and start LM Studio's local server.
3. In Project Akira, open **Models** and select **LM Studio**.
4. Use **Test & refresh**, select the model, choose its reasoning mode, and save the selection.

### Other OpenAI-compatible servers

1. Start the compatible server.
2. Open **Models** and select **OpenAI-compatible**.
3. Enter the server's `/v1` base URL, optional API key, and model ID.
4. Test and save the selection.

After configuring a model, open **Audio** to choose microphone and output devices. Personality, speech, avatar, memory, startup, and other behavior can be adjusted under **Settings**. Voice, avatar, Discord, VMC, and speech output can each be disabled independently.

## Managed llama.cpp details

Project Akira v0.5 pins a tested official llama.cpp release rather than silently following the latest upstream build. The runtime installer:

1. Downloads resumable `.part` files.
2. Verifies pinned SHA-256 hashes.
3. Safely extracts only `llama-server.exe` and required runtime DLLs.
4. Rejects unsafe ZIP paths and symbolic links.
5. Validates the selected acceleration backend and detected devices.
6. Activates the new runtime only after validation succeeds.
7. Preserves a previous working runtime if repair or replacement fails.

Runtime variants:

- **NVIDIA CUDA 12** — installs the official CUDA llama.cpp build and its matching CUDA runtime DLL package
- **Vulkan** — intended for AMD, Intel, and other Vulkan-capable GPUs
- **CPU-only** — smallest fallback when GPU acceleration is unavailable

A manually configured `llama-server.exe` remains supported and takes priority over the managed runtime. See [`docs/llama_cpp_runtime.md`](docs/llama_cpp_runtime.md) for storage paths, executable precedence, repair, removal, and troubleshooting.

## Local data and privacy

Packaged releases store user data at:

```text
%LOCALAPPDATA%\Project Akira\Data
```

This directory contains settings, personalities, conversation history, memory, Discord configuration, downloaded models, managed runtimes, avatar files, and other runtime data. Uninstalling Project Akira preserves this directory so data can survive upgrades and reinstalls.

Source checkouts use the repository's `data` directory unless another data directory is explicitly configured.

Project Akira is designed for local use and does not require a cloud service by itself. When using Managed llama.cpp or another local backend, prompts and replies remain on the local machine. A remote OpenAI-compatible endpoint receives requests according to that service's own privacy policy. Discord integration sends and receives data through Discord only when it is enabled.

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
python build_installer.py --version 0.5.0
```

The installer is written to:

```text
dist\installer\ProjectAkira-Setup-0.5.0.exe
```

To package an already tested PyInstaller distribution:

```powershell
python build_installer.py --version 0.5.0 --skip-app-build
```

Detailed technical notes are available under [`docs`](docs/), including:

- [Managed llama.cpp runtime](docs/llama_cpp_runtime.md)
- [Managed llama.cpp backend](docs/llama_cpp_backend.md)
- [Hardware presets](docs/hardware_presets.md)
- [Backend health checks](docs/backend_health.md)
- [Model and backend selector](docs/model_backend_selector.md)
- [Avatar output backends](docs/avatar_output_backends.md)
- [Audio devices](docs/audio_devices.md)
- [Windows installer](docs/windows_installer.md)

## Project structure

```text
Project-Akira/
├── ai/                 LLM backends, personality, memory, and inference control
├── app/                API, conversations, desktop, Discord, model, and runtime services
├── audio/              Recording, VAD, transcription, and TTS
├── avatar/             Embedded/VMC avatar control and expression logic
├── config/             Persistent runtime settings
├── data/               Source-development runtime data
├── docs/               Configuration, feature, and build documentation
├── installer/          Inno Setup configuration and installer assets
├── tests/              Unit and integration tests
├── web/                Chat, models, audio, avatar, Discord, history, and settings UI
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
| v0.4 | Built-in avatar | Next |
| v0.5 | Built-in LLM | Planned |
| v0.6 | Built-in voice conversion | Planned |
| v0.7 | Minecraft integration | Planned |

Development is tracked through [GitHub issues](https://github.com/ProblemsArising/Project-Akira/issues) and [milestones](https://github.com/ProblemsArising/Project-Akira/milestones).

## Current limitations

- Windows is the primary supported desktop platform.
- The Windows installer is currently unsigned.
- Language models are not bundled and may require substantial storage, RAM, or VRAM.
- Managed runtime and model performance depends heavily on local hardware and drivers.
- Built-in RVC voice conversion is not yet implemented.
- Generic OpenAI-compatible servers do not expose all LM Studio or managed llama.cpp model-management and reasoning controls.
- The transparent avatar overlay is display-only and intentionally uses a fixed-size interaction model.

## Contributing

Issues and pull requests are welcome.

Before submitting a change:

1. Create a focused branch.
2. Add or update tests where practical.
3. Run the complete test suite.
4. Keep commits scoped to one issue or feature.
5. Reference the related issue in the pull request.

Bug reports should include the Project Akira version, Windows version, selected backend, relevant settings, and the complete error message or traceback.

## License

Project Akira is licensed under the [Apache License 2.0](LICENSE). See [`NOTICE`](NOTICE) for project attribution information.

The optional managed llama.cpp runtime is downloaded separately under llama.cpp's MIT license, and its license notice is installed beside the runtime executable.
