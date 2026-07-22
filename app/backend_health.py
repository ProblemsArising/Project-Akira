"""Health checks for Project Akira language-model backends.

The Models page uses these probes to distinguish a reachable server from a
backend that is actually ready for Akira's configured model.  Checks are
read-only: they never load a model, start managed llama.cpp, or initialize the
conversation service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

import httpx

from ai.llm_backend import backend_display_name, normalize_backend_id
from app.model_backends import (
    authorization_headers,
    compatibility_base_url,
    native_lm_studio_root,
    normalize_base_url,
)


HEALTHY = "healthy"
DEGRADED = "degraded"
OFFLINE = "offline"
IDLE = "idle"
MISCONFIGURED = "misconfigured"
HEALTH_STATUSES = {HEALTHY, DEGRADED, OFFLINE, IDLE, MISCONFIGURED}


@dataclass(frozen=True, slots=True)
class BackendHealth:
    """Normalized result returned by backend health probes."""

    backend: str
    display_name: str
    status: str
    base_url: str
    endpoint: str | None
    message: str
    checked_at: str
    reachable: bool = False
    latency_ms: float | None = None
    model: str | None = None
    model_available: bool | None = None
    model_loaded: bool | None = None
    managed: bool = False
    process_running: bool | None = None
    process_pid: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _JsonProbe:
    reachable: bool
    latency_ms: float | None
    payload: dict[str, Any] | None
    message: str | None
    status_code: int | None = None


ManagedSnapshotFactory = Callable[[], Sequence[Any]]
Clock = Callable[[], float]
NowFactory = Callable[[], datetime]


def _default_managed_snapshot() -> Sequence[Any]:
    from ai.llama_cpp_backend import managed_llama_cpp_snapshot

    return managed_llama_cpp_snapshot()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _checked_at(now: NowFactory) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: object) -> str:
    return str(value or "").strip()


def _snapshot_value(snapshot: Any, name: str, default: Any = None) -> Any:
    if isinstance(snapshot, Mapping):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


class BackendHealthChecker:
    """Run short, read-only readiness checks against one configured backend."""

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        timeout_seconds: float = 3.0,
        managed_snapshot: ManagedSnapshotFactory = _default_managed_snapshot,
        clock: Clock = perf_counter,
        now: NowFactory = _utc_now,
    ) -> None:
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.managed_snapshot = managed_snapshot
        self.clock = clock
        self.now = now

    def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self.http_client, "close", None)
        if callable(close):
            close()

    def check(
        self,
        *,
        settings: Any,
        backend: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> BackendHealth:
        backend_id = normalize_backend_id(backend or settings.llm.backend)
        if backend_id == "llama_cpp":
            return self._check_llama_cpp(settings)
        if backend_id == "lm_studio":
            return self._check_lm_studio(
                base_url=(settings.llm.base_url if base_url is None else base_url),
                api_key=api_key if api_key is not None else settings.llm.api_key,
                model=model if model is not None else settings.llm.model,
            )
        if backend_id == "openai_compatible":
            return self._check_openai_compatible(
                base_url=(settings.llm.base_url if base_url is None else base_url),
                api_key=api_key if api_key is not None else settings.llm.api_key,
                model=model if model is not None else settings.llm.model,
            )
        return self._result(
            backend=backend_id,
            status=MISCONFIGURED,
            base_url=_safe_text(base_url),
            endpoint=None,
            message=f"Unsupported LLM backend: {backend_id}.",
        )

    def _result(
        self,
        *,
        backend: str,
        status: str,
        base_url: str,
        endpoint: str | None,
        message: str,
        reachable: bool = False,
        latency_ms: float | None = None,
        model: str | None = None,
        model_available: bool | None = None,
        model_loaded: bool | None = None,
        managed: bool = False,
        process_running: bool | None = None,
        process_pid: int | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> BackendHealth:
        normalized_status = status if status in HEALTH_STATUSES else OFFLINE
        return BackendHealth(
            backend=backend,
            display_name=backend_display_name(backend),
            status=normalized_status,
            base_url=base_url,
            endpoint=endpoint,
            message=message,
            checked_at=_checked_at(self.now),
            reachable=reachable,
            latency_ms=latency_ms,
            model=model or None,
            model_available=model_available,
            model_loaded=model_loaded,
            managed=managed,
            process_running=process_running,
            process_pid=process_pid,
            details=dict(details or {}),
        )

    def _request_json(self, url: str, api_key: str | None) -> _JsonProbe:
        started = self.clock()
        try:
            response = self.http_client.request(
                "GET",
                url,
                headers=authorization_headers(api_key),
                timeout=self.timeout_seconds,
            )
            latency = round(max(0.0, (self.clock() - started) * 1000.0), 2)
        except Exception as error:
            return _JsonProbe(
                reachable=False,
                latency_ms=None,
                payload=None,
                message=str(error).strip() or error.__class__.__name__,
            )

        status_code = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status_code < 300:
            text = _safe_text(getattr(response, "text", ""))
            message = f"HTTP {status_code}"
            if text:
                message = f"{message}: {text[:300]}"
            return _JsonProbe(
                reachable=True,
                latency_ms=latency,
                payload=None,
                message=message,
                status_code=status_code,
            )

        try:
            payload = response.json()
        except Exception as error:
            return _JsonProbe(
                reachable=True,
                latency_ms=latency,
                payload=None,
                message=f"Invalid JSON: {error}",
                status_code=status_code,
            )
        if not isinstance(payload, dict):
            return _JsonProbe(
                reachable=True,
                latency_ms=latency,
                payload=None,
                message="The server returned an unexpected JSON value.",
                status_code=status_code,
            )
        return _JsonProbe(
            reachable=True,
            latency_ms=latency,
            payload=payload,
            message=None,
            status_code=status_code,
        )

    def _check_lm_studio(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str | None,
    ) -> BackendHealth:
        backend = "lm_studio"
        configured_model = _safe_text(model)
        try:
            normalized_url = normalize_base_url(base_url)
            endpoint = f"{native_lm_studio_root(normalized_url)}/api/v1/models"
        except Exception as error:
            return self._result(
                backend=backend,
                status=MISCONFIGURED,
                base_url=_safe_text(base_url),
                endpoint=None,
                model=configured_model,
                message=str(error),
            )

        probe = self._request_json(endpoint, api_key)
        if not probe.reachable:
            return self._result(
                backend=backend,
                status=OFFLINE,
                base_url=normalized_url,
                endpoint=endpoint,
                model=configured_model,
                message=f"LM Studio is not reachable. {probe.message or ''}".strip(),
            )
        if probe.payload is None:
            return self._result(
                backend=backend,
                status=DEGRADED,
                base_url=normalized_url,
                endpoint=endpoint,
                model=configured_model,
                reachable=True,
                latency_ms=probe.latency_ms,
                message=(
                    "LM Studio responded, but its model API was unusable. "
                    f"{probe.message or ''}"
                ).strip(),
                details={"http_status": probe.status_code},
            )

        model_rows = probe.payload.get("models", []) or []
        models: dict[str, Mapping[str, Any]] = {}
        for row in model_rows:
            if not isinstance(row, Mapping):
                continue
            model_id = _safe_text(row.get("key") or row.get("id"))
            if model_id:
                models[model_id] = row

        selected = models.get(configured_model) if configured_model else None
        model_available = selected is not None if configured_model else None
        model_loaded = None
        if selected is not None:
            model_loaded = bool(selected.get("loaded_instances") or [])

        details = {
            "model_count": len(models),
            "loaded_model_count": sum(
                1 for row in models.values() if row.get("loaded_instances")
            ),
            "http_status": probe.status_code,
        }
        if not configured_model:
            status = DEGRADED
            message = "LM Studio is reachable, but no model is selected for Akira."
        elif selected is None:
            status = DEGRADED
            message = f"LM Studio is reachable, but {configured_model} was not found."
        elif not model_loaded:
            status = DEGRADED
            message = f"{configured_model} is available but not loaded in LM Studio."
        else:
            status = HEALTHY
            message = f"LM Studio is ready with {configured_model} loaded."
        return self._result(
            backend=backend,
            status=status,
            base_url=normalized_url,
            endpoint=endpoint,
            model=configured_model,
            model_available=model_available,
            model_loaded=model_loaded,
            reachable=True,
            latency_ms=probe.latency_ms,
            message=message,
            details=details,
        )

    def _check_openai_compatible(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str | None,
    ) -> BackendHealth:
        backend = "openai_compatible"
        configured_model = _safe_text(model)
        try:
            normalized_url = normalize_base_url(base_url)
            endpoint = f"{compatibility_base_url(normalized_url)}/models"
        except Exception as error:
            return self._result(
                backend=backend,
                status=MISCONFIGURED,
                base_url=_safe_text(base_url),
                endpoint=None,
                model=configured_model,
                message=str(error),
            )

        probe = self._request_json(endpoint, api_key)
        if not probe.reachable:
            return self._result(
                backend=backend,
                status=OFFLINE,
                base_url=normalized_url,
                endpoint=endpoint,
                model=configured_model,
                message=(
                    "The OpenAI-compatible server is not reachable. "
                    f"{probe.message or ''}"
                ).strip(),
            )
        if probe.payload is None:
            return self._result(
                backend=backend,
                status=DEGRADED,
                base_url=normalized_url,
                endpoint=endpoint,
                model=configured_model,
                reachable=True,
                latency_ms=probe.latency_ms,
                message=(
                    "The server responded, but /v1/models was unusable. "
                    f"{probe.message or ''}"
                ).strip(),
                details={"http_status": probe.status_code},
            )

        model_ids = {
            _safe_text(row.get("id"))
            for row in (probe.payload.get("data", []) or [])
            if isinstance(row, Mapping) and _safe_text(row.get("id"))
        }
        model_available = configured_model in model_ids if configured_model else None
        if not configured_model:
            status = DEGRADED
            message = "The server is reachable, but no model is selected for Akira."
        elif not model_available:
            status = DEGRADED
            message = f"The server is reachable, but {configured_model} was not listed."
        else:
            status = HEALTHY
            message = f"The server is ready and lists {configured_model}."
        return self._result(
            backend=backend,
            status=status,
            base_url=normalized_url,
            endpoint=endpoint,
            model=configured_model,
            model_available=model_available,
            model_loaded=None,
            reachable=True,
            latency_ms=probe.latency_ms,
            message=message,
            details={
                "model_count": len(model_ids),
                "http_status": probe.status_code,
            },
        )

    def _check_llama_cpp(self, settings: Any) -> BackendHealth:
        backend = "llama_cpp"
        try:
            from ai.llama_cpp_backend import llama_cpp_process_config

            config = llama_cpp_process_config(settings)
        except (ValueError, OSError) as error:
            return self._result(
                backend=backend,
                status=MISCONFIGURED,
                base_url="",
                endpoint=None,
                message=str(error),
                managed=True,
                process_running=False,
            )
        except Exception as error:
            # Import/configuration failures are still configuration failures from
            # the Models page's perspective, but keep the message actionable.
            return self._result(
                backend=backend,
                status=MISCONFIGURED,
                base_url="",
                endpoint=None,
                message=str(error),
                managed=True,
                process_running=False,
            )

        snapshots = tuple(self.managed_snapshot())
        matching = [
            item
            for item in snapshots
            if _safe_text(_snapshot_value(item, "base_url")) == config.base_url
        ]
        running = next(
            (item for item in matching if bool(_snapshot_value(item, "running"))),
            None,
        )
        stopped = matching[0] if matching and running is None else None
        pid = _snapshot_value(running, "pid") if running is not None else None
        probe = self._request_json(config.health_url, None)
        common_details = {
            "executable": str(config.executable),
            "model_path": str(config.model_path),
            "log_file": str(config.log_file),
            "context_size": config.context_size,
            "gpu_layers": config.gpu_layers,
            "threads": config.threads,
            "parallel_slots": config.parallel_slots,
        }

        if probe.reachable and running is None:
            return self._result(
                backend=backend,
                status=DEGRADED,
                base_url=config.base_url,
                endpoint=config.health_url,
                model=config.model_alias,
                model_available=True,
                model_loaded=probe.payload is not None,
                reachable=True,
                latency_ms=probe.latency_ms,
                managed=True,
                process_running=False,
                message=(
                    "A server is responding on the managed llama.cpp port, "
                    "but Project Akira did not launch it. Stop the external "
                    "server or choose another port before Akira starts its model."
                ),
                details={
                    **common_details,
                    "external_server": True,
                    "http_status": probe.status_code,
                },
            )

        if probe.reachable and probe.payload is not None:
            return self._result(
                backend=backend,
                status=HEALTHY,
                base_url=config.base_url,
                endpoint=config.health_url,
                model=config.model_alias,
                model_available=True,
                model_loaded=True,
                reachable=True,
                latency_ms=probe.latency_ms,
                managed=True,
                process_running=True,
                process_pid=int(pid) if pid is not None else None,
                message=(
                    "Managed llama.cpp is running and ready with "
                    f"{config.model_alias}."
                ),
                details=common_details,
            )

        if running is not None:
            return self._result(
                backend=backend,
                status=DEGRADED if probe.reachable else OFFLINE,
                base_url=config.base_url,
                endpoint=config.health_url,
                model=config.model_alias,
                model_available=True,
                model_loaded=False,
                reachable=probe.reachable,
                latency_ms=probe.latency_ms,
                managed=True,
                process_running=True,
                process_pid=int(pid) if pid is not None else None,
                message=(
                    "The managed llama.cpp process exists, but its health endpoint "
                    f"is not ready. {probe.message or ''}"
                ).strip(),
                details=common_details,
            )

        if stopped is not None:
            exit_code = _snapshot_value(stopped, "exit_code")
            return self._result(
                backend=backend,
                status=OFFLINE,
                base_url=config.base_url,
                endpoint=config.health_url,
                model=config.model_alias,
                model_available=True,
                model_loaded=False,
                reachable=probe.reachable,
                latency_ms=probe.latency_ms,
                managed=True,
                process_running=False,
                process_pid=(
                    int(_snapshot_value(stopped, "pid"))
                    if _snapshot_value(stopped, "pid") is not None
                    else None
                ),
                message=(
                    "Managed llama.cpp exited unexpectedly"
                    + (f" with code {exit_code}" if exit_code is not None else "")
                    + f". Check {config.log_file}."
                ),
                details={**common_details, "exit_code": exit_code},
            )

        return self._result(
            backend=backend,
            status=IDLE,
            base_url=config.base_url,
            endpoint=config.health_url,
            model=config.model_alias,
            model_available=True,
            model_loaded=False,
            reachable=False,
            managed=True,
            process_running=False,
            message=(
                "Managed llama.cpp is configured and idle. Akira will start it "
                "automatically when the next message needs the model."
            ),
            details=common_details,
        )


def check_backend_health(
    *,
    settings: Any,
    backend: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    checker: BackendHealthChecker | None = None,
) -> BackendHealth:
    """Convenience wrapper that closes its short-lived HTTP client."""

    active = checker or BackendHealthChecker()
    try:
        return active.check(
            settings=settings,
            backend=backend,
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    finally:
        if checker is None:
            active.close()
