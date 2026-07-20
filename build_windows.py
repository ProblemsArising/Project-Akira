"""Build the Project Akira Windows one-folder distribution."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
BUILD_DIR = ROOT / "build"
SPEC_FILE = ROOT / "ProjectAkira.spec"
OUTPUT_DIR = DIST_DIR / "ProjectAkira"
EXECUTABLE = OUTPUT_DIR / "ProjectAkira.exe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--console", action="store_true", help="Keep a console window for diagnostics.")
    parser.add_argument("--skip-tests", action="store_true", help="Skip the unit-test suite before building.")
    parser.add_argument("--no-clean", action="store_true", help="Keep prior PyInstaller work directories.")
    parser.add_argument("--version", default=os.environ.get("AKIRA_VERSION", "0.3.0-dev"))
    return parser


def run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if os.name != "nt":
        print("Project Akira's Windows build must be created on Windows.")
        return 2

    if not arguments.skip_tests:
        run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])

    if not arguments.no_clean:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)

    environment = os.environ.copy()
    environment["AKIRA_BUILD_CONSOLE"] = "1" if arguments.console else "0"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC_FILE),
    ]
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)

    if not EXECUTABLE.exists():
        raise RuntimeError(f"PyInstaller completed but {EXECUTABLE} was not created.")

    build_info = {
        "name": "Project Akira",
        "version": str(arguments.version),
        "commit": git_commit(),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "architecture": platform.machine(),
        "console": bool(arguments.console),
    }
    (OUTPUT_DIR / "BUILD_INFO.json").write_text(
        json.dumps(build_info, indent=2) + "\n", encoding="utf-8"
    )

    size = sum(path.stat().st_size for path in OUTPUT_DIR.rglob("*") if path.is_file())
    print(f"\nBuild complete: {EXECUTABLE}")
    print(f"Distribution size: {size / (1024 ** 2):.1f} MiB")
    print("Copy the entire dist\\ProjectAkira folder when testing or distributing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
