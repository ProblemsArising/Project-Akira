# Managed llama.cpp backend

`ai.llama_cpp_backend.LlamaCppBackend` starts a local `llama-server` process
and uses its OpenAI-compatible `/v1/chat/completions` endpoint through Project
Akira's common `LLMBackend` interface.

## Scope of issue #32

This first managed backend intentionally requires the user to provide:

- a `llama-server` executable, or an installation available on `PATH`
- a local `.gguf` model file
- manual context, GPU-layer, and thread settings

The following v0.5 issues add the user-facing layers around this backend:

- #33 — model download manager
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

No llama.cpp binary is downloaded by this issue.

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
