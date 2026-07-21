from __future__ import annotations

import unittest

from app.discord_access import DiscordAccessPolicy
from app.discord_notifications import (
    DiscordNotificationService,
    get_discord_notification_service,
)


class FakeNotificationAdapter:
    def __init__(self) -> None:
        self.calls = []

    def send_direct_message(self, user_id, content, *, timeout=10.0):
        self.calls.append((user_id, content, timeout))


class DiscordNotificationServiceTests(unittest.TestCase):
    def test_omitted_recipients_broadcasts_to_all_allowed_users(self):
        adapter = FakeNotificationAdapter()
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([300, 100, 200]),
        )

        report = service.send("System update complete.")

        self.assertEqual(report.recipient_count, 3)
        self.assertEqual(report.message_parts, 1)
        self.assertEqual(report.messages_sent, 3)
        self.assertEqual(
            adapter.calls,
            [
                (100, "System update complete.", 10.0),
                (200, "System update complete.", 10.0),
                (300, "System update complete.", 10.0),
            ],
        )

    def test_explicit_recipients_are_normalized_and_deduplicated(self):
        adapter = FakeNotificationAdapter()
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100, 200, 300]),
        )

        report = service.send(
            "Hello",
            user_ids=[" 200 ", 100, "200"],
            timeout=2.5,
        )

        self.assertEqual(report.recipient_count, 2)
        self.assertEqual(
            adapter.calls,
            [(100, "Hello", 2.5), (200, "Hello", 2.5)],
        )

    def test_unauthorized_recipient_rejects_entire_request(self):
        adapter = FakeNotificationAdapter()
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100]),
        )

        with self.assertRaisesRegex(ValueError, "allowed-user list"):
            service.send("Hello", user_ids=[100, 999])

        self.assertEqual(adapter.calls, [])

    def test_no_allowed_users_and_empty_explicit_list_are_rejected(self):
        adapter = FakeNotificationAdapter()
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy(),
        )

        with self.assertRaisesRegex(ValueError, "at least one allowed"):
            service.send("Hello")

        configured = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100]),
        )
        with self.assertRaisesRegex(ValueError, "at least one.*recipient"):
            configured.send("Hello", user_ids=[])

    def test_long_notification_is_split_for_each_recipient(self):
        adapter = FakeNotificationAdapter()
        service = DiscordNotificationService(
            adapter=adapter,
            access_policy=DiscordAccessPolicy([100, 200]),
        )
        message = ("word " * 900).strip()

        report = service.send(message)

        self.assertGreater(report.message_parts, 1)
        self.assertEqual(
            report.messages_sent,
            report.recipient_count * report.message_parts,
        )
        self.assertTrue(all(len(content) <= 2_000 for _, content, _ in adapter.calls))
        first_recipient = [content for user_id, content, _ in adapter.calls if user_id == 100]
        self.assertEqual(" ".join(first_recipient), message)

    def test_blank_oversized_message_and_invalid_timeout_are_rejected(self):
        service = DiscordNotificationService(
            adapter=FakeNotificationAdapter(),
            access_policy=DiscordAccessPolicy([100]),
        )

        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            service.send("   ")
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            service.send("x" * 20_001)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            service.send("Hello", timeout=0)

    def test_default_service_is_process_wide_singleton(self):
        self.assertIs(
            get_discord_notification_service(),
            get_discord_notification_service(),
        )


if __name__ == "__main__":
    unittest.main()
