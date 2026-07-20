"""Model discovery and lightweight model management for Project Akira.

The WebUI uses this module to inspect an LM Studio server or another
OpenAI-compatible server without loading Project Akira's conversation service.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import httpx


SUPPORTED_BACKENDS = {"lm_studio", "openai_compatible"}


class ModelBackendError(RuntimeError):
    """Raised when a configured model server cannot complete a request."""


@dataclass(frozen=True)
class ModelInfo:
    """Normalized model information returned to the WebUI."""

    id: str
    display_name: str
    model_type: str = "llm"
    publisher: str = ""
    architecture: str | None = None
    quantization: str | None = None
    params: str | None = None
    size_bytes: int | None = None
    max_context_length: int | None = None
    loaded: bool = False
    instance_ids: list[str] = field(default_factory=list)
    reasoning_options: list[str] = field(default_factory=list)
    default_reasoning: str | None = None
    vision: bool = False
    tool_use: bool = False
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelDiscovery:
    backend: str
    base_url: str
    api_url: str
    models: list[ModelInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "base_url": self.base_url,
            "api_url": self.api_url,
            "models": [model.to_dict() for model in self.models],
        }


def normalize_backend(value: str) -> str:
    backend = str(value or "").strip().casefold()
    if backend not in SUPPORTED_BACKENDS:
        choices = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise ModelBackendError(f"Unsupported LLM backend. Choose one of: {choices}.")
    return backend


def normalize_base_url(value: str) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelBackendError(
            "Server URL must include http:// or https:// and a host."
        )
    return base_url


def _server_root(base_url: str) -> str:
    """Remove a compatibility API suffix and return only the server root."""

    parsed = urlsplit(normalize_base_url(base_url))
    path = parsed.path.rstrip("/")
    for suffix in ("/api/v1", "/v1"):
        if path.casefold().endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def compatibility_base_url(base_url: str) -> str:
    root = _server_root(base_url)
    return f"{root}/v1"


def native_lm_studio_root(base_url: str) -> str:
    return _server_root(base_url)


def authorization_headers(api_key: str | None) -> dict[str, str]:
    key = str(api_key or "").strip()
    if not key or key.casefold() == "none":
        return {"Accept": "application/json"}
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
    }


class ModelBackendClient:
    """Discover and manage models through HTTP APIs."""

    def __init__(
        self,
        *,
        http_client: Any | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self.http_client, "close", None)
        if callable(close):
            close()

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        api_key: str | None = None,
        payload: Mapping[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        headers = authorization_headers(api_key)
        if payload is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = self.http_client.request(
                method,
                url,
                headers=headers,
                json=dict(payload) if payload is not None else None,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
        except Exception as error:
            response = getattr(error, "response", None)
            detail = str(getattr(response, "text", "") or "").strip()
            if not detail:
                detail = str(error).strip()
            suffix = f" Details: {detail}" if detail else ""
            raise ModelBackendError(
                f"Could not connect to the model server at {url}.{suffix}"
            ) from error

        try:
            result = response.json()
        except Exception as error:
            raise ModelBackendError(
                f"The model server at {url} returned invalid JSON."
            ) from error
        if not isinstance(result, dict):
            raise ModelBackendError(
                f"The model server at {url} returned an unexpected response."
            )
        return result

    def discover(
        self,
        *,
        backend: str,
        base_url: str,
        api_key: str | None = None,
    ) -> ModelDiscovery:
        backend = normalize_backend(backend)
        base_url = normalize_base_url(base_url)

        if backend == "lm_studio":
            api_url = f"{native_lm_studio_root(base_url)}/api/v1/models"
            payload = self._request_json("GET", api_url, api_key=api_key)
            models = self._parse_lm_studio_models(payload)
        else:
            api_url = f"{compatibility_base_url(base_url)}/models"
            payload = self._request_json("GET", api_url, api_key=api_key)
            models = self._parse_openai_models(payload)

        models.sort(key=lambda item: (not item.loaded, item.display_name.casefold()))
        return ModelDiscovery(
            backend=backend,
            base_url=base_url,
            api_url=api_url,
            models=models,
        )

    def load_lm_studio_model(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        context_length: int | None = None,
    ) -> dict[str, Any]:
        model_id = str(model or "").strip()
        if not model_id:
            raise ModelBackendError("A model must be selected before loading it.")
        payload: dict[str, Any] = {"model": model_id, "echo_load_config": True}
        if context_length is not None:
            payload["context_length"] = max(1, int(context_length))
        url = f"{native_lm_studio_root(base_url)}/api/v1/models/load"
        return self._request_json(
            "POST",
            url,
            api_key=api_key,
            payload=payload,
            timeout_seconds=300.0,
        )

    def unload_lm_studio_model(
        self,
        *,
        base_url: str,
        api_key: str | None,
        instance_id: str,
    ) -> dict[str, Any]:
        normalized = str(instance_id or "").strip()
        if not normalized:
            raise ModelBackendError("A loaded model instance must be selected.")
        url = f"{native_lm_studio_root(base_url)}/api/v1/models/unload"
        return self._request_json(
            "POST",
            url,
            api_key=api_key,
            payload={"instance_id": normalized},
            timeout_seconds=60.0,
        )

    @staticmethod
    def _parse_lm_studio_models(payload: Mapping[str, Any]) -> list[ModelInfo]:
        output: list[ModelInfo] = []
        for raw in payload.get("models", []) or []:
            if not isinstance(raw, Mapping):
                continue
            model_type = str(raw.get("type") or "llm")
            if model_type != "llm":
                continue
            model_id = str(raw.get("key") or raw.get("id") or "").strip()
            if not model_id:
                continue

            quantization_raw = raw.get("quantization")
            quantization = None
            if isinstance(quantization_raw, Mapping):
                quantization = str(quantization_raw.get("name") or "").strip() or None
            elif quantization_raw:
                quantization = str(quantization_raw)

            loaded_instances = raw.get("loaded_instances") or []
            instance_ids = [
                str(item.get("id"))
                for item in loaded_instances
                if isinstance(item, Mapping) and item.get("id")
            ]

            capabilities = raw.get("capabilities") or {}
            if not isinstance(capabilities, Mapping):
                capabilities = {}
            reasoning = capabilities.get("reasoning") or {}
            if not isinstance(reasoning, Mapping):
                reasoning = {}

            output.append(
                ModelInfo(
                    id=model_id,
                    display_name=str(raw.get("display_name") or model_id),
                    model_type=model_type,
                    publisher=str(raw.get("publisher") or ""),
                    architecture=(
                        str(raw.get("architecture"))
                        if raw.get("architecture") is not None
                        else None
                    ),
                    quantization=quantization,
                    params=(
                        str(raw.get("params_string"))
                        if raw.get("params_string") is not None
                        else None
                    ),
                    size_bytes=(
                        int(raw.get("size_bytes"))
                        if isinstance(raw.get("size_bytes"), (int, float))
                        else None
                    ),
                    max_context_length=(
                        int(raw.get("max_context_length"))
                        if isinstance(raw.get("max_context_length"), (int, float))
                        else None
                    ),
                    loaded=bool(instance_ids),
                    instance_ids=instance_ids,
                    reasoning_options=[
                        str(item)
                        for item in reasoning.get("allowed_options", []) or []
                    ],
                    default_reasoning=(
                        str(reasoning.get("default"))
                        if reasoning.get("default") is not None
                        else None
                    ),
                    vision=bool(capabilities.get("vision", False)),
                    tool_use=bool(capabilities.get("trained_for_tool_use", False)),
                    description=(
                        str(raw.get("description"))
                        if raw.get("description") is not None
                        else None
                    ),
                )
            )
        return output

    @staticmethod
    def _parse_openai_models(payload: Mapping[str, Any]) -> list[ModelInfo]:
        output: list[ModelInfo] = []
        for raw in payload.get("data", []) or []:
            if not isinstance(raw, Mapping):
                continue
            model_id = str(raw.get("id") or "").strip()
            if not model_id:
                continue
            output.append(
                ModelInfo(
                    id=model_id,
                    display_name=model_id,
                    publisher=str(raw.get("owned_by") or ""),
                    loaded=False,
                )
            )
        return output
