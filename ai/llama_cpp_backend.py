"""Managed llama.cpp implementation of Project Akira's LLM backend.

This module owns a local ``llama-server`` process and exposes it through the
same OpenAI-compatible conversation implementation used by other backends.
Model downloading and hardware presets are handled by the Models-page tools.
Richer health reporting is added by a later v0.5 issue. Basic process cleanup
is included here so managed servers never survive a normal Project Akira shutdown.
"""

from __future__ import annotations

import atexit
import copy
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from app.paths import USER_DATA_ROOT, resource_path
from config.settings import AppSettings, get_settings

from .llm import LocalLLM
from .llm_backend import LLMBackendInfo, normalize_backend_id


class LlamaCppConfigurationError(ValueError):
    """Raised when managed llama.cpp settings cannot launch a server."""


class LlamaCppStartupError(RuntimeError):
    """Raised when ``llama-server`` exits or never becomes ready."""


@dataclass(frozen=True, slots=True)
class LlamaCppProcessConfig:
    executable: Path
    model_path: Path
    model_alias: str
    host: str = "127.0.0.1"
    port: int = 8080
    context_size: int = 8192
    gpu_layers: str = "auto"
    threads: int = -1
    parallel_slots: int = 1
    startup_timeout_seconds: float = 120.0
    reasoning_mode: str = "off"
    extra_args: tuple[str, ...] = ()
    log_file: Path = USER_DATA_ROOT / "logs" / "llama-server.log"

    @property
    def authority(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    @property
    def base_url(self) -> str:
        return f"http://{self.authority}/v1"

    @property
    def health_url(self) -> str:
        return f"http://{self.authority}/health"


PopenFactory = Callable[..., Any]
HealthProbe = Callable[[str], bool]
PortProbe = Callable[[str, int], bool]


_ACTIVE_PROCESS_LOCK = threading.RLock()
_ACTIVE_PROCESSES: set[Any] = set()


def _register_active_process(process: Any) -> None:
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES.add(process)


def _unregister_active_process(process: Any) -> None:
    with _ACTIVE_PROCESS_LOCK:
        _ACTIVE_PROCESSES.discard(process)


def stop_all_managed_llama_cpp_processes(timeout: float = 10.0) -> int:
    """Stop every llama-server process launched by this Python process."""

    with _ACTIVE_PROCESS_LOCK:
        processes = tuple(_ACTIVE_PROCESSES)
        _ACTIVE_PROCESSES.clear()

    for process in processes:
        try:
            process.stop(timeout=timeout)
        except Exception:
            # Shutdown must continue if a process has already disappeared.
            pass
    return len(processes)


def _default_health_probe(url: str) -> bool:
    try:
        with urlopen(url, timeout=1.0) as response:  # noqa: S310 - local URL only
            return int(getattr(response, "status", 0) or 0) == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def _default_port_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _loopback_host(value: object) -> str:
    host = str(value or "127.0.0.1").strip()
    if host.casefold() == "localhost":
        return "127.0.0.1"
    if host not in {"127.0.0.1", "::1"}:
        raise LlamaCppConfigurationError(
            "Managed llama.cpp must bind to localhost. Use 127.0.0.1, "
            "localhost, or ::1."
        )
    return host


def _normalized_path_text(value: object) -> str:
    """Trim whitespace and one matching pair of wrapping quotes."""

    raw = str(value or "").strip()
    if (
        len(raw) >= 2
        and raw[0] == raw[-1]
        and raw[0] in {"'", '\"'}
    ):
        raw = raw[1:-1].strip()
    return raw


def _resolve_executable(configured: object) -> Path:
    raw = _normalized_path_text(configured)
    if raw:
        candidate = Path(raw).expanduser().resolve()
        if not candidate.is_file():
            raise LlamaCppConfigurationError(
                f"llama-server executable was not found: {candidate}"
            )
        return candidate

    names = ("llama-server.exe", "llama-server")
    bundled = [resource_path("llama.cpp", name) for name in names]
    for candidate in bundled:
        if candidate.is_file():
            return candidate.resolve()

    for name in names:
        discovered = shutil.which(name)
        if discovered:
            return Path(discovered).resolve()

    raise LlamaCppConfigurationError(
        "No llama-server executable is configured. Set "
        "llm.llama_cpp_executable to llama-server.exe or install llama.cpp "
        "so llama-server is available on PATH."
    )


def llama_cpp_process_config(settings: AppSettings) -> LlamaCppProcessConfig:
    llm = settings.llm
    executable = _resolve_executable(llm.llama_cpp_executable)

    raw_model = _normalized_path_text(llm.llama_cpp_model_path)
    if not raw_model:
        raise LlamaCppConfigurationError(
            "No GGUF model is configured. Set llm.llama_cpp_model_path."
        )
    model_path = Path(raw_model).expanduser().resolve()
    if not model_path.is_file():
        raise LlamaCppConfigurationError(
            f"llama.cpp model was not found: {model_path}"
        )
    if model_path.suffix.casefold() != ".gguf":
        raise LlamaCppConfigurationError(
            "Managed llama.cpp requires a .gguf model file."
        )

    alias = str(llm.llama_cpp_model_alias or "").strip()
    if not alias:
        alias = model_path.stem

    host = _loopback_host(llm.llama_cpp_host)
    port = int(llm.llama_cpp_port)
    if not 1 <= port <= 65535:
        raise LlamaCppConfigurationError("llm.llama_cpp_port must be 1-65535.")

    context_size = int(llm.llama_cpp_context_size)
    if context_size < 256:
        raise LlamaCppConfigurationError(
            "llm.llama_cpp_context_size must be at least 256."
        )

    gpu_layers = str(llm.llama_cpp_gpu_layers or "auto").strip().casefold()
    if gpu_layers not in {"auto", "all"}:
        try:
            if int(gpu_layers) < 0:
                raise ValueError
        except ValueError as error:
            raise LlamaCppConfigurationError(
                "llm.llama_cpp_gpu_layers must be 'auto', 'all', or a "
                "non-negative integer."
            ) from error

    reasoning = str(llm.reasoning_mode or "auto").strip().casefold()
    if reasoning not in {"off", "on", "auto"}:
        raise LlamaCppConfigurationError(
            "Managed llama.cpp currently supports reasoning_mode values "
            "'off', 'on', or 'auto'."
        )

    extra_args = tuple(str(item).strip() for item in llm.llama_cpp_extra_args)
    extra_args = tuple(item for item in extra_args if item)
    controlled = {
        "-m",
        "--model",
        "-a",
        "--alias",
        "--host",
        "--port",
        "-c",
        "--ctx-size",
        "-ngl",
        "--gpu-layers",
        "--n-gpu-layers",
        "-t",
        "--threads",
        "-np",
        "--parallel",
        "-rea",
        "--reasoning",
        "--api-prefix",
        "--api-key",
        "--api-key-file",
        "--ssl-key-file",
        "--ssl-cert-file",
    }
    for argument in extra_args:
        flag = argument.split("=", 1)[0]
        if flag in controlled:
            raise LlamaCppConfigurationError(
                f"{flag} is managed by Project Akira and cannot be supplied "
                "through llm.llama_cpp_extra_args."
            )

    return LlamaCppProcessConfig(
        executable=executable,
        model_path=model_path,
        model_alias=alias,
        host=host,
        port=port,
        context_size=context_size,
        gpu_layers=gpu_layers,
        threads=int(llm.llama_cpp_threads),
        startup_timeout_seconds=float(llm.llama_cpp_startup_timeout_seconds),
        reasoning_mode=reasoning,
        extra_args=extra_args,
    )


class ManagedLlamaCppProcess:
    """Own one local ``llama-server`` process and wait for basic readiness."""

    def __init__(
        self,
        config: LlamaCppProcessConfig,
        *,
        popen_factory: PopenFactory = subprocess.Popen,
        health_probe: HealthProbe = _default_health_probe,
        port_probe: PortProbe = _default_port_probe,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._popen_factory = popen_factory
        self._health_probe = health_probe
        self._port_probe = port_probe
        self._monotonic = monotonic
        self._sleep = sleep
        self._process: Any | None = None
        self._log_handle: Any | None = None

    @property
    def base_url(self) -> str:
        return self.config.base_url

    @property
    def command(self) -> tuple[str, ...]:
        command = [
            str(self.config.executable),
            "--model",
            str(self.config.model_path),
            "--alias",
            self.config.model_alias,
            "--host",
            self.config.host,
            "--port",
            str(self.config.port),
            "--ctx-size",
            str(self.config.context_size),
            "--n-gpu-layers",
            self.config.gpu_layers,
            "--threads",
            str(self.config.threads),
            "--parallel",
            str(self.config.parallel_slots),
            "--reasoning",
            self.config.reasoning_mode,
            "--no-webui",
            "--log-colors",
            "off",
        ]
        command.extend(self.config.extra_args)
        return tuple(command)

    @property
    def pid(self) -> int | None:
        return getattr(self._process, "pid", None)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        if self.running:
            return
        if self._port_probe(self.config.host, self.config.port):
            raise LlamaCppStartupError(
                f"Port {self.config.port} is already in use. Stop the existing "
                "server or choose another llama.cpp port."
            )

        self.config.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self.config.log_file.open(
            "a",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )

        kwargs: dict[str, Any] = {
            "cwd": str(self.config.executable.parent),
            "stdin": subprocess.DEVNULL,
            "stdout": self._log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = int(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            kwargs["start_new_session"] = True

        try:
            self._process = self._popen_factory(list(self.command), **kwargs)
            self._wait_until_ready()
            _register_active_process(self)
        except Exception:
            self.stop()
            raise

    def _wait_until_ready(self) -> None:
        deadline = self._monotonic() + max(
            1.0,
            float(self.config.startup_timeout_seconds),
        )
        while self._monotonic() < deadline:
            exit_code = self._process.poll()
            if exit_code is not None:
                raise LlamaCppStartupError(
                    "llama-server exited before becoming ready "
                    f"(exit code {exit_code}). See {self.config.log_file}."
                )
            if self._health_probe(self.config.health_url):
                return
            self._sleep(0.25)

        raise LlamaCppStartupError(
            "llama-server did not become ready within "
            f"{self.config.startup_timeout_seconds:g} seconds. "
            f"See {self.config.log_file}."
        )

    def stop(self, timeout: float = 10.0) -> None:
        _unregister_active_process(self)
        process = self._process
        self._process = None
        try:
            if process is not None and process.poll() is None:
                if os.name == "nt":
                    # Kill the complete Windows process tree. Some llama.cpp
                    # builds release the listening port while a worker process
                    # remains alive and continues holding the loaded model.
                    subprocess.run(
                        [
                            "taskkill",
                            "/PID",
                            str(process.pid),
                            "/T",
                            "/F",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=int(
                            getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        ),
                    )

                    # Unit-test fakes and an occasional Windows race may still
                    # report the parent as alive after taskkill returns. Fall
                    # back to the normal Popen termination method without
                    # weakening the process-tree kill used by real servers.
                    if process.poll() is None:
                        terminate = getattr(process, "terminate", None)
                        if callable(terminate):
                            try:
                                terminate()
                            except OSError:
                                pass
                else:
                    process.terminate()

                try:
                    process.wait(timeout=max(0.1, float(timeout)))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
        finally:
            if self._log_handle is not None:
                self._log_handle.close()
                self._log_handle = None


class LlamaCppBackend(LocalLLM):
    """Stateful Project Akira backend backed by a managed llama-server."""

    backend_id = "llama_cpp"

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        client: Any | None = None,
        process: ManagedLlamaCppProcess | None = None,
    ) -> None:
        resolved = settings or get_settings()
        configured = normalize_backend_id(resolved.llm.backend)
        if configured != self.backend_id:
            raise ValueError(
                "LlamaCppBackend requires llm.backend='llama_cpp'; "
                f"received {configured!r}."
            )

        self.process = process or ManagedLlamaCppProcess(
            llama_cpp_process_config(resolved)
        )
        self.process.start()

        runtime_settings = copy.deepcopy(resolved)
        runtime_settings.llm.base_url = self.process.base_url
        runtime_settings.llm.api_key = "None"
        runtime_settings.llm.model = self.process.config.model_alias

        try:
            super().__init__(runtime_settings, client=client)
        except Exception:
            self.process.stop()
            raise

    @property
    def info(self) -> LLMBackendInfo:
        return LLMBackendInfo(
            backend_id=self.backend_id,
            display_name="Managed llama.cpp",
            managed=True,
        )

    @property
    def base_url(self) -> str:
        return self.process.base_url

    @property
    def server_pid(self) -> int | None:
        return self.process.pid

    def close(self) -> None:
        try:
            super().close()
        finally:
            self.process.stop()


atexit.register(stop_all_managed_llama_cpp_processes)
