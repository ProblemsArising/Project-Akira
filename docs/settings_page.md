# Settings page

Issue #11 adds a browser interface for the central Project Akira settings file.

Run:

```powershell
python server.py
```

Open:

```text
http://127.0.0.1:8000/settings
```

The page loads settings from `GET /api/settings`, saves partial changes through
`PATCH /api/settings`, and restores defaults through `POST /api/settings/reset`.

## Behavior

- Common controls have friendly names, descriptions, limits, and dropdowns.
- Less frequently used values are grouped under Advanced settings.
- Unknown future fields still render automatically based on their stored type.
- Only changed fields are sent back to the backend.
- The backend rejects unknown fields, invalid types, unsupported enum values,
  and unsafe numeric ranges.
- Settings are stored only on the local computer.

If the conversation service has already loaded, the page warns that Project
Akira must be restarted before testing the new runtime values. This avoids
partially rebuilding Whisper, audio, TTS, and avatar components while a turn is
active.

The later personality, audio-calibration, and model/device-selector issues can
replace the basic controls on this page with specialized interfaces without
changing the settings API.
