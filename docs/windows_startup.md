# Launch Project Akira when Windows starts

Issue #20 connects the existing `general.launch_on_startup` setting to the
current user's Windows startup registry.

## Enable it

Open the Settings page and enable:

```text
General → Launch on startup
```

Click **Save changes**. Project Akira immediately creates:

```text
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
Project Akira
```

Because the entry is under `HKEY_CURRENT_USER`, administrator privileges are
not required and the setting applies only to the signed-in Windows account.

Disabling the setting removes that value. Resetting Project Akira's settings
also removes it.

## Development checkout

While running from source, the startup command uses the active environment's
`pythonw.exe` and the repository's `desktop.py`:

```text
"C:\path\to\.venv\Scripts\pythonw.exe" "C:\path\to\Project-Akira\desktop.py"
```

`pythonw.exe` avoids opening a separate console window at login.

Moving or deleting the repository or virtual environment makes an old startup
command stale. Starting Project Akira manually repairs the registration to the
current paths whenever `launch_on_startup` remains enabled.

## Packaged application

When Project Akira is frozen by PyInstaller, the same feature automatically
registers the packaged executable instead of Python and `desktop.py`. No
separate startup implementation is needed for issues #21 or #22.

## Status endpoint

The local backend exposes:

```text
GET /api/startup
```

It reports whether startup is supported, whether a Run entry exists, its
registered command, the expected current command, and whether the two match.

## Troubleshooting

Inspect the status from PowerShell while the backend is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/startup
```

Or inspect the registry directly:

```powershell
Get-ItemProperty `
  "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" `
  -Name "Project Akira"
```
