"""Command-line launcher for Project Akira."""

from app.conversation import ConversationService


def main() -> None:
    service = ConversationService.from_default_components()

    try:
        service.run_voice_loop()
    except KeyboardInterrupt:
        service.request_stop()
        print("\n👋 Stopping Project Akira...")


if __name__ == "__main__":
    main()
