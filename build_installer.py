"""Build the Project Akira Windows installer with Inno Setup."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parent
APP_BUILD_SCRIPT = ROOT / "build_windows.py"
APP_OUTPUT_DIR = ROOT / "dist" / "ProjectAkira"
APP_EXECUTABLE = APP_OUTPUT_DIR / "ProjectAkira.exe"
INSTALLER_SCRIPT = ROOT / "installer" / "ProjectAkira.iss"
INSTALLER_OUTPUT_DIR = ROOT / "dist" / "installer"
DEFAULT_VERSION = os.environ.get("AKIRA_VERSION", "0.3.0-dev")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help="Application version embedded in the installer filename and metadata.",
    )
    parser.add_argument(
        "--iscc",
        help="Explicit path to Inno Setup's ISCC.exe compiler.",
    )
    parser.add_argument(
        "--skip-app-build",
        action="store_true",
        help="Reuse the existing dist\\ProjectAkira application folder.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip unit tests before creating the application build.",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Package the diagnostic console application build.",
    )
    parser.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep prior PyInstaller work directories.",
    )
    return parser


def run(command: list[str]) -> None:
    print("+", subprocess.list2cmdline(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def safe_filename_version(version: str) -> str:
    """Return a version safe to use inside a Windows filename."""

    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(version).strip())
    cleaned = cleaned.strip(" .-_")
    return cleaned or "dev"


def _inno_candidates(environment: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []

    configured = str(environment.get("INNO_SETUP_COMPILER", "")).strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    for command in ("ISCC.exe", "iscc.exe", "iscc"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(Path(resolved))

    roots = [
        environment.get("LOCALAPPDATA"),
        environment.get("ProgramFiles"),
        environment.get("ProgramFiles(x86)"),
    ]
    for raw_root in roots:
        if not raw_root:
            continue
        base = Path(raw_root)
        for folder in (
            "Programs/Inno Setup 7",
            "Inno Setup 7",
            "Inno Setup 6",
        ):
            candidates.append(base / folder / "ISCC.exe")

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(str(candidate)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def find_inno_compiler(
    explicit: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Locate ISCC.exe from an explicit path, PATH, or normal install folders."""

    environment = os.environ if environment is None else environment
    candidates: list[Path] = []

    if explicit:
        supplied = Path(explicit).expanduser()
        candidates.append(supplied)
        resolved = shutil.which(str(explicit))
        if resolved:
            candidates.append(Path(resolved))

    candidates.extend(_inno_candidates(environment))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    raise FileNotFoundError(
        "Inno Setup's ISCC.exe was not found. Install Inno Setup 7 with "
        "`winget install --id JRSoftware.InnoSetup.7 -e -s winget -i`, "
        "then rerun this command. You can also pass `--iscc C:\\path\\ISCC.exe`."
    )


def verify_application_build() -> None:
    if not APP_EXECUTABLE.is_file():
        raise FileNotFoundError(
            f"{APP_EXECUTABLE} does not exist. Run without --skip-app-build "
            "or create the PyInstaller build first."
        )
    if not (APP_OUTPUT_DIR / "BUILD_INFO.json").is_file():
        raise FileNotFoundError(
            f"{APP_OUTPUT_DIR / 'BUILD_INFO.json'} is missing. Rebuild Project Akira "
            "before compiling the installer."
        )


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if os.name != "nt":
        print("Project Akira's Windows installer must be created on Windows.")
        return 2

    version = str(arguments.version).strip()
    if not version or any(character in version for character in ('"', "\r", "\n")):
        raise ValueError("Installer version must be non-empty and contain no quotes or newlines.")

    if arguments.skip_app_build:
        if not arguments.skip_tests:
            run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
    else:
        application_command = [
            sys.executable,
            str(APP_BUILD_SCRIPT),
            "--version",
            version,
        ]
        if arguments.skip_tests:
            application_command.append("--skip-tests")
        if arguments.console:
            application_command.append("--console")
        if arguments.no_clean:
            application_command.append("--no-clean")
        run(application_command)

    verify_application_build()
    compiler = find_inno_compiler(arguments.iscc)
    INSTALLER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    filename_version = safe_filename_version(version)
    output_basename = f"ProjectAkira-Setup-{filename_version}"
    command = [
        str(compiler),
        "/Qp",
        f'/DAppVersion="{version}"',
        f"/O{INSTALLER_OUTPUT_DIR}",
        f"/F{output_basename}",
        str(INSTALLER_SCRIPT),
    ]
    run(command)

    installer = INSTALLER_OUTPUT_DIR / f"{output_basename}.exe"
    if not installer.is_file():
        raise RuntimeError(
            f"Inno Setup completed but the expected installer was not created: {installer}"
        )

    size = installer.stat().st_size / (1024 ** 2)
    print(f"\nInstaller complete: {installer}")
    print(f"Installer size: {size:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
