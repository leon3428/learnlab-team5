"""Command-line entry point."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path
from typing import Sequence

import uvicorn

from .app import create_app
from .database import DatasetError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learnlab-team5",
        description="Explore student code-snapshot histories in a local browser.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "data",
        help="ProgSnap2 dataset directory (default: ./data)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local HTTP port (default: 8000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the application in the default browser.",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild the generated SQLite index before starting.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if not 1 <= args.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")

    try:
        app = create_app(args.data_dir, rebuild_index=args.rebuild_index)
    except DatasetError as error:
        raise SystemExit(f"Dataset error: {error}") from error

    index_result = app.state.index_result
    action = "Built" if index_result.rebuilt else "Reused"
    print(f"{action} dataset index: {index_result.path}")
    url = f"http://127.0.0.1:{args.port}"
    print(f"LearnLab History Explorer: {url}")

    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
