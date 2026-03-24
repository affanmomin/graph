# Repo Memory — Demo Walkthrough

This document shows an end-to-end workflow using the repo-memory commands on a
real project. Every command shown here is implemented and runnable today.

---

## Prerequisites

```bash
pip install code-review-graph
# or, from source:
uv sync --all-extras
```

All examples below use `uv run code-review-graph ...`. If you installed via pip,
drop `uv run` and call `code-review-graph` directly.

---

## Step 1 — Initial setup

Run `memory init` once to scan your repo and write the `.agent-memory/` folder.

```bash
uv run code-review-graph memory init
```

Example output (abbreviated):

```
repo-memory: init
  scanning /path/to/myapp ...

  languages   : python
  frameworks  : fastapi
  source dirs : src
  test dirs   : tests
  confidence  : 87%
  features    : 4
  modules     : 6

  .agent-memory/repo.md                    [created]
  .agent-memory/architecture.md            [created]
  .agent-memory/features/authentication.md [created]
  .agent-memory/features/payments.md       [created]
  .agent-memory/modules/src-auth.md        [created]
  .agent-memory/rules/conventions.md       [created]
  .agent-memory/rules/safe-boundaries.md   [created]
  .agent-memory/metadata/manifest.json     [created]
  .agent-memory/metadata/sources.json      [created]
  .agent-memory/metadata/confidence.json   [created]

  Done. Commit .agent-memory/ to share memory with your team.
```

Commit the result:

```bash
git add .agent-memory/
git commit -m "chore: initialise repo memory"
```

---

## Step 2 — Explore what was generated

```bash
cat .agent-memory/repo.md
cat .agent-memory/features/authentication.md
cat .agent-memory/rules/safe-boundaries.md
```

These are plain Markdown files. Read and edit them freely. Override files (step 4)
are the right place to add human corrections without touching generated files.

---

## Step 3 — Prepare context for a task

Before starting a coding task, ask the memory system what's relevant:

```bash
uv run code-review-graph memory prepare-context "add rate limiting to the API"
```

Example output:

```
repo-memory: prepare-context
  task: add rate limiting to the API

  Relevant features:
    - API Gateway
    - Rate Limiting

  Relevant modules:
    - src.api
    - src.middleware

  Files to inspect:
    - src/api/router.py
    - src/middleware/rate_limit.py
    - src/config/settings.py

  Related tests:
    - tests/test_rate_limit.py

  Summary:
    Task matches 2 feature(s) and 2 module(s).
    3 files to inspect, 1 test file.
    Confidence: medium.
```

Pass this output directly to Claude Code as starting context instead of pasting
architecture docs by hand.

For machine-readable output (e.g. piping to another tool):

```bash
uv run code-review-graph memory prepare-context "add rate limiting" --json
```

---

## Step 4 — Add human overrides

The classifier works from directory structure and naming conventions. For things
it cannot infer — frozen modules, critical files, domain notes — add an override file:

```bash
mkdir -p .agent-memory/overrides
cat > .agent-memory/overrides/global.yaml << 'EOF'
always_include:
  - docs/architecture.md
  - src/auth/middleware.py

never_edit:
  - migrations/
  - generated/

notes:
  - "The payments module is PCI-scoped — any change needs a security review."
  - "Use the custom JWT library in src/auth/jwt.py, not PyJWT directly."

task_hints:
  - pattern: "add endpoint"
    hint: "Register new routes in src/api/router.py and add an OpenAPI tag."
  - pattern: "payment"
    hint: "Payments changes require a review from the security team."
EOF
```

Re-run `prepare-context` and the overrides will be reflected:

```bash
uv run code-review-graph memory prepare-context "fix the payment webhook handler"
```

```
  Warnings:
    ! migrations/ — never edit directly (from overrides)
    ! The payments module is PCI-scoped — any change needs a security review.

  Task hint:
    Payments changes require a review from the security team.
```

Re-run `memory init` to regenerate rule docs with the new overrides baked in:

```bash
uv run code-review-graph memory init
git add .agent-memory/
git commit -m "chore: add team overrides and refresh memory"
```

---

## Step 5 — Explain a feature or module

Look up what the memory system knows about a specific area:

```bash
uv run code-review-graph memory explain authentication
```

Example output:

```
Feature: Authentication

  Confidence : 90% (high)
  Purpose    : Feature covering 4 file(s). Classified by explicit package boundary.

  Main files:
    - src/auth/login.py
    - src/auth/logout.py
    - src/auth/middleware.py
    - src/auth/tokens.py

  Related tests:
    - tests/test_auth.py
    - tests/test_tokens.py

  Freshness  : refreshed 2026-03-24 10:15 UTC (incremental)
```

Also works with file paths and module names:

```bash
uv run code-review-graph memory explain src/auth/middleware.py
uv run code-review-graph memory explain src.auth
```

---

## Step 6 — Check what changed in an area

After merging PRs or pulling updates, check what changed in a specific area:

```bash
uv run code-review-graph memory changed authentication
```

Example output:

```
Feature: Authentication

  Last refresh : 2026-03-24 10:15 UTC (incremental)
  Changed files: 2 in last refresh

  Recently changed in this area:
    - src/auth/middleware.py
    - src/auth/tokens.py

  Status: ✓ artifact was refreshed in last update
```

---

## Step 7 — Keep memory current

After significant changes, refresh the memory:

```bash
# Incremental — only regenerates what changed (fast, default)
uv run code-review-graph memory refresh

# Full rebuild — regenerates everything
uv run code-review-graph memory refresh --full
```

Example incremental output:

```
repo-memory: refresh (incremental)
  repo root     : /path/to/myapp
  target folder : /path/to/myapp/.agent-memory

  mode          : incremental
  changed files : 3
  plan          : 1 feature(s) and 2 module(s) impacted

  Updated:
    .agent-memory/features/authentication.md
    .agent-memory/modules/src-auth.md
    .agent-memory/metadata/freshness.json

  Unchanged:
    .agent-memory/repo.md
    .agent-memory/architecture.md
    .agent-memory/features/payments.md

  Done.
```

Commit updated artifacts:

```bash
git add .agent-memory/
git commit -m "chore: refresh repo memory after auth refactor"
```

---

## Typical daily workflow

```bash
# After pulling or making changes
uv run code-review-graph memory refresh

# Before starting a task
uv run code-review-graph memory prepare-context "<your task description>"

# When you need more detail on one area
uv run code-review-graph memory explain <feature-or-module>

# When you want to understand what changed recently
uv run code-review-graph memory changed <area>
```

---

## What is and is not implemented (alpha)

| Feature | Status |
|---------|--------|
| `memory init` — full artifact generation | ✅ |
| `memory refresh` — incremental and full | ✅ |
| `memory prepare-context` — task-aware context packs | ✅ |
| `memory explain` — feature/module lookup | ✅ |
| `memory changed` — freshness-based change summary | ✅ |
| `memory annotate` — scaffold override file in $EDITOR | Stub (T7) |
| MCP tool access for Claude Code | ✅ (via existing tools.py) |
| Automatic refresh on `code-review-graph update` | Hooks integrated |

---

## Troubleshooting

**`memory explain` says "not found"**
Run `memory init` first, or check that the target name roughly matches a feature or
module in the generated docs (names come from directory structure).

**Artifacts look wrong**
Edit `.agent-memory/overrides/global.yaml` to add corrections, then re-run `memory init`.
Never edit the generated files directly — they will be overwritten on the next init.

**`memory refresh` says `.agent-memory/ not found`**
Run `memory init` before `memory refresh`.

**Slow first run**
The initial scan parses every source file with Tree-sitter. On a 1,000-file repo
this typically takes 5–15 seconds. Subsequent refreshes are much faster (< 2 seconds).
