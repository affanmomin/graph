# Usage Guide

**Version:** v1.8.4 (Alpha)

---

## Installation

```bash
pip install code-review-graph
code-review-graph install    # creates .mcp.json for Claude Code integration
```

Restart Claude Code to pick up the MCP server.

---

## One-time setup

```bash
# 1. Build the code graph (parse codebase into SQLite)
code-review-graph build

# 2. Generate repo memory artifacts
code-review-graph memory init

# 3. Tell Claude Code to load memory at every session start
#    Add this line to your CLAUDE.md:
@.agent-memory/CLAUDE.md

# 4. Commit memory to Git so teammates start with context too
git add .agent-memory/
git commit -m "chore: add repo memory"
```

---

## Core workflows

### Prepare context before a task

The most-used workflow. Give Claude exactly what it needs before you start:

```bash
code-review-graph memory prepare-context "add rate limiting to the auth middleware"
code-review-graph memory prepare-context "fix the streaming bug in HTTP client"
code-review-graph memory prepare-context "migrate users table to UUID primary keys"
```

Returns: relevant features, modules, files to read, tests to run, and a one-paragraph task summary. Paste this into your Claude session before asking it to write any code.

Add `--json` for machine-readable output:

```bash
code-review-graph memory prepare-context "refactor auth" --json
```

### Explain an area

```bash
code-review-graph memory explain authentication
code-review-graph memory explain src/payments/
code-review-graph memory explain code_review_graph/memory/commands.py
```

Shows the stored memory for a feature, module, or path: files, tests, dependencies, confidence score, and last refresh time. Reads from `.agent-memory/` — no re-analysis.

### Trace the impact of a change

```bash
code-review-graph memory changed src/auth/middleware.py
code-review-graph memory changed src/api/
```

Shows which features and modules own the changed files, then uses graph BFS to surface callers, dependents, and related tests — even when those files didn't change directly.

### Refresh memory after significant changes

```bash
code-review-graph memory refresh          # incremental (git-diff based)
code-review-graph memory refresh --full   # regenerate everything
```

Memory also refreshes automatically when you run `code-review-graph update`.

### Add human corrections

```bash
code-review-graph memory annotate
```

Opens `.agent-memory/overrides/global.yaml`. Add domain knowledge the classifier can't infer: always-include paths, never-edit boundaries, free-text notes, and task-pattern hints. Run `memory init` again to apply your overrides.

---

## Graph commands

The graph engine powers memory classification and impact analysis. You generally build it once and let `update` maintain it.

```bash
code-review-graph build                     # full parse (first time; ~10s for 500 files)
code-review-graph update                    # incremental update (changed files only; also refreshes memory)
code-review-graph update --base origin/main # custom git base ref
code-review-graph watch                     # auto-update on every file save
code-review-graph status                    # graph statistics (nodes, edges, languages)
code-review-graph visualize                 # generate interactive D3.js HTML graph
code-review-graph serve                     # start MCP server (stdio)
```

---

## Keeping memory current

| When | Action |
|------|--------|
| First setup | `memory init` |
| After significant feature work | `memory refresh` or `memory refresh --full` |
| After every commit | automatic (via `code-review-graph update`) |
| After writing override corrections | `memory init` |
| Sharing with a new teammate | `git pull` — memory is already committed |

---

## Token budget

Memory artifacts are written as compact Markdown designed to stay under context budget:

| Scenario | Rough token cost |
|----------|-----------------|
| `CLAUDE.md` session bootstrap | ~300–600 tokens |
| `prepare-context` pack for a task | ~400–800 tokens |
| `explain` for one feature | ~200–400 tokens |

---

## Supported languages

Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++

---

## What gets indexed

**Node types:** File, Class, Function/Method, Type, Test

**Edge types:** CALLS, IMPORTS_FROM, INHERITS, IMPLEMENTS, CONTAINS, TESTED_BY, DEPENDS_ON

See [schema.md](schema.md) for full details.

---

## Ignore patterns

Default excluded paths:

```
.code-review-graph/**    node_modules/**    .git/**
__pycache__/**           *.pyc              .venv/**
dist/**                  build/**           *.min.js
*.lock                   package-lock.json  *.db
```

To add custom patterns, create `.code-review-graphignore` in your repo root (gitignore syntax):

```
generated/**
vendor/**
*.generated.ts
```

---

## Skills (Claude Code slash commands)

These skills are registered in the MCP plugin and invoke pre-built review workflows:

```
/code-review-graph:build-graph    # build or update the graph
/code-review-graph:review-delta   # review only changed files + blast radius
/code-review-graph:review-pr      # review a PR or branch diff
```

See [COMMANDS.md](COMMANDS.md) for full parameter reference.
