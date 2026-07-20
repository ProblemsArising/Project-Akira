from __future__ import annotations

import unittest

from app.discord_token_store import (
    DEFAULT_ACCOUNT_NAME,
    DEFAULT_SERVICE_NAME,
    DiscordTokenStore,
    DiscordTokenStoreError,
    KeyringPasswordBackend,
    get_discord_token_store,
)


class FakePasswordBackend:
    def __init__(self) -> None:
        self.values = {}
        self.set_calls = []
        self.get_calls = []
        self.delete_calls = []
        self.error = None

    def set_password(self, service_name, username, password):
        if self.error is not None:
            raise self.error
        self.set_calls.append((service_name, username, password))
        self.values[(service_name, username)] = password

    def get_password(self, service_name, username):
        if self.error is not None:
            raise self.error
        self.get_calls.append((service_name, username))
        return self.values.get((service_name, username))

    def delete_password(self, service_name, username):
        if self.error is not None:
            raise self.error
        self.delete_calls.append((service_name, username))
        self.values.pop((service_name, username), None)


class FakeKeyringModule:
    def __init__(self) -> None:
        self.values = {}

    def set_password(self, service_name, username, password):
        self.values[(service_name, username)] = password

    def get_password(self, service_name, username):
        return self.values.get((service_name, username))

    def delete_password(self, service_name, username):
        self.values.pop((service_name, username), None)


class DiscordTokenStoreTests(unittest.TestCase):
    def test_save_load_status_and_delete_token(self):
        backend = FakePasswordBackend()
        store = DiscordTokenStore(backend=backend)

        self.assertIsNone(store.load_token())
        self.assertFalse(store.status().configured)

        store.save_token("  secret-token  ")

        self.assertEqual(store.load_token(), "secret-token")
        self.assertTrue(store.has_token())
        self.assertTrue(store.status().configured)
        self.assertEqual(
            backend.set_calls,
            [(DEFAULT_SERVICE_NAME, DEFAULT_ACCOUNT_NAME, "secret-token")],
        )

        self.assertTrue(store.delete_token())
        self.assertIsNone(store.load_token())
        self.assertFalse(store.delete_token())

    def test_blank_token_is_rejected_without_touching_backend(self):
        backend = FakePasswordBackend()
        store = DiscordTokenStore(backend=backend)

        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            store.save_token("   ")

        self.assertEqual(backend.set_calls, [])

    def test_status_and_repr_do_not_expose_token(self):
        token = "super-secret-discord-token"
        backend = FakePasswordBackend()
        store = DiscordTokenStore(backend=backend)
        store.save_token(token)

        status = store.status()

        self.assertNotIn(token, repr(status))
        self.assertNotIn(token, repr(store))
        self.assertEqual(status.storage, "system_keyring")

    def test_backend_errors_are_wrapped_without_secret_value(self):
        token = "never-include-this-token"
        backend = FakePasswordBackend()
        backend.error = RuntimeError(f"backend failed while handling {token}")
        store = DiscordTokenStore(backend=backend)

        with self.assertRaises(DiscordTokenStoreError) as context:
            store.save_token(token)

        self.assertNotIn(token, str(context.exception))
        self.assertIn("system credential store", str(context.exception))

    def test_custom_credential_names_are_supported(self):
        backend = FakePasswordBackend()
        store = DiscordTokenStore(
            backend=backend,
            service_name="Akira Test",
            account_name="Discord Test Token",
        )

        store.save_token("token")

        self.assertEqual(
            backend.set_calls,
            [("Akira Test", "Discord Test Token", "token")],
        )

    def test_keyring_backend_forwards_to_injected_module(self):
        module = FakeKeyringModule()
        backend = KeyringPasswordBackend(keyring_module=module)

        backend.set_password("service", "account", "token")
        self.assertEqual(
            backend.get_password("service", "account"),
            "token",
        )
        backend.delete_password("service", "account")
        self.assertIsNone(backend.get_password("service", "account"))

    def test_default_store_is_process_wide_singleton(self):
        self.assertIs(
            get_discord_token_store(),
            get_discord_token_store(),
        )


if __name__ == "__main__":
    unittest.main()
