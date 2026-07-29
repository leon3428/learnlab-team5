"""Build canonical ASTs from Snap snapshots or Scratch project JSON."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypeAlias

from snap_ast.errors import ProgramFormatError
from snap_ast.nodes import AstNode

ProjectInput: TypeAlias = Mapping[str, Any] | str | bytes


def to_ast(project: ProjectInput) -> AstNode:
    """Return an opcode-only AST.

    ``project`` may be a parsed Scratch project, a parsed Snap snapshot, or the
    JSON text returned by the database's ``ast_json`` column. Snap node IDs and
    values are deliberately ignored so equivalent program structures compare
    equally across snapshots.
    """

    parsed = _parse_project(project)
    if _is_snap_snapshot(parsed):
        return _build_snap_node(parsed, include_values=False, path="$", active=set())
    return _build_scratch_project(parsed, include_values=False)


def to_ast_with_values(project: ProjectInput) -> AstNode:
    """Return an AST that retains literal and user-defined values."""

    parsed = _parse_project(project)
    if _is_snap_snapshot(parsed):
        return _build_snap_node(parsed, include_values=True, path="$", active=set())
    return _build_scratch_project(parsed, include_values=True)


def _parse_project(project: ProjectInput) -> dict[str, Any]:
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


def _is_snap_snapshot(project: dict[str, Any]) -> bool:
    return isinstance(project.get("type"), str)


def _build_snap_node(
    raw: Mapping[str, Any],
    *,
    include_values: bool,
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
    if include_values and "value" in raw:
        value = _snap_value(raw["value"], path)
        label = f"{node_type}={value}"

    active.add(identity)
    try:
        children = tuple(
            _build_snap_node(
                child,
                include_values=include_values,
                path=child_path,
                active=active,
            )
            for child_path, child in _snap_children(raw, path)
        )
    finally:
        active.remove(identity)
    return AstNode(label, children)


def _snap_children(
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
        keys = _ordered_child_keys(raw, children, path)
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


def _ordered_child_keys(
    raw: Mapping[str, Any],
    children: Mapping[Any, Any],
    path: str,
) -> list[Any]:
    declared_order = raw.get("children-order")
    if declared_order is None:
        return list(children)
    if not isinstance(declared_order, list):
        raise ProgramFormatError(f"children-order at {path} must be a list")

    ordered: list[Any] = []
    for key in declared_order:
        if key not in children:
            raise ProgramFormatError(
                f"children-order at {path} references missing child {key!r}"
            )
        if key not in ordered:
            ordered.append(key)
    ordered.extend(key for key in children if key not in ordered)
    return ordered


def _snap_value(raw: Any, path: str) -> str:
    if raw is None:
        return "null"
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (str, int, float)):
        return str(raw)
    raise ProgramFormatError(f"Snap AST value at {path} must be scalar")


def _build_scratch_project(
    project: dict[str, Any],
    *,
    include_values: bool,
) -> AstNode:
    targets_in = project.get("targets")
    if not isinstance(targets_in, list):
        return AstNode("program")

    target_nodes: list[AstNode] = []
    for raw in targets_in:
        if not isinstance(raw, dict):
            continue
        node = _build_target_with_values(raw) if include_values else _build_target(raw)
        if node is not None:
            target_nodes.append(node)

    target_nodes.sort(key=_serialize)
    return AstNode("program", tuple(target_nodes))


def _build_target(raw: dict[str, Any]) -> AstNode | None:
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, dict):
        return None
    blocks: dict[str, dict[str, Any]] = {
        bid: block
        for bid, block in raw_blocks.items()
        if isinstance(bid, str) and isinstance(block, dict)
    }
    if not blocks:
        return None

    visited: set[str] = set()
    head_ids = sorted(
        bid for bid, block in blocks.items() if block.get("topLevel") is True
    )
    scripts: list[AstNode] = []
    for head_id in head_ids:
        if head_id in visited:
            continue
        chain = _build_chain(head_id, blocks, visited)
        scripts.append(AstNode("script", tuple(chain)))
    scripts.sort(key=_serialize)

    name = raw.get("name")
    label = f"target:{name}" if isinstance(name, str) else "target:"
    return AstNode(label, tuple(scripts))


def _build_target_with_values(raw: dict[str, Any]) -> AstNode | None:
    raw_blocks = raw.get("blocks")
    if not isinstance(raw_blocks, dict):
        return None
    blocks: dict[str, dict[str, Any]] = {
        bid: block
        for bid, block in raw_blocks.items()
        if isinstance(bid, str) and isinstance(block, dict)
    }
    if not blocks:
        return None

    visited: set[str] = set()
    head_ids = sorted(
        bid for bid, block in blocks.items() if block.get("topLevel") is True
    )
    scripts: list[AstNode] = []
    for head_id in head_ids:
        if head_id in visited:
            continue
        chain = _build_chain_with_values(head_id, blocks, visited)
        scripts.append(AstNode("script", tuple(chain)))
    scripts.sort(key=_serialize)

    name = raw.get("name")
    label = f"target:{name}" if isinstance(name, str) else "target:"
    return AstNode(label, tuple(scripts))


def _build_chain(
    head_id: str,
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> list[AstNode]:
    out: list[AstNode] = []
    cur: str | None = head_id
    local_seen: set[str] = set()
    while isinstance(cur, str):
        if cur in local_seen:
            raise ProgramFormatError(f"cycle detected at block {cur!r}")
        if cur in visited:
            raise ProgramFormatError(
                f"block {cur!r} is referenced from multiple chains"
            )
        block = blocks.get(cur)
        if not isinstance(block, dict):
            break
        local_seen.add(cur)
        visited.add(cur)
        out.append(_build_block(block, blocks, visited))
        nxt = block.get("next")
        cur = nxt if isinstance(nxt, str) else None
    return out


def _build_chain_with_values(
    head_id: str,
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> list[AstNode]:
    out: list[AstNode] = []
    cur: str | None = head_id
    local_seen: set[str] = set()
    while isinstance(cur, str):
        if cur in local_seen:
            raise ProgramFormatError(f"cycle detected at block {cur!r}")
        if cur in visited:
            raise ProgramFormatError(
                f"block {cur!r} is referenced from multiple chains"
            )
        block = blocks.get(cur)
        if not isinstance(block, dict):
            break
        local_seen.add(cur)
        visited.add(cur)
        out.append(_build_block_with_values(block, blocks, visited))
        nxt = block.get("next")
        cur = nxt if isinstance(nxt, str) else None
    return out


def _build_block(
    block: dict[str, Any],
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> AstNode:
    opcode_raw = block.get("opcode")
    opcode = opcode_raw if isinstance(opcode_raw, str) else ""

    raw_inputs = block.get("inputs")
    children: list[AstNode] = []
    if isinstance(raw_inputs, dict):
        for slot_name in sorted(raw_inputs):
            if not isinstance(slot_name, str):
                continue
            children.extend(_build_input(raw_inputs[slot_name], blocks, visited))

    return AstNode(opcode, tuple(children))


def _build_block_with_values(
    block: dict[str, Any],
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> AstNode:
    opcode_raw = block.get("opcode")
    opcode = opcode_raw if isinstance(opcode_raw, str) else ""

    input_nodes: list[AstNode] = []
    body_nodes: list[AstNode] = []
    raw_inputs = block.get("inputs")
    if isinstance(raw_inputs, dict):
        for slot_name, raw_input in raw_inputs.items():
            if not isinstance(slot_name, str):
                continue
            node = _build_input_with_values(
                slot_name,
                raw_input,
                blocks,
                visited,
            )
            if node is None:
                continue
            if slot_name.startswith("SUBSTACK"):
                body_nodes.append(node)
            else:
                input_nodes.append(node)

    field_nodes: list[AstNode] = []
    raw_fields = block.get("fields")
    if isinstance(raw_fields, dict):
        for field_name, raw_field in raw_fields.items():
            if not isinstance(field_name, str):
                continue
            value = _field_value(raw_field)
            if value is not None:
                field_nodes.append(AstNode(f"field:{field_name}={value}"))

    return AstNode(opcode, tuple(input_nodes + field_nodes + body_nodes))


def _build_input(
    raw: Any,
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> list[AstNode]:
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    value = raw[1]
    if not isinstance(value, str):
        return []
    target = blocks.get(value)
    if not isinstance(target, dict):
        return []
    if target.get("shadow") is True:
        return []
    return _build_chain(value, blocks, visited)


def _build_input_with_values(
    slot_name: str,
    raw: Any,
    blocks: dict[str, dict[str, Any]],
    visited: set[str],
) -> AstNode | None:
    if not isinstance(raw, list) or len(raw) < 2:
        return None

    value = raw[1]
    if isinstance(value, str):
        target = blocks.get(value)
        if isinstance(target, dict):
            if target.get("shadow") is True:
                shadow_value = _shadow_block_value(target)
                if shadow_value is None:
                    return None
                return AstNode(f"input:{slot_name}={shadow_value}")
            label = _input_label(slot_name)
            children = _build_chain_with_values(value, blocks, visited)
            return AstNode(label, tuple(children))

    scalar = _literal_value(value)
    if scalar is not None:
        return AstNode(f"input:{slot_name}={scalar}")

    if len(raw) >= 3:
        scalar = _literal_value(raw[2])
        if scalar is not None:
            return AstNode(f"input:{slot_name}={scalar}")
        if isinstance(raw[2], str):
            target = blocks.get(raw[2])
            if isinstance(target, dict) and target.get("shadow") is True:
                shadow_value = _shadow_block_value(target)
                if shadow_value is not None:
                    return AstNode(f"input:{slot_name}={shadow_value}")

    return None


def _input_label(slot_name: str) -> str:
    prefix = "body" if slot_name.startswith("SUBSTACK") else "input"
    return f"{prefix}:{slot_name}"


def _shadow_block_value(block: dict[str, Any]) -> str | None:
    raw_fields = block.get("fields")
    if isinstance(raw_fields, dict):
        for raw_field in raw_fields.values():
            value = _field_value(raw_field)
            if value is not None:
                return value
    raw_inputs = block.get("inputs")
    if isinstance(raw_inputs, dict):
        for raw_input in raw_inputs.values():
            value = _literal_value(raw_input)
            if value is not None:
                return value
    return None


def _field_value(raw: Any) -> str | None:
    if isinstance(raw, list) and raw:
        return _string_value(raw[0])
    return _string_value(raw)


def _literal_value(raw: Any) -> str | None:
    if isinstance(raw, list):
        if len(raw) >= 2:
            return _string_value(raw[1])
        return None
    return _string_value(raw)


def _string_value(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return "true" if raw else "false"
    if isinstance(raw, (str, int, float)):
        return str(raw)
    return None


def _serialize(node: AstNode) -> str:
    return f"({node.name}{''.join(_serialize(c) for c in node.children)})"
