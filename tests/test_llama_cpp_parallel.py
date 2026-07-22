from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai.llama_cpp_backend import (
    LlamaCppConfigurationError,
    LlamaCppProcessConfig,
    ManagedLlamaCppProcess,
    llama_cpp_process_config,
)
from config.settings import AppSettings


class ManagedLlamaCppParallelTests(unittest.TestCase):
    def create_files(self, directory: str) -> tuple[Path, Path]:
        root = Path(directory)
        executable = root / "llama-server.exe"
        executable.write_bytes(b"binary")
        model = root / "model.gguf"
        model.write_bytes(b"GGUF")
        return executable, model

    def test_managed_server_forces_one_parallel_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            config = LlamaCppProcessConfig(
                executable=executable,
                model_path=model,
                model_alias="akira-model",
            )

            command = ManagedLlamaCppProcess(config).command

            parallel_index = command.index("--parallel")
            self.assertEqual(command[parallel_index + 1], "1")
            self.assertEqual(config.parallel_slots, 1)

    def test_parallel_cannot_be_overridden_by_extra_args(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            settings = AppSettings()
            settings.llm.backend = "llama_cpp"
            settings.llm.llama_cpp_executable = str(executable)
            settings.llm.llama_cpp_model_path = str(model)

            for argument in ("--parallel=4", "-np=4"):
                with self.subTest(argument=argument):
                    settings.llm.llama_cpp_extra_args = [argument]
                    with self.assertRaisesRegex(
                        LlamaCppConfigurationError,
                        "managed by Project Akira",
                    ):
                        llama_cpp_process_config(settings)


if __name__ == "__main__":
    unittest.main()
