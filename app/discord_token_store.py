"""Secure Discord bot-token storage for Project Akira.

The token is stored through the operating system credential service via
``keyring``. It is deliberately excluded from Project Akira's JSON settings,
logs, status snapshots, and exception messages.

The default Windows backend uses Windows Credential Locker. Tests and future
platform integrations can inject another backend implementing
``PasswordBackend``.
"""

from __future__ import annotations

import threading

from dataclasses import dataclass
from typing import Any, Protocol


DEFAULT_SERVICE_NAME = "Project Akira"
DEFAULT_ACCOUNT_NAME = "Discord bot token"


class PasswordBackend(Protocol):
    """Small subset of the keyring API used by Project Akira."""

    def set_password(
        self,
        service_name: str,
        username: str,
        password: str,
    ) -> None: ...

    def get_password(
        self,
        service_name: str,
        username: str,
    ) -> str | None: ...

    def delete_password(
        self,
        service_name: str,
        username: str,
    ) -> None: ...


class DiscordTokenStoreError(RuntimeError):
    """Raised when the operating system credential store cannot be accessed."""


@dataclass(frozen=True, slots=True)
class DiscordTokenStatus:
    """Non-secret token state suitable for APIs and user interfaces."""

    configured: bool
    storage: str = "system_keyring"


class KeyringPasswordBackend:
    """Lazy adapter around the optional ``keyring`` dependency."""

    def __init__(self, keyring_module: Any | None = None) -> None:
        self._keyring_module = keyring_module
        self._module_lock = threading.Lock()

    def _keyring(self) -> Any:
        if self._keyring_module is not None:
            return self._keyring_module

        with self._module_lock:
            if self._keyring_module is not None:
                return self._keyring_module

            try:
                import keyring
            except ImportError as error:
                raise RuntimeError(
                    "Secure Discord token storage requires the keyring package."
                ) from error

            self._keyring_module = keyring
            return keyring

    def set_password(
        self,
        service_name: str,
        username: str,
        password: str,
    ) -> None:
        self._keyring().set_password(service_name, username, password)

    def get_password(
        self,
        service_name: str,
        username: str,
    ) -> str | None:
        return self._keyring().get_password(service_name, username)

    def delete_password(
        self,
        service_name: str,
        username: str,
    ) -> None:
        self._keyring().delete_password(service_name, username)


class DiscordTokenStore:
    """Save and retrieve the Discord bot token without caching it in memory."""

    def __init__(
        self,
        *,
        backend: PasswordBackend | None = None,
        service_name: str = DEFAULT_SERVICE_NAME,
        account_name: str = DEFAULT_ACCOUNT_NAME,
    ) -> None:
        normalized_service = str(service_name).strip()
        normalized_account = str(account_name).strip()
        if not normalized_service:
            raise ValueError("Credential service name cannot be blank")
        if not normalized_account:
            raise ValueError("Credential account name cannot be blank")

        self._backend = backend or KeyringPasswordBackend()
        self._service_name = normalized_service
        self._account_name = normalized_account
        self._lock = threading.RLock()

    def save_token(self, token: str) -> None:
        """Store a nonblank bot token in the operating system keyring."""

        normalized = str(token).strip()
        if not normalized:
            raise ValueError("Discord bot token cannot be blank")

        with self._lock:
            try:
                self._backend.set_password(
                    self._service_name,
                    self._account_name,
                    normalized,
                )
            except Exception as error:
                raise DiscordTokenStoreError(
                    "Could not save the Discord bot token in the system "
                    "credential store."
                ) from error

    def load_token(self) -> str | None:
        """Return the stored token, or ``None`` when it is not configured."""

        with self._lock:
            try:
                token = self._backend.get_password(
                    self._service_name,
                    self._account_name,
                )
            except Exception as error:
                raise DiscordTokenStoreError(
                    "Could not read the Discord bot token from the system "
                    "credential store."
                ) from error

        if token is None:
            return None

        normalized = str(token).strip()
        return normalized or None

    def delete_token(self) -> bool:
        """Delete the stored token and report whether one previously existed."""

        with self._lock:
            try:
                existing = self._backend.get_password(
                    self._service_name,
                    self._account_name,
                )
                if existing is None:
                    return False

                self._backend.delete_password(
                    self._service_name,
                    self._account_name,
                )
            except Exception as error:
                raise DiscordTokenStoreError(
                    "Could not delete the Discord bot token from the system "
                    "credential store."
                ) from error

        return True

    def has_token(self) -> bool:
        """Return whether a nonblank token is configured."""

        return self.load_token() is not None

    def status(self) -> DiscordTokenStatus:
        """Return a non-secret status snapshot."""

        return DiscordTokenStatus(configured=self.has_token())


_DEFAULT_TOKEN_STORE: DiscordTokenStore | None = None
_DEFAULT_TOKEN_STORE_LOCK = threading.Lock()


def get_discord_token_store() -> DiscordTokenStore:
    """Return the process-wide secure Discord token store."""

    global _DEFAULT_TOKEN_STORE
    with _DEFAULT_TOKEN_STORE_LOCK:
        if _DEFAULT_TOKEN_STORE is None:
            _DEFAULT_TOKEN_STORE = DiscordTokenStore()
        return _DEFAULT_TOKEN_STORE
