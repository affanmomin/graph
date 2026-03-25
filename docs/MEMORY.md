# Repo Memory — Feature Guide

`code-review-graph` generates durable Markdown artifacts committed to Git under `.agent-memory/`. Claude Code reads these at session start instead of requiring re-explanation of the codebase every time.

---

## What gets generated

```
.agent-memory/
  repo.md                     — one-page repo overview
  architecture.md             — layer/component diagram (text)
  features/<slug>.md          — one file per detected feature
  modules/<slug>.md           — one file per detected module
  rules/conventions.md        — coding conventions derived from code + overrides
  rules/safe-boundaries.md    — paths Claude should never edit (generated + human)
  CLAUDE.md                   — compact session bootstrap for Claude Code
  overrides/global.yaml       — human corrections (never auto-overwritten)
  metadata/manifest.json      — list of all generated artifacts + timestamps
  metadata/sources.json       — which classifier identified each artifact
  metadata/confidence.json    — per-artifact confidence scores
```

---

## Quick start

```bash
# 1. Build the code graph (needed once for full structural context)
code-review-graph build

# 2. Generate memory artifacts
code-review-graph memory init

# 3. Load memory into every Claude Code session
#    Add this line to your repo's CLAUDE.md:
@.agent-memory/CLAUDE.md

# 4. Commit memory to Git so teammates get it too
git add .agent-memory/
git commit -m "chore: add repo memory"
```

---

## CLI commands

### `memory init`

Scan the repo and generate all `.agent-memory/` artifacts. Safe to re-run — only changed content is written.

```bash
code-review-graph memory init
code-review-graph memory init --repo /path/to/repo
```

### `memory refresh`

Incrementally regenerate artifacts affected by recent changes. Faster than a full init.

```bash
code-review-graph memory refresh         # incremental (git-diff based)
code-review-graph memory refresh --full  # regenerate everything
```

### `memory explain <target>`

Print the stored memory for a feature, module, or file path.

```bash
code-review-graph memory explain authentication
code-review-graph memory explain src/payments/
code-review-graph memory explain code_review_graph/memory/commands.py
```

### `memory prepare-context "<task>"`

Build a focused context pack for a natural-language task — relevant features, files, tests, and warnings — ready to inject into a Claude Code session.

```bash
code-review-graph memory prepare-context "add rate limiting to the API"
code-review-graph memory prepare-context "fix the auth token refresh bug" --json
```

### `memory changed <target>`

Show recent git changes in an area and which memory artifacts are affected.

```bash
code-review-graph memory changed authentication
code-review-graph memory changed src/api/
```

### `memory annotate`

Open (or scaffold) `.agent-memory/overrides/global.yaml` to add human corrections.

```bash
code-review-graph memory annotate
```

---

## Human overrides

Edit `.agent-memory/overrides/global.yaml` to add domain knowledge the scanner can't infer:

```yaml
always_include:
  - src/auth/middleware.py      # always surfaced in context packs

never_edit:
  - migrations/                 # Claude must not touch these
  - src/vendor/

notes:
  - The auth module uses a custom JWT library (not PyJWT)
  - All API handlers must validate with the shared RequestValidator

task_hints:
  - pattern: "add endpoint"
    hint: "Register new routes in src/api/router.py"
  - pattern: "database migration"
    hint: "Use alembic revision --autogenerate; never edit existing migrations"
```

Run `memory init` again after editing to regenerate `rules/conventions.md` with your overrides applied. Human edits are **never** auto-overwritten.

---

## MCP tools

Five memory MCP tools are available when the MCP server is running. See [COMMANDS.md](COMMANDS.md#memory-mcp-tools) for full parameter reference.

| Tool | Purpose |
|------|---------|
| `memory_init` | Generate or refresh all `.agent-memory/` artifacts |
| `memory_explain` | Explain a feature, module, or file path |
| `memory_prepare_context` | Build a task-focused context pack |
| `memory_changed` | Show recent changes for an area |
| `memory_annotate` | Scaffold the human override file |

---

## Git workflow

Memory artifacts live in `.agent-memory/` and should be committed:

```bash
# Initial setup
git add .agent-memory/
git commit -m "chore: add repo memory"

# After significant changes
code-review-graph memory refresh
git add .agent-memory/
git commit -m "chore: refresh repo memory"
```

Keep `.code-review-graph/` (the graph database) in `.gitignore` — it's local-only state rebuilt from source.

---

## Automatic refresh

When you run `code-review-graph update`, memory is automatically refreshed if `.agent-memory/` exists. This keeps memory fresh after incremental graph updates without manual intervention.

---

## Status: Alpha

Memory generation is heuristic-based — it works well on most repos but may misclassify features or miss conventions in unusual layouts. Use `memory annotate` to add corrections. Feedback and bug reports welcome.
