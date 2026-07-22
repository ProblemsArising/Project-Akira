"""Hardware-aware, editable managed llama.cpp presets.

The detector uses only the Python standard library and ``nvidia-smi``. Preset
memory numbers are estimates rather than guarantees, but they account for the
selected GGUF size, its layer count when available, context length, and the
requested GPU-offload fraction.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.paths import user_file_path

_GIB = 1024**3
_MIB = 1024**2
_PRESET_FILE_ENV = "AKIRA_HARDWARE_PRESETS_FILE"
_BUILTIN_PRESET_IDS = ("low", "medium", "high", "ultra")
_STORE_LOCK = threading.RLock()


@dataclass(frozen=True, slots=True)
class GPUInfo:
    """A GPU and the dedicated memory reported by ``nvidia-smi``."""

    name: str
    memory_bytes: int


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    """Small, serializable snapshot used to choose managed-server defaults."""

    logical_cpu_count: int
    total_memory_bytes: int
    gpus: tuple[GPUInfo, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def largest_gpu_memory_bytes(self) -> int:
        return max((gpu.memory_bytes for gpu in self.gpus), default=0)

    @property
    def total_gpu_memory_bytes(self) -> int:
        return sum(max(0, gpu.memory_bytes) for gpu in self.gpus)


@dataclass(frozen=True, slots=True)
class GGUFModelProfile:
    """Selected model facts used for approximate memory calculations."""

    size_bytes: int
    layer_count: int | None = None
    kv_bytes_per_token: int | None = None


@dataclass(frozen=True, slots=True)
class HardwarePresetValues:
    context_size: int
    gpu_layers: str
    threads: int


@dataclass(frozen=True, slots=True)
class HardwarePreset:
    """One editable set of existing llama.cpp settings."""

    id: str
    name: str
    description: str
    target_vram_bytes: int
    context_size: int
    gpu_layers: str
    threads: int
    estimated_vram_bytes: int | None = None
    estimated_ram_bytes: int | None = None
    recommended: bool = False
    current: bool = False
    default: bool = False
    customized: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HardwarePresetCatalog:
    """Detected hardware, selected model facts, and generated presets."""

    profile: HardwareProfile
    model_profile: GGUFModelProfile | None
    presets: tuple[HardwarePreset, ...]
    recommended_id: str
    default_id: str
    current_id: str | None

    @property
    def model_size_bytes(self) -> int | None:
        return None if self.model_profile is None else self.model_profile.size_bytes


@dataclass(frozen=True, slots=True)
class HardwarePresetPreferences:
    default_id: str | None = None
    overrides: Mapping[str, HardwarePresetValues] | None = None


CommandRunner = Callable[[Sequence[str]], str]


def _detect_total_memory_bytes() -> int:
    """Return installed physical memory without adding a dependency."""

    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total_physical)
        except (AttributeError, OSError, ValueError):
            return 0

    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        return 0
    return max(0, pages * page_size)


def parse_nvidia_smi_output(output: str) -> tuple[GPUInfo, ...]:
    """Parse every ``name, memory.total`` CSV row using MiB as the unit."""

    gpus: list[GPUInfo] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            name, memory_text = line.rsplit(",", 1)
            memory_mib = int(memory_text.strip())
        except (ValueError, TypeError):
            continue
        name = name.strip()
        if not name or memory_mib <= 0:
            continue
        gpus.append(GPUInfo(name=name, memory_bytes=memory_mib * _MIB))
    return tuple(gpus)


def _default_command_runner(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        capture_output=True,
        check=True,
        text=True,
        timeout=4.0,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    return completed.stdout


def detect_hardware(
    *,
    logical_cpu_count: int | None = None,
    total_memory_bytes: int | None = None,
    nvidia_smi_output: str | None = None,
    command_runner: CommandRunner | None = None,
) -> HardwareProfile:
    """Detect CPU, RAM, and every NVIDIA GPU reported by ``nvidia-smi``."""

    cpu_count = int(logical_cpu_count or os.cpu_count() or 1)
    cpu_count = max(1, cpu_count)
    memory_bytes = (
        _detect_total_memory_bytes()
        if total_memory_bytes is None
        else max(0, int(total_memory_bytes))
    )

    notes: list[str] = []
    gpus: tuple[GPUInfo, ...] = ()

    if nvidia_smi_output is not None:
        gpus = parse_nvidia_smi_output(nvidia_smi_output)
    else:
        executable = shutil.which("nvidia-smi")
        if executable:
            runner = command_runner or _default_command_runner
            try:
                output = runner(
                    (
                        executable,
                        "--query-gpu=name,memory.total",
                        "--format=csv,noheader,nounits",
                    )
                )
                gpus = parse_nvidia_smi_output(output)
                if not gpus:
                    notes.append("nvidia-smi returned no usable GPU memory data.")
            except (OSError, subprocess.SubprocessError, TimeoutError, ValueError):
                notes.append("NVIDIA GPU details could not be read from nvidia-smi.")
        else:
            notes.append(
                "No NVIDIA VRAM information was detected. llama.cpp may still "
                "use another supported accelerator."
            )

    if memory_bytes <= 0:
        notes.append("Installed system memory could not be detected.")

    return HardwareProfile(
        logical_cpu_count=cpu_count,
        total_memory_bytes=memory_bytes,
        gpus=gpus,
        notes=tuple(notes),
    )


def _read_exact(handle: Any, count: int) -> bytes:
    data = handle.read(count)
    if len(data) != count:
        raise ValueError("Truncated GGUF metadata")
    return data


def _read_u32(handle: Any) -> int:
    return struct.unpack("<I", _read_exact(handle, 4))[0]


def _read_u64(handle: Any) -> int:
    return struct.unpack("<Q", _read_exact(handle, 8))[0]


def _read_gguf_string(handle: Any) -> str:
    length = _read_u64(handle)
    if length > 16 * _MIB:
        raise ValueError("GGUF metadata string is unreasonably large")
    return _read_exact(handle, int(length)).decode("utf-8", errors="replace")


def _read_gguf_value(handle: Any, value_type: int, *, keep: bool) -> Any:
    formats: dict[int, tuple[str, int]] = {
        0: ("<B", 1),
        1: ("<b", 1),
        2: ("<H", 2),
        3: ("<h", 2),
        4: ("<I", 4),
        5: ("<i", 4),
        6: ("<f", 4),
        7: ("<?", 1),
        10: ("<Q", 8),
        11: ("<q", 8),
        12: ("<d", 8),
    }
    if value_type in formats:
        fmt, size = formats[value_type]
        raw = _read_exact(handle, size)
        return struct.unpack(fmt, raw)[0] if keep else None
    if value_type == 8:
        if keep:
            return _read_gguf_string(handle)
        length = _read_u64(handle)
        if length > 16 * _MIB:
            raise ValueError("GGUF metadata string is unreasonably large")
        _read_exact(handle, int(length))
        return None
    if value_type == 9:
        item_type = _read_u32(handle)
        count = _read_u64(handle)
        if count > 100_000_000:
            raise ValueError("GGUF metadata array is unreasonably large")
        values = [] if keep and count <= 4096 else None
        for _ in range(int(count)):
            value = _read_gguf_value(handle, item_type, keep=values is not None)
            if values is not None:
                values.append(value)
        return values
    raise ValueError(f"Unsupported GGUF metadata type: {value_type}")


def inspect_gguf_model(path_value: object) -> GGUFModelProfile | None:
    """Read lightweight GGUF metadata needed for memory estimates."""

    raw = str(path_value or "").strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
        if not path.is_file():
            return None
        size_bytes = int(path.stat().st_size)
        metadata: dict[str, Any] = {}
        with path.open("rb") as handle:
            if _read_exact(handle, 4) != b"GGUF":
                return GGUFModelProfile(size_bytes=size_bytes)
            _version = _read_u32(handle)
            _tensor_count = _read_u64(handle)
            metadata_count = _read_u64(handle)
            if metadata_count > 1_000_000:
                raise ValueError("GGUF metadata count is unreasonably large")
            for _ in range(int(metadata_count)):
                key = _read_gguf_string(handle)
                value_type = _read_u32(handle)
                keep = key == "general.architecture" or key.endswith(
                    (
                        ".block_count",
                        ".embedding_length",
                        ".attention.head_count",
                        ".attention.head_count_kv",
                    )
                )
                value = _read_gguf_value(handle, value_type, keep=keep)
                if keep:
                    metadata[key] = value
                    architecture = str(
                        metadata.get("general.architecture") or ""
                    ).strip()
                    if architecture and all(
                        f"{architecture}.{suffix}" in metadata
                        for suffix in (
                            "block_count",
                            "embedding_length",
                            "attention.head_count",
                            "attention.head_count_kv",
                        )
                    ):
                        break
    except (OSError, ValueError, OverflowError, struct.error):
        try:
            return GGUFModelProfile(size_bytes=int(Path(raw).expanduser().stat().st_size))
        except OSError:
            return None

    architecture = str(metadata.get("general.architecture") or "").strip()

    def numeric(suffix: str) -> int | None:
        preferred = metadata.get(f"{architecture}.{suffix}") if architecture else None
        candidates = [preferred]
        candidates.extend(
            value for key, value in metadata.items() if key.endswith(f".{suffix}")
        )
        for value in candidates:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
        return None

    layers = numeric("block_count")
    embedding = numeric("embedding_length")
    heads = numeric("attention.head_count")
    kv_heads = numeric("attention.head_count_kv") or heads
    kv_bytes_per_token: int | None = None
    if layers and embedding:
        kv_width = embedding
        if heads and kv_heads:
            kv_width = max(1, round(embedding * kv_heads / heads))
        kv_bytes_per_token = 2 * layers * kv_width * 2

    return GGUFModelProfile(
        size_bytes=size_bytes,
        layer_count=layers,
        kv_bytes_per_token=kv_bytes_per_token,
    )


def model_size_from_path(path_value: object) -> int | None:
    """Backward-compatible selected GGUF size helper."""

    profile = inspect_gguf_model(path_value)
    return None if profile is None else profile.size_bytes


def _estimated_physical_threads(logical_cpu_count: int) -> int:
    logical = max(1, int(logical_cpu_count))
    estimated = max(1, logical // 2) if logical >= 4 else logical
    return min(16, estimated)


def normalize_gpu_layers(value: object) -> str:
    normalized = str(value or "auto").strip().casefold()
    if normalized in {"auto", "all"}:
        return normalized
    try:
        numeric = int(normalized)
    except ValueError as error:
        raise ValueError("GPU layers must be auto, all, or a non-negative integer.") from error
    if numeric < 0:
        raise ValueError("GPU layers must not be negative.")
    return str(numeric)


def validate_preset_values(
    *, context_size: object, gpu_layers: object, threads: object
) -> HardwarePresetValues:
    try:
        context = int(context_size)
        thread_count = int(threads)
    except (TypeError, ValueError) as error:
        raise ValueError("Context size and CPU threads must be integers.") from error
    if not 256 <= context <= 2_000_000:
        raise ValueError("Context size must be between 256 and 2,000,000.")
    if not -1 <= thread_count <= 1024 or thread_count == 0:
        raise ValueError("CPU threads must be -1 or between 1 and 1,024.")
    return HardwarePresetValues(
        context_size=context,
        gpu_layers=normalize_gpu_layers(gpu_layers),
        threads=thread_count,
    )


def _preset_store_path(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    configured = str(os.environ.get(_PRESET_FILE_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return user_file_path("data/hardware_presets.json")


def load_hardware_preset_preferences(
    path: str | Path | None = None,
) -> HardwarePresetPreferences:
    target = _preset_store_path(path)
    with _STORE_LOCK:
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return HardwarePresetPreferences(default_id=None, overrides={})

    default_id = str(payload.get("default_preset_id") or "").strip().casefold()
    if default_id not in _BUILTIN_PRESET_IDS:
        default_id = None
    overrides: dict[str, HardwarePresetValues] = {}
    raw_presets = payload.get("presets")
    if isinstance(raw_presets, Mapping):
        for preset_id, raw_values in raw_presets.items():
            normalized_id = str(preset_id).strip().casefold()
            if normalized_id not in _BUILTIN_PRESET_IDS or not isinstance(
                raw_values, Mapping
            ):
                continue
            try:
                overrides[normalized_id] = validate_preset_values(
                    context_size=raw_values.get("context_size"),
                    gpu_layers=raw_values.get("gpu_layers"),
                    threads=raw_values.get("threads"),
                )
            except ValueError:
                continue
    return HardwarePresetPreferences(default_id=default_id, overrides=overrides)


def _write_preferences(
    preferences: HardwarePresetPreferences,
    path: str | Path | None = None,
) -> None:
    target = _preset_store_path(path)
    overrides = preferences.overrides or {}
    payload = {
        "default_preset_id": preferences.default_id,
        "presets": {
            preset_id: {
                "context_size": values.context_size,
                "gpu_layers": values.gpu_layers,
                "threads": values.threads,
            }
            for preset_id, values in overrides.items()
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    with _STORE_LOCK:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)


def save_hardware_preset(
    preset_id: object,
    values: HardwarePresetValues,
    *,
    set_default: bool,
    path: str | Path | None = None,
) -> HardwarePresetPreferences:
    normalized_id = str(preset_id or "").strip().casefold()
    if normalized_id not in _BUILTIN_PRESET_IDS:
        raise KeyError(normalized_id)
    current = load_hardware_preset_preferences(path)
    overrides = dict(current.overrides or {})
    overrides[normalized_id] = values
    preferences = HardwarePresetPreferences(
        default_id=normalized_id if set_default else current.default_id,
        overrides=overrides,
    )
    _write_preferences(preferences, path)
    return preferences


def reset_hardware_preset(
    preset_id: object,
    *,
    path: str | Path | None = None,
) -> HardwarePresetPreferences:
    normalized_id = str(preset_id or "").strip().casefold()
    if normalized_id not in _BUILTIN_PRESET_IDS:
        raise KeyError(normalized_id)
    current = load_hardware_preset_preferences(path)
    overrides = dict(current.overrides or {})
    overrides.pop(normalized_id, None)
    preferences = HardwarePresetPreferences(
        default_id=current.default_id,
        overrides=overrides,
    )
    _write_preferences(preferences, path)
    return preferences


def _offload_fraction(gpu_layers: str, layer_count: int | None) -> float:
    normalized = normalize_gpu_layers(gpu_layers)
    if normalized in {"auto", "all"}:
        return 1.0
    layers = int(normalized)
    if layers <= 0:
        return 0.0
    denominator = max(1, layer_count or 80)
    return min(1.0, layers / denominator)


def estimate_memory_usage(
    model_profile: GGUFModelProfile | None,
    values: HardwarePresetValues,
) -> tuple[int | None, int | None]:
    """Estimate resident VRAM and RAM for one launch configuration."""

    if model_profile is None or model_profile.size_bytes <= 0:
        return None, None
    fraction = _offload_fraction(values.gpu_layers, model_profile.layer_count)
    model_bytes = model_profile.size_bytes
    if model_profile.kv_bytes_per_token:
        kv_bytes = model_profile.kv_bytes_per_token * values.context_size
    else:
        kv_bytes = max(
            256 * _MIB,
            int(model_bytes * 0.25 * values.context_size / 8192),
        )
    vram = int(model_bytes * fraction + kv_bytes * fraction)
    ram = int(model_bytes * (1.0 - fraction) + kv_bytes * (1.0 - fraction))
    ram += 1 * _GIB
    if fraction > 0:
        vram += 512 * _MIB
    return vram, ram


def _preset_matches(
    preset: HardwarePreset,
    *,
    context_size: int | None,
    gpu_layers: object,
    threads: int | None,
) -> bool:
    if context_size is None or threads is None:
        return False
    return (
        preset.context_size == int(context_size)
        and preset.gpu_layers.casefold() == str(gpu_layers or "").strip().casefold()
        and preset.threads == int(threads)
    )


def build_hardware_presets(
    profile: HardwareProfile,
    *,
    model_profile: GGUFModelProfile | None = None,
    model_size_bytes: int | None = None,
    preferences: HardwarePresetPreferences | None = None,
    current_context_size: int | None = None,
    current_gpu_layers: object = None,
    current_threads: int | None = None,
) -> HardwarePresetCatalog:
    """Create low, 8 GB, 12 GB, and 16+ GB editable presets."""

    if model_profile is None and model_size_bytes is not None:
        model_profile = GGUFModelProfile(size_bytes=max(0, int(model_size_bytes)))
    preferences = preferences or HardwarePresetPreferences(overrides={})
    overrides = preferences.overrides or {}
    normal_threads = _estimated_physical_threads(profile.logical_cpu_count)
    safe_threads = min(4, normal_threads)

    definitions = (
        (
            "low",
            "Low end",
            "CPU-first settings for systems below 8 GB of usable VRAM.",
            6 * _GIB,
            HardwarePresetValues(4096, "0", safe_threads),
        ),
        (
            "medium",
            "Medium — 8 GB VRAM",
            "Moderate context with automatic offload for an 8 GB GPU class.",
            8 * _GIB,
            HardwarePresetValues(4096, "auto", normal_threads),
        ),
        (
            "high",
            "High — 12 GB VRAM",
            "Full offload and a larger context for a 12 GB GPU class.",
            12 * _GIB,
            HardwarePresetValues(8192, "all", normal_threads),
        ),
        (
            "ultra",
            "Ultra — 16+ GB VRAM",
            "Full offload and extended context for 16 GB or more total VRAM.",
            16 * _GIB,
            HardwarePresetValues(16384, "all", normal_threads),
        ),
    )

    total_vram = profile.total_gpu_memory_bytes
    if total_vram >= 16 * _GIB:
        recommended_id = "ultra"
    elif total_vram >= 12 * _GIB:
        recommended_id = "high"
    elif total_vram >= 8 * _GIB:
        recommended_id = "medium"
    else:
        recommended_id = "low"
    default_id = (
        preferences.default_id
        if preferences.default_id in _BUILTIN_PRESET_IDS
        else recommended_id
    )

    presets: list[HardwarePreset] = []
    current_id: str | None = None
    for preset_id, name, description, target_vram, builtin in definitions:
        values = overrides.get(preset_id, builtin)
        estimated_vram, estimated_ram = estimate_memory_usage(model_profile, values)
        warnings: list[str] = []
        if model_profile is None:
            warnings.append("Select a GGUF model to calculate memory estimates.")
        if estimated_vram is not None and estimated_vram > target_vram:
            warnings.append(
                "Estimated VRAM use exceeds this preset's target hardware tier."
            )
        if total_vram and estimated_vram is not None and estimated_vram > total_vram:
            warnings.append("Estimated VRAM use exceeds detected total VRAM.")
        if (
            profile.total_memory_bytes
            and estimated_ram is not None
            and estimated_ram > profile.total_memory_bytes
        ):
            warnings.append("Estimated RAM use exceeds detected system memory.")
        if not profile.gpus and values.gpu_layers != "0":
            warnings.append(
                "NVIDIA VRAM was not detected; GPU offload may be unavailable."
            )
        if (
            model_profile is not None
            and model_profile.layer_count is None
            and values.gpu_layers not in {"0", "auto", "all"}
        ):
            warnings.append(
                "GGUF layer count was unavailable; partial-offload estimates assume 80 layers."
            )

        preset = HardwarePreset(
            id=preset_id,
            name=name,
            description=description,
            target_vram_bytes=target_vram,
            context_size=values.context_size,
            gpu_layers=values.gpu_layers,
            threads=values.threads,
            estimated_vram_bytes=estimated_vram,
            estimated_ram_bytes=estimated_ram,
            recommended=preset_id == recommended_id,
            default=preset_id == default_id,
            customized=preset_id in overrides,
            warnings=tuple(warnings),
        )
        is_current = _preset_matches(
            preset,
            context_size=current_context_size,
            gpu_layers=current_gpu_layers,
            threads=current_threads,
        )
        if is_current:
            current_id = preset.id
        presets.append(replace(preset, current=is_current))

    return HardwarePresetCatalog(
        profile=profile,
        model_profile=model_profile,
        presets=tuple(presets),
        recommended_id=recommended_id,
        default_id=default_id,
        current_id=current_id,
    )


def get_hardware_preset(
    catalog: HardwarePresetCatalog,
    preset_id: object,
) -> HardwarePreset:
    normalized = str(preset_id or "").strip().casefold()
    for preset in catalog.presets:
        if preset.id.casefold() == normalized:
            return preset
    raise KeyError(normalized)
