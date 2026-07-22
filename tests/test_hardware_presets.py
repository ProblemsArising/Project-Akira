from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from app.hardware_presets import (
    GGUFModelProfile,
    GPUInfo,
    HardwarePresetValues,
    HardwareProfile,
    build_hardware_presets,
    detect_hardware,
    estimate_memory_usage,
    get_hardware_preset,
    inspect_gguf_model,
    load_hardware_preset_preferences,
    parse_nvidia_smi_output,
    reset_hardware_preset,
    save_hardware_preset,
)

GIB = 1024**3
MIB = 1024**2


def _gguf_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack("<Q", len(encoded)) + encoded


def _write_test_gguf(path: Path) -> None:
    entries = [
        ("general.architecture", 8, _gguf_string("llama")),
        ("llama.block_count", 4, struct.pack("<I", 32)),
        ("llama.embedding_length", 4, struct.pack("<I", 4096)),
        ("llama.attention.head_count", 4, struct.pack("<I", 32)),
        ("llama.attention.head_count_kv", 4, struct.pack("<I", 8)),
    ]
    payload = bytearray(b"GGUF")
    payload.extend(struct.pack("<IQQ", 3, 0, len(entries)))
    for key, value_type, value in entries:
        payload.extend(_gguf_string(key))
        payload.extend(struct.pack("<I", value_type))
        payload.extend(value)
    payload.extend(b"\0" * 4096)
    path.write_bytes(payload)


class HardwarePresetTests(unittest.TestCase):
    def test_parses_every_nvidia_smi_gpu_and_totals_vram(self):
        gpus = parse_nvidia_smi_output(
            "NVIDIA GeForce RTX 5070, 12282\n"
            "NVIDIA GeForce RTX 3070 Ti, 8192\n"
            "invalid row\n"
        )
        profile = HardwareProfile(
            logical_cpu_count=20,
            total_memory_bytes=64 * GIB,
            gpus=gpus,
        )

        self.assertEqual(len(gpus), 2)
        self.assertEqual(gpus[0].name, "NVIDIA GeForce RTX 5070")
        self.assertEqual(gpus[0].memory_bytes, 12282 * MIB)
        self.assertEqual(profile.total_gpu_memory_bytes, (12282 + 8192) * MIB)

    def test_detection_accepts_deterministic_values(self):
        profile = detect_hardware(
            logical_cpu_count=20,
            total_memory_bytes=64 * GIB,
            nvidia_smi_output="RTX 5070, 12282\nRTX 3070 Ti, 8192\n",
        )

        self.assertEqual(profile.logical_cpu_count, 20)
        self.assertEqual(profile.total_memory_bytes, 64 * GIB)
        self.assertEqual([gpu.name for gpu in profile.gpus], ["RTX 5070", "RTX 3070 Ti"])
        self.assertEqual(profile.notes, ())

    def test_builds_four_vram_tiers_and_recommends_from_total_vram(self):
        profile = HardwareProfile(
            logical_cpu_count=20,
            total_memory_bytes=64 * GIB,
            gpus=(GPUInfo("GPU A", 12 * GIB), GPUInfo("GPU B", 8 * GIB)),
        )
        catalog = build_hardware_presets(
            profile,
            model_profile=GGUFModelProfile(
                size_bytes=8 * GIB,
                layer_count=40,
                kv_bytes_per_token=256 * 1024,
            ),
        )

        self.assertEqual([preset.id for preset in catalog.presets], ["low", "medium", "high", "ultra"])
        self.assertEqual(catalog.recommended_id, "ultra")
        self.assertEqual(catalog.default_id, "ultra")
        self.assertTrue(get_hardware_preset(catalog, "ultra").recommended)
        self.assertEqual(get_hardware_preset(catalog, "medium").target_vram_bytes, 8 * GIB)

    def test_expected_memory_changes_with_context_and_layer_count(self):
        model = GGUFModelProfile(
            size_bytes=8 * GIB,
            layer_count=40,
            kv_bytes_per_token=256 * 1024,
        )
        cpu_vram, cpu_ram = estimate_memory_usage(
            model,
            HardwarePresetValues(context_size=4096, gpu_layers="0", threads=4),
        )
        gpu_vram, gpu_ram = estimate_memory_usage(
            model,
            HardwarePresetValues(context_size=8192, gpu_layers="all", threads=8),
        )

        self.assertEqual(cpu_vram, 0)
        self.assertGreater(cpu_ram, 8 * GIB)
        self.assertGreater(gpu_vram, 8 * GIB)
        self.assertEqual(gpu_ram, GIB)

    def test_reads_layer_and_attention_metadata_from_gguf(self):
        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "test.gguf"
            _write_test_gguf(model_path)
            profile = inspect_gguf_model(model_path)

        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.layer_count, 32)
        self.assertEqual(profile.kv_bytes_per_token, 2 * 32 * 1024 * 2)
        self.assertGreater(profile.size_bytes, 4096)

    def test_saved_edits_and_default_persist_then_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware_presets.json"
            values = HardwarePresetValues(12288, "24", 10)
            saved = save_hardware_preset(
                "high",
                values,
                set_default=True,
                path=path,
            )
            loaded = load_hardware_preset_preferences(path)

            self.assertEqual(saved.default_id, "high")
            self.assertEqual(loaded.default_id, "high")
            self.assertEqual(loaded.overrides["high"], values)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["presets"]["high"]["gpu_layers"], "24")

            reset_hardware_preset("high", path=path)
            reset = load_hardware_preset_preferences(path)
            self.assertNotIn("high", reset.overrides)
            self.assertEqual(reset.default_id, "high")

    def test_exact_saved_values_mark_current_and_default(self):
        profile = HardwareProfile(
            logical_cpu_count=16,
            total_memory_bytes=32 * GIB,
            gpus=(GPUInfo("RTX", 12 * GIB),),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "presets.json"
            save_hardware_preset(
                "high",
                HardwarePresetValues(12288, "20", 6),
                set_default=True,
                path=path,
            )
            catalog = build_hardware_presets(
                profile,
                preferences=load_hardware_preset_preferences(path),
                current_context_size=12288,
                current_gpu_layers="20",
                current_threads=6,
            )

        high = get_hardware_preset(catalog, "high")
        self.assertEqual(catalog.current_id, "high")
        self.assertTrue(high.current)
        self.assertTrue(high.default)
        self.assertTrue(high.customized)

    def test_unknown_preset_raises_key_error(self):
        catalog = build_hardware_presets(
            HardwareProfile(logical_cpu_count=8, total_memory_bytes=16 * GIB)
        )
        with self.assertRaises(KeyError):
            get_hardware_preset(catalog, "does-not-exist")


if __name__ == "__main__":
    unittest.main()
