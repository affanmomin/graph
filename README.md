<h1 align="center">code-review-graph</h1>

<p align="center">
  <strong>Graph-powered repo memory for Claude Code. Stop re-explaining your codebase every session.</strong>
</p>

<p align="center">
  <a href="https://github.com/tirth8205/code-review-graph/stargazers"><img src="https://img.shields.io/github/stars/tirth8205/code-review-graph?style=flat-square" alt="Stars"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT Licence"></a>
  <a href="https://github.com/tirth8205/code-review-graph/actions/workflows/ci.yml"><img src="https://github.com/tirth8205/code-review-graph/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square" alt="Python 3.10+"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square" alt="MCP"></a>
  <a href="#"><img src="https://img.shields.io/badge/version-1.8.4-purple.svg?style=flat-square" alt="v1.8.4"></a>
</p>

<br>

Every Claude Code session starts cold. You paste architecture docs. You re-explain the file layout. You repeat the same context you gave last week.

`code-review-graph` fixes this permanently. It parses your repo with Tree-sitter, builds a structural code graph, and writes durable Markdown artifacts to `.agent-memory/` — committed to Git and readable by Claude at every session start. When you start a task, one command surfaces exactly the files, features, and tests Claude needs to know about. Nothing more.

---

## Quick Start

```bash
pip install code-review-graph
cd your-project

# Build the graph and generate memory artifacts (one-time setup)
code-review-graph build
code-review-graph memory init

# Commit the memory to Git so every session starts with context
git add .agent-memory/
git commit -m "chore: add repo memory"
```

Now tell Claude what you're working on:

```bash
code-review-graph memory prepare-context "add rate limiting to the API"
```

Claude gets a focused pack — the relevant features, modules, and files — without reading the entire codebase.

---

## The Four Core Workflows

### 1. Prepare context for a task

```bash
code-review-graph memory prepare-context "fix the streaming bug in the HTTP client"
```

Returns a focused context pack: which features are relevant, which files to read, which tests to run. Capped at 20 files. Powered by graph-assisted relevance scoring — not just keyword matching.

### 2. Explain an area of the codebase

```bash
code-review-graph memory explain authentication
code-review-graph memory explain src/payments/
```

Surfaces the stored memory artifact for a feature or module: its files, test coverage, dependencies, confidence score, and last refresh timestamp. No re-analysis needed — reads from `.agent-memory/` on disk.

### 3. Trace the impact of a change

```bash
code-review-graph memory changed src/auth/middleware.py
code-review-graph memory changed src/payments/
```

Shows which memory areas (features and modules) own the changed files, then uses graph BFS to surface structurally related areas that may also be affected — callers, dependents, import chains — even when those files didn't change directly.

### 4. Refresh memory after commits

```bash
code-review-graph memory refresh          # incremental (only changed areas)
code-review-graph memory refresh --full   # full regeneration
```

Runs automatically as a post-commit hook. Only artifacts whose source files changed are regenerated. Graph expansion identifies structurally related areas that should also refresh.

---

## How It Works

### Two distinct layers

**Layer A — The graph engine (local-only)**

Tree-sitter parses your repo into an AST. Nodes (functions, classes, imports) and edges (calls, inheritance, test coverage, imports) are stored in a SQLite database at `.code-review-graph/graph.db`. This is a performance cache — never committed to Git, rebuilt from source on any machine.

**Layer B — Repo memory (committed to Git)**

The memory subsystem uses the graph as structural truth. It classifies features and modules from filesystem heuristics, then refines confidence scores and dependency maps using real graph signals (internal edge density, IMPORTS\_FROM chains, TESTED\_BY edges). The output is written to `.agent-memory/` as plain Markdown and JSON — human-readable, diff-friendly, and committed to Git.

```
Graph engine (local)          Repo memory (committed)
─────────────────────         ──────────────────────────────
.code-review-graph/           .agent-memory/
  graph.db  ──────────────►     repo.md
  (SQLite)                      architecture.md
  14 languages                  features/<slug>.md
  BFS impact analysis           modules/<slug>.md
  incremental updates           rules/conventions.md
                                rules/safe-boundaries.md
                                overrides/global.yaml
                                metadata/manifest.json
                                metadata/freshness.json
                                metadata/confidence.json
                                metadata/sources.json
```

### What gets committed vs what stays local

| Data | Location | Committed to Git? |
|------|----------|-------------------|
| `.agent-memory/` artifacts | repo root | **Yes** — shared with the whole team |
| `.code-review-graph/graph.db` | repo root | **No** — local index, rebuild any time |
| Embeddings cache | local | **No** |

Commit `.agent-memory/` once. Every subsequent session — and every team member — starts with full context automatically.

### Graph-assisted classification

Classification is not just directory heuristics. The graph engine contributes real structural signals:

- **Confidence adjustment**: features/modules with dense internal call graphs score higher confidence; isolated file clusters score lower
- **Test mapping**: TESTED\_BY edges map test files to the features and modules they cover
- **Dependency resolution**: IMPORTS\_FROM edges build accurate module dependency and dependent maps
- **Context expansion**: `prepare-context` and `changed` use graph BFS to pull in structurally related files that keyword matching alone would miss

---

## `.agent-memory/` layout

```
.agent-memory/
  repo.md                     # high-level repo summary (language, size, entry points)
  architecture.md             # module boundaries, data flows, risky areas
  features/
    <slug>.md                 # one file per detected feature: files, tests, description
  modules/
    <slug>.md                 # one file per code module: files, tests, deps, confidence
  rules/
    conventions.md            # coding conventions inferred + human-added
    safe-boundaries.md        # paths that must not be casually edited
  overrides/
    global.yaml               # human corrections, permanent hints, always-include paths
  metadata/
    manifest.json             # artifact inventory with generation timestamps
    freshness.json            # last refresh per artifact + graph-expanded areas
    confidence.json           # classification confidence per feature/module
    sources.json              # file → feature/module ownership index
```

---

## Human overrides

The system infers what it can from code structure. Tell it what it cannot infer:

```yaml
# .agent-memory/overrides/global.yaml

always_include:
  - docs/architecture.md
  - src/auth/middleware.py

never_edit:
  - migrations/
  - generated/

notes:
  - "The payments module is PCI-scoped — any change needs a security review."
  - "We use a custom JWT library, not PyJWT."

task_hints:
  - pattern: "add endpoint"
    hint: "Register new routes in src/api/router.py and add a test under tests/api/."
  - pattern: "database migration"
    hint: "Use alembic revision --autogenerate; never edit existing migrations."
```

Overrides merge into every `prepare-context` result and are reflected in `rules/conventions.md` and `rules/safe-boundaries.md`.

---

## CLI reference

### Memory commands

```bash
code-review-graph memory init                                # Generate .agent-memory/ artifacts
code-review-graph memory refresh                             # Incremental refresh (post-commit)
code-review-graph memory refresh --full                      # Full regeneration
code-review-graph memory prepare-context "<task>"            # Focused context pack for a task
code-review-graph memory explain <feature|module|path>       # Show stored memory for an area
code-review-graph memory changed <file|dir>                  # Impact analysis for changed files
code-review-graph memory annotate                            # Open override file for editing
```

### Graph commands

```bash
code-review-graph build       # Parse entire codebase into the graph
code-review-graph update      # Incremental update (changed files only)
code-review-graph status      # Graph statistics
code-review-graph watch       # Auto-update on file changes
code-review-graph visualize   # Generate interactive D3.js HTML graph
code-review-graph serve       # Start MCP server
code-review-graph install     # Register MCP server with Claude Code
```

All commands accept `--repo <path>` to target a specific directory.

---

## Features

| Feature | Details |
|---------|---------|
| **Durable repo memory** | `.agent-memory/` committed to Git. Every session and every team member starts with full context. |
| **Graph-assisted classification** | Features and modules classified from real structural signals — edge density, import chains, TESTED\_BY edges. |
| **Task-aware context packs** | `prepare-context` assembles focused packs (≤ 20 files) for a specific task, not the whole codebase. |
| **Graph-expanded impact analysis** | `changed` traces not just direct owners but structurally adjacent areas via BFS. |
| **Incremental refresh** | Only artifacts whose source files changed are regenerated. Post-commit hook runs automatically. |
| **Human overrides** | `overrides/global.yaml` teaches the system domain knowledge it cannot infer from code. |
| **14 languages** | Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++ |
| **Local graph, committed memory** | Graph DB is a local performance cache. Memory artifacts are plain Markdown — diffable, reviewable, committable. |
| **MCP-compatible** | Graph tools exposed via MCP for direct Claude Code integration. |
| **Interactive visualisation** | D3.js force-directed graph with edge-type toggles and search. |

---

## MCP tools

When the MCP server is running, Claude can call these directly:

| Tool | Description |
|------|-------------|
| `build_or_update_graph_tool` | Build or incrementally update the code graph |
| `get_impact_radius_tool` | BFS blast radius of changed files |
| `get_review_context_tool` | Structural summary for review context |
| `query_graph_tool` | Callers, callees, tests, imports, inheritance queries |
| `semantic_search_nodes_tool` | Search code entities by name or meaning |
| `list_graph_stats_tool` | Graph size and health stats |
| `find_large_functions_tool` | Find functions/classes exceeding a line threshold |

---

## Graph engine details

<details>
<summary><strong>Blast-radius analysis</strong></summary>
<br>

When a file changes, the graph traces every caller, dependent, and test that could be affected — the "blast radius" of the change. The `memory changed` command uses this to surface related areas beyond the directly changed files.

</details>

<details>
<summary><strong>Incremental updates in &lt; 2 seconds</strong></summary>
<br>

On every git commit or file save, a hook fires. The graph diffs changed files via SHA-256 hash checks and re-parses only what changed. A 2,900-file project re-indexes in under 2 seconds.

</details>

<details>
<summary><strong>14 supported languages</strong></summary>
<br>

Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++

Each language uses full Tree-sitter grammar support for functions, classes, imports, call sites, inheritance, and test detection.

</details>

<details>
<summary><strong>Excluding paths from indexing</strong></summary>
<br>

Create a `.code-review-graphignore` file in your repository root:

```
generated/**
*.generated.ts
vendor/**
node_modules/**
```

</details>

<details>
<summary><strong>Semantic search (optional)</strong></summary>
<br>

Install optional embeddings support for vector-based code search:

```bash
pip install code-review-graph[embeddings]
```

</details>

---

## Contributing

```bash
git clone https://github.com/tirth8205/code-review-graph.git
cd code-review-graph
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ --tb=short -q
```

<details>
<summary><strong>Adding a new language</strong></summary>
<br>

Edit `code_review_graph/parser.py` and add your extension to `EXTENSION_TO_LANGUAGE` along with node type mappings in `_CLASS_TYPES`, `_FUNCTION_TYPES`, `_IMPORT_TYPES`, and `_CALL_TYPES`. Include a test fixture and open a PR.

</details>

## Licence

MIT. See [LICENSE](LICENSE).

<p align="center">
<br>
<code>pip install code-review-graph && code-review-graph memory init</code>
</p>
