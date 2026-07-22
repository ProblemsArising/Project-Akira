# Managed llama.cpp hardware presets

The Models page detects the system and exposes four editable starting points for
managed `llama-server`:

- **Low end** uses CPU-first settings for systems below 8 GB of usable VRAM.
- **Medium — 8 GB VRAM** uses a moderate context and automatic GPU offload.
- **High — 12 GB VRAM** requests full offload with an 8,192-token context.
- **Ultra — 16+ GB VRAM** requests full offload with a 16,384-token context.

Every card lets the user edit context size, GPU-layer count, and CPU-thread
count. GPU layers accept `auto`, `all`, or a non-negative integer. These are
`llama-server` launch arguments, so they apply only while **Managed llama.cpp**
is the selected backend. LM Studio owns its own runtime process and must be
configured inside LM Studio; Project Akira does not pretend that these values
were applied to it.

When LM Studio or another external backend is active, **Apply** is disabled.
**Set as default** remains available and stores the edited values for the next
Managed llama.cpp session. Applying or saving a card updates these settings:

```text
llm.llama_cpp_context_size
llm.llama_cpp_gpu_layers
llm.llama_cpp_threads
```

**Set as default** performs the same apply operation and also marks that card as
the preferred preset. **Restore built-in** removes the saved edits for one card.
Preset edits are stored in `data/hardware_presets.json` during source
development and in Project Akira's per-user data directory in packaged builds.

The configured GGUF path, model alias, executable, host, port, reasoning mode,
and generation settings are left unchanged. The apply endpoint reads the saved
values back before responding. If a managed `llama-server` is already running,
Project Akira stops and recreates it immediately with the new values. If no
managed server is running, the values are marked pending and are used on the
next launch.

The Settings and Models pages now read and write the same `llm.backend`
setting. The Models page includes LM Studio, Managed llama.cpp, and generic
OpenAI-compatible choices. Selecting a downloaded GGUF switches that shared
setting to Managed llama.cpp, and opening either page reflects the same active
backend. LM Studio and OpenAI-compatible endpoints keep independent last-used
URLs, so switching backend cards restores the matching endpoint instead of
carrying over the other backend's URL.

Managed llama.cpp exposes its own Off/On reasoning selector and defaults to
Off. Its generated server URL, API-key field, test button, and LM Studio
load/unload actions are hidden because Project Akira owns that runtime.

The Models page reports the exact configured launch flags after every apply:

```text
--ctx-size <context>
--n-gpu-layers <auto|all|count>
--threads <count>
--parallel 1
```

When a server is restarted, the response also reports its PID and the context,
GPU-layer, and thread values read from the active process configuration. This
makes thread application verifiable without inferring it from CPU utilization.

Project Akira forces managed `llama-server` to one parallel slot. The
`--parallel` option controls how many requests the server can process at once;
recent llama.cpp builds may choose four slots automatically. Akira currently
sends one interactive completion at a time, so the additional lanes remain
unused and make cache and scheduling behavior harder to reason about.
`--parallel 1` makes the managed runtime deterministic for Akira's single-user
workload. With unified KV enabled, four slots do not necessarily consume four
times the memory, and on the tested b10080 build each preset's reported
`n_ctx_slot` already matched its configured context. This change removes unused
concurrency rather than multiplying the selected context size.

## Detection and estimates

Project Akira reads logical CPU count and installed system memory with the
Python standard library. It asks `nvidia-smi` for every NVIDIA GPU, displays each
GPU separately, and sums their dedicated VRAM into a total. Missing NVIDIA
information is not treated as proof that the machine has no supported
accelerator.

When a configured GGUF exists, Project Akira reads its file size and, when
available, lightweight GGUF metadata including block count and attention shape.
Each card then estimates expected VRAM and RAM from:

- model weight size;
- selected GPU-layer offload fraction;
- context length;
- estimated KV-cache size; and
- conservative runtime overhead.

These values are planning estimates, not guarantees. Driver allocation,
backend behavior, cache type, multi-GPU splitting, model architecture, and
other running programs can change actual use. A warning appears when an
estimate exceeds the preset tier, detected total VRAM, or detected system RAM.

## API

```text
GET  /api/models/hardware-presets
POST /api/models/hardware-presets/apply
POST /api/models/hardware-presets/reset
```

The apply body contains the editable values:

```json
{
  "preset_id": "high",
  "context_size": 8192,
  "gpu_layers": "all",
  "threads": 8,
  "set_default": true
}
```

The GET response includes every detected GPU, total VRAM, selected-model
metadata, recommendation, saved default, exact current match, estimates,
warnings, and all saved preset values. The apply response includes the persisted
configuration, active-process values when a server was restarted, runtime PID,
runtime state, and any restart error.
