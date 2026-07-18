"""Command-line launcher for Project Akira."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from app.conversation import ConversationService
from app.listening_controls import ListeningControlSession
from app.text_input import TextInputSession


def build_parser() -> argparse.ArgumentParser:
    """Create the Project Akira command-line argument parser."""

    parser = argparse.ArgumentParser(description="Project Akira local AI companion")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--text",
        action="store_true",
        help="Open interactive text chat instead of continuous microphone input.",
    )
    mode.add_argument(
        "--message",
        metavar="TEXT",
        help="Send one typed message and exit.",
    )
    mode.add_argument(
        "--controls",
        action="store_true",
        help="Open terminal start/stop microphone controls.",
    )
    parser.add_argument(
        "--no-speak",
        action="store_true",
        help="Do not play TTS for typed messages.",
    )
    return parser


def _print_text_reply(reply: str) -> None:
    print(f"Akira: {reply}")


def main(argv: Sequence[str] | None = None) -> int:
    """Launch voice mode, interactive text mode, or a one-shot message."""

    arguments = build_parser().parse_args(argv)
    typed_mode = arguments.text or arguments.message is not None

    if typed_mode:
        service = ConversationService.from_text_components(
            enable_speech=not arguments.no_speak,
            on_reply=_print_text_reply,
        )
    else:
        service = ConversationService.from_default_components(on_reply=print)

    try:
        if arguments.message is not None:
            result = service.process_text(
                arguments.message,
                speak=not arguments.no_speak,
                source="text",
            )
            return 0 if result is not None else 1

        if arguments.text:
            session = TextInputSession(
                service=service,
                speak=not arguments.no_speak,
            )
            session.run()
            return 0

        if arguments.controls:
            ListeningControlSession(service=service).run()
            return 0

        service.run_voice_loop()
        return 0
    except KeyboardInterrupt:
        service.request_stop()
        print("\nStopping Project Akira...")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
