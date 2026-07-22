from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from app.model_downloads import (
    ModelDownloadError,
    ModelDownloadManager,
)


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, *, status: int = 200, headers=None):
        super().__init__(payload)
        self.status = status
        self.headers = dict(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class ImmediateThread:
    def __init__(self, *, target, args, **_kwargs):
        self.target = target
        self.args = args
        self.started = False

    def start(self):
        self.started = True
        self.target(*self.args)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class DeferredThread:
    def __init__(self, *, target, args, **_kwargs):
        self.target = target
        self.args = args
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        return None


class ModelDownloadManagerTests(unittest.TestCase):
    def test_download_verifies_hash_and_atomically_finishes(self):
        payload = b"GGUF test model contents"
        checksum = hashlib.sha256(payload).hexdigest()
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return FakeResponse(
                payload,
                headers={"Content-Length": str(len(payload))},
            )

        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(
                directory,
                opener=opener,
                thread_factory=ImmediateThread,
                chunk_size=4,
            )
            job = manager.start_download(
                url="https://models.example/akira.Q4_K_M.gguf",
                sha256=checksum,
            )

            finished = manager.get_job(job.id)
            target = Path(directory) / "akira.Q4_K_M.gguf"
            self.assertEqual(finished.status, "completed")
            self.assertEqual(finished.downloaded_bytes, len(payload))
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(target.with_suffix(".gguf.part").exists())
            self.assertEqual(requests[0].full_url, job.url)

    def test_existing_partial_file_is_resumed_with_range_request(self):
        requests = []

        def opener(request, **_kwargs):
            requests.append(request)
            return FakeResponse(
                b"UFxx",
                status=206,
                headers={
                    "Content-Length": "4",
                    "Content-Range": "bytes 2-5/6",
                },
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "resume.gguf.part").write_bytes(b"GG")
            manager = ModelDownloadManager(
                root,
                opener=opener,
                thread_factory=ImmediateThread,
            )

            job = manager.start_download(
                url="https://models.example/resume.gguf",
            )

            self.assertEqual(manager.get_job(job.id).status, "completed")
            self.assertEqual((root / "resume.gguf").read_bytes(), b"GGUFxx")
            self.assertEqual(requests[0].get_header("Range"), "bytes=2-")

    def test_hash_mismatch_removes_partial_file_and_reports_failure(self):
        payload = b"GGUF not the expected model"

        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(
                directory,
                opener=lambda *_args, **_kwargs: FakeResponse(payload),
                thread_factory=ImmediateThread,
            )
            job = manager.start_download(
                url="https://models.example/bad.gguf",
                sha256="0" * 64,
            )

            failed = manager.get_job(job.id)
            self.assertEqual(failed.status, "failed")
            self.assertIn("SHA-256", failed.error)
            self.assertFalse((Path(directory) / "bad.gguf").exists())
            self.assertFalse((Path(directory) / "bad.gguf.part").exists())

    def test_truncated_response_keeps_partial_for_resume(self):
        payload = b"GGUF short"

        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(
                directory,
                opener=lambda *_args, **_kwargs: FakeResponse(
                    payload,
                    headers={"Content-Length": str(len(payload) + 10)},
                ),
                thread_factory=ImmediateThread,
            )
            job = manager.start_download(
                url="https://models.example/truncated.gguf",
            )

            failed = manager.get_job(job.id)
            self.assertEqual(failed.status, "failed")
            self.assertIn("expected", failed.error)
            self.assertFalse((Path(directory) / "truncated.gguf").exists())
            self.assertEqual(
                (Path(directory) / "truncated.gguf.part").read_bytes(),
                payload,
            )

    def test_non_gguf_response_is_removed_and_reported(self):
        payload = b"<html>not a model</html>"

        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(
                directory,
                opener=lambda *_args, **_kwargs: FakeResponse(payload),
                thread_factory=ImmediateThread,
            )
            job = manager.start_download(
                url="https://models.example/not-model.gguf",
            )

            failed = manager.get_job(job.id)
            self.assertEqual(failed.status, "failed")
            self.assertIn("valid GGUF", failed.error)
            self.assertFalse((Path(directory) / "not-model.gguf").exists())
            self.assertFalse((Path(directory) / "not-model.gguf.part").exists())

    def test_cancel_queued_download(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(
                directory,
                opener=lambda *_args, **_kwargs: FakeResponse(b"model"),
                thread_factory=DeferredThread,
            )
            job = manager.start_download(
                url="https://models.example/cancel.gguf",
            )

            cancelled = manager.cancel_download(job.id)

            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(manager.get_job(job.id).status, "cancelled")

    def test_filename_url_and_checksum_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ModelDownloadManager(directory, thread_factory=DeferredThread)

            invalid_cases = (
                {"url": "file:///tmp/model.gguf"},
                {"url": "https://example/model.bin"},
                {"url": "https://example/model.gguf", "filename": "../model.gguf"},
                {"url": "https://example/model.gguf", "filename": "bad:name.gguf"},
                {"url": "https://example/model.gguf", "filename": "CON.gguf"},
                {"url": "https://example/model.gguf", "filename": "LPT1.extra.gguf"},
                {"url": "https://example/model.gguf", "filename": "model.gguf."},
                {"url": "https://user:secret@example/model.gguf"},
                {"url": "https://example/model.gguf", "sha256": "abc"},
            )
            for values in invalid_cases:
                with self.subTest(values=values), self.assertRaises(ModelDownloadError):
                    manager.start_download(**values)

    def test_list_resolve_and_delete_local_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.gguf"
            second = root / "second.gguf"
            first.write_bytes(b"one")
            second.write_bytes(b"two-two")
            manager = ModelDownloadManager(root)

            models = manager.list_models(active_path=second)

            self.assertEqual([item.filename for item in models], ["first.gguf", "second.gguf"])
            self.assertFalse(models[0].active)
            self.assertTrue(models[1].active)
            self.assertEqual(manager.resolve_model("first.gguf"), first.resolve())

            with self.assertRaisesRegex(ModelDownloadError, "active"):
                manager.delete_model("second.gguf", active_path=second)

            manager.delete_model("first.gguf", active_path=second)
            self.assertFalse(first.exists())


if __name__ == "__main__":
    unittest.main()
