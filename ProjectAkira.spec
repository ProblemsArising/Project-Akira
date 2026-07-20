# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build for Project Akira on Windows."""

from __future__ import annotations

import os
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

ROOT = Path(SPECPATH).resolve()
CONSOLE = os.environ.get("AKIRA_BUILD_CONSOLE", "0") == "1"


def optional_collect(function, package: str):
    try:
        return function(package)
    except Exception:
        return []


datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE"), "."),
]
datas += optional_collect(collect_data_files, "webview")
datas += optional_collect(copy_metadata, "pywebview")
datas += optional_collect(copy_metadata, "faster-whisper")
datas += optional_collect(copy_metadata, "ctranslate2")

binaries = []
for package in (
    "ctranslate2",
    "nvidia.cublas",
    "nvidia.cudnn",
    "nvidia.cuda_runtime",
    "av",
):
    binaries += optional_collect(collect_dynamic_libs, package)

hiddenimports = [
    # The desktop launcher gives Uvicorn this module as the dynamic import
    # string ``app.api:app``, so PyInstaller cannot discover it statically.
    "app.api",
    # Uvicorn selects these implementations dynamically.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # Windows desktop integrations selected at runtime.
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "pystray._win32",
    "pyttsx3.drivers.sapi5",
    "PIL.Image",
    "PIL.ImageDraw",
]
for package in (
    "webview",
    "faster_whisper",
    "ctranslate2",
    "huggingface_hub",
    "tokenizers",
    "pythonosc",
):
    hiddenimports += optional_collect(collect_submodules, package)

# pywebview can see multiple optional GUI toolkits in a developer environment.
# Project Akira uses the native Windows backend, so avoid bundling unused Qt,
# GTK, Cocoa, and CEF runtimes if they happen to be installed.
excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "qtpy",
    "gi",
    "cocoa",
    "cefpython3",
]

a = Analysis(
    [str(ROOT / "desktop.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ProjectAkira",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=CONSOLE,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ProjectAkira",
)
