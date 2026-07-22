from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import ai.llama_cpp_backend as llama_cpp_backend
from ai.llama_cpp_backend import (
    LlamaCppProcessConfig,
    managed_llama_cpp_snapshot,
)


class FakeProcess:
    pid = 2468

    def __init__(self, exit_code: int | None = None) -> None:
        self.exit_code = exit_code

    def poll(self) -> int | None:
        return self.exit_code


class FakeOwner:
    def __init__(
        self,
        config: LlamaCppProcessConfig,
        process: FakeProcess,
    ) -> None:
        self.config = config
        self._process = process


class ManagedLlamaCppSnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_runtime_state_without_mutating_registry(self):
        config = LlamaCppProcessConfig(
            executable=Path("C:/llama/llama-server.exe"),
            model_path=Path("C:/models/akira.gguf"),
            model_alias="akira-local",
            host="127.0.0.1",
            port=8181,
            context_size=16384,
            gpu_layers="all",
            threads=12,
            parallel_slots=1,
            log_file=Path("C:/Akira/data/logs/llama-server.log"),
        )
        owner = FakeOwner(config, FakeProcess())
        registry = {owner}

        with mock.patch.object(
            llama_cpp_backend,
            "_ACTIVE_PROCESSES",
            registry,
        ):
            snapshots = managed_llama_cpp_snapshot()

        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[0]
        self.assertEqual(snapshot.pid, 2468)
        self.assertTrue(snapshot.running)
        self.assertIsNone(snapshot.exit_code)
        self.assertEqual(snapshot.base_url, "http://127.0.0.1:8181/v1")
        self.assertEqual(snapshot.health_url, "http://127.0.0.1:8181/health")
        self.assertEqual(snapshot.model_alias, "akira-local")
        self.assertEqual(snapshot.context_size, 16384)
        self.assertEqual(snapshot.gpu_layers, "all")
        self.assertEqual(snapshot.threads, 12)
        self.assertEqual(snapshot.parallel_slots, 1)
        self.assertEqual(registry, {owner})

    def test_snapshot_reports_exited_process(self):
        config = LlamaCppProcessConfig(
            executable=Path("llama-server.exe"),
            model_path=Path("akira.gguf"),
            model_alias="akira-local",
        )
        owner = FakeOwner(config, FakeProcess(exit_code=7))

        with mock.patch.object(
            llama_cpp_backend,
            "_ACTIVE_PROCESSES",
            {owner},
        ):
            snapshot = managed_llama_cpp_snapshot()[0]

        self.assertFalse(snapshot.running)
        self.assertEqual(snapshot.exit_code, 7)


if __name__ == "__main__":
    unittest.main()
