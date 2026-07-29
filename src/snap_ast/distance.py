"""Tree edit distance between two canonical program ASTs."""

from __future__ import annotations

from apted import APTED, Config  # type: ignore[import-untyped]

from snap_ast.nodes import AstNode


def tree_edit_distance(a: AstNode, b: AstNode) -> int:
    return APTED(a, b, Config()).compute_edit_distance()
