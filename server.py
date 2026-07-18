"""Run Project Akira's local FastAPI backend."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Akira FastAPI backend")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address to bind. Keep 127.0.0.1 unless remote access is intentional.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Reload the server when Python files change during development.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    uvicorn.run(
        "app.api:app",
        host=arguments.host,
        port=arguments.port,
        reload=arguments.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
