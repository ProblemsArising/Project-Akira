# LM Studio backend

`ai.lm_studio_backend.LMStudioBackend` preserves Project Akira's existing
external LM Studio integration behind the common `LLMBackend` interface.

## Ownership

LM Studio remains user-managed. Project Akira does not start, stop, install, or
update LM Studio and reports `managed=False` through backend metadata.

## Inference modes

- `llm.reasoning_mode = "auto"` uses LM Studio's OpenAI-compatible
  `/v1/chat/completions` endpoint. This accepts normal system, user, and
  assistant message history.
- Any explicit reasoning mode, such as `off` or `on`, uses LM Studio's native
  `/api/v1/chat` endpoint so the preference can be sent directly. Project Akira
  flattens its local short-term history into a labelled transcript because the
  native endpoint does not accept assistant-role messages in the request.

Both modes preserve Project Akira's personality prompt, long-term-memory
context, short-term conversation history, empty-response retry policy, and
saved-conversation restoration.

## Model management

The existing Models page continues to use LM Studio's native v1 REST API for
model discovery, loading, and unloading:

- `GET /api/v1/models`
- `POST /api/v1/models/load`
- `POST /api/v1/models/unload`

Model download management is intentionally deferred to issue #33.

## Configuration

Typical settings:

```json
{
  "llm": {
    "backend": "lm_studio",
    "base_url": "http://localhost:1234/v1",
    "api_key": "None",
    "model": "your-model-id",
    "reasoning_mode": "off"
  }
}
```

LM Studio's server must be running before Project Akira sends a message. API
authentication remains optional and follows the token configured in LM Studio.
