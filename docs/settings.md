# Application settings

Project Akira's central settings system lives in `config/settings.py`.

On first use it creates `data/settings.json`. Existing files are merged with
current defaults, so a future release can add options without requiring users
to delete their settings. Invalid JSON is backed up before defaults are
restored, and saves use an atomic temporary-file replacement.

A documented default template is available at `data/settings.example.json`.

## View active settings

```powershell
python -m config.settings
```

## Read settings

```python
from config import get_settings

settings = get_settings()
print(settings.audio.end_silence_seconds)
print(settings.stt.device)
```

## Update settings

`update_settings` accepts a nested dictionary, matching what the future WebUI
will send to the backend:

```python
from config import update_settings

update_settings({
    "audio": {"end_silence_seconds": 1.4},
    "stt": {"device": "cpu", "compute_type": "int8"},
})
```

## Override the settings path

Tests, portable installs, and packaged builds can set the
`AKIRA_SETTINGS_FILE` environment variable to store the file elsewhere.


## Runtime integration

The LLM, personality, Whisper, microphone/VAD, TTS, avatar, and long-term memory now read their runtime values from this settings object. See [`runtime_settings.md`](runtime_settings.md) for all connected fields and examples.
