"""Print the node opcodes used by the ASTs in CodeStates.csv."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


class OpcodeScanError(RuntimeError):
    """Raised when a CodeStates file cannot be scanned."""


def iter_node_types(value: Any) -> Iterable[str]:
    """Recursively yield every string-valued ``type`` field in an AST."""

    if isinstance(value, dict):
        node_type = value.get("type")
        if isinstance(node_type, str):
            yield node_type
        for child in value.values():
            yield from iter_node_types(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_node_types(child)


def collect_opcodes(code_states_path: Path | str) -> tuple[Counter[str], int]:
    """Return opcode occurrence counts and the number of invalid nonblank ASTs."""

    path = Path(code_states_path)
    if not path.is_file():
        raise OpcodeScanError(f"Code-state file not found: {path}")

    opcodes: Counter[str] = Counter()
    invalid_asts = 0
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or ())
        if "Code" not in columns:
            raise OpcodeScanError(f"{path} does not contain a Code column")

        for row in reader:
            ast_text = row["Code"].strip()
            if not ast_text:
                continue
            try:
                ast = json.loads(ast_text)
            except json.JSONDecodeError:
                invalid_asts += 1
                continue
            opcodes.update(iter_node_types(ast))

    return opcodes, invalid_asts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="learnlab-opcodes",
        description="Print all unique AST node opcodes in CodeStates.csv.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path.cwd() / "data",
        help="Dataset directory containing CodeStates.csv (default: ./data)",
    )
    parser.add_argument(
        "--counts",
        action="store_true",
        help="Print each opcode followed by its total occurrence count.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        opcodes, invalid_asts = collect_opcodes(args.data_dir / "CodeStates.csv")
    except OpcodeScanError as error:
        raise SystemExit(f"Opcode scan error: {error}") from error

    for opcode in sorted(opcodes):
        if args.counts:
            print(f"{opcode}\t{opcodes[opcode]}")
        else:
            print(opcode)

    if invalid_asts:
        noun = "AST" if invalid_asts == 1 else "ASTs"
        print(
            f"Warning: skipped {invalid_asts} invalid nonblank {noun}.",
            file=sys.stderr,
        )
