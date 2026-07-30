"""Canonical Snap/Scratch ASTs and tree-edit-distance comparison."""

from snap_ast.build import ProjectInput, to_ast, to_ast_with_values
from snap_ast.distance import tree_edit_distance
from snap_ast.errors import ProgramFormatError
from snap_ast.exemplar_features import (
    ExemplarFeatureNodeMatch,
    ExemplarSubtreeFeature,
    RubricExemplarFeatureMatches,
    RubricExemplarFeatureSet,
    learn_rubric_exemplar_feature_sets,
    learn_rubric_exemplar_features,
    match_rubric_exemplar_features,
)
from snap_ast.heatmap import (
    RubricHeatmapModel,
    SubtreeCandidate,
    ast_node_count,
    learn_rubric_heatmaps,
    normalize_heatmap_ast,
    normalized_tree_distance,
    subtree_at_depth,
)
from snap_ast.nodes import AstNode

__all__ = [
    "AstNode",
    "ExemplarFeatureNodeMatch",
    "ExemplarSubtreeFeature",
    "ProgramFormatError",
    "ProjectInput",
    "RubricExemplarFeatureMatches",
    "RubricExemplarFeatureSet",
    "RubricHeatmapModel",
    "SubtreeCandidate",
    "ast_node_count",
    "learn_rubric_exemplar_feature_sets",
    "learn_rubric_exemplar_features",
    "learn_rubric_heatmaps",
    "match_rubric_exemplar_features",
    "normalize_heatmap_ast",
    "normalized_tree_distance",
    "subtree_at_depth",
    "to_ast",
    "to_ast_with_values",
    "tree_edit_distance",
]
