from __future__ import annotations

from pathlib import Path

from learnlab_team5.opcodes import collect_opcodes, main


def test_collect_opcodes_recursively_counts_node_types(dataset_dir: Path) -> None:
    opcodes, invalid_asts = collect_opcodes(dataset_dir / "CodeStates.csv")

    assert opcodes == {"snapshot": 2, "stage": 2, "move": 1}
    assert invalid_asts == 1


def test_opcode_cli_prints_sorted_unique_values(
    dataset_dir: Path,
    capsys,
) -> None:
    main(["--data-dir", str(dataset_dir)])

    output = capsys.readouterr()
    assert output.out.splitlines() == ["move", "snapshot", "stage"]
    assert output.err == "Warning: skipped 1 invalid nonblank AST.\n"


def test_opcode_cli_can_include_occurrence_counts(
    dataset_dir: Path,
    capsys,
) -> None:
    main(["--data-dir", str(dataset_dir), "--counts"])

    output = capsys.readouterr()
    assert output.out.splitlines() == ["move\t1", "snapshot\t2", "stage\t2"]
