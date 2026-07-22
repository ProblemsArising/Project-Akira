"""Managed GGUF downloads for Project Akira's built-in llama.cpp backend.

Downloads run in background threads, write to resumable ``.part`` files, and
are atomically renamed only after GGUF validation and an optional SHA-256
check succeed. This module owns files only inside the Project Akira model
directory; callers may not supply arbitrary destination paths.
"""

from __future__ import annotations

import hashlib
import re
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from app.paths import USER_DATA_ROOT

MODEL_DOWNLOAD_ROOT = USER_DATA_ROOT / "models" / "llama.cpp"
_CHUNK_SIZE = 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_STEMS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_MAX_FILENAME_LENGTH = 240
_GGUF_MAGIC = b"GGUF"


class ModelDownloadError(RuntimeError):
    """Raised when a model download or local-model operation is invalid."""


@dataclass(frozen=True, slots=True)
class LocalGGUFModel:
    filename: str
    path: str
    size_bytes: int
    active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelDownloadJob:
    id: str
    url: str
    filename: str
    destination: str
    status: str = "queued"
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    sha256: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        total = self.total_bytes
        result["progress"] = (
            None
            if not total or total <= 0
            else min(1.0, self.downloaded_bytes / total)
        )
        return result


OpenUrl = Callable[..., Any]
ThreadFactory = Callable[..., Any]


def _safe_gguf_filename(value: object) -> str:
    filename = str(value or "").strip()
    if not filename:
        raise ModelDownloadError("A GGUF filename is required.")
    if filename in {".", ".."} or Path(filename).name != filename:
        raise ModelDownloadError("Model filename must not contain a path.")
    if len(filename) > _MAX_FILENAME_LENGTH:
        raise ModelDownloadError(
            f"Model filename must be {_MAX_FILENAME_LENGTH} characters or fewer."
        )
    if (
        filename.rstrip(" .") != filename
        or _WINDOWS_INVALID_FILENAME.search(filename)
    ):
        raise ModelDownloadError("Model filename contains an invalid character.")
    if filename.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_STEMS:
        raise ModelDownloadError("Model filename uses a reserved Windows device name.")
    if Path(filename).suffix.casefold() != ".gguf":
        raise ModelDownloadError(
            "Managed model downloads must use a .gguf filename."
        )
    return filename


def _filename_from_url(url: str) -> str:
    candidate = unquote(Path(urlsplit(url).path).name)
    return _safe_gguf_filename(candidate)


def _normalized_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise ModelDownloadError(
            "Download URL must include http:// or https:// and a host."
        )
    if parsed.username or parsed.password:
        raise ModelDownloadError(
            "Download URLs may not contain embedded credentials."
        )
    return url


def _normalized_sha256(value: object) -> str | None:
    checksum = str(value or "").strip().casefold()
    if not checksum:
        return None
    if not _SHA256_PATTERN.fullmatch(checksum):
        raise ModelDownloadError(
            "SHA-256 must contain exactly 64 hexadecimal characters."
        )
    return checksum


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        getcode = getattr(response, "getcode", None)
        status = getcode() if callable(getcode) else 200
    return int(status or 200)


def _header(headers: Mapping[str, Any] | Any, name: str) -> str:
    get = getattr(headers, "get", None)
    if not callable(get):
        return ""
    return str(get(name, "") or "").strip()


def _response_range_start(response: Any) -> int | None:
    content_range = _header(getattr(response, "headers", {}), "Content-Range")
    if not content_range.casefold().startswith("bytes ") or "-" not in content_range:
        return None
    start_text = content_range[6:].split("-", 1)[0].strip()
    return int(start_text) if start_text.isdigit() else None


def _response_total(response: Any, *, resumed_bytes: int) -> int | None:
    headers = getattr(response, "headers", {})
    content_range = _header(headers, "Content-Range")
    if "/" in content_range:
        total_text = content_range.rsplit("/", 1)[-1].strip()
        if total_text.isdigit():
            return int(total_text)

    content_length = _header(headers, "Content-Length")
    if content_length.isdigit():
        length = int(content_length)
        return resumed_bytes + length if _response_status(response) == 206 else length
    return None


class ModelDownloadManager:
    """Own background model downloads and the managed local GGUF directory."""

    def __init__(
        self,
        root: str | Path = MODEL_DOWNLOAD_ROOT,
        *,
        opener: OpenUrl = urlopen,
        thread_factory: ThreadFactory = threading.Thread,
        chunk_size: int = _CHUNK_SIZE,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self._opener = opener
        self._thread_factory = thread_factory
        self._chunk_size = max(4096, int(chunk_size))
        self._jobs: dict[str, ModelDownloadJob] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._threads: dict[str, Any] = {}
        self._lock = threading.RLock()

    def _target(self, filename: object) -> Path:
        safe = _safe_gguf_filename(filename)
        target = (self.root / safe).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as error:
            raise ModelDownloadError(
                "Model path escaped the managed directory."
            ) from error
        return target

    def start_download(
        self,
        *,
        url: object,
        filename: object = "",
        sha256: object = "",
    ) -> ModelDownloadJob:
        normalized_url = _normalized_url(url)
        resolved_filename = (
            _safe_gguf_filename(filename)
            if str(filename or "").strip()
            else _filename_from_url(normalized_url)
        )
        checksum = _normalized_sha256(sha256)
        target = self._target(resolved_filename)
        self.root.mkdir(parents=True, exist_ok=True)

        with self._lock:
            if target.exists():
                raise ModelDownloadError(
                    f"A downloaded model named {resolved_filename} already exists."
                )
            for existing in self._jobs.values():
                if (
                    Path(existing.destination) == target
                    and existing.status in {"queued", "downloading"}
                ):
                    raise ModelDownloadError(
                        f"{resolved_filename} is already being downloaded."
                    )

            job_id = uuid.uuid4().hex
            job = ModelDownloadJob(
                id=job_id,
                url=normalized_url,
                filename=resolved_filename,
                destination=str(target),
                sha256=checksum,
            )
            cancel_event = threading.Event()
            self._jobs[job_id] = job
            self._cancel_events[job_id] = cancel_event
            thread = self._thread_factory(
                target=self._run_download,
                args=(job_id,),
                name=f"akira-model-download-{job_id[:8]}",
                daemon=True,
            )
            self._threads[job_id] = thread
            thread.start()
            return ModelDownloadJob(**asdict(job))

    def _set_job(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)

    def _run_download(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            cancel_event = self._cancel_events[job_id]
            target = Path(job.destination)
            part = target.with_suffix(target.suffix + ".part")
            url = job.url
            checksum = job.sha256

        resumed_bytes = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "Project-Akira/0.5"}
        if resumed_bytes:
            headers["Range"] = f"bytes={resumed_bytes}-"

        self._set_job(
            job_id,
            status="downloading",
            downloaded_bytes=resumed_bytes,
            error=None,
        )

        try:
            request = Request(url, headers=headers)
            with self._opener(request, timeout=60.0) as response:
                status = _response_status(response)
                append = resumed_bytes > 0 and status == 206
                if append and _response_range_start(response) != resumed_bytes:
                    raise ModelDownloadError(
                        "The server returned an unexpected byte range. "
                        "The partial file was kept for retry."
                    )
                if resumed_bytes and not append:
                    resumed_bytes = 0
                    self._set_job(job_id, downloaded_bytes=0)

                total = _response_total(response, resumed_bytes=resumed_bytes)
                self._set_job(job_id, total_bytes=total)
                mode = "ab" if append else "wb"
                downloaded = resumed_bytes
                with part.open(mode) as output:
                    while True:
                        if cancel_event.is_set():
                            self._set_job(job_id, status="cancelled")
                            return
                        chunk = response.read(self._chunk_size)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        self._set_job(job_id, downloaded_bytes=downloaded)

            if cancel_event.is_set():
                self._set_job(job_id, status="cancelled")
                return

            if total is not None and downloaded != total:
                if downloaded > total:
                    part.unlink(missing_ok=True)
                    disposition = "The invalid partial file was removed."
                else:
                    disposition = "The partial file was kept for resume."
                raise ModelDownloadError(
                    f"Download ended at {downloaded} bytes; expected {total}. "
                    f"{disposition}"
                )

            with part.open("rb") as model_file:
                magic = model_file.read(len(_GGUF_MAGIC))
            if magic != _GGUF_MAGIC:
                part.unlink(missing_ok=True)
                raise ModelDownloadError(
                    "Downloaded content is not a valid GGUF model. "
                    "The invalid partial file was removed."
                )

            if checksum:
                digest = hashlib.sha256()
                with part.open("rb") as model_file:
                    for chunk in iter(lambda: model_file.read(self._chunk_size), b""):
                        digest.update(chunk)
                actual = digest.hexdigest()
                if actual != checksum:
                    part.unlink(missing_ok=True)
                    raise ModelDownloadError(
                        "SHA-256 verification failed. The invalid partial file "
                        "was removed."
                    )

            part.replace(target)
            size = target.stat().st_size
            self._set_job(
                job_id,
                status="completed",
                downloaded_bytes=size,
                total_bytes=size,
            )
        except Exception as error:
            self._set_job(
                job_id,
                status="failed",
                error=str(error) or type(error).__name__,
            )

    def cancel_download(self, job_id: object) -> ModelDownloadJob:
        normalized = str(job_id or "").strip()
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                raise ModelDownloadError("Download job was not found.")
            if job.status not in {"queued", "downloading"}:
                raise ModelDownloadError(
                    f"Download job cannot be cancelled while {job.status}."
                )
            self._cancel_events[normalized].set()
            if job.status == "queued":
                job.status = "cancelled"
            return ModelDownloadJob(**asdict(job))

    def get_job(self, job_id: object) -> ModelDownloadJob:
        normalized = str(job_id or "").strip()
        with self._lock:
            job = self._jobs.get(normalized)
            if job is None:
                raise ModelDownloadError("Download job was not found.")
            return ModelDownloadJob(**asdict(job))

    def list_jobs(self) -> list[ModelDownloadJob]:
        with self._lock:
            return [ModelDownloadJob(**asdict(job)) for job in self._jobs.values()]

    def list_models(
        self,
        *,
        active_path: str | Path | None = None,
    ) -> list[LocalGGUFModel]:
        active = Path(active_path).expanduser().resolve() if active_path else None
        if not self.root.exists():
            return []
        output: list[LocalGGUFModel] = []
        for path in sorted(
            self.root.glob("*.gguf"),
            key=lambda item: item.name.casefold(),
        ):
            if not path.is_file():
                continue
            resolved = path.resolve()
            output.append(
                LocalGGUFModel(
                    filename=path.name,
                    path=str(resolved),
                    size_bytes=path.stat().st_size,
                    active=active == resolved,
                )
            )
        return output

    def resolve_model(self, filename: object) -> Path:
        target = self._target(filename)
        if not target.is_file():
            raise ModelDownloadError(
                f"Downloaded model was not found: {target.name}"
            )
        return target

    def delete_model(
        self,
        filename: object,
        *,
        active_path: str | Path | None = None,
    ) -> None:
        target = self.resolve_model(filename)
        if active_path and target == Path(active_path).expanduser().resolve():
            raise ModelDownloadError(
                "The active llama.cpp model cannot be deleted. "
                "Select another model first."
            )
        with self._lock:
            for job in self._jobs.values():
                if (
                    Path(job.destination) == target
                    and job.status in {"queued", "downloading"}
                ):
                    raise ModelDownloadError(
                        "A model cannot be deleted while downloading."
                    )
        target.unlink()

    def snapshot(self, *, active_path: str | Path | None = None) -> dict[str, Any]:
        return {
            "directory": str(self.root),
            "jobs": [job.to_dict() for job in self.list_jobs()],
            "models": [
                model.to_dict()
                for model in self.list_models(active_path=active_path)
            ],
        }

    def shutdown(self, timeout: float = 2.0) -> None:
        with self._lock:
            events = list(self._cancel_events.values())
            threads = list(self._threads.values())
        for event in events:
            event.set()
        for thread in threads:
            join = getattr(thread, "join", None)
            is_alive = getattr(thread, "is_alive", None)
            if callable(join) and (not callable(is_alive) or is_alive()):
                join(timeout=max(0.0, float(timeout)))


_DEFAULT_MANAGER: ModelDownloadManager | None = None
_DEFAULT_LOCK = threading.Lock()


def get_model_download_manager() -> ModelDownloadManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = ModelDownloadManager()
        return _DEFAULT_MANAGER


__all__ = [
    "LocalGGUFModel",
    "MODEL_DOWNLOAD_ROOT",
    "ModelDownloadError",
    "ModelDownloadJob",
    "ModelDownloadManager",
    "get_model_download_manager",
]
