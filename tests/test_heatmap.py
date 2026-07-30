from __future__ import annotations

import copy
import csv
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from snap_ast import (
    AstNode,
    learn_rubric_heatmaps,
    normalize_heatmap_ast,
    normalized_tree_distance,
    subtree_at_depth,
)


RUBRIC = "Structure"


def node(node_type: str, *children: dict[str, Any], value: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"type": node_type}
    if children:
        result["children"] = list(children)
    if value is not None:
        result["value"] = value
    return result


def snapshot(*children: dict[str, Any]) -> dict[str, Any]:
    return node("snapshot", node("script", *children))


def solution(
    student_id: str,
    ast: dict[str, Any],
    score: float | None,
) -> dict[str, Any]:
    return {
        "student_id": student_id,
        "ast": ast,
        "rubric_scores": {RUBRIC: score},
    }


def walk_raw(ast: dict[str, Any]):
    yield ast
    children = ast.get("children", [])
    for child in children:
        yield from walk_raw(child)


def test_heatmap_normalization_keeps_literals_and_alpha_renames_names() -> None:
    first = snapshot(
        node("customBlock", value="draw %s %s"),
        node("varDec", value="Length"),
        node("var", value="length"),
        node("literal", value="4.0"),
    )
    second = snapshot(
        node("customBlock", value="student procedure %s %s"),
        node("varDec", value="Distance"),
        node("var", value="distance"),
        node("literal", value="4"),
    )
    first["id"] = "snapshot-one"
    second["id"] = "snapshot-two"

    assert normalize_heatmap_ast(first) == normalize_heatmap_ast(second)


def test_depth_truncation_and_normalized_distance() -> None:
    larger = AstNode("repeat", (AstNode("forward"), AstNode("turn")))
    smaller = AstNode("repeat", (AstNode("forward"),))

    assert subtree_at_depth(larger, 1) == AstNode("repeat")
    assert subtree_at_depth(larger, 2) == larger
    assert normalized_tree_distance(larger, smaller) == pytest.approx(1 / 3)
    assert normalized_tree_distance(larger, larger) == 0.0


def test_shared_positive_subtree_is_hot_and_common_scaffolding_is_not() -> None:
    solutions = [
        solution("positive-a", snapshot(node("forward"), node("forward")), 2),
        solution("positive-b", snapshot(node("forward")), 2),
        solution("positive-c", snapshot(node("forward"), node("rare")), 2),
        solution("lower-a", snapshot(node("turn")), 1),
        solution("lower-b", snapshot(node("wait")), 0),
    ]
    original = copy.deepcopy(solutions)

    models, annotated = learn_rubric_heatmaps(
        solutions,
        [RUBRIC],
        max_depth=2,
    )

    assert solutions == original
    model = models[RUBRIC]
    forward = next(
        candidate
        for candidate in model.candidates
        if candidate.depth == 1 and candidate.tree == AstNode("forward")
    )
    snapshot_candidate = next(
        candidate
        for candidate in model.candidates
        if candidate.depth == 1 and candidate.tree == AstNode("snapshot")
    )
    rare = next(
        candidate
        for candidate in model.candidates
        if candidate.depth == 1 and candidate.tree == AstNode("rare")
    )

    assert forward.heat == 1.0
    assert forward.heat == pytest.approx(forward.cohesion * forward.separation)
    assert forward.source_solution_indices == (0, 1, 2)
    assert snapshot_candidate.heat == 0.0
    assert rare.heat == 0.0

    positive_forward = next(
        raw_node
        for raw_node in walk_raw(annotated[0]["ast_heatmap"])
        if raw_node["type"] == "forward"
    )
    assert positive_forward["rubric_heatmap"][RUBRIC] == 1.0
    assert positive_forward["rubric_heatmap_best_depth"][RUBRIC] == 2
    assert positive_forward["rubric_heatmap_best_candidate"][RUBRIC]

    for annotated_solution in annotated:
        for raw_node in walk_raw(annotated_solution["ast_heatmap"]):
            heat = raw_node["rubric_heatmap"][RUBRIC]
            assert math.isfinite(heat)
            assert 0.0 <= heat <= 1.0

    repeated_models, repeated_annotated = learn_rubric_heatmaps(
        solutions,
        [RUBRIC],
        max_depth=2,
    )
    assert repeated_models == models
    assert repeated_annotated == annotated


def test_structurally_similar_subtree_inherits_partial_heat() -> None:
    full_pattern = node("repeat", node("forward"), node("turn"))
    partial_pattern = node("repeat", node("forward"))
    solutions = [
        solution("positive-a", snapshot(full_pattern), 2),
        solution("positive-b", snapshot(copy.deepcopy(full_pattern)), 2),
        solution("lower", snapshot(partial_pattern), 1),
    ]

    models, annotated = learn_rubric_heatmaps(
        solutions,
        [RUBRIC],
        max_depth=2,
    )

    candidate = next(
        candidate
        for candidate in models[RUBRIC].candidates
        if candidate.depth == 2
        and candidate.tree
        == AstNode("repeat", (AstNode("forward"), AstNode("turn")))
    )
    assert 0.0 < candidate.heat < 1.0

    lower_repeat = next(
        raw_node
        for raw_node in walk_raw(annotated[2]["ast_heatmap"])
        if raw_node["type"] == "repeat"
    )
    inherited_heat = lower_repeat["rubric_heatmap"][RUBRIC]
    assert 0.0 < inherited_heat < candidate.heat
    assert lower_repeat["rubric_heatmap_best_depth"][RUBRIC] == 2


def test_learning_requires_positive_and_comparison_examples() -> None:
    with pytest.raises(ValueError, match="at least two full-credit"):
        learn_rubric_heatmaps(
            [
                solution("positive", snapshot(node("forward")), 2),
                solution("lower", snapshot(node("turn")), 1),
            ],
            [RUBRIC],
        )

    with pytest.raises(ValueError, match="no lower-scoring"):
        learn_rubric_heatmaps(
            [
                solution("positive-a", snapshot(node("forward")), 2),
                solution("positive-b", snapshot(node("forward")), 2),
            ],
            [RUBRIC],
        )


def test_real_squiral_solutions_receive_all_rubric_scores() -> None:
    data_dir = Path(__file__).parents[1] / "data"
    database = data_dir / ".learnlab-history.sqlite3"
    rubric_path = data_dir / "squiralHW.csv"
    if not database.is_file() or not rubric_path.is_file():
        pytest.skip("The complete squiral dataset is not available")

    with rubric_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        rows = list(reader)
        rubric_names = [
            column
            for column in (reader.fieldnames or [])
            if column not in {"Project ID", "Graded ID"}
            and column.strip().lower() != "comment"
        ]
    rubric_by_project = {row["Project ID"]: row for row in rows}

    with sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro",
        uri=True,
    ) as connection:
        connection.row_factory = sqlite3.Row
        final_rows = connection.execute(
            """
            WITH ranked AS (
                SELECT
                    subject_id,
                    project_id,
                    code_state_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY assignment_id, subject_id
                        ORDER BY step_index DESC
                    ) AS position
                FROM timeline_steps
                WHERE assignment_id = 'squiralHW'
            )
            SELECT
                r.subject_id,
                r.project_id,
                c.ast_json
            FROM ranked AS r
            JOIN code_states AS c USING (code_state_id)
            WHERE r.position = 1
            ORDER BY r.subject_id
            """
        ).fetchall()

    real_solutions = []
    for final in final_rows:
        rubric_row = rubric_by_project[final["project_id"]]
        scores = {
            rubric: (
                float(rubric_row[rubric]) if rubric_row[rubric].strip() else None
            )
            for rubric in rubric_names
        }
        if not any(score is not None for score in scores.values()):
            continue
        real_solutions.append(
            {
                "student_id": final["subject_id"],
                "ast": json.loads(final["ast_json"]),
                "rubric_scores": scores,
            }
        )

    models, annotated = learn_rubric_heatmaps(
        real_solutions,
        rubric_names,
        max_depth=1,
    )

    assert len(real_solutions) == len(annotated) == 43
    assert set(models) == set(rubric_names)
    for record in annotated:
        for raw_node in walk_raw(record["ast_heatmap"]):
            assert set(raw_node["rubric_heatmap"]) == set(rubric_names)
            assert all(
                math.isfinite(heat) and 0.0 <= heat <= 1.0
                for heat in raw_node["rubric_heatmap"].values()
            )

    rotation_literal = next(
        candidate
        for candidate in models["Repeat Rotations * 4"].candidates
        if candidate.tree == AstNode("literal=4")
    )
    assert rotation_literal.heat > 0.0

    pen_down = next(
        candidate
        for candidate in models["Pen down"].candidates
        if candidate.tree == AstNode("down")
    )
    assert pen_down.mean_high_distance == 0.0
    assert pen_down.mean_low_distance == 0.0
    assert pen_down.heat == 0.0
