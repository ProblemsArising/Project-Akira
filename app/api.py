"""FastAPI backend for Project Akira's future WebUI.

The HTTP layer is deliberately thin. Conversation behavior remains inside
``ConversationService`` while this module translates HTTP requests into service
calls and JSON responses. Heavy audio, Whisper, TTS, and avatar dependencies are
loaded lazily only when an endpoint first needs the conversation service.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Callable, Iterator, Protocol

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.conversation import ConversationResult, ConversationService
from app.history import ChatHistoryStore, get_history_store
from config.settings import get_settings


class ConversationServiceLike(Protocol):
    """Subset of ``ConversationService`` used by the HTTP backend."""

    @property
    def current_conversation_id(self) -> int | None: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def is_listening(self) -> bool: ...

    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: str = "text",
        audio_file: str | None = None,
    ) -> ConversationResult | None: ...

    def start_new_conversation(self, title: str | None = None) -> int | None: ...

    def start_listening(self) -> bool: ...

    def stop_listening(
        self,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool: ...

    def request_stop(self) -> None: ...


ServiceFactory = Callable[[], ConversationServiceLike]
HistoryFactory = Callable[[], ChatHistoryStore]


class BackendRuntime:
    """Own lazily-created backend services shared by all HTTP requests."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory | None = None,
        history_factory: HistoryFactory = get_history_store,
    ) -> None:
        self._service_factory = service_factory
        self._history_factory = history_factory
        self._service: ConversationServiceLike | None = None
        self._history: ChatHistoryStore | None = None
        self._lock = threading.RLock()

    @property
    def service_loaded(self) -> bool:
        with self._lock:
            return self._service is not None

    @property
    def history(self) -> ChatHistoryStore:
        with self._lock:
            if self._history is None:
                self._history = self._history_factory()
            return self._history

    @property
    def service(self) -> ConversationServiceLike:
        with self._lock:
            if self._service is None:
                if self._service_factory is not None:
                    self._service = self._service_factory()
                else:
                    self._service = ConversationService.from_default_components(
                        on_reply=None,
                        history_store=self.history,
                    )
            return self._service

    def status(self) -> dict[str, Any]:
        with self._lock:
            service = self._service
            return {
                "service_loaded": service is not None,
                "is_running": False if service is None else service.is_running,
                "is_listening": False if service is None else service.is_listening,
                "conversation_id": (
                    None if service is None else service.current_conversation_id
                ),
            }

    def shutdown(self) -> None:
        with self._lock:
            service = self._service
        if service is not None:
            service.request_stop()


class StrictModel(BaseModel):
    """Base request/response model that rejects accidental unknown fields."""

    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    status: str
    application: str


class StatusResponse(StrictModel):
    service_loaded: bool
    is_running: bool
    is_listening: bool
    conversation_id: int | None


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=50_000)
    speak: bool = True


class ChatResponse(StrictModel):
    user_text: str
    reply: str
    source: str
    audio_file: str | None
    spoken: bool
    conversation_id: int | None


class NewConversationRequest(StrictModel):
    title: str | None = Field(default=None, max_length=200)


class NewConversationResponse(StrictModel):
    conversation_id: int | None


class ListeningResponse(StrictModel):
    changed: bool
    is_listening: bool
    is_running: bool


class ConversationSummaryResponse(StrictModel):
    id: int
    title: str
    created_at: str
    updated_at: str
    turn_count: int
    last_message: str


class HistoryTurnResponse(StrictModel):
    id: int
    conversation_id: int
    user_text: str
    assistant_text: str
    source: str
    audio_file: str | None
    spoken: bool
    created_at: str


class SettingsResponse(StrictModel):
    settings: dict[str, Any]


def _get_runtime(request: Request) -> BackendRuntime:
    return request.app.state.runtime


def _service_error(error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Project Akira service is unavailable: {error}",
    )


def create_app(runtime: BackendRuntime | None = None) -> FastAPI:
    """Create the FastAPI application.

    Supplying a runtime is mainly useful for tests and future desktop-shell
    embedding. The production server uses one process-wide runtime.
    """

    backend_runtime = runtime or BackendRuntime()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> Iterator[None]:
        application.state.runtime = backend_runtime
        try:
            yield
        finally:
            backend_runtime.shutdown()

    application = FastAPI(
        title="Project Akira API",
        version="0.2.0-alpha",
        description=(
            "Local HTTP backend for Project Akira's chat, listening controls, "
            "settings, and conversation history."
        ),
        lifespan=lifespan,
    )

    @application.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", application="Project Akira")

    @application.get("/api/status", response_model=StatusResponse, tags=["system"])
    def runtime_status(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> StatusResponse:
        return StatusResponse(**active_runtime.status())

    @application.post("/api/chat", response_model=ChatResponse, tags=["chat"])
    def chat(
        payload: ChatRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ChatResponse:
        try:
            result = active_runtime.service.process_text(
                payload.message,
                speak=payload.speak,
                source="text",
            )
        except Exception as error:
            raise _service_error(error) from error

        if result is None:
            raise HTTPException(
                status_code=422,
                detail="The message did not produce a usable reply.",
            )

        return ChatResponse(
            **asdict(result),
            conversation_id=active_runtime.service.current_conversation_id,
        )

    @application.post(
        "/api/conversations",
        response_model=NewConversationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["history"],
    )
    def new_conversation(
        payload: NewConversationRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> NewConversationResponse:
        try:
            conversation_id = active_runtime.service.start_new_conversation(
                payload.title
            )
        except Exception as error:
            raise _service_error(error) from error
        return NewConversationResponse(conversation_id=conversation_id)

    @application.get(
        "/api/conversations",
        response_model=list[ConversationSummaryResponse],
        tags=["history"],
    )
    def list_conversations(
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> list[ConversationSummaryResponse]:
        try:
            conversations = active_runtime.history.list_conversations(
                limit=limit,
                offset=offset,
            )
        except Exception as error:
            raise _service_error(error) from error
        return [ConversationSummaryResponse(**asdict(item)) for item in conversations]

    @application.get(
        "/api/conversations/{conversation_id}",
        response_model=list[HistoryTurnResponse],
        tags=["history"],
    )
    def conversation_turns(
        conversation_id: int,
        limit: int | None = Query(default=None, ge=1, le=5_000),
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> list[HistoryTurnResponse]:
        try:
            turns = active_runtime.history.get_turns(conversation_id, limit=limit)
        except Exception as error:
            raise _service_error(error) from error
        return [HistoryTurnResponse(**asdict(item)) for item in turns]

    @application.post(
        "/api/listening/start",
        response_model=ListeningResponse,
        tags=["listening"],
    )
    def start_listening(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ListeningResponse:
        try:
            service = active_runtime.service
            changed = service.start_listening()
        except Exception as error:
            raise _service_error(error) from error
        return ListeningResponse(
            changed=changed,
            is_listening=service.is_listening,
            is_running=service.is_running,
        )

    @application.post(
        "/api/listening/stop",
        response_model=ListeningResponse,
        tags=["listening"],
    )
    def stop_listening(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ListeningResponse:
        try:
            service = active_runtime.service
            changed = service.stop_listening(wait=False)
        except Exception as error:
            raise _service_error(error) from error
        return ListeningResponse(
            changed=changed,
            is_listening=service.is_listening,
            is_running=service.is_running,
        )

    @application.get(
        "/api/settings",
        response_model=SettingsResponse,
        tags=["settings"],
    )
    def settings() -> SettingsResponse:
        return SettingsResponse(settings=get_settings().to_dict())

    return application


app = create_app()
