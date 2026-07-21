from __future__ import annotations

import unittest

from app.discord_commands import (
    DiscordRemoteStatus,
    format_discord_status,
)


class DiscordHealthStatusTests(unittest.TestCase):
    def test_status_includes_gateway_reconnect_latency_and_uptime(self):
        text = format_discord_status(
            DiscordRemoteStatus(
                adapter_state="running",
                adapter_health="healthy",
                gateway_connected=True,
                gateway_ready=True,
                reconnect_enabled=True,
                reconnect_count=2,
                disconnect_count=3,
                latency_ms=42.4,
                uptime_seconds=3_725,
                token_configured=True,
                allowed_user_count=1,
                active_session_count=2,
                mapped_user_count=3,
            )
        )

        self.assertIn("Connection: running", text)
        self.assertIn("Health: healthy", text)
        self.assertIn("Gateway: ready", text)
        self.assertIn("Reconnect: enabled (2 recoveries, 3 disconnects)", text)
        self.assertIn("Latency: 42 ms", text)
        self.assertIn("Uptime: 1h 2m 5s", text)

    def test_unavailable_health_does_not_expose_error_details(self):
        secret = "sensitive backend error"
        status = DiscordRemoteStatus(
            adapter_state="failed",
            adapter_health="failed",
            gateway_connected=False,
            gateway_ready=False,
            reconnect_enabled=True,
            token_configured=None,
            allowed_user_count=0,
            active_session_count=0,
            mapped_user_count=0,
        )

        text = format_discord_status(status)

        self.assertIn("Health: failed", text)
        self.assertIn("Gateway: disconnected", text)
        self.assertNotIn(secret, text)


if __name__ == "__main__":
    unittest.main()
