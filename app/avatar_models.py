"""Persistent embedded-VRM model storage for Project Akira.

The selected avatar is user-created runtime data. Source checkouts keep it under
``data/avatar`` while frozen builds place it in Project Akira's Local AppData
folder through :func:`app.paths.user_file_path`.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import threading

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.paths import user_file_path


DEFAULT_MODEL_PATH = user_file_path("data/avatar/model.vrm")
DEFAULT_METADATA_PATH = user_file_path("data/avatar/model.json")
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
_GLB_MAGIC = b"glTF"
_GLB_VERSION = 2
_GLB_JSON_CHUNK = 0x4E4F534A


class AvatarModelError(RuntimeError):
    """Base failure raised by the embedded avatar model store."""


class AvatarModelValidationError(AvatarModelError):
    """Raised when an uploaded file is not a supported VRM model."""


@dataclass(frozen=True, slots=True)
class AvatarModelInfo:
    """Non-sensitive metadata exposed to the local WebUI."""

    configured: bool
    filename: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    uploaded_at: str | None = None
    vrm_version: str | None = None
    model_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_filename(value: str) -> str:
    name = Path(str(value).strip()).name
    if not name:
        name = "avatar.vrm"
    if Path(name).suffix.casefold() != ".vrm":
        raise AvatarModelValidationError("Avatar files must use the .vrm extension.")
    return name


def _parse_vrm_glb(data: bytes) -> str:
    """Validate a binary glTF VRM and return its VRM specification label."""

    if len(data) < 20:
        raise AvatarModelValidationError("The selected VRM file is incomplete.")

    magic, glb_version, declared_length = struct.unpack_from("<4sII", data, 0)
    if magic != _GLB_MAGIC or glb_version != _GLB_VERSION:
        raise AvatarModelValidationError(
            "The selected file is not a supported binary VRM model."
        )
    if declared_length != len(data):
        raise AvatarModelValidationError(
            "The selected VRM file has an invalid binary length."
        )

    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    chunk_end = 20 + chunk_length
    if chunk_type != _GLB_JSON_CHUNK or chunk_end > len(data):
        raise AvatarModelValidationError(
            "The selected VRM file is missing its glTF metadata."
        )

    raw_json = data[20:chunk_end].rstrip(b"\x00 \t\r\n")
    try:
        document = json.loads(raw_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AvatarModelValidationError(
            "The selected VRM file contains invalid glTF metadata."
        ) from error

    if not isinstance(document, dict):
        raise AvatarModelValidationError("The selected VRM metadata is invalid.")

    asset = document.get("asset")
    if not isinstance(asset, dict) or str(asset.get("version", "")) != "2.0":
        raise AvatarModelValidationError(
            "Project Akira currently requires a glTF 2.0 VRM model."
        )

    extensions = document.get("extensions")
    extension_names = set()
    if isinstance(extensions, dict):
        extension_names.update(str(name) for name in extensions)

    extensions_used = document.get("extensionsUsed")
    if isinstance(extensions_used, list):
        extension_names.update(str(name) for name in extensions_used)

    if "VRMC_vrm" in extension_names:
        extension = extensions.get("VRMC_vrm") if isinstance(extensions, dict) else None
        if isinstance(extension, dict):
            spec_version = str(extension.get("specVersion", "")).strip()
            if spec_version:
                return spec_version
        return "1.0"

    if "VRM" in extension_names:
        return "0.x"

    raise AvatarModelValidationError(
        "The selected glTF file does not contain VRM avatar metadata."
    )


class AvatarModelStore:
    """Thread-safe atomic storage for one selected embedded VRM avatar."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_MODEL_PATH,
        metadata_path: str | Path = DEFAULT_METADATA_PATH,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.metadata_path = Path(metadata_path).expanduser().resolve()
        self.max_file_bytes = int(max_file_bytes)
        if self.max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be greater than zero")
        self._lock = threading.RLock()

    def status(self) -> AvatarModelInfo:
        with self._lock:
            if not self.model_path.is_file():
                return AvatarModelInfo(configured=False)

            data = self.model_path.read_bytes()
            vrm_version = _parse_vrm_glb(data)
            digest = hashlib.sha256(data).hexdigest()
            uploaded_at = datetime.fromtimestamp(
                self.model_path.stat().st_mtime,
                tz=timezone.utc,
            ).isoformat(timespec="seconds")
            filename = self.model_path.name

            try:
                metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                metadata = {}

            if isinstance(metadata, dict):
                candidate = str(metadata.get("filename", "")).strip()
                if candidate:
                    filename = Path(candidate).name
                candidate = str(metadata.get("uploaded_at", "")).strip()
                if candidate:
                    uploaded_at = candidate

            return AvatarModelInfo(
                configured=True,
                filename=filename,
                size_bytes=len(data),
                sha256=digest,
                uploaded_at=uploaded_at,
                vrm_version=vrm_version,
                model_url="/api/avatar/model/file",
            )

    def save(self, filename: str, data: bytes) -> AvatarModelInfo:
        safe_name = _safe_filename(filename)
        payload = bytes(data)
        if not payload:
            raise AvatarModelValidationError("The selected VRM file is empty.")
        if len(payload) > self.max_file_bytes:
            raise AvatarModelValidationError(
                f"VRM files must be {self.max_file_bytes // (1024 * 1024)} MiB or smaller."
            )

        vrm_version = _parse_vrm_glb(payload)
        uploaded_at = _utc_now()
        digest = hashlib.sha256(payload).hexdigest()
        metadata = {
            "schema_version": 1,
            "filename": safe_name,
            "size_bytes": len(payload),
            "sha256": digest,
            "uploaded_at": uploaded_at,
            "vrm_version": vrm_version,
        }

        with self._lock:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)

            model_temp = self.model_path.with_suffix(self.model_path.suffix + ".tmp")
            metadata_temp = self.metadata_path.with_suffix(
                self.metadata_path.suffix + ".tmp"
            )
            try:
                model_temp.write_bytes(payload)
                metadata_temp.write_text(
                    json.dumps(metadata, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(model_temp, self.model_path)
                os.replace(metadata_temp, self.metadata_path)
            finally:
                model_temp.unlink(missing_ok=True)
                metadata_temp.unlink(missing_ok=True)

        return AvatarModelInfo(
            configured=True,
            filename=safe_name,
            size_bytes=len(payload),
            sha256=digest,
            uploaded_at=uploaded_at,
            vrm_version=vrm_version,
            model_url="/api/avatar/model/file",
        )

    def delete(self) -> bool:
        with self._lock:
            existed = self.model_path.exists() or self.metadata_path.exists()
            self.model_path.unlink(missing_ok=True)
            self.metadata_path.unlink(missing_ok=True)
            return existed


_DEFAULT_STORE: AvatarModelStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def get_avatar_model_store() -> AvatarModelStore:
    """Return the process-wide embedded avatar model store."""

    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = AvatarModelStore()
        return _DEFAULT_STORE


__all__ = [
    "AvatarModelError",
    "AvatarModelInfo",
    "AvatarModelStore",
    "AvatarModelValidationError",
    "DEFAULT_MAX_FILE_BYTES",
    "get_avatar_model_store",
]
