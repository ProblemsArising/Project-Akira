# Audio device selection

Project Akira can enumerate and persist separate microphone and output-device
choices. The settings are stored under `audio.input_device` and
`audio.output_device` in `data/settings.json`.

## List normal devices

```powershell
python assistant.py --devices
```

The normal list shows preferred Windows WASAPI endpoints, which closely matches
the concise device choices shown in Windows Settings. Duplicate MME,
DirectSound and WDM-KS entries are hidden.

Lowercase `i`/`o` mark Windows defaults. Uppercase `I`/`O` mark explicit Project
Akira selections.

For troubleshooting or unusual hardware, show every PortAudio entry:

```powershell
python assistant.py --devices-all
```

## Select devices

Use a numeric index from the normal table:

```powershell
python assistant.py --set-input-device 24
python assistant.py --set-output-device 21
```

A unique name also works:

```powershell
python assistant.py --set-input-device "fifine Microphone"
python assistant.py --set-output-device "soundcore Space One"
```

When the same physical name exists through several Windows host APIs, Project
Akira automatically prefers WASAPI.

Project Akira saves the resolved device name and host API instead of the raw
index, because Windows device indexes can move when hardware or drivers change.

Restore normal Windows default routing with:

```powershell
python assistant.py --set-input-device default
python assistant.py --set-output-device default
```

## Test output

```powershell
python assistant.py --test-output
```

When an explicit output device is configured, pyttsx3 first renders speech to a
temporary WAV file and Project Akira plays that file through the selected
PortAudio output. This allows direct routing to speakers, headphones or a
virtual cable without changing Windows per-application audio settings.

When no explicit output is selected, the original direct pyttsx3 playback path
is preserved.

## Reusable API

The future WebUI can use:

```python
from audio.devices import (
    configure_audio_devices,
    current_audio_selection,
    input_devices,
    list_audio_devices,
    output_devices,
    preferred_audio_devices,
)
```

Changing persisted devices affects newly created `ConversationService`
instances. A running microphone stream should be stopped before rebuilding the
service with a different device.
