from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from audio.rvc import (
    RVCConfigurationError,
    RVCConverter,
    RVCDependencyError,
    RVCInferenceError,
    RVCModelConfig,
)
from audio.tts import SynthesizedAudio


class _FakeLoader:
    def __init__(
        self,
        result: tuple[np.ndarray, int] | object | None = None,
        *,
        generate_error: Exception | None = None,
    ) -> None:
        self.result = (
            np.array([-32768, 0, 32767], dtype=np.int16),
            40_000,
        ) if result is None else result
        self.generate_error = generate_error
        self.apply_calls: list[dict[str, object]] = []
        self.generate_calls: list[dict[str, object]] = []
        self.unload_calls = 0

    def apply_conf(self, **kwargs: object) -> None:
        self.apply_calls.append(kwargs)

    def generate_from_cache(self, **kwargs: object) -> object:
        self.generate_calls.append(kwargs)
        if self.generate_error is not None:
            raise self.generate_error
        return self.result

    def unload_models(self) -> None:
        self.unload_calls += 1


class RVCModelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "akira.pth"
        self.index_path = self.root / "akira.index"
        self.model_path.touch()
        self.index_path.touch()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_normalizes_paths_and_options(self) -> None:
        config = RVCModelConfig(
            model_path=self.model_path,
            index_path=self.index_path,
            pitch_algorithm=" rmvpe+ ",
            pitch_shift="12",  # type: ignore[arg-type]
            tag=" Akira ",
        )

        self.assertEqual(config.model_path, self.model_path.resolve())
        self.assertEqual(config.index_path, self.index_path.resolve())
        self.assertEqual(config.pitch_algorithm, "rmvpe+")
        self.assertEqual(config.pitch_shift, 12)
        self.assertEqual(config.tag, "Akira")

    def test_rejects_missing_or_invalid_model_files(self) -> None:
        with self.assertRaisesRegex(RVCConfigurationError, "does not exist"):
            RVCModelConfig(model_path=self.root / "missing.pth")

        wrong_extension = self.root / "voice.bin"
        wrong_extension.touch()
        with self.assertRaisesRegex(RVCConfigurationError, "\\.pth"):
            RVCModelConfig(model_path=wrong_extension)

    def test_rejects_invalid_index_and_ratios(self) -> None:
        wrong_index = self.root / "voice.txt"
        wrong_index.touch()
        with self.assertRaisesRegex(RVCConfigurationError, "\\.index"):
            RVCModelConfig(model_path=self.model_path, index_path=wrong_index)

        with self.assertRaisesRegex(RVCConfigurationError, "between 0.0 and 1.0"):
            RVCModelConfig(model_path=self.model_path, index_influence=1.1)

        with self.assertRaisesRegex(RVCConfigurationError, "cannot be negative"):
            RVCModelConfig(model_path=self.model_path, median_filter_radius=-1)


class RVCConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "voice.pth"
        self.index_path = self.root / "voice.index"
        self.model_path.touch()
        self.index_path.touch()
        self.config = RVCModelConfig(
            model_path=self.model_path,
            index_path=self.index_path,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_model_once_and_converts_audio_buffers(self) -> None:
        fake = _FakeLoader()
        factory_calls: list[dict[str, object]] = []

        def factory(**kwargs: object) -> _FakeLoader:
            factory_calls.append(kwargs)
            return fake

        converter = RVCConverter(
            self.config,
            hubert_path=self.root / "hubert.pt",
            rmvpe_path=self.root / "rmvpe.pt",
            loader_factory=factory,
        )
        source = SynthesizedAudio(
            samples=np.array([-1.0, 0.0, 1.0], dtype=np.float32),
            sample_rate=22_050,
        )

        first = converter.convert(source)
        second = converter.convert(source)

        self.assertTrue(converter.loaded)
        self.assertEqual(len(factory_calls), 1)
        self.assertEqual(
            factory_calls[0],
            {
                "only_cpu": False,
                "hubert_path": str(self.root / "hubert.pt"),
                "rmvpe_path": str(self.root / "rmvpe.pt"),
            },
        )
        self.assertEqual(len(fake.apply_calls), 1)
        self.assertEqual(
            fake.apply_calls[0],
            {
                "tag": "voice",
                "file_model": str(self.model_path.resolve()),
                "pitch_algo": "rmvpe+",
                "pitch_lvl": 0,
                "file_index": str(self.index_path.resolve()),
                "index_influence": 0.66,
                "respiration_median_filtering": 3,
                "envelope_ratio": 0.25,
                "consonant_breath_protection": 0.33,
            },
        )
        self.assertEqual(len(fake.generate_calls), 2)
        payload, sample_rate = fake.generate_calls[0]["audio_data"]  # type: ignore[misc]
        np.testing.assert_array_equal(
            payload,
            np.array([-32767, 0, 32767], dtype=np.int16),
        )
        self.assertEqual(sample_rate, 22_050)
        self.assertEqual(fake.generate_calls[0]["tag"], "voice")
        self.assertEqual(first.sample_rate, 40_000)
        self.assertEqual(second.sample_rate, 40_000)
        np.testing.assert_allclose(
            first.samples,
            np.array([-1.0, 0.0, 32767 / 32768], dtype=np.float32),
        )

    def test_downmixes_multichannel_input_before_inference(self) -> None:
        fake = _FakeLoader(
            result=(np.array([0.25, -0.25], dtype=np.float32), 48_000)
        )
        converter = RVCConverter(self.config, loader_factory=lambda **_: fake)
        source = SynthesizedAudio(
            samples=np.array([[1.0, -1.0], [0.5, 0.5]], dtype=np.float32),
            sample_rate=16_000,
        )

        result = converter.convert(source)

        payload, _ = fake.generate_calls[0]["audio_data"]  # type: ignore[misc]
        np.testing.assert_array_equal(
            payload,
            np.array([0, 16384], dtype=np.int16),
        )
        self.assertEqual(result.sample_rate, 48_000)
        np.testing.assert_allclose(
            result.samples,
            np.array([0.25, -0.25], dtype=np.float32),
        )

    def test_close_unloads_model_and_allows_a_fresh_load(self) -> None:
        first = _FakeLoader()
        second = _FakeLoader()
        loaders = iter((first, second))
        converter = RVCConverter(
            self.config,
            loader_factory=lambda **_: next(loaders),
        )
        source = SynthesizedAudio(np.array([0.0], dtype=np.float32), 16_000)

        converter.convert(source)
        converter.close()
        converter.convert(source)

        self.assertEqual(first.unload_calls, 1)
        self.assertEqual(len(second.apply_calls), 1)

    def test_reports_missing_optional_dependency(self) -> None:
        converter = RVCConverter(self.config)
        source = SynthesizedAudio(np.array([0.0], dtype=np.float32), 16_000)

        with patch(
            "audio.rvc.import_module",
            side_effect=ModuleNotFoundError("infer_rvc_python"),
        ):
            with self.assertRaisesRegex(RVCDependencyError, "requirements-rvc.txt"):
                converter.convert(source)

    def test_wraps_loader_initialization_failures(self) -> None:
        source = SynthesizedAudio(np.array([0.0], dtype=np.float32), 16_000)

        def failing_factory(**_: object) -> object:
            raise RuntimeError("backend initialization failed")

        converter = RVCConverter(self.config, loader_factory=failing_factory)
        with self.assertRaisesRegex(RVCInferenceError, "initialization failed"):
            converter.convert(source)

    def test_wraps_backend_failures_and_invalid_results(self) -> None:
        source = SynthesizedAudio(np.array([0.0], dtype=np.float32), 16_000)
        failing = _FakeLoader(generate_error=RuntimeError("CUDA failed"))
        converter = RVCConverter(self.config, loader_factory=lambda **_: failing)

        with self.assertRaisesRegex(RVCInferenceError, "CUDA failed"):
            converter.convert(source)

        malformed = _FakeLoader(result="not-a-tuple")
        converter = RVCConverter(self.config, loader_factory=lambda **_: malformed)
        with self.assertRaisesRegex(RVCInferenceError, "invalid result"):
            converter.convert(source)


if __name__ == "__main__":
    unittest.main()
