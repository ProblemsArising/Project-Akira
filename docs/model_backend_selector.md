# Model and backend selector

Issue #15 adds a dedicated model page at:

```text
http://127.0.0.1:8000/models
```

## Supported backends

### LM Studio

Project Akira uses LM Studio's native v1 REST API to:

- list downloaded LLMs and their loaded instances;
- read model metadata, context limits, quantization, and reasoning options;
- load a selected model into memory;
- unload a loaded model instance; and
- enforce Project Akira's reasoning selection during chat.

The normal server address is:

```text
http://localhost:1234/v1
```

Project Akira derives `/api/v1/*` model-management endpoints from that address.

### OpenAI-compatible

This mode discovers models through:

```text
GET <base_url>/models
```

It is intended for local servers such as llama.cpp, Ollama's OpenAI-compatible
API, and similar software. Because generic compatibility servers do not share a
standard reasoning-control parameter, Project Akira uses reasoning mode `auto`
for this backend.

## Selection behavior

Saving a different backend or model updates the `llm` section of
`data/settings.json`. The cached LLM client and its short-term context are
released so the next message uses the newly selected model. SQLite transcripts
and long-term memory are not deleted.

## API routes

```text
GET  /api/models/config
POST /api/models/discover
POST /api/models/select
POST /api/models/load
POST /api/models/unload
```

The page and discovery endpoints do not initialize Whisper, TTS, the avatar, or
the normal conversation service.
