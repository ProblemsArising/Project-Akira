"""Audio device discovery and persistent selection for Project Akira.

The future WebUI can call the functions in this module directly.  The current
command-line interface also uses them for listing and selecting microphone and
speaker devices without editing Python files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from config.settings import get_settings, update_settings

AudioDeviceKind = Literal["input", "output"]

# Windows exposes the same physical endpoint through several PortAudio host APIs.
# WASAPI most closely matches the concise device list shown by Windows itself.
_HOST_API_PRIORITY = {
    "windows wasapi": 0,
    "windows directsound": 1,
    "mme": 2,
    "windows wdm-ks": 3,
}


class AudioDeviceError(RuntimeError):
    """Raised when an audio device cannot be found or used for the requested role."""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """Serializable description of one PortAudio device."""

    index: int
    name: str
    host_api: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_default_input: bool = False
    is_default_output: bool = False

    @property
    def supports_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def supports_output(self) -> bool:
        return self.max_output_channels > 0

    @property
    def selection_key(self) -> str:
        """Stable-ish selector stored in settings instead of a volatile index."""

        return f"{self.name} | {self.host_api}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "host_api": self.host_api,
            "selection_key": self.selection_key,
            "max_input_channels": self.max_input_channels,
            "max_output_channels": self.max_output_channels,
            "default_sample_rate": self.default_sample_rate,
            "is_default_input": self.is_default_input,
            "is_default_output": self.is_default_output,
        }


def _sounddevice():
    try:
        import sounddevice as sd
    except ImportError as error:  # pragma: no cover - normal install includes it.
        raise AudioDeviceError(
            "The sounddevice package is required for audio device selection."
        ) from error
    return sd


def _default_pair(sd_module: Any) -> tuple[int | None, int | None]:
    raw = getattr(getattr(sd_module, "default", None), "device", (None, None))

    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        input_index = raw[0] if len(raw) > 0 else None
        output_index = raw[1] if len(raw) > 1 else None
    else:
        input_index = raw
        output_index = raw

    def clean(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    return clean(input_index), clean(output_index)


def list_audio_devices(*, sd_module: Any | None = None) -> list[AudioDevice]:
    """Return all available PortAudio devices with input/output capabilities."""

    sd_module = sd_module or _sounddevice()
    try:
        raw_devices = list(sd_module.query_devices())
        raw_host_apis = list(sd_module.query_hostapis())
    except Exception as error:
        raise AudioDeviceError(f"Could not query audio devices: {error}") from error

    default_input, default_output = _default_pair(sd_module)
    devices: list[AudioDevice] = []

    for index, raw in enumerate(raw_devices):
        host_index = int(raw.get("hostapi", -1))
        if 0 <= host_index < len(raw_host_apis):
            host_name = str(raw_host_apis[host_index].get("name", f"Host API {host_index}"))
        else:
            host_name = f"Host API {host_index}"

        devices.append(
            AudioDevice(
                index=index,
                name=str(raw.get("name", f"Device {index}")),
                host_api=host_name,
                max_input_channels=int(raw.get("max_input_channels", 0)),
                max_output_channels=int(raw.get("max_output_channels", 0)),
                default_sample_rate=float(raw.get("default_samplerate", 0.0)),
                is_default_input=index == default_input,
                is_default_output=index == default_output,
            )
        )

    return devices



def _host_api_rank(device: AudioDevice) -> int:
    return _HOST_API_PRIORITY.get(device.host_api.casefold(), 50)


def _normalized_device_name(name: str) -> str:
    """Normalize an endpoint name for cross-host-API comparisons."""

    return " ".join(str(name).casefold().split())


def _same_physical_endpoint(left: AudioDevice, right: AudioDevice) -> bool:
    """Best-effort comparison for one endpoint exposed by multiple host APIs."""

    a = _normalized_device_name(left.name)
    b = _normalized_device_name(right.name)
    if a == b:
        return True

    # MME may truncate long endpoint names. Treat a reasonably long prefix as
    # the same endpoint so default markers can transfer to the WASAPI entry.
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 12 and longer.startswith(shorter)


def preferred_audio_devices(
    *,
    devices: Sequence[AudioDevice] | None = None,
    kind: AudioDeviceKind | None = None,
) -> list[AudioDevice]:
    """Return a concise user-facing device list.

    On Windows, PortAudio commonly exposes every physical endpoint through MME,
    DirectSound, WASAPI and WDM-KS. If WASAPI endpoints are available for the
    requested capability, only WASAPI entries are shown. The raw list remains
    available for troubleshooting through ``--devices-all``.
    """

    available = list(devices or list_audio_devices())
    if kind is not None:
        available = _eligible_devices(available, kind)

    wasapi = [
        device
        for device in available
        if device.host_api.casefold() == "windows wasapi"
    ]
    return wasapi if wasapi else available


def _preferred_match(matches: Sequence[AudioDevice]) -> AudioDevice | None:
    """Resolve duplicate names by choosing the best Windows host API."""

    if not matches:
        return None

    ranked = sorted(matches, key=lambda item: (_host_api_rank(item), item.index))
    best_rank = _host_api_rank(ranked[0])
    best = [item for item in ranked if _host_api_rank(item) == best_rank]
    return best[0] if len(best) == 1 else None

def input_devices(*, devices: Sequence[AudioDevice] | None = None) -> list[AudioDevice]:
    return [device for device in (devices or list_audio_devices()) if device.supports_input]


def output_devices(*, devices: Sequence[AudioDevice] | None = None) -> list[AudioDevice]:
    return [device for device in (devices or list_audio_devices()) if device.supports_output]


def _eligible_devices(
    devices: Sequence[AudioDevice],
    kind: AudioDeviceKind,
) -> list[AudioDevice]:
    return input_devices(devices=devices) if kind == "input" else output_devices(devices=devices)


def _normalize_selector(selector: int | str | None) -> int | str | None:
    if selector is None:
        return None
    if isinstance(selector, int):
        return selector

    value = str(selector).strip()
    if not value or value.casefold() in {"default", "auto", "none", "null"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def resolve_audio_device(
    selector: int | str | None,
    kind: AudioDeviceKind,
    *,
    devices: Sequence[AudioDevice] | None = None,
) -> AudioDevice | None:
    """Resolve an index, name, or selection key to one input/output device.

    ``None`` means use the operating system's default device.  Name matching is
    case-insensitive. Exact matches are preferred; otherwise a unique substring
    match is accepted.
    """

    normalized = _normalize_selector(selector)
    if normalized is None:
        return None

    all_devices = list(devices or list_audio_devices())
    eligible = _eligible_devices(all_devices, kind)

    if isinstance(normalized, int):
        for device in eligible:
            if device.index == normalized:
                return device
        raise AudioDeviceError(
            f"Audio device index {normalized} is not a usable {kind} device."
        )

    query = normalized.casefold()
    exact = [
        device
        for device in eligible
        if query in {device.name.casefold(), device.selection_key.casefold()}
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        preferred = _preferred_match(exact)
        if preferred is not None:
            return preferred
        raise AudioDeviceError(_ambiguous_message(normalized, kind, exact))

    partial = [
        device
        for device in eligible
        if query in device.name.casefold()
        or query in device.host_api.casefold()
        or query in device.selection_key.casefold()
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        preferred = _preferred_match(partial)
        if preferred is not None:
            return preferred
        raise AudioDeviceError(_ambiguous_message(normalized, kind, partial))

    raise AudioDeviceError(f"No {kind} audio device matched {normalized!r}.")


def _ambiguous_message(
    selector: str,
    kind: AudioDeviceKind,
    matches: Sequence[AudioDevice],
) -> str:
    options = ", ".join(f"{item.index}: {item.selection_key}" for item in matches)
    return f"{kind.title()} device {selector!r} is ambiguous. Use an index: {options}"


def canonical_device_selector(
    selector: int | str | None,
    kind: AudioDeviceKind,
    *,
    devices: Sequence[AudioDevice] | None = None,
) -> str | None:
    """Validate a selector and return the persistent value saved to settings."""

    device = resolve_audio_device(selector, kind, devices=devices)
    return None if device is None else device.selection_key


def configure_audio_devices(
    *,
    input_device: int | str | None | object = ...,
    output_device: int | str | None | object = ...,
    devices: Sequence[AudioDevice] | None = None,
):
    """Validate and persist microphone/speaker selections.

    Omitted arguments are left unchanged. Passing ``None`` (or a CLI value such
    as ``default``) restores operating-system default routing.
    """

    available = list(devices or list_audio_devices())
    changes: dict[str, dict[str, str | None]] = {"audio": {}}

    if input_device is not ...:
        changes["audio"]["input_device"] = canonical_device_selector(
            input_device, "input", devices=available
        )
    if output_device is not ...:
        changes["audio"]["output_device"] = canonical_device_selector(
            output_device, "output", devices=available
        )

    if not changes["audio"]:
        return get_settings()
    return update_settings(changes)


def current_audio_selection(
    *,
    devices: Sequence[AudioDevice] | None = None,
) -> dict[str, AudioDevice | None]:
    """Return resolved configured devices, using ``None`` for system defaults."""

    available = list(devices or list_audio_devices())
    settings = get_settings()
    return {
        "input": resolve_audio_device(
            settings.audio.input_device, "input", devices=available
        ),
        "output": resolve_audio_device(
            settings.audio.output_device, "output", devices=available
        ),
    }


def format_audio_device_table(
    *,
    devices: Sequence[AudioDevice] | None = None,
    show_all: bool = False,
) -> str:
    """Create a readable device table for the temporary command-line UI.

    The normal view shows preferred Windows WASAPI endpoints. ``show_all=True``
    exposes every raw PortAudio entry for advanced troubleshooting.
    """

    available = list(devices or list_audio_devices())
    settings = get_settings()

    try:
        configured_input = resolve_audio_device(
            settings.audio.input_device, "input", devices=available
        )
    except AudioDeviceError:
        configured_input = None
    try:
        configured_output = resolve_audio_device(
            settings.audio.output_device, "output", devices=available
        )
    except AudioDeviceError:
        configured_output = None

    if show_all:
        displayed = list(available)
    else:
        displayed = preferred_audio_devices(devices=available)

        # An explicitly configured legacy/advanced endpoint should remain visible
        # even when it is outside the normal WASAPI list.
        for selected in (configured_input, configured_output):
            if selected is not None and all(
                item.index != selected.index for item in displayed
            ):
                displayed.append(selected)

        displayed.sort(key=lambda item: (item.name.casefold(), item.index))

    default_inputs = [item for item in available if item.is_default_input]
    default_outputs = [item for item in available if item.is_default_output]

    def is_default_equivalent(
        device: AudioDevice,
        defaults: Sequence[AudioDevice],
    ) -> bool:
        return any(_same_physical_endpoint(device, default) for default in defaults)

    def format_section(
        title: str,
        kind: AudioDeviceKind,
        section_devices: Sequence[AudioDevice],
    ) -> list[str]:
        lines = [title, "-" * len(title)]
        eligible = _eligible_devices(section_devices, kind)

        if not eligible:
            lines.append("  No devices found.")
            return lines

        for device in eligible:
            if kind == "input":
                configured = (
                    configured_input is not None
                    and device.index == configured_input.index
                )
                system_default = (
                    settings.audio.input_device is None
                    and (
                        device.is_default_input
                        or is_default_equivalent(device, default_inputs)
                    )
                )
                marker = "I" if configured else "i" if system_default else "-"
                channels = f"{device.max_input_channels} in"
            else:
                configured = (
                    configured_output is not None
                    and device.index == configured_output.index
                )
                system_default = (
                    settings.audio.output_device is None
                    and (
                        device.is_default_output
                        or is_default_equivalent(device, default_outputs)
                    )
                )
                marker = "O" if configured else "o" if system_default else "-"
                channels = f"{device.max_output_channels} out"

            host = f" | {device.host_api}" if show_all else ""
            lines.append(
                f"[{marker}] {device.index:>3} | {device.name}{host} | {channels}"
            )

        return lines

    lines = [
        "Audio devices",
        "-------------",
        "Markers: I/O = Project Akira selection, i/o = Windows default",
        "",
    ]
    lines.extend(format_section("Microphones", "input", displayed))
    lines.append("")
    lines.extend(format_section("Outputs", "output", displayed))

    if not show_all:
        lines.extend(
            [
                "",
                "Showing preferred Windows audio endpoints.",
                "Use `python assistant.py --devices-all` for the raw PortAudio list.",
            ]
        )

    lines.extend(
        [
            "",
            f"Configured input:  {settings.audio.input_device or 'system default'}",
            f"Configured output: {settings.audio.output_device or 'system default'}",
        ]
    )
    return "\n".join(lines)
