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
    mode.add_argument(
        "--devices",
        action="store_true",
        help="List normal Windows microphone/output choices.",
    )
    mode.add_argument(
        "--devices-all",
        action="store_true",
        help="List every raw PortAudio device for advanced troubleshooting.",
    )
    parser.add_argument(
        "--no-speak",
        action="store_true",
        help="Do not play TTS for typed messages.",
    )
    parser.add_argument(
        "--set-input-device",
        metavar="DEVICE",
        help="Select a microphone by index/name, or use 'default'.",
    )
    parser.add_argument(
        "--set-output-device",
        metavar="DEVICE",
        help="Select a speaker/virtual cable by index/name, or use 'default'.",
    )
    parser.add_argument(
        "--test-output",
        action="store_true",
        help="Speak a short test through the configured output device.",
    )
    return parser


def _print_text_reply(reply: str) -> None:
    print(f"Akira: {reply}")


def _audio_device_command(arguments: argparse.Namespace) -> int:
    from audio.devices import (
        AudioDeviceError,
        configure_audio_devices,
        format_audio_device_table,
        list_audio_devices,
        resolve_audio_device,
    )

    try:
        devices = list_audio_devices()
        if arguments.set_input_device is not None or arguments.set_output_device is not None:
            changes = {}
            if arguments.set_input_device is not None:
                changes["input_device"] = arguments.set_input_device
            if arguments.set_output_device is not None:
                changes["output_device"] = arguments.set_output_device
            configure_audio_devices(devices=devices, **changes)
            print("✅ Audio device settings saved.\n")

        print(format_audio_device_table(devices=devices, show_all=arguments.devices_all))

        if arguments.test_output:
            from audio.tts import create_speaker
            from config.settings import get_settings

            selection = resolve_audio_device(
                get_settings(reload=True).audio.output_device,
                "output",
                devices=devices,
            )
            speaker = create_speaker(
                output_device=None if selection is None else selection.index
            )
            speaker("Project Akira audio output test.")
            print("✅ Output test completed.")

        return 0
    except AudioDeviceError as error:
        print(f"❌ {error}")
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Launch voice mode, interactive text mode, or a one-shot message."""

    arguments = build_parser().parse_args(argv)
    device_mode = (
        arguments.devices
        or arguments.devices_all
        or arguments.set_input_device is not None
        or arguments.set_output_device is not None
        or arguments.test_output
    )
    if device_mode:
        return _audio_device_command(arguments)

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
