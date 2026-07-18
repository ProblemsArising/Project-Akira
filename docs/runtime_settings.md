# Runtime configuration

Issue #7 connects Project Akira's production components to the central settings
file at `data/settings.json`. The file is created or upgraded automatically when
`get_settings()` first runs.

View active settings:

```powershell
python -m config.settings
```

The future WebUI will use the same `update_settings()` API. For now, close
Project Akira and edit `data/settings.json`, or update values from Python.

## LLM

The `llm` section controls the current OpenAI-compatible LM Studio connection:

```json
{
  "llm": {
    "base_url": "http://localhost:1234/v1",
    "api_key": "None",
    "model": "gemma4-12b-qat-uncensored-hauhaucs-balanced",
    "temperature": 0.75,
    "top_p": 0.9,
    "max_tokens": 512,
    "max_short_term_messages": 20
  }
}
```

If a reasoning model consumes its output budget without producing visible
assistant text, Project Akira retries once with a larger token budget. These
settings control that behavior:

```json
{
  "llm": {
    "retry_empty_response": true,
    "empty_response_retries": 1,
    "retry_token_multiplier": 2.0,
    "max_retry_tokens": 2048
  }
}
```

If every attempt is empty, Project Akira raises a clear
`EmptyModelResponseError` instead of silently returning nothing or sending an
empty string to TTS.

`reasoning_mode` is stored now for the future LM Studio native REST backend.
The current `/v1/chat/completions` backend leaves reasoning selection on the
model/server default because that endpoint does not expose LM Studio's native
`off`, `low`, `medium`, `high`, and `on` modes.

## Personality

Leave `personality.prompt` empty to use the built-in gamer personality. Put a
complete custom system prompt in it to override the default:

```json
{
  "personality": {
    "preset": "custom",
    "prompt": "You are Akira..."
  }
}
```

Changing LLM or personality settings rebuilds the process-wide LLM client on
the next message. This starts a fresh short-term conversation; file-based
long-term memory remains available.

## Whisper

```json
{
  "stt": {
    "model": "base",
    "device": "cuda",
    "compute_type": "float16",
    "language": null,
    "beam_size": 5
  }
}
```

Example CPU configuration:

```json
{
  "stt": {
    "device": "cpu",
    "compute_type": "int8"
  }
}
```

The Faster-Whisper model loads lazily the first time voice transcription is
used. Text-only mode does not initialize Whisper or CUDA.

## Microphone and VAD

The `audio` section now controls the recording filename, requested sample rate,
channels, VAD frame size, pre-roll, silence timeout, recording limits,
calibration time, and start/stop thresholds. The selected microphone can still
fall back to its native sample rate when a Windows WASAPI endpoint rejects the
requested rate.

## TTS

The `tts` section controls pyttsx3 voice index, speaking rate, and volume. The
configured output endpoint from issue #6 is applied at service creation.

## Avatar

The `avatar` section controls the VMC address, mouth timing and shape strength,
face update rate, expression strength, idle/body movement, and calibrated arm
movement. Avatar settings are read when `avatar.vmc` loads, so restart Project
Akira after changing them.

Set `avatar.enabled` to `false` to disable VMC output.

## Memory

The `memory` section controls the JSON path and retention/context limits:

```json
{
  "memory": {
    "file": "data/memories.json",
    "max_turns": 300,
    "max_facts": 200,
    "max_context_chars": 2500,
    "recent_turns": 6,
    "relevant_limit": 6
  }
}
```

Relative paths are resolved from the Project Akira repository root.
