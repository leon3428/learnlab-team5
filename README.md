# LearnLab Development History Explorer

A local browser application for walking through the pseudocode snapshots in the
LearnLab ProgSnap2 programming-process dataset.

## Run

The repository expects the supplied CSV files in `./data`. Start the explorer with:

```bash
uv run learnlab-team5
```

The first launch builds `data/.learnlab-history.sqlite3`, then opens
<http://127.0.0.1:8000>. Later launches reuse that index until one of the source
files changes.

Useful options:

```text
--data-dir PATH    Read the dataset from a different directory
--port PORT        Use a different local port
--no-browser       Start without opening a browser
--rebuild-index    Force the SQLite index to be regenerated
```

The server binds only to `127.0.0.1`. Student identifiers are already anonymized
in the dataset, and no data leaves the computer.

## List AST opcodes

Print every unique value used in an AST node's `type` field, sorted
alphabetically:

```bash
uv run learnlab-opcodes
```

Add occurrence counts as a tab-separated second column:

```bash
uv run learnlab-opcodes --counts
```

Use `--data-dir PATH` to scan a different `CodeStates.csv`.

## Parse Snap ASTs in Python

The `snap_ast` package accepts both parsed snapshot dictionaries and the JSON
text stored in the database:

```python
import sqlite3

from snap_ast import to_ast, to_ast_with_values, tree_edit_distance

connection = sqlite3.connect("data/.learnlab-history.sqlite3")
before_json = connection.execute(
    "SELECT ast_json FROM code_states WHERE code_state_id = '1'"
).fetchone()[0]
after_json = connection.execute(
    "SELECT ast_json FROM code_states WHERE code_state_id = '2'"
).fetchone()[0]

before = to_ast(before_json)
after = to_ast(after_json)
distance = tree_edit_distance(before, after)
```

`to_ast` keeps node opcodes and ordered children while ignoring snapshot-specific
IDs and values. Use `to_ast_with_values` when literal and user-defined values
should participate in comparisons. The original Scratch project format remains
supported and is detected automatically.

## Learn rubric heatmaps

`snap_ast` can learn rubric-specific node heat from graded Snap ASTs. Full-credit
subtrees are compared with their closest same-depth matches in the other
full-credit and lower-scoring solutions:

```python
from snap_ast import learn_rubric_heatmaps

models, annotated_solutions = learn_rubric_heatmaps(
    solutions,
    rubric_names,
    max_depth=4,
    progress=print,
)
```

The input `solutions` are not modified. Each returned record has an
`ast_heatmap` copy whose nodes contain `rubric_heatmap`,
`rubric_heatmap_best_depth`, and `rubric_heatmap_best_candidate`. See
`notebooks/build_solutions.ipynb` for the complete dataset workflow.

## Match exemplar subtree features

Retain up to five subtree occurrences with heat strictly greater than `0.2`
from every full-credit exemplar, then find the nearest retained occurrence for
each node in another AST:

```python
from snap_ast import (
    learn_rubric_exemplar_features,
    match_rubric_exemplar_features,
)

features = learn_rubric_exemplar_features(
    training_solutions,
    "Variable Increment",
    max_depth=4,
    features_per_exemplar=5,
    minimum_heat=0.2,
)
matches = match_rubric_exemplar_features(features, target_ast)

for match in matches.node_matches:
    print(match.target_node_name, match.minimum_distance)
```

Feature occurrences are not deduplicated. Matching uses normalized tree-edit
distance and compares each feature with the target subtree at the feature's
depth. The result contains the winning feature metadata but does not calculate
a grade.

## Create an analysis database

Create a separate database with exactly two tables:

```bash
uv run learnlab-analysis-db
```

This reads `data/.learnlab-history.sqlite3` in immutable, read-only mode and
creates `data/learnlab-analysis.sqlite3`:

- `grades` has one row per rubric item for each student–task pair.
- `traces` has one row per programming step, with its AST JSON and pseudocode
  directly in the row.

Only exact assignment/project matches with both a rubric and a trace are
included. Blank rubric cells remain SQL `NULL`. The command refuses to replace
an existing output unless `--overwrite` is explicitly supplied, and always
refuses to use the source index as its output.

## What the explorer shows

- Assignment-first navigation across all recorded student histories.
- Every observed code-state transition in `EventID` order, including undo/redo.
- Full pseudocode with changed lines highlighted and a unified changes view.
- Event, timestamp, session, project, and code-state metadata.
- A collapsible pretty-printed copy of the complete AST when available.
- Bookmarkable assignment, student, and step selections.

Consecutive events that reference the same code state are collapsed. The blank
state `914` remains visible as an unavailable snapshot so histories keep their
original shape.

## Tests

```bash
uv run pytest
```

The test suite uses a small synthetic ProgSnap2 fixture and, when `./data` is
present, validates the generated index against the complete supplied dataset.
