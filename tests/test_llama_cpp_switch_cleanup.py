from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai.llama_cpp_backend import (
    LlamaCppProcessConfig,
    ManagedLlamaCppProcess,
)


class FakePopen:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode = None
        self.wait_calls = []
        self.terminate_called = False
        self.kill_called = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminate_called = True
        self.returncode = 0

    def kill(self):
        self.kill_called = True
        self.returncode = -9


class WindowsProcessTreeCleanupTests(unittest.TestCase):
    def test_stop_uses_taskkill_for_complete_windows_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "llama-server.exe"
            model = root / "model.gguf"
            executable.write_bytes(b"binary")
            model.write_bytes(b"GGUF")
            config = LlamaCppProcessConfig(
                executable=executable,
                model_path=model,
                model_alias="akira-model",
                log_file=root / "server.log",
            )
            fake = FakePopen()
            process = ManagedLlamaCppProcess(
                config,
                popen_factory=lambda *_args, **_kwargs: fake,
                port_probe=lambda _host, _port: False,
                health_probe=lambda _url: True,
                monotonic=mock.Mock(side_effect=[0.0, 0.0]),
                sleep=lambda _seconds: None,
            )
            process.start()

            with mock.patch(
                "ai.llama_cpp_backend.os.name",
                "nt",
            ), mock.patch(
                "ai.llama_cpp_backend.subprocess.run",
            ) as taskkill:
                process.stop(timeout=0.5)

            taskkill.assert_called_once()
            self.assertEqual(
                taskkill.call_args.args[0],
                ["taskkill", "/PID", "4321", "/T", "/F"],
            )
            self.assertEqual(
                taskkill.call_args.kwargs["stdin"],
                subprocess.DEVNULL,
            )
            self.assertTrue(fake.terminate_called)
            self.assertFalse(process.running)


if __name__ == "__main__":
    unittest.main()
