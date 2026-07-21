"""Persistent configuration and runtime control for Discord messaging."""

from __future__ import annotations

import json
import os
import threading

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Protocol

from app.discord_access import (
    DiscordAccessPolicy,
    get_discord_access_policy,
    normalize_discord_user_id,
)
from app.discord_adapter import DiscordAdapterService, get_discord_adapter_service
from app.discord_token_store import DiscordTokenStore, get_discord_token_store


DISCORD_SETTINGS_FILENAME = "discord_settings.json"
DISCORD_SETTINGS_ENV_VAR = "PROJECT_AKIRA_DISCORD_SETTINGS_FILE"
DISCORD_SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DiscordSettings:
    enabled: bool = False
    allowed_user_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISCORD_SETTINGS_SCHEMA_VERSION,
            "enabled": self.enabled,
            # Snowflakes exceed JavaScript's safe integer range. Persist and
            # expose them as decimal strings so the WebUI never rounds an ID.
            "allowed_user_ids": [str(value) for value in self.allowed_user_ids],
        }


@dataclass(frozen=True, slots=True)
class DiscordSettingsSnapshot:
    enabled: bool
    token_configured: bool
    allowed_user_ids: tuple[str, ...]
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


class AdapterLike(Protocol):
    @property
    def is_running(self) -> bool: ...

    def start(self, token: str, *, timeout: float = 5.0) -> bool: ...

    def stop(self, *, timeout: float = 5.0) -> bool: ...

    def snapshot(self): ...


class TokenStoreLike(Protocol):
    def save_token(self, token: str) -> None: ...

    def load_token(self) -> str | None: ...

    def delete_token(self) -> bool: ...

    def status(self): ...


class DiscordSettingsStore:
    """Atomic JSON storage for non-secret Discord configuration."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = self._resolve_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_path(path: str | Path | None) -> Path:
        if path is not None:
            return Path(path).expanduser().resolve()

        environment_path = os.getenv(DISCORD_SETTINGS_ENV_VAR)
        if environment_path:
            return Path(environment_path).expanduser().resolve()

        from app.history import get_history_store

        history_path = Path(get_history_store().path).expanduser().resolve()
        return history_path.with_name(DISCORD_SETTINGS_FILENAME)

    def load(self) -> DiscordSettings:
        with self._lock:
            if not self.path.exists():
                return DiscordSettings()

            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                return self._decode(raw)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                self._backup_invalid_file()
                return DiscordSettings()

    def save(self, settings: DiscordSettings) -> DiscordSettings:
        normalized = DiscordSettings(
            enabled=bool(settings.enabled),
            allowed_user_ids=tuple(
                sorted(
                    {
                        normalize_discord_user_id(value)
                        for value in settings.allowed_user_ids
                    }
                )
            ),
        )
        payload = json.dumps(normalized.to_dict(), indent=2) + "\n"

        with self._lock:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            try:
                temporary.write_text(payload, encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return normalized

    @staticmethod
    def _decode(raw: object) -> DiscordSettings:
        if not isinstance(raw, Mapping):
            raise ValueError("Discord settings must be an object")

        values = raw.get("allowed_user_ids", [])
        if not isinstance(values, list):
            raise ValueError("allowed_user_ids must be a list")

        normalized = tuple(
            sorted({normalize_discord_user_id(value) for value in values})
        )
        return DiscordSettings(
            enabled=bool(raw.get("enabled", False)),
            allowed_user_ids=normalized,
        )

    def _backup_invalid_file(self) -> None:
        if not self.path.exists():
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(
            f"{self.path.stem}.broken-{timestamp}{self.path.suffix}"
        )
        try:
            self.path.replace(backup)
        except OSError:
            pass


class DiscordSettingsController:
    """Coordinate persisted settings, keyring credentials, and the adapter."""

    def __init__(
        self,
        *,
        settings_store: DiscordSettingsStore | None = None,
        token_store: TokenStoreLike | None = None,
        access_policy: DiscordAccessPolicy | None = None,
        adapter: AdapterLike | None = None,
    ) -> None:
        self._settings_store = settings_store or DiscordSettingsStore()
        self._token_store = token_store or get_discord_token_store()
        self._access_policy = access_policy or get_discord_access_policy()
        self._adapter = adapter or get_discord_adapter_service()
        self._lock = threading.RLock()
        self._apply_policy(self._settings_store.load())

    def snapshot(self) -> DiscordSettingsSnapshot:
        with self._lock:
            settings = self._settings_store.load()
            self._apply_policy(settings)
            token_configured = self._token_store.status().configured
            adapter = self._adapter.snapshot()

        return DiscordSettingsSnapshot(
            enabled=settings.enabled,
            token_configured=token_configured,
            allowed_user_ids=tuple(
                str(value) for value in settings.allowed_user_ids
            ),
            adapter_state=adapter.state.value,
            health=adapter.health.value,
            running=adapter.running,
            connected=adapter.connected,
            ready=adapter.ready,
            reconnect_enabled=adapter.reconnect_enabled,
            reconnect_count=adapter.reconnect_count,
            disconnect_count=adapter.disconnect_count,
            latency_ms=adapter.latency_ms,
            uptime_seconds=adapter.uptime_seconds,
            failed=adapter.state.value == "failed",
        )

    def configure(
        self,
        *,
        enabled: bool,
        allowed_user_ids: Iterable[object],
        bot_token: str | None = None,
    ) -> DiscordSettingsSnapshot:
        normalized_ids = tuple(
            sorted(
                {
                    normalize_discord_user_id(value)
                    for value in allowed_user_ids
                }
            )
        )
        normalized_token = None
        if bot_token is not None:
            normalized_token = str(bot_token).strip()
            if not normalized_token:
                normalized_token = None

        with self._lock:
            if enabled and not normalized_ids:
                raise ValueError(
                    "Add at least one allowed Discord user ID before enabling remote messaging."
                )

            token_available = bool(normalized_token) or self._token_store.status().configured
            if enabled and not token_available:
                raise ValueError(
                    "Save a Discord bot token before enabling remote messaging."
                )

            if normalized_token is not None:
                self._token_store.save_token(normalized_token)

            settings = self._settings_store.save(
                DiscordSettings(
                    enabled=enabled,
                    allowed_user_ids=normalized_ids,
                )
            )
            self._apply_policy(settings)

            if not enabled:
                self._stop_adapter()
            elif normalized_token is not None and self._adapter.is_running:
                self._stop_adapter()
                self._start_adapter()
            elif not self._adapter.is_running:
                self._start_adapter()

        return self.snapshot()

    def start(self, *, persist: bool = True) -> DiscordSettingsSnapshot:
        with self._lock:
            settings = self._settings_store.load()
            if not settings.allowed_user_ids:
                raise ValueError(
                    "Add at least one allowed Discord user ID before starting remote messaging."
                )
            if not self._token_store.status().configured:
                raise ValueError(
                    "Save a Discord bot token before starting remote messaging."
                )

            if persist and not settings.enabled:
                settings = self._settings_store.save(
                    DiscordSettings(
                        enabled=True,
                        allowed_user_ids=settings.allowed_user_ids,
                    )
                )
            self._apply_policy(settings)
            self._start_adapter()
        return self.snapshot()

    def stop(self, *, persist: bool = True) -> DiscordSettingsSnapshot:
        with self._lock:
            settings = self._settings_store.load()
            if persist and settings.enabled:
                settings = self._settings_store.save(
                    DiscordSettings(
                        enabled=False,
                        allowed_user_ids=settings.allowed_user_ids,
                    )
                )
            self._apply_policy(settings)
            self._stop_adapter()
        return self.snapshot()

    def delete_token(self) -> DiscordSettingsSnapshot:
        with self._lock:
            self._stop_adapter()
            settings = self._settings_store.load()
            if settings.enabled:
                settings = self._settings_store.save(
                    DiscordSettings(
                        enabled=False,
                        allowed_user_ids=settings.allowed_user_ids,
                    )
                )
            self._token_store.delete_token()
            self._apply_policy(settings)
        return self.snapshot()

    def start_if_enabled(self) -> bool:
        """Apply saved access policy and attempt startup without breaking Akira."""

        try:
            settings = self._settings_store.load()
            self._apply_policy(settings)
            if not settings.enabled:
                return False
            self.start(persist=False)
            return True
        except Exception:
            return False

    def shutdown(self) -> None:
        """Stop Discord for application shutdown without changing preferences."""

        with self._lock:
            self._stop_adapter()

    def _start_adapter(self) -> bool:
        if self._adapter.is_running:
            return False
        token = self._token_store.load_token()
        if not token:
            raise ValueError("Discord bot token is not configured.")
        return bool(self._adapter.start(token, timeout=5.0))

    def _stop_adapter(self) -> bool:
        if not self._adapter.is_running:
            return False
        return bool(self._adapter.stop(timeout=5.0))

    def _apply_policy(self, settings: DiscordSettings) -> None:
        self._access_policy.replace_allowed_user_ids(
            settings.allowed_user_ids
        )


_DEFAULT_CONTROLLER: DiscordSettingsController | None = None
_DEFAULT_CONTROLLER_LOCK = threading.Lock()


def get_discord_settings_controller() -> DiscordSettingsController:
    """Return the process-wide Discord settings controller."""

    global _DEFAULT_CONTROLLER
    with _DEFAULT_CONTROLLER_LOCK:
        if _DEFAULT_CONTROLLER is None:
            _DEFAULT_CONTROLLER = DiscordSettingsController()
        return _DEFAULT_CONTROLLER
