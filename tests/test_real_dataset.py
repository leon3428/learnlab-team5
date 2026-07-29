from __future__ import annotations

from pathlib import Path

import pytest

from learnlab_team5.database import Repository, ensure_index


def test_complete_supplied_dataset_counts() -> None:
    data_dir = Path(__file__).parents[1] / "data"
    if not (data_dir / "MainTable.csv").is_file():
        pytest.skip("The complete dataset is not available")

    repository = Repository(ensure_index(data_dir).path)
    assert repository.statistics() == {
        "assignments": 6,
        "students": 55,
        "histories": 266,
        "code_states": 40_607,
        "timeline_steps": 51_065,
    }
