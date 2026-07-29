from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from snap_ast import (
    AstNode,
    ProgramFormatError,
    to_ast,
    to_ast_with_values,
    tree_edit_distance,
)


SNAPSHOT = {
    "type": "snapshot",
    "id": "project-id-that-must-be-ignored",
    "children": [
        {
            "type": "stage",
            "value": "Stage",
            "id": "stage",
            "children": [
                {
                    "type": "sprite",
                    "value": "Sprite",
                    "id": "sprite-id",
                    "children": [
                        {
                            "type": "script",
                            "children": [
                                {"type": "receiveGo", "id": "block-1"},
                                {"type": "forward", "value": "10", "id": "block-2"},
                            ],
                        }
                    ],
                }
            ],
        }
    ],
}


def test_snap_snapshot_builds_ordered_opcode_tree_and_ignores_ids() -> None:
    tree = to_ast(SNAPSHOT)

    assert tree == AstNode(
        "snapshot",
        (
            AstNode(
                "stage",
                (
                    AstNode(
                        "sprite",
                        (
                            AstNode(
                                "script",
                                (AstNode("receiveGo"), AstNode("forward")),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_accepts_database_json_text_and_can_keep_snap_values() -> None:
    tree = to_ast_with_values(json.dumps(SNAPSHOT))

    assert tree.name == "snapshot"
    assert tree.children[0].name == "stage=Stage"
    assert tree.children[0].children[0].name == "sprite=Sprite"
    script = tree.children[0].children[0].children[0]
    assert [child.name for child in script.children] == ["receiveGo", "forward=10"]


def test_supports_mapping_children_and_declared_child_order() -> None:
    snapshot = {
        "type": "snapshot",
        "children": {
            "second": {"type": "turn"},
            "first": {"type": "forward"},
        },
        "children-order": ["first", "second"],
    }

    assert [child.name for child in to_ast(snapshot).children] == ["forward", "turn"]


def test_rejects_malformed_snap_nodes() -> None:
    with pytest.raises(ProgramFormatError, match="not valid JSON"):
        to_ast("{broken")
    with pytest.raises(ProgramFormatError, match="not an object"):
        to_ast({"type": "snapshot", "children": ["bad"]})
    with pytest.raises(ProgramFormatError, match="no string type"):
        to_ast({"type": "snapshot", "children": [{}]})


def test_scratch_project_format_remains_supported() -> None:
    scratch = {
        "targets": [
            {
                "name": "Sprite1",
                "blocks": {
                    "a": {
                        "opcode": "event_whenflagclicked",
                        "topLevel": True,
                        "next": "b",
                        "inputs": {},
                    },
                    "b": {
                        "opcode": "motion_movesteps",
                        "topLevel": False,
                        "next": None,
                        "inputs": {},
                    },
                },
            }
        ]
    }

    tree = to_ast(scratch)
    assert tree.name == "program"
    assert tree.children[0].name == "target:Sprite1"
    assert [node.name for node in tree.children[0].children[0].children] == [
        "event_whenflagclicked",
        "motion_movesteps",
    ]


def test_tree_edit_distance_works_for_snap_snapshots() -> None:
    before = to_ast({"type": "snapshot", "children": [{"type": "forward"}]})
    after = to_ast(
        {
            "type": "snapshot",
            "children": [{"type": "forward"}, {"type": "turn"}],
        }
    )

    assert tree_edit_distance(before, after) == 1


def test_real_database_snapshot_is_supported() -> None:
    database = Path(__file__).parents[1] / "data" / ".learnlab-history.sqlite3"
    if not database.is_file():
        pytest.skip("The history database is not available")

    with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
        ast_json = connection.execute(
            "SELECT ast_json FROM code_states WHERE code_state_id = '2'"
        ).fetchone()[0]

    tree = to_ast(ast_json)
    assert tree.name == "snapshot"
    script = tree.children[0].children[0].children[0]
    assert [node.name for node in script.children] == ["receiveGo", "clear"]
