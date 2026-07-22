# Managed llama.cpp backend

`ai.llama_cpp_backend.LlamaCppBackend` starts a local `llama-server` process
and uses its OpenAI-compatible `/v1/chat/completions` endpoint through Project
Akira's common `LLMBackend` interface.

## v0.5 rollout

The managed backend requires a `llama-server` executable, or an installation
available on `PATH`. Issue #33 adds the built-in GGUF download manager so a
model no longer has to be placed and configured manually.

The remaining v0.5 issues add:

- #34 — hardware presets
- #35 — detailed backend health checks
- #36 — richer lifecycle status, shutdown reporting, and recovery

`LlamaCppBackend.close()` already stops a process it owns. Issue #36 connects
that lifecycle to every Project Akira exit path.

## Executable discovery

When `llm.llama_cpp_executable` is empty, Project Akira checks:

1. `llama.cpp/llama-server.exe` in the packaged resource directory
2. `llama.cpp/llama-server` in the packaged resource directory
3. `llama-server.exe` or `llama-server` on `PATH`

The model manager downloads GGUF weights only. A llama.cpp executable is still
provided separately or bundled by a release build.

## Managed model downloads

The Models page accepts a direct HTTP or HTTPS URL to a `.gguf` file. Downloads
are stored under `Data/models/llama.cpp` in an installed build and
`data/models/llama.cpp` in a source checkout.

- Incomplete downloads use a `.gguf.part` file and send an HTTP `Range` request
  when retried.
- The completed file must begin with the GGUF magic header before it is renamed.
- An optional SHA-256 value verifies the completed file before it is renamed.
  Invalid content and checksum failures are removed instead of kept for resume.
- Completed files are listed on the Models page and can be selected for the
  managed llama.cpp backend or deleted when they are not active.
- Closing Project Akira requests cancellation of active model downloads. The
  partial file remains available for a later resume.

Selecting a downloaded model updates `llm.backend`,
`llm.llama_cpp_model_path`, and `llm.llama_cpp_model_alias`; the next message
starts the managed server with that file.

## Local-only server

The managed server may bind only to `127.0.0.1`, `localhost`, or `::1`. This
prevents an unauthenticated inference endpoint from being exposed to the local
network by an accidental setting change.

Project Akira launches the server with:

- the configured GGUF model
- a stable API model alias
- context size
- GPU-layer offload
- CPU thread count
- reasoning mode
- Web UI disabled
- a private localhost host and port

Logs are written to `Data/logs/llama-server.log` in an installed build and to
`data/logs/llama-server.log` in a source checkout.

## Configuration example

```json
{
  "llm": {
    "backend": "llama_cpp",
    "llama_cpp_executable": "C:/Tools/llama.cpp/llama-server.exe",
    "llama_cpp_model_path": "D:/Models/model.Q4_K_M.gguf",
    "llama_cpp_model_alias": "akira-local",
    "llama_cpp_host": "127.0.0.1",
    "llama_cpp_port": 8080,
    "llama_cpp_context_size": 8192,
    "llama_cpp_gpu_layers": "auto",
    "llama_cpp_threads": -1,
    "llama_cpp_startup_timeout_seconds": 120.0,
    "llama_cpp_extra_args": []
  }
}
```

The ordinary `llm.temperature`, `llm.top_p`, `llm.max_tokens`, stop sequences,
personality, short-term history, long-term memory, and saved-conversation logic
remain shared with the other backends.

## Process cleanup

Project Akira closes managed llama.cpp backends when settings switch to another
backend, when the API lifespan ends, and during normal Python interpreter
shutdown. This prevents `llama-server` from keeping its port and model memory
after the desktop application closes.
