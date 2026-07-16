# ConversationService

`app.conversation.ConversationService` owns Project Akira's conversation
pipeline. It replaces the module-level infinite loop that previously lived in
`assistant.py`.

## Production use

```python
from app.conversation import ConversationService

service = ConversationService.from_default_components()
service.run_voice_loop()
```

`assistant.py` is now only a command-line launcher around that service.

## Typed messages

The same service can already process text without recording or Whisper:

```python
result = service.process_text("Hello, Akira", speak=True)
```

This is the entry point the future WebUI can use for its text box.

## One voice turn

```python
result = service.process_voice_once()
```

This records, transcribes, generates a reply, and speaks exactly once. It is
useful for push-to-talk or a future start/stop listening controller.

## Dependency injection

Tests and alternate frontends can supply lightweight replacements:

```python
service = ConversationService(
    recorder=lambda: "input.wav",
    transcriber=lambda path: "hello",
    responder=lambda text: "Hey!",
    speaker=lambda reply: None,
)
```

This avoids importing CUDA, audio devices, TTS, or VMC when testing the
conversation orchestration itself.

## Current stop behavior

`request_stop()` stops `run_voice_loop()` after the current blocking microphone
recording finishes. Issue #5 can make the recorder itself interruptible when
start/stop listening controls are implemented.
