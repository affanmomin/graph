# Architecture: graph

> Auto-generated architectural overview. Confidence: 100%. Add corrections in `.agent-memory/overrides/`.

## Major boundaries

- **`benchmarks/`** — production source code
- **`code_review_graph/`** — production source code
- **`tests/`** — test suite (separate from production code)
- **`docs/`** — documentation

## Inferred layout

**Pattern**: Single-application repo with dedicated source directory

## Coupling and ambiguity notes

- Multi-language repo (c, cpp, csharp, go, java, kotlin, php, python, ruby, rust, solidity, swift, typescript, vue). Check language boundaries before refactoring.

## Inspect first

- `benchmarks/` — main source code
- `code_review_graph/` — main source code
- `pyproject.toml` — project metadata and dependencies
- `README.md` — project intent and setup
- `tests/` — to understand expected behaviour
