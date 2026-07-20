# Audio calibration page

Issue #14 adds a local WebUI for testing Project Akira's microphone and tuning
energy-based voice activity detection (VAD).

Start the backend and open:

```powershell
python server.py
```

```text
http://127.0.0.1:8000/audio
```

## Calibration flow

1. Select **System default** or a specific microphone. System default is
   recommended on Windows because the same physical endpoint can behave
   differently through WASAPI, MME, DirectSound, and WDM-KS.
2. Start calibration and remain quiet during the noise-floor phase.
3. Speak normally during the speech phase.
4. Review the measured noise floor, speech peak, suggested threshold floors, and
   the actual WAV that Whisper would receive.
5. Apply the recommendation or manually tune VAD values, then save.

## Settings changed

The page writes only the existing `audio` settings:

- `input_device`
- `start_threshold_multiplier`
- `end_threshold_multiplier`
- `min_start_threshold`
- `min_end_threshold`
- `end_silence_seconds`
- `calibration_seconds`

The speech recording is stored temporarily at
`data/calibration_sample.wav`. It is ignored by Git.

## API

```text
GET       /api/audio/devices
WebSocket /api/audio/calibration
GET       /api/audio/calibration/sample
```

The calibration WebSocket accepts `start` and `ping` commands. Live
`calibration.level` messages contain RMS, peak, dBFS, phase, and progress.
