from __future__ import annotations

from pathlib import Path

from learnlab_team5.database import Repository, ensure_index


def test_index_preserves_transitions_undo_and_sessions(dataset_dir: Path, tmp_path: Path) -> None:
    index_path = tmp_path / "history.sqlite3"
    result = ensure_index(dataset_dir, index_path=index_path)
    repository = Repository(result.path)

    assert result.rebuilt is True
    assert repository.assignments() == [
        {
            "assignment_id": "demoLab",
            "title": "Demonstration Lab",
            "assignment_type": "In-Lab",
            "due_date": "2017-01-26T00:00:00",
            "student_count": 2,
            "snapshot_count": 5,
        }
    ]

    steps = repository.history("demoLab", "student/one")
    assert [step["code_state_id"] for step in steps] == ["A", "B", "A", "C"]
    assert [step["event_id"] for step in steps] == [0, 2, 3, 5]
    assert steps[-1]["event_type"] == "Run.Program"
    assert steps[-1]["session_changed"] is True
    assert steps[0]["previous_code_state_id"] is None
    assert steps[2]["previous_code_state_id"] == "B"


def test_index_is_reused_when_sources_are_unchanged(dataset_dir: Path, tmp_path: Path) -> None:
    index_path = tmp_path / "history.sqlite3"
    first = ensure_index(dataset_dir, index_path=index_path)
    second = ensure_index(dataset_dir, index_path=index_path)

    assert first.rebuilt is True
    assert second.rebuilt is False
    assert first.path == second.path


def test_index_rebuilds_when_a_source_changes(dataset_dir: Path, tmp_path: Path) -> None:
    index_path = tmp_path / "history.sqlite3"
    ensure_index(dataset_dir, index_path=index_path)
    markdown = dataset_dir / "Assignments.md"
    markdown.write_text(markdown.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert ensure_index(dataset_dir, index_path=index_path).rebuilt is True


def test_snapshot_diff_ast_and_blank_state(dataset_dir: Path, tmp_path: Path) -> None:
    repository = Repository(ensure_index(dataset_dir, index_path=tmp_path / "db.sqlite3").path)

    snapshot = repository.snapshot("B", "A")
    assert snapshot is not None
    assert snapshot["pseudocode_available"] is True
    assert snapshot["ast_available"] is True
    assert snapshot["ast_nodes"] == [
        {"name": "snapshot", "depth": 0},
        {"name": "stage", "depth": 1},
        {"name": "move", "depth": 2},
    ]
    assert snapshot["ast_with_values_nodes"][-1] == {"name": "move=10", "depth": 2}
    assert snapshot["changed_lines"] == [2, 3, 4]
    assert {block["kind"] for block in snapshot["diff"]} >= {"equal", "insert"}

    malformed = repository.snapshot("C", "A")
    assert malformed is not None
    assert malformed["ast_available"] is False
    assert malformed["ast_error"] == "The stored AST is not valid JSON."
    assert malformed["ast_nodes"] == []
    assert malformed["ast_with_values_nodes"] == []

    blank = repository.snapshot("914")
    assert blank is not None
    assert blank["pseudocode_available"] is False
    assert blank["ast_available"] is False
    assert blank["ast_nodes"] == []
    assert blank["diff"] == []


def test_student_search_escapes_sql_wildcards(dataset_dir: Path, tmp_path: Path) -> None:
    repository = Repository(ensure_index(dataset_dir, index_path=tmp_path / "db.sqlite3").path)

    assert [row["subject_id"] for row in repository.students("demoLab", "student/")] == [
        "student/one"
    ]
    assert repository.students("demoLab", "%") == []
