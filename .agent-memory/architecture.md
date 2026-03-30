# Architecture: graph

> Auto-generated architectural overview. Confidence: 100%. Add corrections in `.agent-memory/overrides/`.

## Major boundaries

- **`code_review_graph/`** — production source code
- **`code-review-graph-vscode/test/`** — test suite (separate from production code)
- **`tests/`** — test suite (separate from production code)
- **`docs/`** — documentation
- **`benchmarks/`** — tooling / scripts
- **`evaluate/`** — tooling / scripts

## Inferred layout

**Pattern**: Single-application repo with dedicated source directory

## Inspect first

- `code_review_graph/` — main source code
- `pyproject.toml` — project metadata and dependencies
- `README.md` — project intent and setup
- `code-review-graph-vscode/test/` — to understand expected behaviour
