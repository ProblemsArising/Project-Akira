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
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from typing import Any, AsyncIterator, Callable, Mapping, Protocol

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
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
from app.model_backends import (
    ModelBackendClient,
    ModelBackendError,
    ModelDiscovery,
    ModelInfo,
    normalize_backend,
    normalize_base_url,
)
from app.paths import resource_path, user_file_path
from app.personalities import (
    PersonalityPreset,
    PersonalityStore,
    PersonalityStoreError,
    get_personality_store,
)
from app.startup import StartupRegistrationError, get_startup_manager
from config.settings import get_settings, reset_settings, update_settings
from config.settings_validation import SettingsValidationError, validate_settings_changes


CHAT_WEB_ROOT = resource_path("web", "chat")
SETTINGS_WEB_ROOT = resource_path("web", "settings")
HISTORY_WEB_ROOT = resource_path("web", "history")
PERSONALITIES_WEB_ROOT = resource_path("web", "personalities")
AUDIO_WEB_ROOT = resource_path("web", "audio")
MODELS_WEB_ROOT = resource_path("web", "models")
AVATAR_WEB_ROOT = resource_path("web", "avatar")
CALIBRATION_SAMPLE_PATH = user_file_path("data/calibration_sample.wav")


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

    def detach_conversation(self, conversation_id: int | None = None) -> bool: ...

    def activate_conversation(self, conversation_id: int) -> int: ...

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
PersonalityFactory = Callable[[], PersonalityStore]
CalibrationFactory = Callable[..., Any]
ModelBackendFactory = Callable[[], ModelBackendClient]


class BackendRuntime:
    """Own lazily-created services and the process-wide WebSocket event hub."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory | None = None,
        history_factory: HistoryFactory = get_history_store,
        personality_factory: PersonalityFactory = get_personality_store,
        calibration_factory: CalibrationFactory | None = None,
        model_backend_factory: ModelBackendFactory | None = None,
        event_hub: EventHub | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._history_factory = history_factory
        self._personality_factory = personality_factory
        self._calibration_factory = calibration_factory
        self._model_backend_factory = model_backend_factory
        self._service: ConversationServiceLike | None = None
        self._history: ChatHistoryStore | None = None
        self._personalities: PersonalityStore | None = None
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

    @property
    def personalities(self) -> PersonalityStore:
        with self._lock:
            if self._personalities is None:
                self._personalities = self._personality_factory()
            return self._personalities

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

    def detach_deleted_conversation(self, conversation_id: int) -> bool:
        """Detach a deleted active conversation without loading heavy services."""

        with self._lock:
            service = self._service
        if service is None or service.current_conversation_id != int(conversation_id):
            return False

        detach = getattr(service, "detach_conversation", None)
        if not callable(detach):
            return False
        return bool(detach(conversation_id))

    def create_audio_calibration(
        self,
        *,
        input_device: int | str | None | object = ...,
    ) -> Any:
        """Create a short-lived microphone calibration session lazily."""

        if self._calibration_factory is not None:
            return self._calibration_factory(input_device=input_device)

        from audio.calibration import AudioCalibrationSession

        return AudioCalibrationSession(
            input_device=input_device,
            output_file=CALIBRATION_SAMPLE_PATH,
        )

    def create_model_backend_client(self) -> ModelBackendClient:
        """Create a short-lived model discovery/management client."""

        if self._model_backend_factory is not None:
            return self._model_backend_factory()
        return ModelBackendClient()

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


class RenameConversationRequest(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class ActivateConversationResponse(StrictModel):
    conversation_id: int
    title: str
    restored_turns: int


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


class SettingsUpdateRequest(StrictModel):
    changes: dict[str, Any]


class SettingsMutationResponse(StrictModel):
    settings: dict[str, Any]
    changed_sections: list[str]
    restart_required: bool


class StartupStatusResponse(StrictModel):
    supported: bool
    enabled: bool
    registered_command: str | None
    expected_command: str
    matches_current: bool


class ModelConfigResponse(StrictModel):
    backend: str
    base_url: str
    api_key: str
    model: str
    reasoning_mode: str
    service_loaded: bool


class ModelConnectionRequest(StrictModel):
    backend: str
    base_url: str = Field(min_length=1, max_length=2_000)
    api_key: str = Field(default="", max_length=10_000)


class ModelInfoResponse(StrictModel):
    id: str
    display_name: str
    model_type: str
    publisher: str
    architecture: str | None
    quantization: str | None
    params: str | None
    size_bytes: int | None
    max_context_length: int | None
    loaded: bool
    instance_ids: list[str]
    reasoning_options: list[str]
    default_reasoning: str | None
    vision: bool
    tool_use: bool
    description: str | None


class ModelDiscoveryResponse(StrictModel):
    backend: str
    base_url: str
    api_url: str
    models: list[ModelInfoResponse]


class ModelSelectionRequest(ModelConnectionRequest):
    model: str = Field(min_length=1, max_length=2_000)
    reasoning_mode: str = Field(default="off", max_length=20)


class ModelSelectionResponse(StrictModel):
    backend: str
    base_url: str
    model: str
    reasoning_mode: str
    context_reset: bool


class ModelLoadRequest(ModelConnectionRequest):
    model: str = Field(min_length=1, max_length=2_000)
    context_length: int | None = Field(default=None, ge=1, le=2_000_000)


class ModelUnloadRequest(ModelConnectionRequest):
    instance_id: str = Field(min_length=1, max_length=2_000)


class ModelActionResponse(StrictModel):
    status: str
    instance_id: str | None = None
    details: dict[str, Any]


class AudioDeviceResponse(StrictModel):
    index: int
    name: str
    host_api: str
    selection_key: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    is_default_input: bool
    is_default_output: bool


class AudioDeviceListResponse(StrictModel):
    configured_input: int | str | None
    devices: list[AudioDeviceResponse]


class PersonalityPresetResponse(StrictModel):
    id: str
    name: str
    description: str
    prompt: str
    built_in: bool
    created_at: str
    updated_at: str


class PersonalityListResponse(StrictModel):
    active_id: str
    legacy_prompt_override: bool
    presets: list[PersonalityPresetResponse]


class CreatePersonalityRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    prompt: str = Field(min_length=20, max_length=20_000)
    activate: bool = False


class UpdatePersonalityRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=300)
    prompt: str | None = Field(default=None, min_length=20, max_length=20_000)


class DuplicatePersonalityRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    activate: bool = False


class PersonalityMutationResponse(StrictModel):
    preset: PersonalityPresetResponse
    active_id: str
    applied_live: bool


def _model_info_response(model: ModelInfo) -> ModelInfoResponse:
    return ModelInfoResponse(**model.to_dict())


def _model_discovery_response(discovery: ModelDiscovery) -> ModelDiscoveryResponse:
    return ModelDiscoveryResponse(
        backend=discovery.backend,
        base_url=discovery.base_url,
        api_url=discovery.api_url,
        models=[_model_info_response(model) for model in discovery.models],
    )


def _close_model_client(client: ModelBackendClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _preset_response(preset: PersonalityPreset) -> PersonalityPresetResponse:
    return PersonalityPresetResponse(**preset.to_dict())


def _refresh_personality_prompt(prompt: str) -> bool:
    # Import lazily so the personality page never initializes the LLM stack.
    from ai.llm import refresh_personality_prompt

    return refresh_personality_prompt(prompt)


def _activate_personality(
    preset: PersonalityPreset,
    runtime: BackendRuntime,
) -> tuple[str, bool]:
    updated = update_settings(
        {
            "personality": {
                "preset": preset.id,
                "prompt": "",
            }
        }
    )
    applied_live = _refresh_personality_prompt(preset.prompt)
    runtime.events.publish(
        "personality.changed",
        {
            "preset_id": preset.id,
            "name": preset.name,
            "applied_live": applied_live,
        },
    )
    return updated.personality.preset, applied_live


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
    application.mount(
        "/static/settings",
        StaticFiles(directory=SETTINGS_WEB_ROOT),
        name="settings-static",
    )
    application.mount(
        "/static/history",
        StaticFiles(directory=HISTORY_WEB_ROOT),
        name="history-static",
    )
    application.mount(
        "/static/personalities",
        StaticFiles(directory=PERSONALITIES_WEB_ROOT),
        name="personalities-static",
    )
    application.mount(
        "/static/audio",
        StaticFiles(directory=AUDIO_WEB_ROOT),
        name="audio-static",
    )
    application.mount(
        "/static/models",
        StaticFiles(directory=MODELS_WEB_ROOT),
        name="models-static",
    )
    application.mount(
        "/static/avatar",
        StaticFiles(directory=AVATAR_WEB_ROOT),
        name="avatar-static",
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

    @application.get("/settings", include_in_schema=False)
    def settings_page() -> FileResponse:
        """Serve the local Project Akira settings interface."""

        return FileResponse(
            SETTINGS_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/history", include_in_schema=False)
    def history_page() -> FileResponse:
        """Serve the local Project Akira conversation-history interface."""

        return FileResponse(
            HISTORY_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/personalities", include_in_schema=False)
    def personalities_page() -> FileResponse:
        """Serve the local Project Akira personality editor."""

        return FileResponse(
            PERSONALITIES_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/audio", include_in_schema=False)
    def audio_calibration_page() -> FileResponse:
        """Serve the local microphone calibration interface."""

        return FileResponse(
            AUDIO_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/models", include_in_schema=False)
    def models_page() -> FileResponse:
        """Serve the local model/backend selector."""

        return FileResponse(
            MODELS_WEB_ROOT / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @application.get("/avatar", include_in_schema=False)
    def avatar_page() -> FileResponse:
        """Serve the dedicated desktop avatar-stage shell."""

        return FileResponse(
            AVATAR_WEB_ROOT / "index.html",
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
        query: str | None = Query(default=None, max_length=500),
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> list[ConversationSummaryResponse]:
        try:
            conversations = active_runtime.history.list_conversations(
                limit=limit,
                offset=offset,
                query=query,
            )
        except Exception as error:
            raise _service_error(error) from error
        return [ConversationSummaryResponse(**asdict(item)) for item in conversations]

    @application.get(
        "/api/conversations/{conversation_id}/summary",
        response_model=ConversationSummaryResponse,
        tags=["history"],
    )
    def conversation_summary(
        conversation_id: int,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ConversationSummaryResponse:
        try:
            summary = active_runtime.history.get_conversation(conversation_id)
        except Exception as error:
            raise _service_error(error) from error
        if summary is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return ConversationSummaryResponse(**asdict(summary))

    @application.post(
        "/api/conversations/{conversation_id}/activate",
        response_model=ActivateConversationResponse,
        tags=["history"],
    )
    def activate_conversation(
        conversation_id: int,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ActivateConversationResponse:
        try:
            summary = active_runtime.history.get_conversation(conversation_id)
            if summary is None:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation not found.",
                )
            turns = active_runtime.history.get_turns(conversation_id)
            active_runtime.service.activate_conversation(conversation_id)
        except HTTPException:
            raise
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise _service_error(error) from error

        return ActivateConversationResponse(
            conversation_id=conversation_id,
            title=summary.title,
            restored_turns=len(turns),
        )

    @application.patch(
        "/api/conversations/{conversation_id}",
        response_model=ConversationSummaryResponse,
        tags=["history"],
    )
    def rename_conversation(
        conversation_id: int,
        payload: RenameConversationRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ConversationSummaryResponse:
        try:
            active_runtime.history.rename_conversation(conversation_id, payload.title)
            summary = active_runtime.history.get_conversation(conversation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            raise _service_error(error) from error

        if summary is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        active_runtime.events.publish(
            "history.conversation_renamed",
            {"conversation_id": conversation_id, "title": summary.title},
        )
        return ConversationSummaryResponse(**asdict(summary))

    @application.delete(
        "/api/conversations/{conversation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        tags=["history"],
    )
    def delete_conversation(
        conversation_id: int,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> Response:
        try:
            deleted = active_runtime.history.delete_conversation(conversation_id)
        except Exception as error:
            raise _service_error(error) from error
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found.")

        detached = active_runtime.detach_deleted_conversation(conversation_id)
        active_runtime.events.publish(
            "history.conversation_deleted",
            {"conversation_id": conversation_id, "detached_active": detached},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
        "/api/models/config",
        response_model=ModelConfigResponse,
        tags=["models"],
    )
    def model_configuration(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ModelConfigResponse:
        llm = get_settings().llm
        return ModelConfigResponse(
            backend=llm.backend,
            base_url=llm.base_url,
            api_key="" if str(llm.api_key or "").casefold() == "none" else llm.api_key,
            model=llm.model,
            reasoning_mode=llm.reasoning_mode,
            service_loaded=active_runtime.service_loaded,
        )

    @application.post(
        "/api/models/discover",
        response_model=ModelDiscoveryResponse,
        tags=["models"],
    )
    def discover_models(
        payload: ModelConnectionRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ModelDiscoveryResponse:
        client = active_runtime.create_model_backend_client()
        try:
            discovery = client.discover(
                backend=payload.backend,
                base_url=payload.base_url,
                api_key=payload.api_key,
            )
        except ModelBackendError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        finally:
            _close_model_client(client)
        return _model_discovery_response(discovery)

    @application.post(
        "/api/models/select",
        response_model=ModelSelectionResponse,
        tags=["models"],
    )
    def select_model(
        payload: ModelSelectionRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ModelSelectionResponse:
        try:
            backend = normalize_backend(payload.backend)
            base_url = normalize_base_url(payload.base_url)
        except ModelBackendError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        model_id = payload.model.strip()
        if not model_id:
            raise HTTPException(status_code=422, detail="Model ID cannot be blank.")

        reasoning_mode = str(payload.reasoning_mode or "auto").strip().casefold()
        if backend == "openai_compatible":
            reasoning_mode = "auto"

        changes = {
            "llm": {
                "backend": backend,
                "base_url": base_url,
                "api_key": payload.api_key.strip() or "None",
                "model": model_id,
                "reasoning_mode": reasoning_mode,
            }
        }
        current = get_settings().to_dict()
        try:
            normalized = validate_settings_changes(changes, current)
        except SettingsValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        updated = update_settings(normalized)
        from ai.llm import invalidate_default_llm

        context_reset = invalidate_default_llm()
        active_runtime.events.publish(
            "model.changed",
            {
                "backend": updated.llm.backend,
                "base_url": updated.llm.base_url,
                "model": updated.llm.model,
                "reasoning_mode": updated.llm.reasoning_mode,
                "context_reset": context_reset,
            },
        )
        return ModelSelectionResponse(
            backend=updated.llm.backend,
            base_url=updated.llm.base_url,
            model=updated.llm.model,
            reasoning_mode=updated.llm.reasoning_mode,
            context_reset=context_reset,
        )

    @application.post(
        "/api/models/load",
        response_model=ModelActionResponse,
        tags=["models"],
    )
    def load_model(
        payload: ModelLoadRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ModelActionResponse:
        try:
            if normalize_backend(payload.backend) != "lm_studio":
                raise ModelBackendError("Model loading is available only for LM Studio.")
            client = active_runtime.create_model_backend_client()
            try:
                result = client.load_lm_studio_model(
                    base_url=payload.base_url,
                    api_key=payload.api_key,
                    model=payload.model,
                    context_length=payload.context_length,
                )
            finally:
                _close_model_client(client)
        except ModelBackendError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        active_runtime.events.publish(
            "model.loaded",
            {
                "model": payload.model,
                "instance_id": result.get("instance_id"),
            },
        )
        return ModelActionResponse(
            status=str(result.get("status") or "loaded"),
            instance_id=(
                str(result.get("instance_id"))
                if result.get("instance_id") is not None
                else None
            ),
            details=dict(result),
        )

    @application.post(
        "/api/models/unload",
        response_model=ModelActionResponse,
        tags=["models"],
    )
    def unload_model(
        payload: ModelUnloadRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> ModelActionResponse:
        try:
            if normalize_backend(payload.backend) != "lm_studio":
                raise ModelBackendError("Model unloading is available only for LM Studio.")
            client = active_runtime.create_model_backend_client()
            try:
                result = client.unload_lm_studio_model(
                    base_url=payload.base_url,
                    api_key=payload.api_key,
                    instance_id=payload.instance_id,
                )
            finally:
                _close_model_client(client)
        except ModelBackendError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        active_runtime.events.publish(
            "model.unloaded",
            {"instance_id": payload.instance_id},
        )
        return ModelActionResponse(
            status="unloaded",
            instance_id=payload.instance_id,
            details=dict(result),
        )

    @application.get(
        "/api/personalities",
        response_model=PersonalityListResponse,
        tags=["personalities"],
    )
    def list_personalities(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> PersonalityListResponse:
        settings = get_settings()
        return PersonalityListResponse(
            active_id=settings.personality.preset or "gamer",
            legacy_prompt_override=bool(str(settings.personality.prompt or "").strip()),
            presets=[
                _preset_response(preset)
                for preset in active_runtime.personalities.list_presets()
            ],
        )

    @application.post(
        "/api/personalities",
        response_model=PersonalityMutationResponse,
        tags=["personalities"],
        status_code=201,
    )
    def create_personality(
        payload: CreatePersonalityRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> PersonalityMutationResponse:
        try:
            preset = active_runtime.personalities.create_preset(
                name=payload.name,
                description=payload.description,
                prompt=payload.prompt,
            )
        except PersonalityStoreError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        active_id = get_settings().personality.preset or "gamer"
        applied_live = False
        if payload.activate:
            active_id, applied_live = _activate_personality(preset, active_runtime)
        active_runtime.events.publish(
            "personality.created",
            {"preset_id": preset.id, "name": preset.name},
        )
        return PersonalityMutationResponse(
            preset=_preset_response(preset),
            active_id=active_id,
            applied_live=applied_live,
        )

    @application.patch(
        "/api/personalities/{preset_id}",
        response_model=PersonalityMutationResponse,
        tags=["personalities"],
    )
    def update_personality(
        preset_id: str,
        payload: UpdatePersonalityRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> PersonalityMutationResponse:
        try:
            preset = active_runtime.personalities.update_preset(
                preset_id,
                name=payload.name,
                description=payload.description,
                prompt=payload.prompt,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Personality not found.") from error
        except PersonalityStoreError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        settings = get_settings()
        active_id = settings.personality.preset or "gamer"
        applied_live = False
        if active_id == preset.id and not str(settings.personality.prompt or "").strip():
            applied_live = _refresh_personality_prompt(preset.prompt)
            active_runtime.events.publish(
                "personality.changed",
                {
                    "preset_id": preset.id,
                    "name": preset.name,
                    "applied_live": applied_live,
                    "edited": True,
                },
            )
        active_runtime.events.publish(
            "personality.updated",
            {"preset_id": preset.id, "name": preset.name},
        )
        return PersonalityMutationResponse(
            preset=_preset_response(preset),
            active_id=active_id,
            applied_live=applied_live,
        )

    @application.post(
        "/api/personalities/{preset_id}/duplicate",
        response_model=PersonalityMutationResponse,
        tags=["personalities"],
        status_code=201,
    )
    def duplicate_personality(
        preset_id: str,
        payload: DuplicatePersonalityRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> PersonalityMutationResponse:
        try:
            preset = active_runtime.personalities.duplicate_preset(
                preset_id,
                name=payload.name,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Personality not found.") from error
        except PersonalityStoreError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        active_id = get_settings().personality.preset or "gamer"
        applied_live = False
        if payload.activate:
            active_id, applied_live = _activate_personality(preset, active_runtime)
        active_runtime.events.publish(
            "personality.created",
            {
                "preset_id": preset.id,
                "name": preset.name,
                "duplicated_from": preset_id,
            },
        )
        return PersonalityMutationResponse(
            preset=_preset_response(preset),
            active_id=active_id,
            applied_live=applied_live,
        )

    @application.post(
        "/api/personalities/{preset_id}/activate",
        response_model=PersonalityMutationResponse,
        tags=["personalities"],
    )
    def activate_personality(
        preset_id: str,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> PersonalityMutationResponse:
        preset = active_runtime.personalities.get_preset(preset_id)
        if preset is None:
            raise HTTPException(status_code=404, detail="Personality not found.")
        active_id, applied_live = _activate_personality(preset, active_runtime)
        return PersonalityMutationResponse(
            preset=_preset_response(preset),
            active_id=active_id,
            applied_live=applied_live,
        )

    @application.delete(
        "/api/personalities/{preset_id}",
        status_code=204,
        tags=["personalities"],
    )
    def delete_personality(
        preset_id: str,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> Response:
        active_id = get_settings().personality.preset or "gamer"
        if preset_id == active_id:
            raise HTTPException(
                status_code=409,
                detail="Activate another personality before deleting this one.",
            )
        try:
            active_runtime.personalities.delete_preset(preset_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Personality not found.") from error
        except PersonalityStoreError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        active_runtime.events.publish(
            "personality.deleted",
            {"preset_id": preset_id},
        )
        return Response(status_code=204)

    @application.get(
        "/api/audio/devices",
        response_model=AudioDeviceListResponse,
        tags=["audio"],
    )
    def audio_input_devices() -> AudioDeviceListResponse:
        """Return a concise list of microphones for the calibration page."""

        try:
            from audio.devices import (
                list_audio_devices,
                preferred_audio_devices,
                resolve_audio_device,
            )

            all_devices = list_audio_devices()
            devices = preferred_audio_devices(devices=all_devices, kind="input")
            configured = get_settings().audio.input_device
            if configured is not None:
                selected = resolve_audio_device(configured, "input", devices=all_devices)
                if selected is not None and all(
                    item.index != selected.index for item in devices
                ):
                    devices.append(selected)
            devices.sort(key=lambda item: (item.name.casefold(), item.index))
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=f"Could not query microphone devices: {error}",
            ) from error

        return AudioDeviceListResponse(
            configured_input=get_settings().audio.input_device,
            devices=[AudioDeviceResponse(**device.to_dict()) for device in devices],
        )

    @application.get(
        "/api/audio/calibration/sample",
        tags=["audio"],
        include_in_schema=False,
    )
    def calibration_sample() -> FileResponse:
        if not CALIBRATION_SAMPLE_PATH.exists():
            raise HTTPException(status_code=404, detail="No calibration sample exists yet.")
        return FileResponse(
            CALIBRATION_SAMPLE_PATH,
            media_type="audio/wav",
            headers={"Cache-Control": "no-store"},
        )

    @application.websocket("/api/audio/calibration")
    async def websocket_audio_calibration(websocket: WebSocket) -> None:
        """Stream live microphone levels during one short calibration pass."""

        await websocket.accept()
        active_session: Any | None = None
        try:
            settings_snapshot = get_settings().audio
            await websocket.send_json(
                {
                    "type": "calibration.ready",
                    "data": {
                        "configured_input": settings_snapshot.input_device,
                        "duration_seconds": 7.0,
                        "calibration_seconds": max(
                            0.6,
                            float(settings_snapshot.calibration_seconds),
                        ),
                    },
                }
            )

            while True:
                payload = await websocket.receive_json()
                command = payload.get("type") if isinstance(payload, dict) else None

                if command == "ping":
                    await websocket.send_json(
                        {"type": "calibration.pong", "data": payload.get("data")}
                    )
                    continue

                if command != "start":
                    await websocket.send_json(
                        {
                            "type": "calibration.error",
                            "data": {"error": "Supported commands: start, ping."},
                        }
                    )
                    continue

                if backend_runtime.status()["is_listening"]:
                    await websocket.send_json(
                        {
                            "type": "calibration.error",
                            "data": {
                                "error": (
                                    "Stop normal microphone listening before running "
                                    "audio calibration."
                                )
                            },
                        }
                    )
                    continue

                duration = min(max(float(payload.get("duration_seconds", 7.0)), 2.0), 20.0)
                quiet_seconds = min(
                    max(float(payload.get("calibration_seconds", 1.5)), 0.3),
                    max(0.3, duration - 0.75),
                )
                input_device = payload.get(
                    "input_device",
                    get_settings().audio.input_device,
                )
                active_session = backend_runtime.create_audio_calibration(
                    input_device=input_device,
                )

                await websocket.send_json(
                    {
                        "type": "calibration.started",
                        "data": {
                            "duration_seconds": duration,
                            "calibration_seconds": quiet_seconds,
                        },
                    }
                )

                loop = asyncio.get_running_loop()
                levels: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)

                def publish_level(level: dict[str, Any]) -> None:
                    def put_latest() -> None:
                        if levels.full():
                            with suppress(asyncio.QueueEmpty):
                                levels.get_nowait()
                        levels.put_nowait(dict(level))

                    loop.call_soon_threadsafe(put_latest)

                task = asyncio.create_task(
                    asyncio.to_thread(
                        active_session.run,
                        duration_seconds=duration,
                        calibration_seconds=quiet_seconds,
                        on_level=publish_level,
                    )
                )

                while not task.done() or not levels.empty():
                    try:
                        level = await asyncio.wait_for(levels.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                    await websocket.send_json(
                        {"type": "calibration.level", "data": level}
                    )

                try:
                    result = await task
                except Exception as error:
                    await websocket.send_json(
                        {
                            "type": "calibration.error",
                            "data": {"error": str(error)},
                        }
                    )
                else:
                    result_data = result.to_dict()
                    result_data["sample_url"] = (
                        "/api/audio/calibration/sample?cache="
                        f"{int(asyncio.get_running_loop().time() * 1000)}"
                    )
                    await websocket.send_json(
                        {"type": "calibration.completed", "data": result_data}
                    )
                finally:
                    active_session = None
        except WebSocketDisconnect:
            pass
        finally:
            if active_session is not None:
                request_stop = getattr(active_session, "request_stop", None)
                if callable(request_stop):
                    request_stop()

    @application.get(
        "/api/startup",
        response_model=StartupStatusResponse,
        tags=["settings"],
    )
    def startup_status() -> StartupStatusResponse:
        """Return the real Windows startup-registration state."""

        try:
            state = get_startup_manager().status()
        except StartupRegistrationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return StartupStatusResponse(**state.to_dict())

    @application.get(
        "/api/settings",
        response_model=SettingsResponse,
        tags=["settings"],
    )
    def settings() -> SettingsResponse:
        return SettingsResponse(settings=get_settings().to_dict())

    @application.patch(
        "/api/settings",
        response_model=SettingsMutationResponse,
        tags=["settings"],
    )
    def save_runtime_settings(
        payload: SettingsUpdateRequest,
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> SettingsMutationResponse:
        current = get_settings().to_dict()
        try:
            normalized = validate_settings_changes(payload.changes, current)
        except SettingsValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

        startup_general = normalized.get("general", {})
        startup_requested = "launch_on_startup" in startup_general
        previous_startup = bool(
            current.get("general", {}).get("launch_on_startup", False)
        )
        startup_state = None

        if startup_requested:
            try:
                startup_state = get_startup_manager().set_enabled(
                    bool(startup_general["launch_on_startup"])
                )
            except StartupRegistrationError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error

        try:
            updated = update_settings(normalized)
        except Exception:
            if startup_requested:
                try:
                    get_startup_manager().set_enabled(previous_startup)
                except StartupRegistrationError:
                    pass
            raise

        changed_sections = sorted(normalized)
        restart_required = active_runtime.service_loaded
        event_data: dict[str, Any] = {
            "changed_sections": changed_sections,
            "restart_required": restart_required,
        }
        if startup_state is not None:
            event_data["startup"] = startup_state.to_dict()
            active_runtime.events.publish(
                "startup.changed",
                startup_state.to_dict(),
            )
        active_runtime.events.publish("settings.updated", event_data)
        return SettingsMutationResponse(
            settings=updated.to_dict(),
            changed_sections=changed_sections,
            restart_required=restart_required,
        )

    @application.post(
        "/api/settings/reset",
        response_model=SettingsMutationResponse,
        tags=["settings"],
    )
    def restore_default_settings(
        active_runtime: BackendRuntime = Depends(_get_runtime),
    ) -> SettingsMutationResponse:
        previous_startup = bool(get_settings().general.launch_on_startup)
        try:
            startup_state = get_startup_manager().set_enabled(False)
        except StartupRegistrationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        try:
            defaults = reset_settings()
        except Exception:
            if previous_startup:
                try:
                    get_startup_manager().set_enabled(True)
                except StartupRegistrationError:
                    pass
            raise

        changed_sections = [
            "general",
            "llm",
            "personality",
            "stt",
            "audio",
            "tts",
            "avatar",
            "memory",
        ]
        restart_required = active_runtime.service_loaded
        active_runtime.events.publish(
            "startup.changed",
            startup_state.to_dict(),
        )
        active_runtime.events.publish(
            "settings.updated",
            {
                "changed_sections": changed_sections,
                "restart_required": restart_required,
                "reset": True,
                "startup": startup_state.to_dict(),
            },
        )
        return SettingsMutationResponse(
            settings=defaults.to_dict(),
            changed_sections=changed_sections,
            restart_required=restart_required,
        )

    return application


app = create_app()
