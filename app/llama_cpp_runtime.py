"""Install and discover Project Akira-managed llama.cpp runtimes.

The runtime installer downloads a pinned, known-good official llama.cpp release,
verifies every archive, extracts it into a staging directory, validates
``llama-server`` before activation, and only then updates Project Akira's
configured executable path. Manual executable selection remains supported and
always takes precedence over the managed runtime fallback.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import stat
import subprocess
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from app.paths import USER_DATA_ROOT


LLAMA_CPP_RUNTIME_VERSION = "b10080"
LLAMA_CPP_RUNTIME_ROOT = USER_DATA_ROOT / "runtimes" / "llama.cpp"
_CHUNK_SIZE = 1024 * 1024
_ACTIVE_RECORD = "active.json"
_RUNTIME_RECORD = "runtime.json"

_LLAMA_CPP_LICENSE = """MIT License

Copyright (c) 2023-2026 The ggml authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


class LlamaCppRuntimeError(RuntimeError):
    """Raised when a managed runtime operation cannot be completed."""


@dataclass(frozen=True, slots=True)
class RuntimeAsset:
    filename: str
    url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeVariant:
    id: str
    name: str
    description: str
    assets: tuple[RuntimeAsset, ...]
    required_files: tuple[str, ...] = ("llama-server.exe",)
    device_marker: str | None = None

    def to_dict(self, *, recommended: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "asset_count": len(self.assets),
            "recommended": recommended,
        }


_RELEASE_ROOT = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_CPP_RUNTIME_VERSION}"
)

RUNTIME_VARIANTS: dict[str, RuntimeVariant] = {
    "cuda12": RuntimeVariant(
        id="cuda12",
        name="NVIDIA CUDA 12",
        description=(
            "Recommended for NVIDIA GPUs. Downloads the llama.cpp CUDA build "
            "and its matching CUDA 12.4 runtime DLL package."
        ),
        assets=(
            RuntimeAsset(
                filename=(
                    "llama-b10080-bin-win-cuda-12.4-x64.zip"
                ),
                url=(
                    f"{_RELEASE_ROOT}/"
                    "llama-b10080-bin-win-cuda-12.4-x64.zip"
                ),
                sha256=(
                    "29dd04069a62c41acf6924fc6ca72951"
                    "b9f4a277ba064406bee4ea1f28365d3c"
                ),
            ),
            RuntimeAsset(
                filename="cudart-llama-bin-win-cuda-12.4-x64.zip",
                url=(
                    f"{_RELEASE_ROOT}/"
                    "cudart-llama-bin-win-cuda-12.4-x64.zip"
                ),
                sha256=(
                    "8c79a9b226de4b3cacfd1f83d24f962"
                    "d0773be79f1e7b75c6af4ded7e32ae1d6"
                ),
            ),
        ),
        required_files=(
            "llama-server.exe",
            "ggml-cuda.dll",
            "cudart64_12.dll",
            "cublas64_12.dll",
            "cublasLt64_12.dll",
        ),
        device_marker="cuda",
    ),
    "vulkan": RuntimeVariant(
        id="vulkan",
        name="Vulkan",
        description=(
            "Recommended for AMD, Intel, and other Vulkan-capable GPUs."
        ),
        assets=(
            RuntimeAsset(
                filename="llama-b10080-bin-win-vulkan-x64.zip",
                url=(
                    f"{_RELEASE_ROOT}/"
                    "llama-b10080-bin-win-vulkan-x64.zip"
                ),
                sha256=(
                    "773ee55da651e5ee4e9e7769edc3e641"
                    "9024f9dec1ea279e5be08048247e4466"
                ),
            ),
        ),
        required_files=("llama-server.exe", "ggml-vulkan.dll"),
        device_marker="vulkan",
    ),
    "cpu": RuntimeVariant(
        id="cpu",
        name="CPU only",
        description=(
            "Smallest fallback for systems without usable GPU acceleration."
        ),
        assets=(
            RuntimeAsset(
                filename="llama-b10080-bin-win-cpu-x64.zip",
                url=(
                    f"{_RELEASE_ROOT}/"
                    "llama-b10080-bin-win-cpu-x64.zip"
                ),
                sha256=(
                    "c8526c6a207d672160d80e990b8ae10a"
                    "914efcd1a51e46a8da1a08970fb4db42"
                ),
            ),
        ),
    ),
}


@dataclass(slots=True)
class RuntimeInstallJob:
    id: str
    version: str
    variant: str
    status: str = "queued"
    current_asset: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    error: str | None = None
    executable: str | None = None
    devices: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        total = self.total_bytes
        payload["progress"] = (
            None
            if not total or total <= 0
            else min(1.0, self.downloaded_bytes / total)
        )
        return payload


OpenUrl = Callable[..., Any]
ThreadFactory = Callable[..., Any]
Runner = Callable[..., Any]
ActivationCallback = Callable[[Path], str | None]
RemovalCallback = Callable[[Path, str | None], None]


def _default_activate(executable: Path) -> str | None:
    from config.settings import get_settings, update_settings

    previous = str(get_settings().llm.llama_cpp_executable or "").strip() or None
    update_settings(
        {"llm": {"llama_cpp_executable": str(executable.resolve())}}
    )
    return previous


def _default_remove(
    executable: Path,
    previous_executable: str | None,
) -> None:
    from config.settings import get_settings, update_settings

    configured = str(get_settings().llm.llama_cpp_executable or "").strip()
    if not configured:
        return
    if Path(configured).expanduser().resolve() != executable.resolve():
        return
    restored = str(previous_executable or "").strip()
    if restored and not Path(restored).expanduser().is_file():
        restored = ""
    update_settings({"llm": {"llama_cpp_executable": restored}})


def _supported_windows_x64(
    *, system: str | None = None, machine: str | None = None
) -> bool:
    system_value = (system or platform.system()).casefold()
    machine_value = (machine or platform.machine()).casefold()
    return system_value == "windows" and machine_value in {
        "amd64",
        "x86_64",
        "x64",
    }


def detect_recommended_variant(
    *,
    runner: Runner = subprocess.run,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """Return the recommended Windows runtime without changing the system."""

    if not _supported_windows_x64(system=system, machine=machine):
        return "cpu"
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        result = runner(
            [
                "nvidia-smi",
                "--query-gpu=name",
                "--format=csv,noheader",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5.0,
            check=False,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    if result is not None and int(getattr(result, "returncode", 1)) == 0:
        if str(getattr(result, "stdout", "") or "").strip():
            return "cuda12"
    return "vulkan"


def _hash_file(path: Path, chunk_size: int = _CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else 200
    return int(status or 200)


def _header(headers: Mapping[str, Any] | Any, name: str) -> str:
    getter = getattr(headers, "get", None)
    return str(getter(name, "") or "").strip() if callable(getter) else ""


def _range_start(response: Any) -> int | None:
    value = _header(getattr(response, "headers", {}), "Content-Range")
    if not value.casefold().startswith("bytes ") or "-" not in value:
        return None
    start = value[6:].split("-", 1)[0].strip()
    return int(start) if start.isdigit() else None


def _response_total(response: Any, resumed: int) -> int | None:
    headers = getattr(response, "headers", {})
    content_range = _header(headers, "Content-Range")
    if "/" in content_range:
        value = content_range.rsplit("/", 1)[-1].strip()
        if value.isdigit():
            return int(value)
    length = _header(headers, "Content-Length")
    if length.isdigit():
        parsed = int(length)
        return resumed + parsed if _response_status(response) == 206 else parsed
    return None


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    """Extract a ZIP without allowing traversal or symbolic links."""

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as package:
            for info in package.infolist():
                if _is_zip_symlink(info):
                    raise LlamaCppRuntimeError(
                        f"Runtime archive contains a symbolic link: {info.filename}"
                    )
                target = (destination / info.filename).resolve()
                try:
                    target.relative_to(destination)
                except ValueError as error:
                    raise LlamaCppRuntimeError(
                        "Runtime archive attempted to write outside the "
                        f"staging directory: {info.filename}"
                    ) from error
            package.extractall(destination)
    except zipfile.BadZipFile as error:
        raise LlamaCppRuntimeError(
            f"Runtime archive is not a valid ZIP file: {archive.name}"
        ) from error


def _extract_runtime_payload(
    archive: Path,
    destination: Path,
    *,
    include_server: bool,
) -> set[str]:
    """Extract only llama-server and its DLL dependencies.

    Official llama.cpp Windows release archives also contain many standalone
    helper executables. Project Akira does not invoke those tools, and some
    antivirus products heuristically quarantine individual helpers while a ZIP
    is being extracted. Writing only the server executable and DLLs avoids
    exposing unused binaries and prevents a quarantine from interrupting the
    installation halfway through.

    Files are flattened into one directory because Windows resolves the
    server's runtime DLL dependencies beside ``llama-server.exe``.
    """

    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    extracted: set[str] = set()
    try:
        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            for info in members:
                if _is_zip_symlink(info):
                    raise LlamaCppRuntimeError(
                        f"Runtime archive contains a symbolic link: {info.filename}"
                    )
                target = (destination / info.filename).resolve()
                try:
                    target.relative_to(destination)
                except ValueError as error:
                    raise LlamaCppRuntimeError(
                        "Runtime archive attempted to write outside the "
                        f"staging directory: {info.filename}"
                    ) from error

            for info in members:
                if info.is_dir():
                    continue
                archive_name = info.filename.replace("\\", "/")
                filename = PurePosixPath(archive_name).name
                lowered = filename.casefold()
                if lowered == "llama-server.exe":
                    if not include_server:
                        continue
                elif not lowered.endswith(".dll"):
                    continue

                target = destination / filename
                with package.open(info, "r") as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=_CHUNK_SIZE)
                extracted.add(filename)
    except zipfile.BadZipFile as error:
        raise LlamaCppRuntimeError(
            f"Runtime archive is not a valid ZIP file: {archive.name}"
        ) from error
    return extracted


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def find_managed_llama_cpp_executable(
    root: str | Path = LLAMA_CPP_RUNTIME_ROOT,
) -> Path | None:
    """Resolve the active managed runtime without importing settings."""

    base = Path(root).expanduser().resolve()
    record = _read_json(base / _ACTIVE_RECORD)
    if record:
        raw = str(record.get("executable") or "").strip()
        if raw:
            candidate = Path(raw).expanduser().resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                return None
            if candidate.is_file():
                return candidate
    candidates = sorted(
        base.glob("*/**/llama-server.exe"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )
    return candidates[0].resolve() if candidates else None


def is_managed_llama_cpp_path(
    path: str | Path,
    root: str | Path = LLAMA_CPP_RUNTIME_ROOT,
) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).resolve())
    except (OSError, ValueError):
        return False
    return True


class LlamaCppRuntimeManager:
    """Own one background managed-runtime install or repair operation."""

    def __init__(
        self,
        root: str | Path = LLAMA_CPP_RUNTIME_ROOT,
        *,
        variants: Mapping[str, RuntimeVariant] = RUNTIME_VARIANTS,
        opener: OpenUrl = urlopen,
        runner: Runner = subprocess.run,
        thread_factory: ThreadFactory = threading.Thread,
        activate: ActivationCallback = _default_activate,
        remove_callback: RemovalCallback = _default_remove,
        chunk_size: int = _CHUNK_SIZE,
        system: str | None = None,
        machine: str | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.variants = dict(variants)
        self._opener = opener
        self._runner = runner
        self._thread_factory = thread_factory
        self._activate = activate
        self._remove_callback = remove_callback
        self._chunk_size = max(4096, int(chunk_size))
        self._system = system
        self._machine = machine
        self._job: RuntimeInstallJob | None = None
        self._thread: Any | None = None
        self._recommended: str | None = None
        self._cancel = threading.Event()
        self._lock = threading.RLock()

    @property
    def supported(self) -> bool:
        return _supported_windows_x64(
            system=self._system,
            machine=self._machine,
        )

    def recommended_variant(self) -> str:
        with self._lock:
            cached = self._recommended
        if cached is not None:
            return cached
        if not self.supported:
            selected = "cpu"
        else:
            selected = detect_recommended_variant(
                runner=self._runner,
                system=self._system,
                machine=self._machine,
            )
        with self._lock:
            self._recommended = selected
        return selected

    def _copy_job(self) -> RuntimeInstallJob | None:
        with self._lock:
            return (
                None
                if self._job is None
                else RuntimeInstallJob(**asdict(self._job))
            )

    def _set_job(self, **changes: Any) -> None:
        with self._lock:
            if self._job is None:
                return
            for name, value in changes.items():
                setattr(self._job, name, value)

    def _installed_record(self) -> dict[str, Any] | None:
        active = _read_json(self.root / _ACTIVE_RECORD)
        if not active:
            return None
        raw = str(active.get("executable") or "").strip()
        if not raw:
            return None
        executable = Path(raw).expanduser().resolve()
        if not executable.is_file() or not is_managed_llama_cpp_path(
            executable, self.root
        ):
            return None
        record = dict(active)
        record["executable"] = str(executable)
        return record

    def snapshot(self) -> dict[str, Any]:
        recommended = self.recommended_variant()
        installed = self._installed_record()
        return {
            "supported": self.supported,
            "version": LLAMA_CPP_RUNTIME_VERSION,
            "root": str(self.root),
            "recommended_variant": recommended,
            "variants": [
                variant.to_dict(recommended=variant.id == recommended)
                for variant in self.variants.values()
            ],
            "installed": installed is not None,
            "installed_version": None if installed is None else installed.get("version"),
            "installed_variant": None if installed is None else installed.get("variant"),
            "executable": None if installed is None else installed.get("executable"),
            "devices": [] if installed is None else list(installed.get("devices") or []),
            "job": None if self._job is None else self._job.to_dict(),
        }

    def start_install(self, variant: object = "recommended") -> RuntimeInstallJob:
        if not self.supported:
            raise LlamaCppRuntimeError(
                "The managed llama.cpp installer currently supports Windows x64."
            )
        requested = str(variant or "recommended").strip().casefold()
        selected = self.recommended_variant() if requested == "recommended" else requested
        if selected not in self.variants:
            raise LlamaCppRuntimeError(f"Unknown runtime variant: {requested}")
        with self._lock:
            if self._job is not None and self._job.status in {
                "queued",
                "downloading",
                "verifying",
                "extracting",
                "validating",
                "activating",
            }:
                raise LlamaCppRuntimeError(
                    "A llama.cpp runtime operation is already in progress."
                )
            self._cancel = threading.Event()
            self._job = RuntimeInstallJob(
                id=uuid.uuid4().hex,
                version=LLAMA_CPP_RUNTIME_VERSION,
                variant=selected,
            )
            self._thread = self._thread_factory(
                target=self._run_install,
                name=f"akira-llama-runtime-{self._job.id[:8]}",
                daemon=True,
            )
            self._thread.start()
            return RuntimeInstallJob(**asdict(self._job))

    def cancel(self) -> RuntimeInstallJob:
        with self._lock:
            if self._job is None:
                raise LlamaCppRuntimeError("No runtime installation is active.")
            if self._job.status not in {
                "queued",
                "downloading",
                "verifying",
                "extracting",
                "validating",
            }:
                raise LlamaCppRuntimeError(
                    f"Runtime operation cannot be cancelled while {self._job.status}."
                )
            self._cancel.set()
            if self._job.status == "queued":
                self._job.status = "cancelled"
            return RuntimeInstallJob(**asdict(self._job))

    def _check_cancelled(self) -> None:
        if self._cancel.is_set():
            raise InterruptedError("Runtime installation was cancelled.")

    def _download_asset(
        self,
        asset: RuntimeAsset,
        downloads: Path,
        completed_bytes: int,
    ) -> Path:
        self._check_cancelled()
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / asset.filename
        if archive.is_file() and _hash_file(archive, self._chunk_size) == asset.sha256:
            self._set_job(
                current_asset=asset.filename,
                downloaded_bytes=completed_bytes + archive.stat().st_size,
                total_bytes=completed_bytes + archive.stat().st_size,
            )
            return archive
        if archive.exists():
            archive.unlink()
        partial = archive.with_suffix(archive.suffix + ".part")
        resumed = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "Project-Akira"}
        if resumed:
            headers["Range"] = f"bytes={resumed}-"
        request = Request(asset.url, headers=headers)
        self._set_job(
            status="downloading",
            current_asset=asset.filename,
            downloaded_bytes=completed_bytes + resumed,
            total_bytes=None,
            error=None,
        )
        with self._opener(request, timeout=60.0) as response:
            status_code = _response_status(response)
            append = resumed > 0 and status_code == 206
            if append and _range_start(response) != resumed:
                raise LlamaCppRuntimeError(
                    "The release server returned an unexpected byte range. "
                    "The partial archive was kept for retry."
                )
            if resumed and not append:
                resumed = 0
            total = _response_total(response, resumed)
            self._set_job(
                downloaded_bytes=completed_bytes + resumed,
                total_bytes=(
                    None if total is None else completed_bytes + total
                ),
            )
            mode = "ab" if append else "wb"
            downloaded = resumed
            with partial.open(mode) as output:
                while True:
                    self._check_cancelled()
                    chunk = response.read(self._chunk_size)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    self._set_job(
                        downloaded_bytes=completed_bytes + downloaded
                    )
            if total is not None and downloaded != total:
                raise LlamaCppRuntimeError(
                    f"Download ended at {downloaded} bytes; expected {total}. "
                    "The partial archive was kept for retry."
                )
        self._check_cancelled()
        self._set_job(status="verifying")
        actual = _hash_file(partial, self._chunk_size)
        if actual != asset.sha256:
            partial.unlink(missing_ok=True)
            raise LlamaCppRuntimeError(
                f"SHA-256 verification failed for {asset.filename}. "
                "The invalid archive was removed."
            )
        partial.replace(archive)
        return archive

    def _validate_runtime(
        self,
        directory: Path,
        variant: RuntimeVariant,
    ) -> tuple[Path, list[str]]:
        for filename in variant.required_files:
            if not (directory / filename).is_file():
                raise LlamaCppRuntimeError(
                    f"Installed runtime is missing required file: {filename}"
                )
        executable = directory / "llama-server.exe"
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            result = self._runner(
                [str(executable), "--list-devices"],
                cwd=str(directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=30.0,
                check=False,
                creationflags=flags,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise LlamaCppRuntimeError(
                f"llama-server device validation could not run: {error}"
            ) from error
        output = str(getattr(result, "stdout", "") or "").strip()
        if int(getattr(result, "returncode", 1)) != 0:
            raise LlamaCppRuntimeError(
                "llama-server --list-devices failed. "
                f"{output or 'No diagnostic output was returned.'}"
            )
        if variant.device_marker and variant.device_marker not in output.casefold():
            raise LlamaCppRuntimeError(
                f"The {variant.name} runtime did not report a usable "
                f"{variant.device_marker.upper()} device. Choose another "
                "variant or update the graphics driver."
            )
        devices = [line.strip() for line in output.splitlines() if line.strip()]
        return executable, devices

    def _run_install(self) -> None:
        job = self._copy_job()
        if job is None:
            return
        variant = self.variants[job.variant]
        downloads = self.root / "downloads" / LLAMA_CPP_RUNTIME_VERSION
        staging = self.root / f".install-{job.id}"
        payload = staging / "payload"
        target = self.root / LLAMA_CPP_RUNTIME_VERSION / variant.id
        backup = target.with_name(target.name + ".backup")
        try:
            completed = 0
            archives: list[Path] = []
            for asset in variant.assets:
                self._check_cancelled()
                archive = self._download_asset(asset, downloads, completed)
                archives.append(archive)
                completed += archive.stat().st_size
            self._check_cancelled()
            self._set_job(
                status="extracting",
                downloaded_bytes=completed,
                total_bytes=completed,
            )
            staging.mkdir(parents=True, exist_ok=False)
            payload.mkdir(parents=True, exist_ok=True)
            for index, archive in enumerate(archives):
                self._check_cancelled()
                _extract_runtime_payload(
                    archive,
                    payload,
                    include_server=index == 0,
                )
            (payload / "llama.cpp-LICENSE.txt").write_text(
                _LLAMA_CPP_LICENSE,
                encoding="utf-8",
            )
            self._check_cancelled()
            self._set_job(status="validating")
            executable, devices = self._validate_runtime(payload, variant)
            record = {
                "version": LLAMA_CPP_RUNTIME_VERSION,
                "variant": variant.id,
                "executable": str(target / executable.name),
                "devices": devices,
                "source": "official llama.cpp GitHub release",
            }
            _write_json(payload / _RUNTIME_RECORD, record)
            self._check_cancelled()
            self._set_job(status="activating")
            target.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                target.replace(backup)
            active_record_path = self.root / _ACTIVE_RECORD
            previous_active_record = _read_json(active_record_path)
            try:
                payload.replace(target)
                active_executable = target / "llama-server.exe"
                record["executable"] = str(active_executable.resolve())
                _write_json(target / _RUNTIME_RECORD, record)
                _write_json(active_record_path, record)
                previous_executable = self._activate(active_executable)
                if previous_executable and is_managed_llama_cpp_path(
                    previous_executable,
                    self.root,
                ):
                    previous_executable = (
                        None
                        if previous_active_record is None
                        else previous_active_record.get("previous_executable")
                    )
                if previous_executable:
                    record["previous_executable"] = str(previous_executable)
                    _write_json(target / _RUNTIME_RECORD, record)
                    _write_json(active_record_path, record)
            except Exception:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if backup.exists():
                    backup.replace(target)
                if previous_active_record is None:
                    active_record_path.unlink(missing_ok=True)
                else:
                    _write_json(active_record_path, previous_active_record)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            self._set_job(
                status="installed",
                executable=str(active_executable.resolve()),
                devices=devices,
                error=None,
            )
        except InterruptedError:
            self._set_job(status="cancelled", error=None)
        except Exception as error:
            self._set_job(
                status="failed",
                error=str(error) or type(error).__name__,
            )
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def remove(self) -> dict[str, Any]:
        with self._lock:
            if self._job is not None and self._job.status in {
                "queued",
                "downloading",
                "verifying",
                "extracting",
                "validating",
                "activating",
            }:
                raise LlamaCppRuntimeError(
                    "Cancel the active runtime operation before removal."
                )
        record = self._installed_record()
        if record is None:
            raise LlamaCppRuntimeError("No managed llama.cpp runtime is installed.")
        executable = Path(str(record["executable"])).resolve()
        self._remove_callback(
            executable,
            str(record.get("previous_executable") or "").strip() or None,
        )
        # Keep verified release archives for resumable reinstall, but remove
        # every extracted managed runtime so executable fallback cannot silently
        # reactivate an older variant after the user chooses Remove.
        if self.root.exists():
            for child in self.root.iterdir():
                if child.name == "downloads" or child.name == _ACTIVE_RECORD:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=False)
        active = self.root / _ACTIVE_RECORD
        active.unlink(missing_ok=True)
        with self._lock:
            self._job = None
        return self.snapshot()

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            job = self._job
            if job is not None and job.status in {
                "queued",
                "downloading",
                "verifying",
                "extracting",
                "validating",
            }:
                self._cancel.set()
        join = getattr(thread, "join", None)
        is_alive = getattr(thread, "is_alive", None)
        if callable(join) and (not callable(is_alive) or is_alive()):
            join(timeout=max(0.0, float(timeout)))


_DEFAULT_MANAGER: LlamaCppRuntimeManager | None = None
_DEFAULT_LOCK = threading.Lock()


def get_llama_cpp_runtime_manager() -> LlamaCppRuntimeManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = LlamaCppRuntimeManager()
        return _DEFAULT_MANAGER


__all__ = [
    "LLAMA_CPP_RUNTIME_ROOT",
    "LLAMA_CPP_RUNTIME_VERSION",
    "LlamaCppRuntimeError",
    "LlamaCppRuntimeManager",
    "RUNTIME_VARIANTS",
    "RuntimeAsset",
    "RuntimeInstallJob",
    "RuntimeVariant",
    "detect_recommended_variant",
    "find_managed_llama_cpp_executable",
    "get_llama_cpp_runtime_manager",
    "is_managed_llama_cpp_path",
    "safe_extract_zip",
]
