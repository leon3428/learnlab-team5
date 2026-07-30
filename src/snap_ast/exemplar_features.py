"""Extract exemplar-specific rubric features and match them to a target AST."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any, TypeAlias

from snap_ast.heatmap import (
    normalize_heatmap_ast,
    normalized_tree_distance,
    subtree_at_depth,
)
from snap_ast.nodes import AstNode

AstInput: TypeAlias = Mapping[str, Any] | str | bytes


@dataclass(frozen=True, slots=True)
class ExemplarSubtreeFeature:
    """One retained subtree occurrence from one full-credit exemplar."""

    feature_id: str
    exemplar_id: str
    source_solution_index: int
    source_node_path: tuple[int, ...]
    depth: int
    tree: AstNode
    heat: float
    mean_high_distance: float
    mean_low_distance: float
    cohesion: float
    separation: float


@dataclass(frozen=True, slots=True)
class RubricExemplarFeatureSet:
    """All retained feature occurrences for one rubric."""

    rubric_name: str
    max_depth: int
    full_credit_score: float
    features_per_exemplar: int
    minimum_heat: float
    positive_solution_indices: tuple[int, ...]
    comparison_solution_indices: tuple[int, ...]
    excluded_missing_score_count: int
    excluded_missing_ast_count: int
    features: tuple[ExemplarSubtreeFeature, ...]

    @property
    def positive_count(self) -> int:
        return len(self.positive_solution_indices)

    @property
    def comparison_count(self) -> int:
        return len(self.comparison_solution_indices)


@dataclass(frozen=True, slots=True)
class ExemplarFeatureNodeMatch:
    """The nearest retained feature occurrence for one target AST node."""

    target_node_path: tuple[int, ...]
    target_node_name: str
    minimum_distance: float
    feature: ExemplarSubtreeFeature


@dataclass(frozen=True, slots=True)
class RubricExemplarFeatureMatches:
    """Canonical target AST and one nearest-feature result per node."""

    rubric_name: str
    canonical_ast: AstNode
    node_matches: tuple[ExemplarFeatureNodeMatch, ...]


def learn_rubric_exemplar_features(
    solutions: Sequence[Mapping[str, Any]],
    rubric_name: str,
    *,
    max_depth: int = 4,
    features_per_exemplar: int = 5,
    minimum_heat: float = 0.2,
    full_credit_score: float = 2.0,
) -> RubricExemplarFeatureSet:
    """Retain high-heat subtree occurrences from every full-credit exemplar.

    Duplicate subtrees are intentionally preserved when they occur at different
    nodes or in different exemplars. Heat must be strictly greater than
    ``minimum_heat`` for an occurrence to be retained.
    """

    _validate_learning_options(
        (rubric_name,),
        max_depth=max_depth,
        features_per_exemplar=features_per_exemplar,
        minimum_heat=minimum_heat,
        full_credit_score=full_credit_score,
    )
    workspace = _FeatureWorkspace(solutions, max_depth=max_depth)
    return _learn_with_workspace(
        workspace,
        solutions,
        rubric_name,
        features_per_exemplar=features_per_exemplar,
        minimum_heat=minimum_heat,
        full_credit_score=full_credit_score,
        allow_empty=False,
    )


def learn_rubric_exemplar_feature_sets(
    solutions: Sequence[Mapping[str, Any]],
    rubric_names: Sequence[str],
    *,
    max_depth: int = 4,
    features_per_exemplar: int = 5,
    minimum_heat: float = 0.2,
    full_credit_score: float = 2.0,
    allow_empty: bool = False,
) -> dict[str, RubricExemplarFeatureSet]:
    """Learn multiple rubric feature sets with one shared subtree workspace.

    When ``allow_empty`` is true, rubrics with no feature above the heat
    threshold return an empty feature set instead of raising an error.
    """

    _validate_learning_options(
        rubric_names,
        max_depth=max_depth,
        features_per_exemplar=features_per_exemplar,
        minimum_heat=minimum_heat,
        full_credit_score=full_credit_score,
    )
    workspace = _FeatureWorkspace(solutions, max_depth=max_depth)
    return {
        rubric_name: _learn_with_workspace(
            workspace,
            solutions,
            rubric_name,
            features_per_exemplar=features_per_exemplar,
            minimum_heat=minimum_heat,
            full_credit_score=full_credit_score,
            allow_empty=allow_empty,
        )
        for rubric_name in rubric_names
    }


def _validate_learning_options(
    rubric_names: Sequence[str],
    *,
    max_depth: int,
    features_per_exemplar: int,
    minimum_heat: float,
    full_credit_score: float,
) -> None:
    if not rubric_names:
        raise ValueError("at least one rubric name is required")
    if any(not isinstance(name, str) or not name for name in rubric_names):
        raise ValueError("rubric names must be nonempty strings")
    if len(set(rubric_names)) != len(rubric_names):
        raise ValueError("rubric names must be unique")
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if features_per_exemplar < 1:
        raise ValueError("features_per_exemplar must be at least 1")
    if not math.isfinite(minimum_heat) or not 0.0 <= minimum_heat <= 1.0:
        raise ValueError("minimum_heat must be finite and between 0 and 1")
    if not math.isfinite(full_credit_score):
        raise ValueError("full_credit_score must be finite")


def _learn_with_workspace(
    workspace: "_FeatureWorkspace",
    solutions: Sequence[Mapping[str, Any]],
    rubric_name: str,
    *,
    features_per_exemplar: int,
    minimum_heat: float,
    full_credit_score: float,
    allow_empty: bool,
) -> RubricExemplarFeatureSet:
    positive_indices: list[int] = []
    comparison_indices: list[int] = []
    missing_score_count = 0
    missing_ast_count = 0

    for index, solution in enumerate(solutions):
        scores = solution.get("rubric_scores")
        if not isinstance(scores, Mapping):
            raise ValueError(f"solution {index} has no rubric_scores mapping")
        raw_score = scores.get(rubric_name)
        if raw_score is None:
            missing_score_count += 1
            continue
        try:
            score = float(raw_score)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"solution {index} has a nonnumeric score for {rubric_name!r}"
            ) from error
        if not math.isfinite(score):
            raise ValueError(
                f"solution {index} has a nonfinite score for {rubric_name!r}"
            )
        if score not in {0.0, 1.0, 2.0}:
            raise ValueError(
                f"solution {index} score for {rubric_name!r} must be 0, 1, or 2"
            )
        if workspace.roots[index] is None:
            missing_ast_count += 1
            continue
        if score == full_credit_score:
            exemplar_id = solution.get("student_id")
            if not isinstance(exemplar_id, str) or not exemplar_id:
                raise ValueError(
                    f"full-credit solution {index} has no nonempty student_id"
                )
            positive_indices.append(index)
        elif score < full_credit_score:
            comparison_indices.append(index)
        else:
            raise ValueError(
                f"solution {index} score for {rubric_name!r} exceeds "
                f"the full-credit score {full_credit_score:g}"
            )

    if len(positive_indices) < 2:
        raise ValueError(
            f"rubric {rubric_name!r} needs at least two full-credit ASTs "
            "for source-excluded heat learning"
        )
    if not comparison_indices:
        raise ValueError(
            f"rubric {rubric_name!r} has no lower-scoring comparison ASTs"
        )

    retained: list[ExemplarSubtreeFeature] = []
    for source_index in positive_indices:
        exemplar_id = str(solutions[source_index]["student_id"])
        node_winners: list[ExemplarSubtreeFeature] = []
        for path, node in workspace.nodes[source_index]:
            depth_features = [
                workspace.feature_for_occurrence(
                    exemplar_id=exemplar_id,
                    source_index=source_index,
                    source_path=path,
                    source_node=node,
                    depth=depth,
                    positive_indices=positive_indices,
                    comparison_indices=comparison_indices,
                )
                for depth in range(1, workspace.max_depth + 1)
            ]
            winner = min(
                depth_features,
                key=lambda feature: (
                    -feature.heat,
                    -feature.depth,
                    feature.feature_id,
                ),
            )
            if winner.heat > minimum_heat:
                node_winners.append(winner)

        node_winners.sort(
            key=lambda feature: (
                -feature.heat,
                feature.source_node_path,
                -feature.depth,
                feature.feature_id,
            )
        )
        retained.extend(node_winners[:features_per_exemplar])

    if not retained and not allow_empty:
        raise ValueError(
            f"rubric {rubric_name!r} has no exemplar feature with heat "
            f"strictly greater than {minimum_heat:g}"
        )

    return RubricExemplarFeatureSet(
        rubric_name=rubric_name,
        max_depth=workspace.max_depth,
        full_credit_score=full_credit_score,
        features_per_exemplar=features_per_exemplar,
        minimum_heat=minimum_heat,
        positive_solution_indices=tuple(positive_indices),
        comparison_solution_indices=tuple(comparison_indices),
        excluded_missing_score_count=missing_score_count,
        excluded_missing_ast_count=missing_ast_count,
        features=tuple(retained),
    )


def match_rubric_exemplar_features(
    feature_set: RubricExemplarFeatureSet,
    target_ast: AstInput,
) -> RubricExemplarFeatureMatches:
    """Find the nearest retained feature occurrence for every target AST node."""

    if not feature_set.features:
        raise ValueError("feature_set contains no exemplar features")

    canonical_ast = normalize_heatmap_ast(target_ast)
    target_nodes = tuple(_iter_nodes_with_paths(canonical_ast))
    target_subtrees: dict[tuple[tuple[int, ...], int], AstNode] = {}
    distance_cache: dict[tuple[AstNode, AstNode], float] = {}
    matches: list[ExemplarFeatureNodeMatch] = []

    for path, node in target_nodes:
        candidates: list[tuple[float, ExemplarSubtreeFeature]] = []
        for feature in feature_set.features:
            subtree_key = (path, feature.depth)
            target_subtree = target_subtrees.setdefault(
                subtree_key,
                subtree_at_depth(node, feature.depth),
            )
            distance_key = (target_subtree, feature.tree)
            reverse_key = (feature.tree, target_subtree)
            if distance_key in distance_cache:
                distance = distance_cache[distance_key]
            elif reverse_key in distance_cache:
                distance = distance_cache[reverse_key]
            else:
                distance = normalized_tree_distance(target_subtree, feature.tree)
                distance_cache[distance_key] = distance
            candidates.append((distance, feature))

        minimum_distance, winning_feature = min(
            candidates,
            key=lambda item: (
                item[0],
                -item[1].heat,
                item[1].exemplar_id,
                item[1].source_node_path,
                item[1].feature_id,
            ),
        )
        matches.append(
            ExemplarFeatureNodeMatch(
                target_node_path=path,
                target_node_name=node.name,
                minimum_distance=minimum_distance,
                feature=winning_feature,
            )
        )

    return RubricExemplarFeatureMatches(
        rubric_name=feature_set.rubric_name,
        canonical_ast=canonical_ast,
        node_matches=tuple(matches),
    )


class _FeatureWorkspace:
    def __init__(
        self,
        solutions: Sequence[Mapping[str, Any]],
        *,
        max_depth: int,
    ) -> None:
        self.max_depth = max_depth
        self.roots: list[AstNode | None] = []
        self.nodes: list[tuple[tuple[tuple[int, ...], AstNode], ...]] = []
        self.targets: list[dict[int, tuple[AstNode, ...]]] = []
        self._nearest_cache: dict[tuple[int, str, int], float] = {}
        self._distance_cache: dict[tuple[int, str, str], float] = {}

        for solution in solutions:
            raw_ast = solution.get("ast")
            if raw_ast is None:
                self.roots.append(None)
                self.nodes.append(())
                self.targets.append(
                    {depth: () for depth in range(1, max_depth + 1)}
                )
                continue

            root = normalize_heatmap_ast(raw_ast)
            nodes = tuple(_iter_nodes_with_paths(root))
            self.roots.append(root)
            self.nodes.append(nodes)
            self.targets.append(
                {
                    depth: tuple(
                        sorted(
                            {
                                subtree_at_depth(node, depth)
                                for _, node in nodes
                            },
                            key=lambda tree: _tree_id(depth, tree),
                        )
                    )
                    for depth in range(1, max_depth + 1)
                }
            )

    def feature_for_occurrence(
        self,
        *,
        exemplar_id: str,
        source_index: int,
        source_path: tuple[int, ...],
        source_node: AstNode,
        depth: int,
        positive_indices: Sequence[int],
        comparison_indices: Sequence[int],
    ) -> ExemplarSubtreeFeature:
        tree = subtree_at_depth(source_node, depth)
        other_positive_indices = [
            index for index in positive_indices if index != source_index
        ]
        mean_high_distance = fmean(
            self._nearest_distance(depth, tree, index)
            for index in other_positive_indices
        )
        mean_low_distance = fmean(
            self._nearest_distance(depth, tree, index)
            for index in comparison_indices
        )
        cohesion = 1.0 - mean_high_distance
        separation = max(0.0, mean_low_distance - mean_high_distance)
        heat = min(1.0, max(0.0, cohesion * separation))
        return ExemplarSubtreeFeature(
            feature_id=_occurrence_id(
                source_index,
                source_path,
                depth,
                tree,
            ),
            exemplar_id=exemplar_id,
            source_solution_index=source_index,
            source_node_path=source_path,
            depth=depth,
            tree=tree,
            heat=heat,
            mean_high_distance=mean_high_distance,
            mean_low_distance=mean_low_distance,
            cohesion=cohesion,
            separation=separation,
        )

    def _nearest_distance(
        self,
        depth: int,
        feature: AstNode,
        solution_index: int,
    ) -> float:
        feature_id = _tree_id(depth, feature)
        cache_key = (depth, feature_id, solution_index)
        if cache_key not in self._nearest_cache:
            targets = self.targets[solution_index][depth]
            if not targets:
                raise ValueError(
                    f"solution {solution_index} has no AST subtrees at depth {depth}"
                )
            self._nearest_cache[cache_key] = min(
                self._distance(depth, feature, target)
                for target in targets
            )
        return self._nearest_cache[cache_key]

    def _distance(self, depth: int, first: AstNode, second: AstNode) -> float:
        first_id = _tree_id(depth, first)
        second_id = _tree_id(depth, second)
        low_id, high_id = sorted((first_id, second_id))
        cache_key = (depth, low_id, high_id)
        if cache_key not in self._distance_cache:
            self._distance_cache[cache_key] = normalized_tree_distance(
                first,
                second,
            )
        return self._distance_cache[cache_key]


def _iter_nodes_with_paths(
    root: AstNode,
    path: tuple[int, ...] = (),
) -> Iterable[tuple[tuple[int, ...], AstNode]]:
    yield path, root
    for index, child in enumerate(root.children):
        yield from _iter_nodes_with_paths(child, (*path, index))


def _occurrence_id(
    solution_index: int,
    source_path: tuple[int, ...],
    depth: int,
    tree: AstNode,
) -> str:
    path = "root" if not source_path else "-".join(map(str, source_path))
    return f"s{solution_index}-p{path}-d{depth}-{_tree_id(depth, tree)}"


def _tree_id(depth: int, tree: AstNode) -> str:
    serialized = json.dumps(
        _tree_tuple(tree),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()[:16]
    return f"d{depth}-{digest}"


def _tree_tuple(tree: AstNode) -> tuple[str, tuple[Any, ...]]:
    return tree.name, tuple(_tree_tuple(child) for child in tree.children)
