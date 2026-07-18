# Start and stop listening controls

`ConversationService` now exposes microphone controls that can be called by the
future WebUI without starting a second assistant process.

## Service API

```python
from app.conversation import ConversationService

service = ConversationService.from_default_components()

service.start_listening()               # Starts a background microphone loop
service.is_listening                    # True while listening is active
service.stop_listening(wait=True)       # Interrupts VAD and waits for shutdown
service.start_listening()               # The same service can be started again
```

`start_listening()` returns `False` when listening is already active.
`stop_listening()` returns `False` when it was already stopped.

The older synchronous API remains available:

```python
service.run_voice_loop()
service.request_stop()
```

## Interruptible microphone recording

`audio.microphone.MicrophoneRecorder` owns a stop event. The recorder checks the
event during calibration and between 30 ms audio frames. Stopping listening
therefore cancels an active VAD wait quickly and discards any partial sentence.

## Temporary terminal controls

Until the WebUI is built, the controls can be tested from a terminal:

```powershell
python assistant.py --controls
```

Commands:

- `start` — start microphone listening
- `stop` — stop microphone listening
- `status` — show the current state
- `help` — show commands
- `quit` — stop listening and exit

The terminal control screen is only a test interface. The future WebUI should
call the service API directly.
