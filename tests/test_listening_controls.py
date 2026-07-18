from __future__ import annotations

import unittest

from app.listening_controls import ListeningControlSession


class FakeListeningService:
    def __init__(self) -> None:
        self.is_listening = False
        self.start_calls = 0
        self.stop_calls = []

    def start_listening(self) -> bool:
        self.start_calls += 1
        if self.is_listening:
            return False
        self.is_listening = True
        return True

    def stop_listening(self, *, wait=False, timeout=None) -> bool:
        self.stop_calls.append((wait, timeout))
        was_listening = self.is_listening
        self.is_listening = False
        return was_listening


class ListeningControlSessionTests(unittest.TestCase):
    def test_auto_start_status_stop_start_and_quit(self):
        service = FakeListeningService()
        commands = iter(["status", "stop", "status", "start", "quit"])
        output = []
        session = ListeningControlSession(
            service=service,
            input_function=lambda prompt: next(commands),
            output_function=output.append,
        )

        session.run()

        self.assertEqual(service.start_calls, 2)
        self.assertFalse(service.is_listening)
        self.assertIn("Listening started.", output)
        self.assertIn("Microphone is listening.", output)
        self.assertIn("Listening stopped.", output)
        self.assertIn("Microphone is stopped.", output)
        self.assertEqual(service.stop_calls[-1], (True, 2.0))

    def test_start_reports_when_already_active(self):
        service = FakeListeningService()
        commands = iter(["start", "quit"])
        output = []
        session = ListeningControlSession(
            service=service,
            input_function=lambda prompt: next(commands),
            output_function=output.append,
        )

        session.run()

        self.assertIn("Listening is already active.", output)

    def test_stop_reports_when_already_stopped(self):
        service = FakeListeningService()
        commands = iter(["stop", "quit"])
        output = []
        session = ListeningControlSession(
            service=service,
            auto_start=False,
            input_function=lambda prompt: next(commands),
            output_function=output.append,
        )

        session.run()

        self.assertIn("Listening is already stopped.", output)

    def test_unknown_command_shows_help_hint(self):
        service = FakeListeningService()
        commands = iter(["dance", "quit"])
        output = []
        session = ListeningControlSession(
            service=service,
            auto_start=False,
            input_function=lambda prompt: next(commands),
            output_function=output.append,
        )

        session.run()

        self.assertTrue(any("Unknown command" in message for message in output))

    def test_eof_stops_listening(self):
        service = FakeListeningService()

        def closed_input(prompt):
            raise EOFError

        session = ListeningControlSession(
            service=service,
            input_function=closed_input,
            output_function=lambda message: None,
        )

        session.run()

        self.assertFalse(service.is_listening)
        self.assertEqual(service.stop_calls[-1], (True, 2.0))


if __name__ == "__main__":
    unittest.main()
