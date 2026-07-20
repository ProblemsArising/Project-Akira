from __future__ import annotations

import unittest

from app.discord_access import (
    MAX_DISCORD_SNOWFLAKE,
    DiscordAccessDenied,
    DiscordAccessPolicy,
    DiscordAccessReason,
    get_discord_access_policy,
    normalize_discord_user_id,
)


class FakeDiscordUser:
    def __init__(self, user_id):
        self.id = user_id


class DiscordAccessPolicyTests(unittest.TestCase):
    def test_empty_policy_denies_everyone_by_default(self):
        policy = DiscordAccessPolicy()

        decision = policy.authorize(123456789)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, DiscordAccessReason.NOT_CONFIGURED)
        self.assertEqual(policy.allowed_user_ids, ())
        self.assertFalse(policy.snapshot().configured)
        self.assertTrue(policy.snapshot().default_deny)

    def test_only_configured_users_are_allowed(self):
        policy = DiscordAccessPolicy([111, "222"])

        self.assertTrue(policy.is_allowed(111))
        self.assertTrue(policy.is_allowed("222"))
        self.assertFalse(policy.is_allowed(333))
        self.assertEqual(
            policy.authorize(333).reason,
            DiscordAccessReason.USER_NOT_ALLOWED,
        )

    def test_discord_like_user_object_is_supported(self):
        policy = DiscordAccessPolicy([456])

        self.assertTrue(policy.is_allowed(FakeDiscordUser("456")))
        self.assertFalse(policy.is_allowed(FakeDiscordUser("789")))

    def test_replacement_normalizes_deduplicates_and_sorts(self):
        policy = DiscordAccessPolicy()

        result = policy.replace_allowed_user_ids(
            [" 300 ", 100, "200", 100, "000200"]
        )

        self.assertEqual(result, (100, 200, 300))
        self.assertEqual(policy.allowed_user_ids, (100, 200, 300))
        self.assertEqual(policy.snapshot().allowed_user_count, 3)

    def test_invalid_replacement_leaves_existing_policy_unchanged(self):
        policy = DiscordAccessPolicy([123])

        with self.assertRaises(ValueError):
            policy.replace_allowed_user_ids([456, "not-an-id"])

        self.assertEqual(policy.allowed_user_ids, (123,))
        self.assertTrue(policy.is_allowed(123))
        self.assertFalse(policy.is_allowed(456))

    def test_add_remove_and_clear_report_changes(self):
        policy = DiscordAccessPolicy([100])

        self.assertFalse(policy.add_allowed_user_id("100"))
        self.assertTrue(policy.add_allowed_user_id("200"))
        self.assertEqual(policy.allowed_user_ids, (100, 200))

        self.assertFalse(policy.remove_allowed_user_id(300))
        self.assertTrue(policy.remove_allowed_user_id(100))
        self.assertEqual(policy.allowed_user_ids, (200,))

        self.assertTrue(policy.clear())
        self.assertFalse(policy.clear())
        self.assertFalse(policy.snapshot().configured)

    def test_invalid_runtime_ids_are_denied_instead_of_raising(self):
        policy = DiscordAccessPolicy([123])

        for value in (None, True, 0, -1, 1.5, "", "abc", MAX_DISCORD_SNOWFLAKE + 1):
            with self.subTest(value=value):
                decision = policy.authorize(value)
                self.assertFalse(decision.allowed)
                self.assertEqual(
                    decision.reason,
                    DiscordAccessReason.INVALID_USER_ID,
                )

    def test_normalizer_accepts_valid_snowflakes_and_rejects_bad_values(self):
        self.assertEqual(normalize_discord_user_id(" 123 "), 123)
        self.assertEqual(
            normalize_discord_user_id(MAX_DISCORD_SNOWFLAKE),
            MAX_DISCORD_SNOWFLAKE,
        )

        for value in (False, 0, -2, "12.3", "+123", "１２３"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_discord_user_id(value)

    def test_require_allowed_raises_generic_non_identifying_error(self):
        denied_id = 987654321
        policy = DiscordAccessPolicy([123])

        with self.assertRaises(DiscordAccessDenied) as context:
            policy.require_allowed(denied_id)

        self.assertEqual(
            context.exception.reason,
            DiscordAccessReason.USER_NOT_ALLOWED,
        )
        self.assertNotIn(str(denied_id), str(context.exception))

        policy.require_allowed(123)

    def test_snapshot_does_not_enumerate_allowed_ids(self):
        policy = DiscordAccessPolicy([123456789])

        snapshot = policy.snapshot()

        self.assertEqual(snapshot.allowed_user_count, 1)
        self.assertNotIn("123456789", repr(snapshot))

    def test_default_policy_is_process_wide_singleton(self):
        self.assertIs(
            get_discord_access_policy(),
            get_discord_access_policy(),
        )


if __name__ == "__main__":
    unittest.main()
