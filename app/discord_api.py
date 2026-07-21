"""FastAPI endpoints and local WebUI registration for Discord settings."""

from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.discord_settings import (
    DiscordSettingsController,
    DiscordSettingsSnapshot,
    get_discord_settings_controller,
)


router = APIRouter(prefix="/api/discord", tags=["discord"])


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiscordSettingsResponse(StrictModel):
    enabled: bool
    token_configured: bool
    allowed_user_ids: list[str]
    adapter_state: str
    health: str
    running: bool
    connected: bool
    ready: bool
    reconnect_enabled: bool
    reconnect_count: int
    disconnect_count: int
    latency_ms: float | None
    uptime_seconds: float | None
    failed: bool


class DiscordSettingsUpdateRequest(StrictModel):
    enabled: bool = False
    allowed_user_ids: list[str] = Field(default_factory=list, max_length=100)
    bot_token: str | None = Field(default=None, max_length=2_000)


def _controller(request: Request) -> DiscordSettingsController:
    controller = getattr(request.app.state, "discord_controller", None)
    return controller or get_discord_settings_controller()


def _response(snapshot: DiscordSettingsSnapshot) -> DiscordSettingsResponse:
    return DiscordSettingsResponse(
        enabled=snapshot.enabled,
        token_configured=snapshot.token_configured,
        allowed_user_ids=list(snapshot.allowed_user_ids),
        adapter_state=snapshot.adapter_state,
        health=snapshot.health,
        running=snapshot.running,
        connected=snapshot.connected,
        ready=snapshot.ready,
        reconnect_enabled=snapshot.reconnect_enabled,
        reconnect_count=snapshot.reconnect_count,
        disconnect_count=snapshot.disconnect_count,
        latency_ms=snapshot.latency_ms,
        uptime_seconds=snapshot.uptime_seconds,
        failed=snapshot.failed,
    )


def _discord_error(error: Exception) -> HTTPException:
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Discord remote messaging could not complete that operation.",
    )


@router.get("/settings", response_model=DiscordSettingsResponse)
def read_discord_settings(request: Request) -> DiscordSettingsResponse:
    try:
        return _response(_controller(request).snapshot())
    except Exception as error:
        raise _discord_error(error) from error


@router.put("/settings", response_model=DiscordSettingsResponse)
def save_discord_settings(
    payload: DiscordSettingsUpdateRequest,
    request: Request,
) -> DiscordSettingsResponse:
    try:
        snapshot = _controller(request).configure(
            enabled=payload.enabled,
            allowed_user_ids=payload.allowed_user_ids,
            bot_token=payload.bot_token,
        )
    except Exception as error:
        raise _discord_error(error) from error
    return _response(snapshot)


@router.post("/start", response_model=DiscordSettingsResponse)
def start_discord(request: Request) -> DiscordSettingsResponse:
    try:
        return _response(_controller(request).start(persist=True))
    except Exception as error:
        raise _discord_error(error) from error


@router.post("/stop", response_model=DiscordSettingsResponse)
def stop_discord(request: Request) -> DiscordSettingsResponse:
    try:
        return _response(_controller(request).stop(persist=True))
    except Exception as error:
        raise _discord_error(error) from error


@router.delete("/token", response_model=DiscordSettingsResponse)
def delete_discord_token(request: Request) -> DiscordSettingsResponse:
    try:
        return _response(_controller(request).delete_token())
    except Exception as error:
        raise _discord_error(error) from error


def register_discord_routes(application: FastAPI, web_root: str | Path) -> None:
    """Register the Discord page, static assets, and local API endpoints."""

    root = Path(web_root).resolve()
    application.mount(
        "/static/discord",
        StaticFiles(directory=root),
        name="discord-static",
    )

    @application.get("/discord", include_in_schema=False)
    def discord_page() -> FileResponse:
        return FileResponse(
            root / "index.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    application.include_router(router)
