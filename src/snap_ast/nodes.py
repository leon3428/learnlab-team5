"""Canonical AST node for tree-edit-distance comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AstNode:
    name: str
    children: tuple["AstNode", ...] = ()
