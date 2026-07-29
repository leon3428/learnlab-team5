from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    _write_csv(
        data_dir / "Assignment.csv",
        ["AssignmentID", "URL", "Type", "ApproxDueDate"],
        [
            {
                "AssignmentID": "demoLab",
                "URL": "file:///Assignments.md",
                "Type": "In-Lab",
                "ApproxDueDate": "2017-01-26T00:00:00",
            }
        ],
    )
    (data_dir / "Assignments.md").write_text(
        "# Assignments\n\n## Demonstration Lab\n\n**ID**: `demoLab`\n",
        encoding="utf-8",
    )

    ast_a = {"type": "snapshot", "children": [{"type": "stage"}]}
    ast_b = {
        "type": "snapshot",
        "children": [{"type": "stage", "children": [{"type": "move", "value": "10"}]}],
    }
    ast_c = "{not valid json"
    _write_csv(
        data_dir / "CodeStates.csv",
        ["CodeStateID", "Code", "Pseudocode"],
        [
            {"CodeStateID": "A", "Code": json.dumps(ast_a), "Pseudocode": "snapshot {\n  stage\n}"},
            {
                "CodeStateID": "B",
                "Code": json.dumps(ast_b),
                "Pseudocode": "snapshot {\n  stage {\n    move 10\n  }\n}",
            },
            {"CodeStateID": "C", "Code": ast_c, "Pseudocode": "snapshot {\n  say hello\n}"},
            {"CodeStateID": "914", "Code": "", "Pseudocode": ""},
        ],
    )

    columns = [
        "EventType",
        "EventID",
        "Order",
        "SubjectID",
        "Toolnstances",
        "CodeStateID",
        "ClientTimestamp",
        "ClientTimezone",
        "SessionID",
        "ProjectID",
        "CourseID",
        "TermID",
        "AssignmentID",
    ]

    def event(
        event_id: int,
        subject: str,
        state: str,
        event_type: str,
        session: str = "session-one",
    ) -> dict[str, str]:
        return {
            "EventType": event_type,
            "EventID": str(event_id),
            "Order": str(event_id),
            "SubjectID": subject,
            "Toolnstances": "iSnap",
            "CodeStateID": state,
            "ClientTimestamp": f"2017-01-26T10:00:{event_id:02d}",
            "ClientTimezone": "-0500",
            "SessionID": session,
            "ProjectID": f"project-{subject}",
            "CourseID": "course",
            "TermID": "term",
            "AssignmentID": "demoLab",
        }

    _write_csv(
        data_dir / "MainTable.csv",
        columns,
        [
            event(0, "student/one", "A", "Session.Start"),
            event(1, "student/one", "A", "Run.Program"),
            event(2, "student/one", "B", "File.Edit"),
            event(3, "student/one", "A", "File.Edit"),
            event(4, "student/one", "A", "File.Save"),
            event(5, "student/one", "C", "Run.Program", "session-two"),
            event(6, "student_two", "914", "Session.Start"),
        ],
    )
    return data_dir
