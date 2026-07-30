from __future__ import annotations

import copy
import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from snap_ast import (
    AstNode,
    ProgramFormatError,
    learn_rubric_exemplar_feature_sets,
    learn_rubric_exemplar_features,
    match_rubric_exemplar_features,
)


RUBRIC = "Structure"


def node(node_type: str, *children: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"type": node_type}
    if children:
        result["children"] = list(children)
    return result


def snapshot(*children: dict[str, Any]) -> dict[str, Any]:
    return node("snapshot", node("script", *children))


def solution(
    student_id: str,
    ast: dict[str, Any] | None,
    score: float | None,
) -> dict[str, Any]:
    return {
        "student_id": student_id,
        "ast": ast,
        "rubric_scores": {RUBRIC: score},
    }


def test_source_exclusion_and_duplicate_occurrences_are_preserved() -> None:
    solutions = [
        solution(
            "positive-a",
            snapshot(*(node("forward") for _ in range(10))),
            2,
        ),
        solution("positive-b", snapshot(node("forward")), 2),
        solution("lower", snapshot(node("turn")), 0),
    ]
    original = copy.deepcopy(solutions)

    feature_set = learn_rubric_exemplar_features(
        solutions,
        RUBRIC,
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.2,
    )

    assert solutions == original
    counts = Counter(feature.exemplar_id for feature in feature_set.features)
    assert counts["positive-a"] == 5
    assert counts["positive-b"] <= 5

    repeated = [
        feature
        for feature in feature_set.features
        if feature.tree == AstNode("forward")
    ]
    assert sum(
        feature.exemplar_id == "positive-a" for feature in repeated
    ) == 5
    assert any(feature.exemplar_id == "positive-b" for feature in repeated)
    assert len({feature.feature_id for feature in repeated}) == len(repeated)
    assert all(feature.mean_high_distance == 0.0 for feature in repeated)
    assert all(feature.mean_low_distance == 1.0 for feature in repeated)
    assert all(feature.heat == 1.0 for feature in repeated)

    repeated_feature_set = learn_rubric_exemplar_features(
        solutions,
        RUBRIC,
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.2,
    )
    assert repeated_feature_set == feature_set
    assert learn_rubric_exemplar_feature_sets(
        solutions,
        [RUBRIC],
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.2,
    ) == {RUBRIC: feature_set}


def test_heat_threshold_is_strict() -> None:
    full_pattern = node(
        "block",
        node("a"),
        node("b"),
        node("c"),
        node("d"),
    )
    lower_pattern = node(
        "block",
        node("a"),
        node("b"),
        node("c"),
        node("x"),
    )
    solutions = [
        solution("positive-a", snapshot(full_pattern), 2),
        solution("positive-b", snapshot(copy.deepcopy(full_pattern)), 2),
        solution("lower", snapshot(lower_pattern), 0),
    ]
    expected_tree = AstNode(
        "block",
        (
            AstNode("a"),
            AstNode("b"),
            AstNode("c"),
            AstNode("d"),
        ),
    )

    below = learn_rubric_exemplar_features(
        solutions,
        RUBRIC,
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.199,
    )
    exact_heat_features = [
        feature for feature in below.features if feature.tree == expected_tree
    ]
    assert exact_heat_features
    assert all(feature.heat == pytest.approx(0.2) for feature in exact_heat_features)

    at_threshold = learn_rubric_exemplar_features(
        solutions,
        RUBRIC,
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.2,
    )
    assert all(feature.tree != expected_tree for feature in at_threshold.features)


def test_matching_uses_feature_depth_and_returns_one_result_per_node() -> None:
    full_pattern = node("repeat", node("forward"), node("turn"))
    solutions = [
        solution("positive-a", snapshot(full_pattern), 2),
        solution("positive-b", snapshot(copy.deepcopy(full_pattern)), 2),
        solution("lower", snapshot(node("wait")), 0),
    ]
    feature_set = learn_rubric_exemplar_features(
        solutions,
        RUBRIC,
        max_depth=2,
        features_per_exemplar=5,
        minimum_heat=0.2,
    )
    target = snapshot(node("repeat", node("forward")))
    original = copy.deepcopy(target)

    result = match_rubric_exemplar_features(feature_set, target)

    assert target == original
    assert [match.target_node_path for match in result.node_matches] == [
        (),
        (0,),
        (0, 0),
        (0, 0, 0),
    ]
    repeat_match = next(
        match
        for match in result.node_matches
        if match.target_node_name == "repeat"
    )
    assert repeat_match.minimum_distance == pytest.approx(1 / 3)
    assert repeat_match.feature.tree == AstNode(
        "repeat",
        (AstNode("forward"), AstNode("turn")),
    )
    assert repeat_match.feature.depth == 2
    assert 0.0 < repeat_match.minimum_distance < 1.0

    forward_match = next(
        match
        for match in result.node_matches
        if match.target_node_name == "forward"
    )
    assert forward_match.minimum_distance == 0.0
    assert forward_match.feature.exemplar_id == "positive-a"

    assert match_rubric_exemplar_features(feature_set, target) == result
    assert all(
        math.isfinite(match.minimum_distance)
        and 0.0 <= match.minimum_distance <= 1.0
        for match in result.node_matches
    )


def test_learning_and_matching_fail_clearly_for_unavailable_inputs() -> None:
    with pytest.raises(ValueError, match="at least two full-credit"):
        learn_rubric_exemplar_features(
            [
                solution("positive", snapshot(node("forward")), 2),
                solution("lower", snapshot(node("turn")), 0),
            ],
            RUBRIC,
        )

    with pytest.raises(ValueError, match="no lower-scoring"):
        learn_rubric_exemplar_features(
            [
                solution("positive-a", snapshot(node("forward")), 2),
                solution("positive-b", snapshot(node("forward")), 2),
            ],
            RUBRIC,
        )

    with pytest.raises(ValueError, match="no exemplar feature"):
        learn_rubric_exemplar_features(
            [
                solution("positive-a", snapshot(node("forward")), 2),
                solution("positive-b", snapshot(node("forward")), 2),
                solution("lower", snapshot(node("forward")), 0),
            ],
            RUBRIC,
            minimum_heat=0.2,
        )

    empty_sets = learn_rubric_exemplar_feature_sets(
        [
            solution("positive-a", snapshot(node("forward")), 2),
            solution("positive-b", snapshot(node("forward")), 2),
            solution("lower", snapshot(node("forward")), 0),
        ],
        [RUBRIC],
        minimum_heat=0.2,
        allow_empty=True,
    )
    assert empty_sets[RUBRIC].features == ()

    feature_set = learn_rubric_exemplar_features(
        [
            solution("positive-a", snapshot(node("forward")), 2),
            solution("positive-b", snapshot(node("forward")), 2),
            solution("lower", snapshot(node("turn")), 0),
        ],
        RUBRIC,
        max_depth=1,
    )
    with pytest.raises(ProgramFormatError, match="has no string type"):
        match_rubric_exemplar_features(feature_set, {"children": []})


def test_real_squiral_features_and_matches_are_bounded() -> None:
    data_dir = Path(__file__).parents[1] / "data"
    database = data_dir / ".learnlab-history.sqlite3"
    rubric_path = data_dir / "squiralHW.csv"
    if not database.is_file() or not rubric_path.is_file():
        pytest.skip("The complete squiral dataset is not available")

    with rubric_path.open(newline="", encoding="utf-8-sig") as source:
        rubric_rows = list(csv.DictReader(source))
    rubric_by_project = {row["Project ID"]: row for row in rubric_rows}

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

    rubric_name = "Variable Increment"
    real_solutions = []
    for final in final_rows:
        raw_score = rubric_by_project[final["project_id"]][rubric_name].strip()
        if not raw_score:
            continue
        real_solutions.append(
            {
                "student_id": final["subject_id"],
                "ast": json.loads(final["ast_json"]),
                "rubric_scores": {rubric_name: float(raw_score)},
            }
        )

    feature_set = learn_rubric_exemplar_features(
        real_solutions,
        rubric_name,
        max_depth=1,
        features_per_exemplar=5,
        minimum_heat=0.2,
    )

    counts = Counter(feature.exemplar_id for feature in feature_set.features)
    assert feature_set.positive_count == 39
    assert feature_set.comparison_count == 4
    assert feature_set.features
    assert all(count <= 5 for count in counts.values())
    assert len(feature_set.features) <= feature_set.positive_count * 5
    assert len({feature.tree for feature in feature_set.features}) < len(
        feature_set.features
    )

    result = match_rubric_exemplar_features(
        feature_set,
        real_solutions[0]["ast"],
    )
    assert result.node_matches
    assert all(
        math.isfinite(match.minimum_distance)
        and 0.0 <= match.minimum_distance <= 1.0
        for match in result.node_matches
    )
