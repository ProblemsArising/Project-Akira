from __future__ import annotations

import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from app.desktop import BackendServer, _default_managed_process_cleanup


class FakeThread:
    def __init__(self, events: list[tuple[str, float]]) -> None:
        self.events = events
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.events.append(("join", float(timeout or 0.0)))
        self.alive = False


class ManagedInferenceExitTests(unittest.TestCase):
    def test_backend_stop_cleans_up_managed_process_after_uvicorn(self) -> None:
        events: list[tuple[str, float]] = []

        def cleanup(timeout: float) -> int:
            events.append(("cleanup", timeout))
            return 1

        backend = BackendServer(
            port=49135,
            managed_process_cleanup=cleanup,
        )
        server = SimpleNamespace(should_exit=False)
        thread = FakeThread(events)
        backend._server = server
        backend._thread = thread

        backend.stop(timeout=2.5)

        self.assertTrue(server.should_exit)
        self.assertEqual(events, [("join", 2.5), ("cleanup", 2.5)])
        self.assertIsNone(backend._server)
        self.assertIsNone(backend._thread)

    def test_cleanup_failure_does_not_prevent_desktop_shutdown(self) -> None:
        def cleanup(_timeout: float) -> int:
            raise RuntimeError("cleanup failed")

        backend = BackendServer(
            port=49136,
            managed_process_cleanup=cleanup,
        )
        backend._server = SimpleNamespace(should_exit=False)
        backend._thread = None

        output = io.StringIO()
        with redirect_stdout(output):
            backend.stop()

        self.assertIn("Could not stop managed llama.cpp server", output.getvalue())
        self.assertIsNone(backend._server)
        self.assertIsNone(backend._thread)

    def test_default_cleanup_uses_only_an_already_loaded_backend_module(self) -> None:
        module_name = "ai.llama_cpp_backend"
        previous = sys.modules.get(module_name)
        calls: list[float] = []
        fake_module = types.ModuleType(module_name)

        def cleanup(*, timeout: float) -> int:
            calls.append(timeout)
            return 2

        fake_module.stop_all_managed_llama_cpp_processes = cleanup
        sys.modules[module_name] = fake_module
        try:
            stopped = _default_managed_process_cleanup(1.25)
        finally:
            if previous is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous

        self.assertEqual(stopped, 2)
        self.assertEqual(calls, [1.25])


if __name__ == "__main__":
    unittest.main()
