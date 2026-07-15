import os
import sys
from pathlib import Path


def add_cuda_dll_dirs():
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"

    dll_dirs = [
        site_packages / "nvidia" / "cublas" / "bin",
        site_packages / "nvidia" / "cudnn" / "bin",
        site_packages / "nvidia" / "cuda_runtime" / "bin",

        # Fallbacks if torch has CUDA DLLs installed
        site_packages / "torch" / "lib",
        site_packages / "~orch" / "lib",
    ]

    for folder in dll_dirs:
        if folder.exists():
            os.add_dll_directory(str(folder))
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ["PATH"]
            print(f"✅ Added DLL path: {folder}")


add_cuda_dll_dirs()

from faster_whisper import WhisperModel


AUDIO_FILE = "input.wav"

whisper = WhisperModel(
    "base",
    device="cuda",
    compute_type="float16"
)


def transcribe():
    segments, _ = whisper.transcribe(AUDIO_FILE)
    text = " ".join(segment.text.strip() for segment in segments)
    print("You said:", text)
    return text