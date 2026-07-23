from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from app.llama_cpp_runtime import (
    LLAMA_CPP_RUNTIME_VERSION,
    LlamaCppRuntimeError,
    LlamaCppRuntimeManager,
    RUNTIME_VARIANTS,
    RuntimeAsset,
    RuntimeVariant,
    detect_recommended_variant,
    find_managed_llama_cpp_executable,
    safe_extract_zip,
)


class FakeResponse(BytesIO):
    status = 200

    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


class ImmediateThread:
    def __init__(self, *, target, **_kwargs) -> None:
        self.target = target
        self.alive = False

    def start(self) -> None:
        self.alive = True
        try:
            self.target()
        finally:
            self.alive = False

    def join(self, _timeout=None) -> None:
        return None

    def is_alive(self) -> bool:
        return self.alive


def zip_payload(files: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for filename, payload in files.items():
            package.writestr(filename, payload)
    return output.getvalue()


class LlamaCppRuntimeTests(unittest.TestCase):
    def test_pinned_release_manifest_contains_verified_official_variants(self):
        self.assertEqual(LLAMA_CPP_RUNTIME_VERSION, "b10080")
        self.assertEqual(set(RUNTIME_VARIANTS), {"cuda12", "vulkan", "cpu"})
        self.assertEqual(len(RUNTIME_VARIANTS["cuda12"].assets), 2)
        for variant in RUNTIME_VARIANTS.values():
            for asset in variant.assets:
                self.assertIn(
                    f"/releases/download/{LLAMA_CPP_RUNTIME_VERSION}/",
                    asset.url,
                )
                self.assertEqual(len(asset.sha256), 64)
                int(asset.sha256, 16)

    def test_recommends_cuda_when_nvidia_smi_reports_a_gpu(self):
        def runner(command, **_kwargs):
            self.assertEqual(command[0], "nvidia-smi")
            return SimpleNamespace(returncode=0, stdout="NVIDIA RTX Test\n")

        self.assertEqual(
            detect_recommended_variant(
                runner=runner,
                system="Windows",
                machine="AMD64",
            ),
            "cuda12",
        )

    def test_recommends_vulkan_when_nvidia_is_unavailable(self):
        def runner(_command, **_kwargs):
            raise FileNotFoundError

        self.assertEqual(
            detect_recommended_variant(
                runner=runner,
                system="Windows",
                machine="AMD64",
            ),
            "vulkan",
        )

    def test_safe_extract_rejects_zip_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            archive.write_bytes(zip_payload({"../escape.txt": b"no"}))
            with self.assertRaisesRegex(
                LlamaCppRuntimeError,
                "outside the staging directory",
            ):
                safe_extract_zip(archive, root / "extract")
            self.assertFalse((root / "escape.txt").exists())

    def test_install_validates_activates_and_removes_managed_runtime(self):
        archive_bytes = zip_payload(
            {
                "llama-server.exe": b"fake executable",
                "nested/ggml-base.dll": b"fake dependency",
                "llama-results.exe": b"optional utility",
                "llama-tokenize.exe": b"optional utility",
            }
        )
        digest = hashlib.sha256(archive_bytes).hexdigest()
        variant = RuntimeVariant(
            id="test",
            name="Test runtime",
            description="Test-only runtime.",
            assets=(
                RuntimeAsset(
                    filename="test-runtime.zip",
                    url="https://example.invalid/test-runtime.zip",
                    sha256=digest,
                ),
            ),
        )
        activated: list[Path] = []
        removed: list[tuple[Path, str | None]] = []

        def activate(executable: Path) -> str:
            activated.append(executable)
            return "C:/custom-llama/llama-server.exe"

        def opener(_request, **_kwargs):
            return FakeResponse(archive_bytes)

        def runner(command, **_kwargs):
            if command[0] == "nvidia-smi":
                raise FileNotFoundError
            self.assertEqual(command[1], "--list-devices")
            return SimpleNamespace(returncode=0, stdout="CPU\n")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtimes"
            manager = LlamaCppRuntimeManager(
                root,
                variants={"test": variant},
                opener=opener,
                runner=runner,
                thread_factory=ImmediateThread,
                activate=activate,
                remove_callback=lambda executable, previous: removed.append(
                    (executable, previous)
                ),
                system="Windows",
                machine="AMD64",
            )

            job = manager.start_install("test")
            self.assertEqual(job.status, "installed")
            snapshot = manager.snapshot()
            self.assertTrue(snapshot["installed"])
            self.assertEqual(snapshot["installed_variant"], "test")
            executable = Path(snapshot["executable"])
            self.assertTrue(executable.is_file())
            self.assertEqual(activated, [executable])
            self.assertTrue((executable.parent / "llama.cpp-LICENSE.txt").is_file())
            self.assertTrue((executable.parent / "ggml-base.dll").is_file())
            self.assertFalse((executable.parent / "llama-results.exe").exists())
            self.assertFalse((executable.parent / "llama-tokenize.exe").exists())
            self.assertEqual(
                find_managed_llama_cpp_executable(root),
                executable,
            )

            older = root / "b9999" / "cpu"
            older.mkdir(parents=True)
            (older / "llama-server.exe").write_bytes(b"older runtime")

            removed_snapshot = manager.remove()
            self.assertFalse(removed_snapshot["installed"])
            self.assertEqual(
                removed,
                [(executable, "C:/custom-llama/llama-server.exe")],
            )
            self.assertFalse(executable.exists())
            self.assertFalse(older.exists())
            self.assertIsNone(find_managed_llama_cpp_executable(root))

    def test_hash_failure_does_not_activate_runtime(self):
        archive_bytes = zip_payload({"llama-server.exe": b"fake"})
        variant = RuntimeVariant(
            id="bad",
            name="Bad hash",
            description="Test-only runtime.",
            assets=(
                RuntimeAsset(
                    filename="bad.zip",
                    url="https://example.invalid/bad.zip",
                    sha256="0" * 64,
                ),
            ),
        )
        activated: list[Path] = []

        with tempfile.TemporaryDirectory() as temporary:
            manager = LlamaCppRuntimeManager(
                Path(temporary) / "runtimes",
                variants={"bad": variant},
                opener=lambda *_args, **_kwargs: FakeResponse(archive_bytes),
                runner=lambda *_args, **_kwargs: SimpleNamespace(
                    returncode=0, stdout="CPU\n"
                ),
                thread_factory=ImmediateThread,
                activate=activated.append,
                system="Windows",
                machine="AMD64",
            )
            job = manager.start_install("bad")
            self.assertEqual(job.status, "failed")
            self.assertIn("SHA-256 verification failed", job.error)
            self.assertEqual(activated, [])
            self.assertFalse(manager.snapshot()["installed"])


if __name__ == "__main__":
    unittest.main()
