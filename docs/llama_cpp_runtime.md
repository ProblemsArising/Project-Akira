# Managed llama.cpp runtime

Project Akira can install a tested llama.cpp runtime from the **Models** page. The runtime is downloaded separately from the Project Akira installer so users do not receive a large, unused GPU runtime and llama.cpp can be repaired without reinstalling Akira.

## Pinned release

The initial managed runtime is pinned to official llama.cpp release `b10080`. Project Akira does not silently follow llama.cpp's rapidly changing latest release. Updating the pin should be handled as a tested Project Akira change because server arguments and runtime behavior may change between llama.cpp builds.

Available Windows x64 variants are:

- **NVIDIA CUDA 12** — downloads both the CUDA llama.cpp archive and the matching CUDA 12.4 DLL archive.
- **Vulkan** — intended for AMD, Intel, and other Vulkan-capable GPUs.
- **CPU only** — the smallest fallback when GPU acceleration is unavailable.

An NVIDIA GPU reported by `nvidia-smi` selects CUDA 12 as the recommendation. Other Windows x64 systems default to Vulkan, while CPU remains manually selectable.

## Installation behavior

Runtime archives are downloaded under:

```text
%LOCALAPPDATA%\Project Akira\Data\runtimes\llama.cpp\downloads\b10080
```

Source checkouts use the repository `data` directory through the normal Project Akira path resolver.

The active runtime is installed under:

```text
<user data>\runtimes\llama.cpp\b10080\<variant>
```

Project Akira:

1. Downloads to `.part` files and resumes when the release server supports byte ranges.
2. Verifies the pinned SHA-256 digest for every archive.
3. Rejects ZIP path traversal and symbolic-link entries.
4. Extracts into a temporary staging directory.
5. Confirms required executable and DLL files are present.
6. Runs `llama-server.exe --list-devices` from the extracted directory.
7. Requires the selected GPU backend to report a matching usable device.
8. Atomically replaces the previous copy only after validation succeeds.
9. Writes the llama.cpp MIT license beside the executable.
10. Saves the validated executable as `llm.llama_cpp_executable`.

A failed repair leaves the previous working installation in place. Cancelling keeps valid partial archives so a retry can resume.

## Executable precedence

Managed llama.cpp resolves the executable in this order:

1. A valid manually configured executable.
2. The active Project Akira-managed runtime.
3. A runtime bundled with the application in a future release.
4. `llama-server` available on the system `PATH`.

Manual selection therefore remains available for developers and advanced users who prefer a custom or newer llama.cpp build. When installation temporarily replaces a manually configured path, Project Akira records it and restores it when the managed runtime is removed, provided the previous executable still exists.

## Removal

**Remove managed runtime** deletes Project Akira's extracted managed runtime copies and clears or restores the executable setting only when it points to the managed copy. Verified release archives are kept for a faster reinstall. Downloaded GGUF models and manually selected llama.cpp installations are not removed.

## Troubleshooting

If CUDA installation fails validation, update the NVIDIA driver and retry. The CUDA option includes both official archives because the llama.cpp CUDA executable archive does not contain every required CUDA runtime DLL by itself.

If Vulkan does not report a usable device, retry with **CPU only** or provide a compatible custom `llama-server.exe` through Settings.
