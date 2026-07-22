from __future__ import annotations

import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ai.llama_cpp_backend import (
    LlamaCppBackend,
    LlamaCppConfigurationError,
    LlamaCppProcessConfig,
    LlamaCppStartupError,
    ManagedLlamaCppProcess,
    llama_cpp_process_config,
    stop_all_managed_llama_cpp_processes,
)
from ai.llm import create_llm_backend
from ai.llm_backend import LLMBackend
from config.settings import AppSettings
from config.settings_validation import validate_settings_changes


class FakePopen:
    def __init__(self, *, exit_code=None, pid=4321):
        self.exit_code = exit_code
        self.pid = pid
        self.terminate_called = False
        self.kill_called = False
        self.wait_calls = []

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminate_called = True
        self.exit_code = 0

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.exit_code

    def kill(self):
        self.kill_called = True
        self.exit_code = -9


class FakeManagedProcess:
    def __init__(self, alias="akira-test"):
        self.config = types.SimpleNamespace(model_alias=alias)
        self.base_url = "http://127.0.0.1:8080/v1"
        self.pid = 999
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class LlamaCppBackendTests(unittest.TestCase):
    def create_files(self, directory: str):
        root = Path(directory)
        executable = root / "llama-server.exe"
        executable.write_bytes(b"binary")
        model = root / "model.Q4_K_M.gguf"
        model.write_bytes(b"GGUF")
        return executable, model

    def test_settings_build_process_config(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            settings = AppSettings()
            settings.llm.backend = "llama_cpp"
            settings.llm.llama_cpp_executable = str(executable)
            settings.llm.llama_cpp_model_path = str(model)
            settings.llm.llama_cpp_model_alias = "akira-model"
            settings.llm.llama_cpp_port = 8091
            settings.llm.llama_cpp_gpu_layers = "all"
            settings.llm.llama_cpp_threads = 8
            settings.llm.reasoning_mode = "auto"

            config = llama_cpp_process_config(settings)

            self.assertEqual(config.executable, executable.resolve())
            self.assertEqual(config.model_path, model.resolve())
            self.assertEqual(config.model_alias, "akira-model")
            self.assertEqual(config.base_url, "http://127.0.0.1:8091/v1")
            self.assertEqual(config.health_url, "http://127.0.0.1:8091/health")
            self.assertEqual(config.gpu_layers, "all")
            self.assertEqual(config.threads, 8)
            self.assertEqual(config.reasoning_mode, "auto")

    def test_quoted_executable_and_model_paths_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            settings = AppSettings()
            settings.llm.backend = "llama_cpp"
            settings.llm.llama_cpp_executable = f'"{executable}"'
            settings.llm.llama_cpp_model_path = f'"{model}"'

            config = llama_cpp_process_config(settings)

            self.assertEqual(config.executable, executable.resolve())
            self.assertEqual(config.model_path, model.resolve())

    def test_invalid_model_and_remote_host_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, _ = self.create_files(directory)
            settings = AppSettings()
            settings.llm.backend = "llama_cpp"
            settings.llm.llama_cpp_executable = str(executable)
            settings.llm.llama_cpp_model_path = str(Path(directory) / "missing.gguf")

            with self.assertRaisesRegex(LlamaCppConfigurationError, "not found"):
                llama_cpp_process_config(settings)

            model = Path(directory) / "model.gguf"
            model.write_bytes(b"GGUF")
            settings.llm.llama_cpp_model_path = str(model)
            settings.llm.llama_cpp_host = "0.0.0.0"
            with self.assertRaisesRegex(LlamaCppConfigurationError, "localhost"):
                llama_cpp_process_config(settings)

    def test_process_launches_expected_command_and_waits_for_health(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            config = LlamaCppProcessConfig(
                executable=executable,
                model_path=model,
                model_alias="akira-model",
                context_size=4096,
                gpu_layers="all",
                threads=6,
                reasoning_mode="off",
                extra_args=("--no-warmup",),
                log_file=Path(directory) / "server.log",
            )
            fake = FakePopen()
            calls = []
            health_results = iter((False, True))
            clock_values = iter((0.0, 0.0, 0.1, 0.2))

            def popen(command, **kwargs):
                calls.append((command, kwargs))
                return fake

            process = ManagedLlamaCppProcess(
                config,
                popen_factory=popen,
                health_probe=lambda _url: next(health_results),
                port_probe=lambda _host, _port: False,
                monotonic=lambda: next(clock_values),
                sleep=lambda _seconds: None,
            )
            process.start()

            command, kwargs = calls[0]
            self.assertEqual(command[0], str(executable))
            self.assertIn("--model", command)
            self.assertIn(str(model), command)
            self.assertIn("--ctx-size", command)
            self.assertIn("4096", command)
            self.assertIn("--n-gpu-layers", command)
            self.assertIn("all", command)
            self.assertIn("--reasoning", command)
            self.assertIn("off", command)
            self.assertIn("--no-webui", command)
            self.assertEqual(command[-1], "--no-warmup")
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertTrue(process.running)
            self.assertEqual(process.pid, 4321)

            process.stop()
            self.assertTrue(fake.terminate_called)
            self.assertFalse(process.running)

    def test_global_shutdown_stops_registered_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            config = LlamaCppProcessConfig(
                executable=executable,
                model_path=model,
                model_alias="akira-model",
                log_file=Path(directory) / "server.log",
            )
            first = FakePopen(pid=1001)
            second = FakePopen(pid=1002)
            popens = iter((first, second))

            processes = [
                ManagedLlamaCppProcess(
                    config,
                    popen_factory=lambda *_args, **_kwargs: next(popens),
                    port_probe=lambda _host, _port: False,
                    health_probe=lambda _url: True,
                    monotonic=mock.Mock(side_effect=[0.0, 0.0]),
                    sleep=lambda _seconds: None,
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()

            stopped = stop_all_managed_llama_cpp_processes(timeout=0.5)

            self.assertEqual(stopped, 2)
            self.assertTrue(first.terminate_called)
            self.assertTrue(second.terminate_called)
            self.assertFalse(processes[0].running)
            self.assertFalse(processes[1].running)
            self.assertEqual(stop_all_managed_llama_cpp_processes(), 0)

    def test_occupied_port_and_early_exit_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            executable, model = self.create_files(directory)
            config = LlamaCppProcessConfig(
                executable=executable,
                model_path=model,
                model_alias="akira-model",
                log_file=Path(directory) / "server.log",
            )
            occupied = ManagedLlamaCppProcess(
                config,
                port_probe=lambda _host, _port: True,
            )
            with self.assertRaisesRegex(LlamaCppStartupError, "already in use"):
                occupied.start()

            failed = FakePopen(exit_code=7)
            process = ManagedLlamaCppProcess(
                config,
                popen_factory=lambda *_args, **_kwargs: failed,
                port_probe=lambda _host, _port: False,
                health_probe=lambda _url: False,
                monotonic=mock.Mock(side_effect=[0.0, 0.0]),
                sleep=lambda _seconds: None,
            )
            with self.assertRaisesRegex(LlamaCppStartupError, "exit code 7"):
                process.start()

    def test_backend_uses_managed_process_and_shared_chat_client(self):
        settings = AppSettings()
        settings.llm.backend = "llama_cpp"
        process = FakeManagedProcess(alias="managed-model")
        response = types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="Managed reply"),
                    finish_reason="stop",
                )
            ]
        )
        create = mock.Mock(return_value=response)
        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            ),
            close=mock.Mock(),
        )

        backend = LlamaCppBackend(settings, client=client, process=process)
        try:
            with mock.patch("ai.llm.build_memory_context", return_value=""), mock.patch(
                "ai.llm.remember_turn"
            ):
                reply = backend.ask("Hello")

            self.assertEqual(reply, "Managed reply")
            self.assertTrue(process.started)
            self.assertIsInstance(backend, LLMBackend)
            self.assertEqual(backend.info.backend_id, "llama_cpp")
            self.assertEqual(backend.info.display_name, "Managed llama.cpp")
            self.assertTrue(backend.info.managed)
            self.assertEqual(backend.base_url, process.base_url)
            self.assertEqual(backend.server_pid, 999)
            call = create.call_args
            self.assertEqual(call.kwargs["model"], "managed-model")
        finally:
            backend.close()

        self.assertTrue(process.stopped)
        client.close.assert_not_called()

    def test_factory_dispatches_to_managed_backend(self):
        settings = AppSettings()
        settings.llm.backend = "llama_cpp"
        sentinel = object()

        with mock.patch(
            "ai.llama_cpp_backend.LlamaCppBackend",
            return_value=sentinel,
        ) as constructor:
            result = create_llm_backend(
                settings,
                client="client",
                llama_process="process",
            )

        self.assertIs(result, sentinel)
        constructor.assert_called_once_with(
            settings,
            client="client",
            process="process",
        )

    def test_settings_validation_accepts_managed_backend_fields(self):
        current = AppSettings().to_dict()
        changes = validate_settings_changes(
            {
                "llm": {
                    "backend": "llama_cpp",
                    "llama_cpp_port": 8088,
                    "llama_cpp_context_size": 16384,
                    "llama_cpp_threads": -1,
                    "llama_cpp_startup_timeout_seconds": 180,
                    "llama_cpp_extra_args": ["--no-warmup"],
                }
            },
            current,
        )

        self.assertEqual(changes["llm"]["backend"], "llama_cpp")
        self.assertEqual(changes["llm"]["llama_cpp_port"], 8088)
        self.assertEqual(
            changes["llm"]["llama_cpp_startup_timeout_seconds"],
            180.0,
        )


if __name__ == "__main__":
    unittest.main()
