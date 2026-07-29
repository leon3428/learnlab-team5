"""Dataset ingestion and read-only query helpers."""

from __future__ import annotations

import csv
import difflib
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from snap_ast import AstNode, ProgramFormatError, to_ast, to_ast_with_values

SCHEMA_VERSION = 1
SOURCE_FILES = ("Assignment.csv", "Assignments.md", "MainTable.csv", "CodeStates.csv")


class DatasetError(RuntimeError):
    """Raised when the supplied dataset cannot be indexed."""


@dataclass(frozen=True)
class IndexResult:
    path: Path
    rebuilt: bool


def _signature(data_dir: Path) -> dict[str, dict[str, int]]:
    signature: dict[str, dict[str, int]] = {}
    missing: list[str] = []
    for name in SOURCE_FILES:
        path = data_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        stat = path.stat()
        signature[name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if missing:
        joined = ", ".join(missing)
        raise DatasetError(f"Missing required dataset files in {data_dir}: {joined}")
    return signature


def _is_current(index_path: Path, signature: dict[str, dict[str, int]]) -> bool:
    if not index_path.is_file():
        return False
    try:
        with sqlite3.connect(index_path) as connection:
            rows = dict(connection.execute("SELECT key, value FROM metadata"))
        return (
            int(rows.get("schema_version", "-1")) == SCHEMA_VERSION
            and json.loads(rows.get("source_signature", "{}")) == signature
        )
    except (sqlite3.Error, ValueError, json.JSONDecodeError):
        return False


def ensure_index(
    data_dir: Path | str,
    *,
    force: bool = False,
    index_path: Path | str | None = None,
) -> IndexResult:
    """Create or reuse the generated SQLite index."""

    resolved_data_dir = Path(data_dir).expanduser().resolve()
    signature = _signature(resolved_data_dir)
    resolved_index = (
        Path(index_path).expanduser().resolve()
        if index_path is not None
        else resolved_data_dir / ".learnlab-history.sqlite3"
    )

    if not force and _is_current(resolved_index, signature):
        return IndexResult(resolved_index, rebuilt=False)

    resolved_index.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".learnlab-history-",
        suffix=".sqlite3",
        dir=resolved_index.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        _build_index(resolved_data_dir, temporary_path, signature)
        os.replace(temporary_path, resolved_index)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return IndexResult(resolved_index, rebuilt=True)


def _connect(path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE assignments (
            assignment_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            assignment_type TEXT NOT NULL,
            due_date TEXT NOT NULL,
            sort_order INTEGER NOT NULL
        );

        CREATE TABLE code_states (
            code_state_id TEXT PRIMARY KEY,
            ast_json TEXT,
            pseudocode TEXT,
            ast_valid INTEGER NOT NULL,
            pseudocode_available INTEGER NOT NULL
        );

        CREATE TABLE timeline_steps (
            assignment_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            step_index INTEGER NOT NULL,
            event_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            client_timestamp TEXT NOT NULL,
            client_timezone TEXT NOT NULL,
            session_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            code_state_id TEXT NOT NULL,
            session_changed INTEGER NOT NULL,
            PRIMARY KEY (assignment_id, subject_id, step_index)
        );

        CREATE TABLE history_summary (
            assignment_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            snapshot_count INTEGER NOT NULL,
            first_timestamp TEXT NOT NULL,
            last_timestamp TEXT NOT NULL,
            PRIMARY KEY (assignment_id, subject_id)
        );

        CREATE INDEX timeline_lookup
            ON timeline_steps (assignment_id, subject_id, step_index);
        CREATE INDEX summary_by_assignment
            ON history_summary (assignment_id, subject_id);
        """
    )


def _assignment_titles(markdown: str) -> dict[str, str]:
    pattern = re.compile(
        r"^##\s+(.+?)\s*$.*?^\*\*ID\*\*:\s*`([^`]+)`",
        re.MULTILINE | re.DOTALL,
    )
    return {assignment_id: title for title, assignment_id in pattern.findall(markdown)}


def _humanize_assignment_id(assignment_id: str) -> str:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])", " ", assignment_id)
    return spaced.replace("_", " ").strip().title()


def _require_columns(reader: csv.DictReader, required: set[str], filename: str) -> None:
    columns = set(reader.fieldnames or ())
    missing = sorted(required - columns)
    if missing:
        raise DatasetError(f"{filename} is missing required columns: {', '.join(missing)}")


def _import_assignments(connection: sqlite3.Connection, data_dir: Path) -> None:
    titles = _assignment_titles((data_dir / "Assignments.md").read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str, str, int]] = []
    with (data_dir / "Assignment.csv").open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        _require_columns(
            reader,
            {"AssignmentID", "Type", "ApproxDueDate"},
            "Assignment.csv",
        )
        for sort_order, row in enumerate(reader):
            assignment_id = row["AssignmentID"]
            rows.append(
                (
                    assignment_id,
                    titles.get(assignment_id, _humanize_assignment_id(assignment_id)),
                    row["Type"],
                    row["ApproxDueDate"],
                    sort_order,
                )
            )
    connection.executemany(
        """
        INSERT INTO assignments
            (assignment_id, title, assignment_type, due_date, sort_order)
        VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _import_code_states(connection: sqlite3.Connection, data_dir: Path) -> None:
    batch: list[tuple[str, str | None, str | None, int, int]] = []
    with (data_dir / "CodeStates.csv").open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        _require_columns(reader, {"CodeStateID", "Code", "Pseudocode"}, "CodeStates.csv")
        for row in reader:
            ast_text = row["Code"].strip()
            pseudocode = row["Pseudocode"]
            ast_valid = 0
            if ast_text:
                try:
                    json.loads(ast_text)
                    ast_valid = 1
                except json.JSONDecodeError:
                    ast_valid = 0
            batch.append(
                (
                    row["CodeStateID"],
                    ast_text or None,
                    pseudocode if pseudocode.strip() else None,
                    ast_valid,
                    int(bool(pseudocode.strip())),
                )
            )
            if len(batch) >= 1_000:
                connection.executemany(
                    "INSERT INTO code_states VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            connection.executemany(
                "INSERT INTO code_states VALUES (?, ?, ?, ?, ?)",
                batch,
            )


def _timeline_rows(data_dir: Path) -> Iterator[tuple[Any, ...]]:
    last_state: dict[tuple[str, str], str] = {}
    last_snapshot_session: dict[tuple[str, str], str] = {}
    step_counts: defaultdict[tuple[str, str], int] = defaultdict(int)
    prior_event_id = -1

    with (data_dir / "MainTable.csv").open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        _require_columns(
            reader,
            {
                "AssignmentID",
                "SubjectID",
                "CodeStateID",
                "EventID",
                "EventType",
                "ClientTimestamp",
                "ClientTimezone",
                "SessionID",
                "ProjectID",
            },
            "MainTable.csv",
        )
        for row in reader:
            event_id = int(row["EventID"])
            if event_id <= prior_event_id:
                raise DatasetError("MainTable.csv must be ordered by increasing EventID")
            prior_event_id = event_id

            key = (row["AssignmentID"], row["SubjectID"])
            state_id = row["CodeStateID"]
            if not state_id or last_state.get(key) == state_id:
                continue

            session_id = row["SessionID"]
            previous_session = last_snapshot_session.get(key)
            step_index = step_counts[key]
            yield (
                key[0],
                key[1],
                step_index,
                event_id,
                row["EventType"],
                row["ClientTimestamp"],
                row["ClientTimezone"],
                session_id,
                row["ProjectID"],
                state_id,
                int(previous_session is not None and previous_session != session_id),
            )
            last_state[key] = state_id
            last_snapshot_session[key] = session_id
            step_counts[key] += 1


def _import_timeline(connection: sqlite3.Connection, data_dir: Path) -> None:
    batch: list[tuple[Any, ...]] = []
    for row in _timeline_rows(data_dir):
        batch.append(row)
        if len(batch) >= 2_000:
            connection.executemany(
                "INSERT INTO timeline_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                batch,
            )
            batch.clear()
    if batch:
        connection.executemany(
            "INSERT INTO timeline_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )

    connection.execute(
        """
        INSERT INTO history_summary
        SELECT
            assignment_id,
            subject_id,
            COUNT(*),
            MIN(client_timestamp),
            MAX(client_timestamp)
        FROM timeline_steps
        GROUP BY assignment_id, subject_id
        """
    )


def _build_index(
    data_dir: Path,
    index_path: Path,
    signature: dict[str, dict[str, int]],
) -> None:
    with _connect(index_path) as connection:
        _create_schema(connection)
        _import_assignments(connection, data_dir)
        _import_code_states(connection, data_dir)
        _import_timeline(connection, data_dir)
        connection.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            (
                ("schema_version", str(SCHEMA_VERSION)),
                ("source_signature", json.dumps(signature, sort_keys=True)),
            ),
        )
        connection.commit()
        connection.execute("PRAGMA optimize")


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class Repository:
    """Read-only access to an indexed dataset."""

    def __init__(self, index_path: Path | str):
        self.index_path = Path(index_path)

    def _connection(self) -> sqlite3.Connection:
        connection = _connect(self.index_path)
        connection.execute("PRAGMA query_only = ON")
        return connection

    def assignments(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    a.assignment_id,
                    a.title,
                    a.assignment_type,
                    a.due_date,
                    COUNT(h.subject_id) AS student_count,
                    COALESCE(SUM(h.snapshot_count), 0) AS snapshot_count
                FROM assignments AS a
                LEFT JOIN history_summary AS h USING (assignment_id)
                GROUP BY a.assignment_id
                ORDER BY a.sort_order
                """
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def assignment_exists(self, assignment_id: str) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return row is not None

    def students(self, assignment_id: str, query: str = "") -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT subject_id, snapshot_count, first_timestamp, last_timestamp
                FROM history_summary
                WHERE assignment_id = ?
                  AND subject_id LIKE ? ESCAPE '\\'
                ORDER BY subject_id COLLATE NOCASE
                """,
                (assignment_id, f"%{_escape_like(query)}%"),
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def history(self, assignment_id: str, subject_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    step_index,
                    event_id,
                    event_type,
                    client_timestamp,
                    client_timezone,
                    session_id,
                    project_id,
                    code_state_id,
                    session_changed,
                    LAG(code_state_id) OVER (ORDER BY step_index) AS previous_code_state_id
                FROM timeline_steps
                WHERE assignment_id = ? AND subject_id = ?
                ORDER BY step_index
                """,
                (assignment_id, subject_id),
            ).fetchall()
        result = [_row_dict(row) for row in rows]
        for row in result:
            row["session_changed"] = bool(row["session_changed"])
        return result

    def snapshot(
        self,
        code_state_id: str,
        previous_code_state_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            current = connection.execute(
                "SELECT * FROM code_states WHERE code_state_id = ?",
                (code_state_id,),
            ).fetchone()
            previous = (
                connection.execute(
                    "SELECT pseudocode FROM code_states WHERE code_state_id = ?",
                    (previous_code_state_id,),
                ).fetchone()
                if previous_code_state_id is not None
                else None
            )
        if current is None:
            return None

        pseudocode = current["pseudocode"]
        ast_text = current["ast_json"]
        ast_pretty: str | None = None
        ast_error: str | None = None
        ast_nodes: list[dict[str, Any]] = []
        ast_with_values_nodes: list[dict[str, Any]] = []
        if ast_text and current["ast_valid"]:
            parsed_ast = json.loads(ast_text)
            ast_pretty = json.dumps(parsed_ast, indent=2, ensure_ascii=False)
            try:
                ast_nodes = _flatten_ast(to_ast(parsed_ast))
                ast_with_values_nodes = _flatten_ast(to_ast_with_values(parsed_ast))
            except ProgramFormatError as error:
                ast_error = f"The stored AST cannot be interpreted: {error}"
        elif ast_text:
            ast_error = "The stored AST is not valid JSON."

        previous_pseudocode = previous["pseudocode"] if previous is not None else None
        diff, changed_lines = _line_diff(previous_pseudocode, pseudocode)
        return {
            "code_state_id": code_state_id,
            "pseudocode": pseudocode,
            "pseudocode_available": bool(current["pseudocode_available"]),
            "ast": ast_pretty,
            "ast_available": ast_pretty is not None,
            "ast_error": ast_error,
            "ast_nodes": ast_nodes,
            "ast_with_values_nodes": ast_with_values_nodes,
            "diff": diff,
            "changed_lines": changed_lines,
        }

    def statistics(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM assignments) AS assignments,
                    (SELECT COUNT(DISTINCT subject_id) FROM history_summary) AS students,
                    (SELECT COUNT(*) FROM history_summary) AS histories,
                    (SELECT COUNT(*) FROM code_states) AS code_states,
                    (SELECT COUNT(*) FROM timeline_steps) AS timeline_steps
                """
            ).fetchone()
        return _row_dict(row)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _flatten_ast(root: AstNode) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    stack: list[tuple[AstNode, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        nodes.append({"name": node.name, "depth": depth})
        stack.extend((child, depth + 1) for child in reversed(node.children))
    return nodes


def _line_diff(
    previous: str | None,
    current: str | None,
) -> tuple[list[dict[str, Any]], list[int]]:
    if previous is None or current is None:
        return [], []

    old_lines = previous.splitlines()
    new_lines = current.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    blocks: list[dict[str, Any]] = []
    changed_lines: list[int] = []

    def add_block(
        kind: str,
        lines: list[str],
        old_start: int,
        new_start: int,
    ) -> None:
        if lines:
            blocks.append(
                {
                    "kind": kind,
                    "lines": lines,
                    "old_start": old_start + 1,
                    "new_start": new_start + 1,
                }
            )

    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            add_block("equal", new_lines[new_start:new_end], old_start, new_start)
        elif tag == "delete":
            add_block("delete", old_lines[old_start:old_end], old_start, new_start)
        elif tag == "insert":
            add_block("insert", new_lines[new_start:new_end], old_start, new_start)
            changed_lines.extend(range(new_start + 1, new_end + 1))
        elif tag == "replace":
            add_block("delete", old_lines[old_start:old_end], old_start, new_start)
            add_block("insert", new_lines[new_start:new_end], old_end, new_start)
            changed_lines.extend(range(new_start + 1, new_end + 1))

    return blocks, changed_lines
