"""Interactive text-message input for Project Akira.

The future WebUI can call :meth:`ConversationService.process_text` directly.
This module provides a small terminal interface today so typed conversations
can be used and tested without microphone or Whisper input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from app.conversation import ConversationResult

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]


class TextConversationService(Protocol):
    """Conversation methods required by :class:`TextInputSession`."""

    def process_text(
        self,
        text: str,
        *,
        speak: bool = True,
        source: str = "text",
        audio_file: str | None = None,
    ) -> ConversationResult | None: ...

    def start_new_conversation(self, title: str | None = None) -> int | None: ...


@dataclass(slots=True)
class TextInputSession:
    """Run an interactive terminal text-chat session.

    Commands are deliberately minimal because this is a temporary user
    interface before the WebUI. All regular messages are delegated to the same
    ``ConversationService`` used by microphone conversations.
    """

    service: TextConversationService
    speak: bool = True
    input_function: InputFunction = input
    output_function: OutputFunction = print
    prompt: str = "You: "

    def submit(self, text: str) -> ConversationResult | None:
        """Submit one typed message using the current speech preference."""

        return self.service.process_text(text, speak=self.speak, source="text")

    def run(self) -> None:
        """Read and process messages until the user exits or input closes."""

        self.output_function("Project Akira text chat")
        self.output_function("Type /help for commands or /quit to exit.")

        while True:
            try:
                raw_text = self.input_function(self.prompt)
            except EOFError:
                self.output_function("\nText input closed.")
                return
            except KeyboardInterrupt:
                self.output_function("\nStopping text chat...")
                return

            text = str(raw_text).strip()
            if not text:
                continue

            if text.startswith("/"):
                if self._handle_command(text):
                    return
                continue

            self.submit(text)

    def _handle_command(self, command_text: str) -> bool:
        """Handle a slash command.

        Returns ``True`` when the session should exit.
        """

        command, _, argument = command_text.partition(" ")
        command = command.lower()
        argument = argument.strip()

        if command in {"/quit", "/exit"}:
            self.output_function("Stopping text chat...")
            return True

        if command == "/help":
            self.output_function(
                "Commands: /new [title], /speak on|off, /quit"
            )
            return False

        if command == "/new":
            conversation_id = self.service.start_new_conversation(argument or None)
            if conversation_id is None:
                self.output_function("Started a new conversation (history disabled).")
            else:
                self.output_function(f"Started conversation #{conversation_id}.")
            return False

        if command == "/speak":
            if not argument:
                state = "on" if self.speak else "off"
                self.output_function(f"Typed-message speech is {state}.")
                return False

            normalized = argument.lower()
            if normalized in {"on", "true", "yes", "1"}:
                self.speak = True
            elif normalized in {"off", "false", "no", "0"}:
                self.speak = False
            else:
                self.output_function("Usage: /speak on|off")
                return False

            state = "on" if self.speak else "off"
            self.output_function(f"Typed-message speech turned {state}.")
            return False

        self.output_function(f"Unknown command: {command}. Type /help for commands.")
        return False
