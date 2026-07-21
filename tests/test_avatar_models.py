from __future__ import annotations

import json
import struct
import tempfile
import unittest

from pathlib import Path

from app.avatar_models import (
    AvatarModelStore,
    AvatarModelValidationError,
)


def make_vrm(*, version: str = "1.0") -> bytes:
    if version == "0.x":
        extensions = {"VRM": {"specVersion": "0.0"}}
        extensions_used = ["VRM"]
    else:
        extensions = {"VRMC_vrm": {"specVersion": version}}
        extensions_used = ["VRMC_vrm"]

    document = {
        "asset": {"version": "2.0"},
        "extensionsUsed": extensions_used,
        "extensions": extensions,
        "scenes": [{"nodes": []}],
        "scene": 0,
        "nodes": [],
    }
    encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
    encoded += b" " * ((4 - len(encoded) % 4) % 4)
    total_length = 12 + 8 + len(encoded)
    return (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
    )


class AvatarModelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.store = AvatarModelStore(
            root / "avatar" / "model.vrm",
            root / "avatar" / "model.json",
            max_file_bytes=1024 * 1024,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_save_status_and_delete_vrm_1_model(self) -> None:
        payload = make_vrm(version="1.0")

        saved = self.store.save("Akira Model.vrm", payload)

        self.assertTrue(saved.configured)
        self.assertEqual(saved.filename, "Akira Model.vrm")
        self.assertEqual(saved.size_bytes, len(payload))
        self.assertEqual(saved.vrm_version, "1.0")
        self.assertEqual(saved.model_url, "/api/avatar/model/file")
        self.assertEqual(self.store.model_path.read_bytes(), payload)

        restored = self.store.status()
        self.assertEqual(restored, saved)

        self.assertTrue(self.store.delete())
        self.assertFalse(self.store.status().configured)
        self.assertFalse(self.store.delete())

    def test_vrm_zero_model_is_supported(self) -> None:
        saved = self.store.save("legacy.vrm", make_vrm(version="0.x"))
        self.assertEqual(saved.vrm_version, "0.x")

    def test_invalid_extension_and_non_vrm_glb_are_rejected(self) -> None:
        with self.assertRaisesRegex(AvatarModelValidationError, ".vrm extension"):
            self.store.save("avatar.glb", make_vrm())

        document = {
            "asset": {"version": "2.0"},
            "scenes": [{"nodes": []}],
            "scene": 0,
            "nodes": [],
        }
        encoded = json.dumps(document).encode("utf-8")
        encoded += b" " * ((4 - len(encoded) % 4) % 4)
        payload = (
            struct.pack("<4sII", b"glTF", 2, 20 + len(encoded))
            + struct.pack("<II", len(encoded), 0x4E4F534A)
            + encoded
        )
        with self.assertRaisesRegex(AvatarModelValidationError, "VRM avatar metadata"):
            self.store.save("not-vrm.vrm", payload)

    def test_oversized_and_truncated_files_are_rejected(self) -> None:
        tiny_store = AvatarModelStore(
            self.store.model_path,
            self.store.metadata_path,
            max_file_bytes=16,
        )
        with self.assertRaisesRegex(AvatarModelValidationError, "smaller"):
            tiny_store.save("large.vrm", make_vrm())

        with self.assertRaisesRegex(AvatarModelValidationError, "incomplete"):
            self.store.save("broken.vrm", b"glTF")

    def test_corrupt_metadata_does_not_hide_valid_model(self) -> None:
        self.store.save("avatar.vrm", make_vrm())
        self.store.metadata_path.write_text("not-json", encoding="utf-8")

        status = self.store.status()

        self.assertTrue(status.configured)
        self.assertEqual(status.filename, "model.vrm")
        self.assertEqual(status.vrm_version, "1.0")


if __name__ == "__main__":
    unittest.main()
