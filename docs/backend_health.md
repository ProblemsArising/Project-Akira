# Backend health checks

The Models page includes a read-only health panel for the selected language-model
backend. A check verifies more than whether a TCP port answers: it also determines
whether Akira's configured model is available and, where the backend exposes that
information, loaded and ready.

Health checks never send a chat prompt, load a model, start managed llama.cpp, or
initialize the conversation service. The page checks when it opens, after backend
changes and connection tests, every 30 seconds while visible, and whenever **Check
now** is selected.

## Status meanings

- **Ready**: the backend answered and the configured model is ready for Akira.
- **Needs attention**: a server answered, but the configured model is unavailable,
  unloaded, or the managed port belongs to an external process.
- **Offline**: the configured service or an existing managed process did not answer
  its health endpoint.
- **Idle**: managed llama.cpp is valid but has not been started yet. This is normal;
  Akira launches it lazily when a message needs the model.
- **Misconfigured**: required settings such as a server URL, `llama-server`
  executable, or GGUF model path are invalid.

## Backend-specific checks

### LM Studio

Project Akira calls LM Studio's native `/api/v1/models` endpoint. A healthy result
requires the selected model to be present and have a loaded instance. The panel
also reports response time and model counts.

### OpenAI-compatible server

Project Akira calls `/v1/models`. A healthy result requires the configured model ID
to appear in the returned model list. Generic OpenAI-compatible APIs do not expose
a standard loaded/unloaded state, so only availability can be verified.

### Managed llama.cpp

The check first validates the configured executable, GGUF path, host, port, context,
GPU-layer, thread, reasoning, and extra-argument settings without launching a
process. It then compares the `/health` response with Project Akira's managed
process registry.

The panel distinguishes these cases:

- a Project Akira-managed process is running and healthy;
- configuration is valid but the lazy process is idle;
- a managed process exited or stopped answering;
- another program is responding on the port reserved for managed llama.cpp.

When available, the panel displays the managed PID, context size, GPU layers,
thread count, parallel-slot count, and log-file location. The default request
timeout is three seconds so an offline backend cannot hold the Models page open.
