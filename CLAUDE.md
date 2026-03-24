# CLAUDE.md - Project Context for Claude Code

## Product Direction

> Full product context (Founder Memo, PRD, RFC, TRD) is in [`PRODUCT.md`](./PRODUCT.md).

**Mission**: Turn the existing graph engine into a durable repo memory product for Claude Code.
**Core promise**: Stop re-explaining your repo to AI every session.

This is a **fork and extend** — not a greenfield rewrite. The existing graph/parse/MCP infrastructure is Layer A. We are adding Layers B and C on top.

**What the existing project already provides:**
- code graph / structural repo understanding
- parsing and graph/indexing infrastructure (Tree-sitter + SQLite)
- incremental update foundations
- CLI entrypoint (`cli.py`)
- MCP / Claude tool plumbing (`tools.py`, `main.py`)

**What we are adding:**
- `code_review_graph/memory/` — new memory subsystem
- `.agent-memory/` — durable repo artifacts committed to Git
- task-aware context packs for Claude Code
- automatic memory refresh tied to repo changes
- human overrides and safe boundaries
- freshness / confidence / source metadata

**Architecture layers**:
- **Layer A** (current codebase): code intelligence engine — parsing, graph, impact analysis
- **Layer B** (to build): memory engine — classifies areas, generates `.agent-memory/` artifacts, handles refresh and overrides
- **Layer C** (to build): agent interface — CLI commands + MCP tools that serve task context packs to Claude Code

**Target `.agent-memory/` layout** (eventual):
```
.agent-memory/
  repo.md
  architecture.md
  features/*.md
  modules/*.md
  tasks/*.md
  changes/recent.md
  changes/hotspots.md
  rules/conventions.md
  rules/safe-boundaries.md
  overrides/*.yaml
  metadata/manifest.json
  metadata/freshness.json
  metadata/confidence.json
  metadata/sources.json
```

---

## Project Overview

**code-review-graph** is a persistent, incrementally-updated knowledge graph for token-efficient code reviews with Claude Code. It parses codebases using Tree-sitter, builds a structural graph in SQLite, and exposes it via MCP tools.

## Architecture

- **Core Package**: `code_review_graph/` (Python 3.10+)
  - `parser.py` — Tree-sitter multi-language AST parser (14 languages including Vue SFC and Solidity)
  - `graph.py` — SQLite-backed graph store (nodes, edges, BFS impact analysis)
  - `tools.py` — 9 MCP tool implementations
  - `incremental.py` — Git-based change detection, file watching
  - `embeddings.py` — Optional vector embeddings (local or Google Gemini)
  - `visualization.py` — D3.js interactive HTML graph generator
  - `cli.py` — CLI entry point (`code-review-graph build/update/watch/serve/...`)
  - `main.py` — FastMCP server entry point (stdio transport)

- **VS Code Extension**: `code-review-graph-vscode/` (TypeScript)
  - Separate subproject with its own `package.json`, `tsconfig.json`
  - Reads from `.code-review-graph/graph.db` via SQLite

- **Database**: `.code-review-graph/graph.db` (SQLite, WAL mode)

## Key Commands

```bash
# Development
uv run pytest tests/ --tb=short -q          # Run tests (182 tests)
uv run ruff check code_review_graph/        # Lint
uv run mypy code_review_graph/ --ignore-missing-imports --no-strict-optional

# Build & test
uv run code-review-graph build              # Full graph build
uv run code-review-graph update             # Incremental update
uv run code-review-graph status             # Show stats
uv run code-review-graph serve              # Start MCP server
```

## Ticket Rules (apply to every task)

### Product Context
We are **extending** the existing Python project — not building a separate tool and not rewriting the engine. The product is a Git-native repo memory system for Claude Code built on top of the existing graph/parse/MCP foundation.

**On every ticket: do only what the ticket asks. Avoid touching unrelated subsystems. Keep changes focused and reviewable.**

### High-Level Rules
1. Do not rewrite the existing graph engine unless absolutely necessary.
2. Do not rename the project/package yet.
3. Do not build cloud, SaaS, GUI, multi-repo, or enterprise features.
4. Keep all outputs concise, stable, deterministic, and Git-friendly.
5. Prefer incremental updates over full regeneration.
6. Keep heavy runtime state local only; commit only durable artifacts into `.agent-memory/`.
7. Business logic must be shared between CLI and MCP, not duplicated.
8. Keep the architecture layered and modular.
9. Every output must work on messy real-world repos, not only perfect repos.

### Technical Rules
1. Stay in Python for V1.
2. Reuse the existing CLI entrypoint and package structure.
3. Add new memory subsystem under `code_review_graph/memory/`.
4. Avoid large refactors outside the scope of the current ticket.
5. Add tests for everything you add.
6. Use clear typing and docstrings.
7. Use stable sorting and deterministic writing to avoid noisy Git diffs.
8. Keep generated markdown compact and practical.
9. Do not over-engineer with unnecessary abstraction.
10. Do not silently invent behavior that was not requested.

### Product Behavior Rules
1. The graph is the engine. The memory is the product.
2. Memory should be agent-first but human-readable.
3. Memory should be committed to Git; heavy state stays local-only.
4. The killer feature is task-aware context preparation.
5. Repo summaries are not enough — memory must support actual task execution.
6. Human corrections and rules must be supported.
7. The system must work even if repo docs are weak or absent.
8. Confidence/freshness/source traceability should exist wherever practical.

### Required Output at End of Every Ticket
1. Summary of changes made
2. Files created
3. Files modified
4. Tests added or updated
5. Commands to run locally
6. Design notes
7. Any risks or follow-up suggestions

---

## Code Conventions

- **Line length**: 100 chars (ruff)
- **Python target**: 3.10+
- **SQL**: Always use parameterized queries (`?` placeholders), never f-string values
- **Error handling**: Catch specific exceptions, log with `logger.warning/error`
- **Thread safety**: `threading.Lock` for shared caches, `check_same_thread=False` for SQLite
- **Node names**: Always sanitize via `_sanitize_name()` before returning to MCP clients
- **File reads**: Read bytes once, hash, then parse (TOCTOU-safe pattern)

## Security Invariants

- No `eval()`, `exec()`, `pickle`, or `yaml.unsafe_load()`
- No `shell=True` in subprocess calls
- `_validate_repo_root()` prevents path traversal via repo_root parameter
- `_sanitize_name()` strips control characters, caps at 256 chars (prompt injection defense)
- `escH()` in visualization escapes HTML entities including quotes and backticks
- SRI hash on D3.js CDN script tag
- API keys only from environment variables, never hardcoded

## Test Structure

- `tests/test_parser.py` — Parser correctness, cross-file resolution
- `tests/test_graph.py` — Graph CRUD, stats, impact radius
- `tests/test_tools.py` — MCP tool integration tests
- `tests/test_visualization.py` — Export, HTML generation, C++ resolution
- `tests/test_incremental.py` — Build, update, migration, git ops
- `tests/test_multilang.py` — 14 language parsing tests (including Vue and Solidity)
- `tests/test_embeddings.py` — Vector encode/decode, similarity, store
- `tests/fixtures/` — Sample files for each supported language

## CI Pipeline

- **lint**: ruff on Python 3.10
- **type-check**: mypy
- **security**: bandit scan
- **test**: pytest matrix (3.10, 3.11, 3.12, 3.13) with 50% coverage minimum
