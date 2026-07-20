# Windows installer

Issue #22 packages the PyInstaller one-folder build as a normal Windows installer
using Inno Setup. The installer is per-user, so it installs without an
administrator prompt under:

```text
%LOCALAPPDATA%\Programs\Project Akira
```

It creates a Start-menu shortcut, offers an optional desktop shortcut, registers
a normal Windows uninstaller, and can launch Project Akira when setup finishes.

## Install the compiler

Install the current 64-bit Inno Setup release:

```powershell
winget install --id JRSoftware.InnoSetup.7 -e -s winget -i
```

Restart PowerShell after installation if `ISCC.exe` is not detected immediately.

## Build the installer

From the repository root and the normal Python 3.10 virtual environment:

```powershell
python build_installer.py --version 0.3.0
```

The script:

1. Runs the unit tests.
2. Creates a fresh `dist\ProjectAkira` PyInstaller build.
3. Locates Inno Setup's `ISCC.exe`.
4. Produces a single distributable installer:

```text
dist\installer\ProjectAkira-Setup-0.3.0.exe
```

The build remains unsigned until a Windows code-signing certificate is added.
Windows SmartScreen or antivirus reputation warnings are therefore possible.

## Useful options

Reuse a PyInstaller build that was already manually tested:

```powershell
python build_installer.py --version 0.3.0 --skip-app-build
```

Create a diagnostic installer containing the console build:

```powershell
python build_installer.py --version 0.3.0 --console
```

Point to a nonstandard compiler location:

```powershell
python build_installer.py --iscc "C:\Program Files\Inno Setup 7\ISCC.exe"
```

`INNO_SETUP_COMPILER` may also contain the full path to `ISCC.exe`.

## Manual release test

Test the generated installer on Windows in this order:

1. Install without changing the default folder.
2. Confirm the Start-menu shortcut launches Project Akira.
3. Confirm the optional desktop shortcut works when selected.
4. Test chat, settings, history, microphone, Whisper, TTS, tray, and avatar.
5. Enable **Launch on startup**, restart Akira, and confirm the registered
   command points to the installed executable.
6. Run the same installer again and confirm it upgrades the existing installation.
7. Uninstall from Windows **Installed apps**.
8. Confirm the application folder and startup registry entry are removed.
9. Confirm `%LOCALAPPDATA%\Project Akira\Data` remains intact so settings,
   history, personalities, and memory survive reinstalling.

User data is deliberately not removed by uninstall. It can be deleted manually
when a complete reset is desired.

## Silent installation

Inno Setup also supports unattended installation:

```powershell
.\ProjectAkira-Setup-0.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

The application is not automatically launched after a silent install.
