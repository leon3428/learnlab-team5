"""Learn rubric-specific AST heatmaps from local subtree similarity."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from statistics import fmean
from typing import Any, TypeAlias

from snap_ast.distance import tree_edit_distance
from snap_ast.errors import ProgramFormatError
from snap_ast.nodes import AstNode

HeatmapAstInput: TypeAlias = Mapping[str, Any] | str | bytes
ProgressCallback: TypeAlias = Callable[[str], None]

_VARIABLE_TYPES = frozenset({"var", "varDec", "varMenu"})
_CUSTOM_BLOCK_TYPES = frozenset({"customBlock", "evaluateCustomBlock"})
_CUSTOM_INPUT = re.compile(r"%[A-Za-z]")


@dataclass(frozen=True, slots=True)
class SubtreeCandidate:
    """One deduplicated positive subtree and its learned rubric association."""

    candidate_id: str
    depth: int
    tree: AstNode
    source_solution_indices: tuple[int, ...]
    mean_high_distance: float
    mean_low_distance: float
    cohesion: float
    separation: float
    heat: float


@dataclass(frozen=True, slots=True)
class RubricHeatmapModel:
    """Learned subtree candidates for one rubric criterion."""

    rubric_name: str
    max_depth: int
    full_credit_score: float
    positive_solution_indices: tuple[int, ...]
    comparison_solution_indices: tuple[int, ...]
    excluded_missing_score_count: int
    excluded_missing_ast_count: int
    candidates: tuple[SubtreeCandidate, ...]

    @property
    def positive_count(self) -> int:
        return len(self.positive_solution_indices)

    @property
    def comparison_count(self) -> int:
        return len(self.comparison_solution_indices)

    def top_candidates(self, limit: int = 10) -> tuple[SubtreeCandidate, ...]:
        """Return the strongest candidates in deterministic order."""

        if limit < 0:
            raise ValueError("limit must be nonnegative")
        return self.candidates[:limit]


def normalize_heatmap_ast(project: HeatmapAstInput) -> AstNode:
    """Build a canonical Snap AST for subtree heatmap comparisons.

    Snapshot IDs and container names are ignored. Literal values are retained
    after scalar normalization, variable names are alpha-renamed by first
    occurrence, and custom-block names are reduced to their input arity.
    """

    parsed = _parse_project(project)
    variable_names: dict[str, str] = {}
    return _normalize_node(parsed, variable_names, path="$", active=set())


def subtree_at_depth(tree: AstNode, depth: int) -> AstNode:
    """Return ``tree`` truncated to ``depth`` levels, counting its root as 1."""

    if depth < 1:
        raise ValueError("depth must be at least 1")
    if depth == 1:
        return AstNode(tree.name)
    return AstNode(
        tree.name,
        tuple(subtree_at_depth(child, depth - 1) for child in tree.children),
    )


def ast_node_count(tree: AstNode) -> int:
    """Return the number of nodes in a canonical AST."""

    return 1 + sum(ast_node_count(child) for child in tree.children)


def normalized_tree_distance(first: AstNode, second: AstNode) -> float:
    """Return unit-cost tree-edit distance normalized to the interval [0, 1]."""

    # denominator = max(ast_node_count(first), ast_node_count(second))
    denominator = ast_node_count(first) + ast_node_count(second)
    distance = tree_edit_distance(first, second) / denominator
    return min(1.0, max(0.0, float(distance)))


def learn_rubric_heatmaps(
    solutions: Sequence[Mapping[str, Any]],
    rubric_names: Sequence[str],
    *,
    max_depth: int = 4,
    full_credit_score: float = 2.0,
    progress: ProgressCallback | None = None,
) -> tuple[dict[str, RubricHeatmapModel], list[dict[str, Any]]]:
    """Learn rubric models and return copies of all solutions with annotated ASTs.

    Each solution must contain an ``ast`` field and a ``rubric_scores`` mapping.
    The input objects and their ASTs are never mutated.
    """

    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    if not math.isfinite(full_credit_score):
        raise ValueError("full_credit_score must be finite")
    if not rubric_names:
        raise ValueError("at least one rubric name is required")
    if len(set(rubric_names)) != len(rubric_names):
        raise ValueError("rubric names must be unique")

    workspace = _SubtreeWorkspace(solutions, max_depth=max_depth, progress=progress)
    models: dict[str, RubricHeatmapModel] = {}
    for rubric_name in rubric_names:
        model = workspace.learn_rubric(
            rubric_name,
            full_credit_score=full_credit_score,
        )
        models[rubric_name] = model
        workspace.report(
            f"Learned {rubric_name}: {model.positive_count} full-credit, "
            f"{model.comparison_count} lower-scoring, "
            f"{len(model.candidates)} unique candidates"
        )

    annotated = workspace.annotate_solutions(models)
    return models, annotated


class _SubtreeWorkspace:
    def __init__(
        self,
        solutions: Sequence[Mapping[str, Any]],
        *,
        max_depth: int,
        progress: ProgressCallback | None,
    ) -> None:
        self.solutions = solutions
        self.max_depth = max_depth
        self.progress = progress
        self.normalized_roots: list[AstNode | None] = []
        self.node_patterns: list[tuple[dict[int, AstNode], ...]] = []
        self.solution_patterns: list[dict[int, tuple[AstNode, ...]]] = []
        self._tree_ids: dict[tuple[int, AstNode], str] = {}
        self._node_counts: dict[AstNode, int] = {}
        self._distance_cache: dict[tuple[int, str, str], float] = {}
        self._nearest_cache: dict[tuple[int, str, int], float] = {}

        self.report(
            f"Indexing {len(solutions)} solutions through subtree depth {max_depth}"
        )
        for solution in solutions:
            raw_ast = solution.get("ast")
            if raw_ast is None:
                self.normalized_roots.append(None)
                self.node_patterns.append(())
                self.solution_patterns.append(
                    {depth: () for depth in range(1, max_depth + 1)}
                )
                continue

            root = normalize_heatmap_ast(raw_ast)
            self.normalized_roots.append(root)
            per_node: list[dict[int, AstNode]] = []
            unique_by_depth: dict[int, set[AstNode]] = {
                depth: set() for depth in range(1, max_depth + 1)
            }
            for node in _iter_ast_nodes(root):
                depth_patterns: dict[int, AstNode] = {}
                for depth in range(1, max_depth + 1):
                    pattern = subtree_at_depth(node, depth)
                    depth_patterns[depth] = pattern
                    unique_by_depth[depth].add(pattern)
                    self._register_pattern(depth, pattern)
                per_node.append(depth_patterns)

            self.node_patterns.append(tuple(per_node))
            self.solution_patterns.append(
                {
                    depth: tuple(
                        sorted(
                            unique_by_depth[depth],
                            key=lambda pattern: self._pattern_id(depth, pattern),
                        )
                    )
                    for depth in range(1, max_depth + 1)
                }
            )

        unique_counts = {
            depth: len(
                {
                    pattern
                    for patterns in self.solution_patterns
                    for pattern in patterns[depth]
                }
            )
            for depth in range(1, max_depth + 1)
        }
        self.report(f"Unique normalized subtrees by depth: {unique_counts}")

    def report(self, message: str) -> None:
        if self.progress is not None:
            self.progress(message)

    def learn_rubric(
        self,
        rubric_name: str,
        *,
        full_credit_score: float,
    ) -> RubricHeatmapModel:
        positive_indices: list[int] = []
        comparison_indices: list[int] = []
        missing_score_count = 0
        missing_ast_count = 0

        for index, solution in enumerate(self.solutions):
            scores = solution.get("rubric_scores")
            if not isinstance(scores, Mapping):
                raise ValueError(
                    f"solution {index} has no rubric_scores mapping"
                )
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
            if self.normalized_roots[index] is None:
                missing_ast_count += 1
                continue
            if score == full_credit_score:
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
                "for leave-one-source-out learning"
            )
        if not comparison_indices:
            raise ValueError(
                f"rubric {rubric_name!r} has no lower-scoring ASTs"
            )

        sources_by_candidate: dict[tuple[int, AstNode], set[int]] = defaultdict(set)
        for source_index in positive_indices:
            for depth in range(1, self.max_depth + 1):
                for pattern in self.solution_patterns[source_index][depth]:
                    sources_by_candidate[(depth, pattern)].add(source_index)

        candidates: list[SubtreeCandidate] = []
        for (depth, pattern), source_indices in sources_by_candidate.items():
            low_distances = [
                self._nearest_distance(depth, pattern, comparison_index)
                for comparison_index in comparison_indices
            ]
            mean_low_distance = fmean(low_distances)

            source_high_distances: list[float] = []
            for source_index in sorted(source_indices):
                other_positive_indices = [
                    index for index in positive_indices if index != source_index
                ]
                mean_high_distance = fmean(
                    self._nearest_distance(depth, pattern, positive_index)
                    for positive_index in other_positive_indices
                )
                source_high_distances.append(mean_high_distance)

            mean_high_distance = fmean(source_high_distances)
            cohesion = 1.0 - mean_high_distance
            separation = max(0.0, mean_low_distance - mean_high_distance)
            heat = min(1.0, max(0.0, cohesion * separation))
            candidates.append(
                SubtreeCandidate(
                    candidate_id=self._pattern_id(depth, pattern),
                    depth=depth,
                    tree=pattern,
                    source_solution_indices=tuple(sorted(source_indices)),
                    mean_high_distance=mean_high_distance,
                    mean_low_distance=mean_low_distance,
                    cohesion=cohesion,
                    separation=separation,
                    heat=heat,
                )
            )

        candidates.sort(key=lambda candidate: (-candidate.heat, candidate.candidate_id))
        return RubricHeatmapModel(
            rubric_name=rubric_name,
            max_depth=self.max_depth,
            full_credit_score=full_credit_score,
            positive_solution_indices=tuple(positive_indices),
            comparison_solution_indices=tuple(comparison_indices),
            excluded_missing_score_count=missing_score_count,
            excluded_missing_ast_count=missing_ast_count,
            candidates=tuple(candidates),
        )

    def annotate_solutions(
        self,
        models: Mapping[str, RubricHeatmapModel],
    ) -> list[dict[str, Any]]:
        annotations: list[
            list[
                tuple[
                    dict[str, float],
                    dict[str, int | None],
                    dict[str, str | None],
                ]
            ]
        ] = [
            [({}, {}, {}) for _ in patterns]
            for patterns in self.node_patterns
        ]

        all_patterns_by_depth: dict[int, set[AstNode]] = {
            depth: {
                pattern
                for solution_patterns in self.solution_patterns
                for pattern in solution_patterns[depth]
            }
            for depth in range(1, self.max_depth + 1)
        }

        for rubric_name, model in models.items():
            self.report(f"Applying learned candidates for {rubric_name}")
            candidates_by_depth: dict[int, tuple[SubtreeCandidate, ...]] = {
                depth: tuple(
                    candidate
                    for candidate in model.candidates
                    if candidate.depth == depth and candidate.heat > 0.0
                )
                for depth in range(1, self.max_depth + 1)
            }
            pattern_matches: dict[
                tuple[int, AstNode], tuple[float, str | None]
            ] = {}
            for depth in range(1, self.max_depth + 1):
                candidates = candidates_by_depth[depth]
                for target in all_patterns_by_depth[depth]:
                    best_heat = 0.0
                    best_candidate_id: str | None = None
                    for candidate in candidates:
                        similarity = 1.0 - self._distance(
                            depth,
                            target,
                            candidate.tree,
                        )
                        heat = candidate.heat * similarity
                        if (
                            heat > best_heat
                            or (
                                math.isclose(heat, best_heat)
                                and heat > 0.0
                                and (
                                    best_candidate_id is None
                                    or candidate.candidate_id < best_candidate_id
                                )
                            )
                        ):
                            best_heat = heat
                            best_candidate_id = candidate.candidate_id
                    pattern_matches[(depth, target)] = (
                        min(1.0, max(0.0, best_heat)),
                        best_candidate_id,
                    )

            for solution_index, per_node in enumerate(self.node_patterns):
                for node_index, depth_patterns in enumerate(per_node):
                    best_heat = 0.0
                    best_depth: int | None = None
                    best_candidate_id: str | None = None
                    for depth, target in depth_patterns.items():
                        heat, candidate_id = pattern_matches[(depth, target)]
                        if (
                            heat > best_heat
                            or (
                                math.isclose(heat, best_heat)
                                and heat > 0.0
                                and (best_depth is None or depth > best_depth)
                            )
                            or (
                                math.isclose(heat, best_heat)
                                and heat > 0.0
                                and depth == best_depth
                                and candidate_id is not None
                                and (
                                    best_candidate_id is None
                                    or candidate_id < best_candidate_id
                                )
                            )
                        ):
                            best_heat = heat
                            best_depth = depth
                            best_candidate_id = candidate_id

                    heatmap, depths, candidate_ids = annotations[solution_index][
                        node_index
                    ]
                    heatmap[rubric_name] = min(1.0, max(0.0, best_heat))
                    depths[rubric_name] = best_depth
                    candidate_ids[rubric_name] = best_candidate_id

        annotated_solutions: list[dict[str, Any]] = []
        for solution_index, solution in enumerate(self.solutions):
            annotated_solution = dict(solution)
            raw_ast = solution.get("ast")
            if raw_ast is None:
                annotated_solution["ast_heatmap"] = None
                annotated_solutions.append(annotated_solution)
                continue

            ast_heatmap = copy.deepcopy(_parse_project(raw_ast))
            raw_nodes = list(_iter_raw_nodes(ast_heatmap))
            if len(raw_nodes) != len(annotations[solution_index]):
                raise RuntimeError("normalized and source AST traversals do not align")
            for node, (heatmap, depths, candidate_ids) in zip(
                raw_nodes,
                annotations[solution_index],
            ):
                node["rubric_heatmap"] = heatmap
                node["rubric_heatmap_best_depth"] = depths
                node["rubric_heatmap_best_candidate"] = candidate_ids
            annotated_solution["ast_heatmap"] = ast_heatmap
            annotated_solutions.append(annotated_solution)

        return annotated_solutions

    def _register_pattern(self, depth: int, pattern: AstNode) -> None:
        key = (depth, pattern)
        if key not in self._tree_ids:
            self._tree_ids[key] = _candidate_id(depth, pattern)

    def _pattern_id(self, depth: int, pattern: AstNode) -> str:
        self._register_pattern(depth, pattern)
        return self._tree_ids[(depth, pattern)]

    def _distance(self, depth: int, first: AstNode, second: AstNode) -> float:
        first_id = self._pattern_id(depth, first)
        second_id = self._pattern_id(depth, second)
        low_id, high_id = sorted((first_id, second_id))
        cache_key = (depth, low_id, high_id)
        if cache_key not in self._distance_cache:
            if first == second:
                distance = 0.0
            else:
                first_count = self._node_counts.setdefault(
                    first,
                    ast_node_count(first),
                )
                second_count = self._node_counts.setdefault(
                    second,
                    ast_node_count(second),
                )
                distance = tree_edit_distance(first, second) / max(
                    first_count,
                    second_count,
                )
            self._distance_cache[cache_key] = min(
                1.0,
                max(0.0, float(distance)),
            )
        return self._distance_cache[cache_key]

    def _nearest_distance(
        self,
        depth: int,
        candidate: AstNode,
        solution_index: int,
    ) -> float:
        candidate_id = self._pattern_id(depth, candidate)
        cache_key = (depth, candidate_id, solution_index)
        if cache_key not in self._nearest_cache:
            targets = self.solution_patterns[solution_index][depth]
            if not targets:
                raise ValueError(
                    f"solution {solution_index} has no AST subtrees at depth {depth}"
                )
            self._nearest_cache[cache_key] = min(
                self._distance(depth, candidate, target)
                for target in targets
            )
        return self._nearest_cache[cache_key]


def _parse_project(project: HeatmapAstInput) -> dict[str, Any]:
    if isinstance(project, bytes):
        try:
            project = project.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProgramFormatError("project bytes are not valid UTF-8") from error
    if isinstance(project, str):
        try:
            project = json.loads(project)
        except json.JSONDecodeError as error:
            raise ProgramFormatError(f"project is not valid JSON: {error.msg}") from error
    if not isinstance(project, Mapping):
        raise ProgramFormatError("project must be a JSON object or JSON object text")
    return dict(project)


def _normalize_node(
    raw: Mapping[str, Any],
    variable_names: dict[str, str],
    *,
    path: str,
    active: set[int],
) -> AstNode:
    identity = id(raw)
    if identity in active:
        raise ProgramFormatError(f"cycle detected in Snap AST at {path}")

    node_type = raw.get("type")
    if not isinstance(node_type, str) or not node_type:
        raise ProgramFormatError(f"Snap AST node at {path} has no string type")

    label = node_type
    if node_type == "literal" and "value" in raw:
        label = f"{node_type}={_normalize_literal(raw['value'])}"
    elif node_type in _VARIABLE_TYPES and "value" in raw:
        raw_name = _normalize_name(raw["value"])
        canonical_name = variable_names.setdefault(
            raw_name,
            f"VAR_{len(variable_names) + 1}",
        )
        label = f"{node_type}={canonical_name}"
    elif node_type in _CUSTOM_BLOCK_TYPES:
        value = raw.get("value")
        arity = len(_CUSTOM_INPUT.findall(str(value))) if value is not None else 0
        label = f"{node_type}=arity:{arity}"

    active.add(identity)
    try:
        children = tuple(
            _normalize_node(
                child,
                variable_names,
                path=child_path,
                active=active,
            )
            for child_path, child in _raw_children(raw, path)
        )
    finally:
        active.remove(identity)
    return AstNode(label, children)


def _normalize_literal(raw: Any) -> str:
    if raw is None:
        return "null"
    if isinstance(raw, bool):
        return "true" if raw else "false"
    value = " ".join(str(raw).strip().casefold().split())
    try:
        number = Decimal(value)
    except InvalidOperation:
        return value
    if not number.is_finite():
        return value
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _normalize_name(raw: Any) -> str:
    return " ".join(str(raw).strip().casefold().split())


def _raw_children(
    raw: Mapping[str, Any],
    path: str,
) -> list[tuple[str, Mapping[str, Any]]]:
    children = raw.get("children")
    if children is None:
        return []
    if isinstance(children, list):
        result: list[tuple[str, Mapping[str, Any]]] = []
        for index, child in enumerate(children):
            if not isinstance(child, Mapping):
                raise ProgramFormatError(
                    f"Snap AST child at {path}.children[{index}] is not an object"
                )
            result.append((f"{path}.children[{index}]", child))
        return result
    if isinstance(children, Mapping):
        declared_order = raw.get("children-order")
        if declared_order is None:
            keys = list(children)
        elif isinstance(declared_order, list):
            keys = []
            for key in declared_order:
                if key not in children:
                    raise ProgramFormatError(
                        f"children-order at {path} references missing child {key!r}"
                    )
                if key not in keys:
                    keys.append(key)
            keys.extend(key for key in children if key not in keys)
        else:
            raise ProgramFormatError(f"children-order at {path} must be a list")

        result = []
        for key in keys:
            child = children[key]
            if not isinstance(child, Mapping):
                raise ProgramFormatError(
                    f"Snap AST child at {path}.children[{key!r}] is not an object"
                )
            result.append((f"{path}.children[{key!r}]", child))
        return result
    raise ProgramFormatError(f"Snap AST children at {path} must be a list or object")


def _iter_ast_nodes(root: AstNode) -> Iterable[AstNode]:
    yield root
    for child in root.children:
        yield from _iter_ast_nodes(child)


def _iter_raw_nodes(root: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield root
    for _, child in _raw_children(root, "$"):
        child_dict = child if isinstance(child, dict) else dict(child)
        yield from _iter_raw_nodes(child_dict)


def _candidate_id(depth: int, tree: AstNode) -> str:
    serialized = json.dumps(
        _tree_tuple(tree),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()[:16]
    return f"d{depth}-{digest}"


def _tree_tuple(tree: AstNode) -> tuple[str, tuple[Any, ...]]:
    return tree.name, tuple(_tree_tuple(child) for child in tree.children)
