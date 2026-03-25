<h1 align="center">code-review-graph</h1>

<p align="center">
  <strong>Your codebase, remembered. Stop re-explaining your repo to AI every session.</strong>
</p>

<p align="center">
  <a href="https://github.com/tirth8205/code-review-graph/stargazers"><img src="https://img.shields.io/github/stars/tirth8205/code-review-graph?style=flat-square" alt="Stars"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square" alt="MIT Licence"></a>
  <a href="https://github.com/tirth8205/code-review-graph/actions/workflows/ci.yml"><img src="https://github.com/tirth8205/code-review-graph/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg?style=flat-square" alt="Python 3.10+"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-compatible-green.svg?style=flat-square" alt="MCP"></a>
  <a href="#"><img src="https://img.shields.io/badge/status-alpha-orange.svg?style=flat-square" alt="Alpha"></a>
</p>

<br>

Every Claude Code session starts cold. You paste architecture context. You re-explain the file layout. You repeat what you said last week.

`code-review-graph` fixes this permanently. It parses your repo with Tree-sitter, builds a structural graph of calls, imports, and tests, then writes plain Markdown artifacts to `.agent-memory/` — committed to Git, shared with your team, loaded automatically by Claude at every session start.

Before you start a task, one command tells Claude exactly which files matter, which tests cover them, and what not to touch. Nothing more.

---

## Setup

```bash
pip install code-review-graph
cd your-project

code-review-graph install         # add MCP server to .mcp.json, then restart Claude Code
code-review-graph build           # parse codebase into graph (run once; ~10s for 500 files)
code-review-graph memory init     # generate .agent-memory/ artifacts
```

**One manual step** — add this line to your repo's `CLAUDE.md` so Claude loads memory at every session start:

```
@.agent-memory/CLAUDE.md
```

> Claude Code does not auto-load files in subdirectories. The `@` import makes it explicit. `memory init` reminds you if the line is missing.

Then commit the memory so your whole team starts with context:

```bash
git add .agent-memory/
git commit -m "chore: add repo memory"
```

---

## The four workflows

### 1. Prime Claude before starting a task

```bash
$ code-review-graph memory prepare-context "add rate limiting to the auth middleware"

  repo-memory: prepare-context
    task: add rate limiting to the auth middleware

    Relevant features:
      - authentication
      - api-gateway

    Files to inspect:
      - src/auth/middleware.py
      - src/api/router.py
      - src/config/rate_limits.py

    Related tests:
      - tests/test_auth.py
      - tests/test_api_router.py

    Summary:
      Task touches the auth middleware (authentication feature, 91% confidence).
      Rate limiting config sits near request routing — check src/api/router.py.
      Run tests/test_auth.py before and after making changes.
```

Paste this into your Claude Code session. Claude starts with the exact files it needs — not the whole codebase. Context stays under budget.

### 2. Explain any area of the codebase

```bash
$ code-review-graph memory explain authentication

  ## authentication  [feature]  confidence: 91%

  Files (6):
    src/auth/middleware.py, src/auth/tokens.py, src/auth/session.py ...

  Tests (3):
    tests/test_auth.py, tests/test_tokens.py, tests/test_session.py

  Imports from:   src/config/, src/db/
  Imported by:    src/api/router.py, src/api/admin.py
  Last refreshed: 2026-03-25
```

Reads from `.agent-memory/` on disk. No re-analysis, no tokens spent on scanning.

### 3. Trace the impact of a change

```bash
$ code-review-graph memory changed src/auth/middleware.py

  Owner areas: authentication (feature), middleware (module)

  Graph impact — 1-hop BFS:
    Impacted files: src/api/router.py, src/api/admin.py
    Tests to run:   tests/test_auth.py, tests/test_api_router.py

  Recent changes (last 10 commits):
    2026-03-24  fix token expiry edge case   [src/auth/middleware.py]
    2026-03-21  add refresh token endpoint   [src/auth/tokens.py]
    2026-03-18  update session timeout       [src/auth/session.py]
```

Not just "who owns this file" — which callers, dependents, and tests to check, pulled from the graph.

### 4. Refresh memory after commits

```bash
code-review-graph memory refresh          # incremental — only changed areas
code-review-graph memory refresh --full   # regenerate everything
```

Also runs automatically when you run `code-review-graph update`. Only artifacts whose source files changed are regenerated — graph BFS catches structurally related areas too.

---

## How it works

Two distinct layers. One is local-only. One is committed to Git.

```
Graph engine (local, gitignored)     Repo memory (committed to Git)
────────────────────────────────     ──────────────────────────────────────────
.code-review-graph/                  .agent-memory/
  graph.db  ───────────────────────►   CLAUDE.md           ← session bootstrap
  (SQLite)                              repo.md              ← one-page overview
  14 languages                          architecture.md      ← module map
  Tree-sitter AST                       features/<slug>.md   ← per feature
  Call / import graphs                  modules/<slug>.md    ← per module
  BFS impact analysis                   rules/conventions.md
  Incremental SHA-256 updates           rules/safe-boundaries.md
                                        overrides/global.yaml← human corrections
                                        metadata/*.json      ← freshness, confidence
```

**The graph is the engine.** Tree-sitter parses 14 languages into a SQLite graph of nodes (functions, classes, imports) and edges (calls, inheritance, TESTED_BY, IMPORTS_FROM). It lives in `.code-review-graph/` — gitignored, never committed, rebuilt from source on any machine in seconds.

**Memory is the product.** The memory subsystem reads the graph as structural truth, classifies features and modules, and writes plain Markdown to `.agent-memory/`. These files are human-readable, diff-friendly, and committed to Git. Every teammate and every Claude session starts with full context automatically — no setup required after the first `memory init`.

**Graph signals used in classification:**

- **Edge density** — modules with dense internal call graphs receive higher confidence scores
- **TESTED_BY edges** — maps test files to the features they cover, more accurate than filename heuristics
- **IMPORTS_FROM chains** — builds accurate module dependency and dependent maps
- **BFS expansion** — `prepare-context` and `changed` pull in structurally related files that keyword matching alone would miss

### Local vs committed

| | Location | Committed to Git? |
|---|---|---|
| `.agent-memory/` artifacts | repo root | **Yes** — shared with the whole team |
| `.code-review-graph/graph.db` | repo root | **No** — local cache, rebuild any time |
| Embedding vectors | local | **No** |

---

## `.agent-memory/` layout

```
.agent-memory/
  CLAUDE.md                     # compact session bootstrap (loaded via @.agent-memory/CLAUDE.md)
  repo.md                       # language stack, size, entry points
  architecture.md               # module boundaries, data flow, risky areas
  features/
    <slug>.md                   # one file per detected feature: files, tests, deps, confidence
  modules/
    <slug>.md                   # one file per code module: files, tests, deps, confidence
  rules/
    conventions.md              # coding conventions derived from code + human overrides
    safe-boundaries.md          # paths Claude should never casually edit
  overrides/
    global.yaml                 # human corrections (never auto-overwritten)
  metadata/
    manifest.json               # artifact inventory with generation timestamps
    freshness.json              # last refresh per artifact
    confidence.json             # classification confidence per feature/module
    sources.json                # file → feature/module ownership index
```

---

## Human overrides

The classifier infers what it can from code structure. `overrides/global.yaml` teaches it what code cannot tell you:

```yaml
# .agent-memory/overrides/global.yaml

always_include:
  - docs/architecture.md          # always surfaced in context packs
  - src/auth/middleware.py

never_edit:
  - migrations/                   # Claude must not suggest changes here
  - generated/

notes:
  - "The payments module is PCI-scoped — any change needs a security review."
  - "We use a custom JWT library, not PyJWT."

task_hints:
  - pattern: "add endpoint"
    hint: "Register new routes in src/api/router.py and add tests under tests/api/."
  - pattern: "database migration"
    hint: "Use alembic revision --autogenerate; never edit existing migrations."
```

After editing, run `memory init` to regenerate `rules/conventions.md` with your corrections applied. **Human edits are never auto-overwritten.**

Run `memory annotate` to open the file (or scaffold it on first use):

```bash
code-review-graph memory annotate
```

---

## CLI reference

### Memory commands

```bash
code-review-graph memory init                            # generate .agent-memory/ (run once)
code-review-graph memory refresh                         # incremental refresh after commits
code-review-graph memory refresh --full                  # full regeneration
code-review-graph memory prepare-context "<task>"        # focused context pack for a task
code-review-graph memory explain <feature|module|path>   # show stored memory for an area
code-review-graph memory changed <file|dir>              # impact analysis for changed files
code-review-graph memory annotate                        # open override file for editing
```

### Graph commands

```bash
code-review-graph build       # parse codebase into graph (also refreshes memory if .agent-memory/ exists)
code-review-graph update      # incremental update (also auto-refreshes memory)
code-review-graph status      # graph statistics
code-review-graph watch       # auto-update on file saves
code-review-graph visualize   # generate interactive D3.js HTML graph
code-review-graph install     # register MCP server with Claude Code
code-review-graph serve       # start MCP server
```

All commands accept `--repo <path>` to target a specific directory.

---

## MCP tools

When the MCP server is running (`code-review-graph serve`), Claude Code can call these directly:

### Memory tools

| Tool | Description |
|------|-------------|
| `memory_init` | Generate or refresh all `.agent-memory/` artifacts |
| `memory_prepare_context` | Build a task-focused context pack |
| `memory_explain` | Explain a feature, module, or file path |
| `memory_changed` | Impact analysis for a changed area |
| `memory_annotate` | Scaffold the human override file |

### Graph tools

| Tool | Description |
|------|-------------|
| `build_or_update_graph_tool` | Build or incrementally update the code graph |
| `get_impact_radius_tool` | BFS blast radius of changed files (depth-configurable) |
| `get_review_context_tool` | Structural context bundle for code reviews |
| `query_graph_tool` | Callers, callees, tests, imports, inheritance queries |
| `semantic_search_nodes_tool` | Search code entities by name or semantic meaning |
| `find_large_functions_tool` | Find functions/classes exceeding a line threshold |
| `list_graph_stats_tool` | Graph size, language breakdown, health stats |

---

## Features

| | |
|---|---|
| **Durable repo memory** | `.agent-memory/` committed to Git. Every session and every teammate starts with full context — no re-explaining. |
| **Task-aware context packs** | `prepare-context` assembles focused packs (≤ 20 files) for a specific task, not the whole codebase. |
| **Graph-assisted classification** | Features and modules classified from real structural signals — edge density, import chains, TESTED_BY edges. |
| **Graph-expanded impact analysis** | `changed` traces callers, dependents, and tests via BFS — not just the directly changed files. |
| **Incremental refresh** | Only artifacts whose source files changed are regenerated. Runs automatically after `update`. |
| **Human overrides** | `overrides/global.yaml` teaches the system domain knowledge it cannot infer from code. Never auto-overwritten. |
| **14 languages** | Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++ |
| **Local graph, committed memory** | Graph DB is a local performance cache — gitignored, fast to rebuild. Memory is plain Markdown — diffable and reviewable. |
| **MCP-native** | Graph and memory tools exposed via MCP for direct Claude Code integration. |
| **Interactive visualisation** | D3.js force-directed graph with edge-type toggles, search, and expand-on-click. |
| **Semantic search (optional)** | `pip install code-review-graph[embeddings]` — vector-based code entity search. |

---

## Graph engine details

**14 supported languages** — Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++. Each uses full Tree-sitter grammar support for functions, classes, imports, call sites, inheritance, and test detection.

**Incremental updates** — SHA-256 hash diffing re-parses only changed files. A 2,900-file project re-indexes in under 2 seconds.

**Ignore patterns** — create `.code-review-graphignore` in your repo root (gitignore syntax):

```
generated/**
vendor/**
*.generated.ts
node_modules/**
```

---

## Contributing

```bash
git clone https://github.com/tirth8205/code-review-graph.git
cd code-review-graph
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ --tb=short -q
```

To add a language: edit `code_review_graph/parser.py`, add to `EXTENSION_TO_LANGUAGE` and the four node-type maps, add a test fixture, open a PR.

See [`docs/`](docs/) for architecture, schema, and full command reference.

---

## Status

**Alpha.** Memory classification is heuristic-based — no LLMs, just code structure. It works well on most repos but may misclassify features or miss conventions in unusual layouts. Use `memory annotate` to add corrections. Bug reports and feedback welcome at [GitHub Issues](https://github.com/tirth8205/code-review-graph/issues).

## Licence

MIT. See [LICENSE](LICENSE).

<p align="center">
<br>
<code>pip install code-review-graph && code-review-graph build && code-review-graph memory init</code>
</p>
