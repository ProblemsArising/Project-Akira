"""FastAPI backend for Project Akira's future WebUI.

The HTTP layer is deliberately thin. Conversation behavior remains inside
``ConversationService`` while this module translates HTTP requests into service
calls and JSON responses. Heavy audio, Whisper, TTS, and avatar dependencies are
loaded lazily only when an endpoint first needs the conversation service.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any, AsyncIterator, Callable, Mapping, Protocol

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.conversation import ConversationResult, ConversationService
from app.events import EventHub, EventStreamClosed, EventSubscription
from app.history import ChatHistoryStore, get_history_store
from config.settings import get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHAT_WEB_ROOT = PROJECT_ROOT / "web" / "chat"


class ConversationServiceLike(Protocol):
    """Subset of ``ConversationService`` used by the backend."""

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
    """Own lazily-created services and the process-wide WebSocket event hub."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory | None = None,
        history_factory: HistoryFactory = get_history_store,
        event_hub: EventHub | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._history_factory = history_factory
        self._service: ConversationServiceLike | None = None
        self._history: ChatHistoryStore | None = None
        self._events = event_hub or EventHub()
        self._lock = threading.RLock()

    @property
    def service_loaded(self) -> bool:
        with self._lock:
            return self._service is not None

    @property
    def events(self) -> EventHub:
        return self._events

    @property
    def history(self) -> ChatHistoryStore:
        with self._lock:
            if self._history is None:
                self._history = self._history_factory()
            return self._history

    def _publish_service_event(
        self,
        event_type: str,
        data: Mapping[str, Any],
    ) -> None:
        payload = dict(data)
        with self._lock:
            service = self._service
        if service is not None:
            payload.setdefault("conversation_id", service.current_conversation_id)
        self._events.publish(event_type, payload)

    @property
    def service(self) -> ConversationServiceLike:
        with self._lock:
            if self._service is None:
                if self._service_factory is not None:
                    service = self._service_factory()
                else:
                    service = ConversationService.from_default_components(
                        on_reply=None,
                        history_store=self.history,
                    )

                self._service = service
                add_listener = getattr(service, "add_event_listener", None)
                if callable(add_listener):
                    add_listener(self._publish_service_event)
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
                "event_clients": self._events.subscriber_count,
            }

    def shutdown(self) -> None:
        with self._lock:
            service = self._service

        self._events.publish("system.shutdown", self.status())
        if service is not None:
            service.request_stop()
        self._events.close()


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
    event_clients: int


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


async def _send_websocket_events(
    websocket: WebSocket,
    subscription: EventSubscription,
) -> None:
    while True:
        event = await subscription.receive()
        await websocket.send_json(event)


async def _receive_websocket_commands(
    websocket: WebSocket,
    subscription: EventSubscription,
    runtime: BackendRuntime,
) -> None:
    """Handle tiny connection-level commands; app actions remain HTTP calls."""

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return

        raw_text = message.get("text")
        if raw_text is None:
            await subscription.send_direct(
                runtime.events.create_event(
                    "connection.error",
                    {"error": "Only JSON text messages are supported."},
                )
            )
            continue

        try:
            payload = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            await subscription.send_direct(
                runtime.events.create_event(
                    "connection.error",
                    {"error": "WebSocket command must be valid JSON."},
                )
            )
            continue

        command = payload.get("type") if isinstance(payload, dict) else None
        if command == "ping":
            await subscription.send_direct(
                runtime.events.create_event(
                    "connection.pong",
                    {"echo": payload.get("data")},
                )
            )
        elif command == "status":
            await subscription.send_direct(
                runtime.events.create_event(
                    "status.snapshot",
                    runtime.status(),
                )
            )
        else:
            await subscription.send_direct(
                runtime.events.create_event(
                    "connection.error",
                    {
                        "error": "Unknown WebSocket command.",
                        "supported": ["ping", "status"],
                    },
                )
            )


def create_app(runtime: BackendRuntime | None = None) -> FastAPI:
    """Create the FastAPI application.

    Supplying a runtime is mainly useful for tests and future desktop-shell
    embedding. The production server uses one process-wide runtime.
    """

    backend_runtime = runtime or BackendRuntime()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runtime = backend_runtime
        try:
            yield
        finally:
            backend_runtime.shutdown()

    application = FastAPI(
        title="Project Akira API",
        version="0.2.0-alpha",
        description=(
            "Local HTTP and WebSocket backend for Project Akira's chat, "
            "listening controls, settings, conversation history, and live state."
        ),
        lifespan=lifespan,
    )

    application.mount(
        "/static/chat",
        StaticFiles(directory=CHAT_WEB_ROOT),
        name="chat-static",
    )

    @application.get("/", include_in_schema=False)
    @application.get("/chat", include_in_schema=False)
    def chat_page() -> FileResponse:
        """Serve the local Project Akira chat interface."""

        return FileResponse(
            CHAT_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/api/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", application="Project Akira")

    @application.get("/api/status", response_model=StatusResponse, tags=["system"])
    def runtime_status(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> StatusResponse:
        return StatusResponse(**active_runtime.status())

    @application.websocket("/api/events")
    async def websocket_events(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            subscription = backend_runtime.events.subscribe()
        except EventStreamClosed:
            await websocket.close(code=1012, reason="Event system is shutting down")
            return

        await subscription.send_direct(
            backend_runtime.events.create_event(
                "connection.ready",
                {
                    "status": backend_runtime.status(),
                    "commands": ["ping", "status"],
                },
            )
        )

        sender = asyncio.create_task(
            _send_websocket_events(websocket, subscription),
            name="ProjectAkiraWebSocketSender",
        )
        receiver = asyncio.create_task(
            _receive_websocket_commands(websocket, subscription, backend_runtime),
            name="ProjectAkiraWebSocketReceiver",
        )

        try:
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                with suppress(
                    asyncio.CancelledError,
                    EventStreamClosed,
                    WebSocketDisconnect,
                    RuntimeError,
                ):
                    task.result()
        finally:
            subscription.close()

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
