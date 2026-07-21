from __future__ import annotations

import sys
import unittest

from pathlib import Path

from app.runtime_streams import (
    configure_standard_streams,
    prepare_standard_stream,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeStream:
    def __init__(self):
        self.reconfigure_calls = []

    def reconfigure(self, **kwargs):
        self.reconfigure_calls.append(kwargs)

    def write(self, text):
        return len(text)

    def flush(self):
        return None


class RuntimeStreamTests(unittest.TestCase):
    def test_missing_stream_becomes_utf8_writable_sink(self):
        stream = prepare_standard_stream(None)
        try:
            self.assertEqual(stream.encoding.casefold(), "utf-8")
            self.assertGreater(stream.write("Listening\n"), 0)
            stream.flush()
        finally:
            stream.close()

    def test_existing_stream_is_reconfigured(self):
        stream = FakeStream()

        result = prepare_standard_stream(stream)

        self.assertIs(result, stream)
        self.assertEqual(
            stream.reconfigure_calls,
            [{"encoding": "utf-8", "errors": "replace"}],
        )

    def test_pythonw_style_missing_streams_are_supported(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        replacement_stdout = None
        replacement_stderr = None

        try:
            sys.stdout = None
            sys.stderr = None

            configure_standard_streams()

            replacement_stdout = sys.stdout
            replacement_stderr = sys.stderr
            replacement_stdout.write("Listening\n")
            replacement_stderr.write("diagnostic\n")
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

            if replacement_stdout is not None:
                replacement_stdout.close()
            if replacement_stderr is not None:
                replacement_stderr.close()

        self.assertIsNotNone(replacement_stdout)
        self.assertIsNotNone(replacement_stderr)

    def test_streams_are_configured_before_desktop_import(self):
        source = (ROOT / "desktop.py").read_text(encoding="utf-8")

        configure_position = source.index("configure_standard_streams()")
        app_import_position = source.index("from app.desktop import main")

        self.assertLess(configure_position, app_import_position)


if __name__ == "__main__":
    unittest.main()
