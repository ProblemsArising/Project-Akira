"""Temporary terminal controls for starting and stopping microphone listening.

The future WebUI should call ``ConversationService.start_listening`` and
``ConversationService.stop_listening`` directly. This small command interface
exists so the controls can be tested before the WebUI is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


class ListeningConversationService(Protocol):
    @property
    def is_listening(self) -> bool: ...

    def start_listening(self) -> bool: ...

    def stop_listening(
        self,
        *,
        wait: bool = False,
        timeout: float | None = None,
    ) -> bool: ...


@dataclass(slots=True)
class ListeningControlSession:
    """Run a simple start/stop/status command prompt."""

    service: ListeningConversationService
    auto_start: bool = True
    input_function: InputFunction = input
    output_function: OutputFunction = print
    prompt: str = "listen> "

    def run(self) -> None:
        self.output_function("Project Akira listening controls")
        self.output_function("Commands: start, stop, status, help, quit")

        if self.auto_start:
            self._start()

        try:
            while True:
                try:
                    raw_command = self.input_function(self.prompt)
                except EOFError:
                    self.output_function("\nListening controls closed.")
                    return
                except KeyboardInterrupt:
                    self.output_function("\nStopping listening controls...")
                    return

                command = str(raw_command).strip().lower()
                if not command:
                    continue

                if command in {"quit", "exit", "/quit", "/exit"}:
                    self.output_function("Stopping listening controls...")
                    return
                if command in {"start", "on", "listen", "/start"}:
                    self._start()
                    continue
                if command in {"stop", "off", "pause", "/stop"}:
                    self._stop()
                    continue
                if command in {"status", "/status"}:
                    state = "listening" if self.service.is_listening else "stopped"
                    self.output_function(f"Microphone is {state}.")
                    continue
                if command in {"help", "/help"}:
                    self.output_function("Commands: start, stop, status, help, quit")
                    continue

                self.output_function(f"Unknown command: {command}. Type help for commands.")
        finally:
            self.service.stop_listening(wait=True, timeout=2.0)

    def _start(self) -> None:
        if self.service.start_listening():
            self.output_function("Listening started.")
        else:
            self.output_function("Listening is already active.")

    def _stop(self) -> None:
        if self.service.stop_listening(wait=True, timeout=2.0):
            self.output_function("Listening stopped.")
        else:
            self.output_function("Listening is already stopped.")
