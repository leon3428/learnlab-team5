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
