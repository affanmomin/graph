# Benchmark Scorecard — Manual Evaluation

> Auto-generated results: `benchmarks/results/graph-2026-03-27.json`
> Runner version: 2 (includes explain quality + cache timing)

---

## Repo Under Test

| Field          | Value |
|----------------|-------|
| Repo name      | repomind (code-review-graph) |
| Repo URL/path  | C:\Users\Lenovo\graph |
| Evaluator      | Claude Sonnet 4.6 (Phase 6 validation) |
| Date           | 2026-03-27 |
| Runner results | `benchmarks/results/graph-2026-03-27.json` |

---

## 0. Context: What kind of repo is this?

This repo is a **library/tool** (`code_review_graph/` Python package), not a
feature-oriented application.  It has no `auth/`, `billing/`, `dashboard/`
style subdirectories.  This is a known structural challenge for the classifier,
which relies on directory keywords and file groupings.

The **only cleanly classified module** is `code_review_graph/memory/` (15
files, avg confidence 0.85).  All root-package files (`parser.py`, `graph.py`,
`tools.py`, `visualization.py`, etc.) are invisible to the classifier.

Five heuristic features were detected: Cache, Cli, Core, Models, Search.
These are low-confidence groupings (avg 0.41) based on file names.

---

## 1. Classification Quality

### Features

| Feature name | Makes sense? | Files correct? | Confidence plausible? | Notes |
|--------------|-------------|---------------|----------------------|-------|
| Cache        | Y           | Y (signal_cache.py) | N (0.41 is low) | Correctly groups caching files |
| Cli          | Y           | Partial (cli.ts + cli.py mixed) | N | Cross-language grouping is spurious |
| Core         | N           | N (too broad) | N | Catch-all for misc files — not useful |
| Models       | Y           | Y (models.py) | N | Single-file feature — trivial |
| Search       | N           | N             | N | Spurious — no search feature exists |

**Feature classification score**: 2 / 5
- Features are weakly detected and low-confidence.
- "Core" and "Search" would mislead an agent.
- Without a built graph.db, feature confidence cannot be grounded.

### Modules

| Module name | Makes sense? | Dependencies correct? | Dependents correct? | Notes |
|-------------|-------------|----------------------|--------------------|-------|
| code_review_graph/memory | Y | N (0 deps detected) | N/A | Correctly captures the memory subsystem; dependency resolution requires built graph |

**Module classification score**: 4 / 5
- The one detected module is accurate and high-confidence (0.85).
- 0 dependencies because graph.db was not built.

---

## 2. Context Pack Relevance (per task)

| Task ID | Task description (short) | Relevance (1–5) | Key files present? | Noisy files? | Notes |
|---------|--------------------------|----------------|--------------------|--------------|-------|
| task-001 | Add CLI command | 3 | Y (commands.py) | Y (cli.ts, signal_cache.py) | Correct file found but 18 files returned; too many |
| task-002 | Improve classifier confidence | 2 | N | Y | classifier.py NOT returned; "Core" feature is the wrong group |
| task-003 | Refactor context builder | 3 | Y (context_builder.py) | Y | 16 files; target present but noisy |
| task-004 | Change refresh planning | 2 | N | Y | refresh.py NOT returned; "Core" swallows it |
| task-005 | Add MCP freshness metadata | 3 | Y (metadata.py) | Y | 16 files; target present |
| task-006 | Update graph bridge | 2 | N | Y | graph_bridge.py NOT returned |
| task-007 | Fix parser bug (STRESS) | 1 | N | Y | parser.py invisible; wrong context returned |
| task-008 | D3.js visualization (STRESS) | 1 | N | Y | visualization.py invisible; wrong context returned |

**Average context pack relevance**: 2.1 / 5

Notes:
- Tasks 001, 003, 005 land in the memory module or a heuristic feature, so
  the key file is present — but the pack contains 16–18 files (too broad).
- Tasks 002, 004, 006 match "Core" which doesn't contain the target files.
- Tasks 007, 008 are expected failures (root-package blind spot).

---

## 3. Test File Inclusion

| Task ID | Test files returned | Expected test files | Included? | Notes |
|---------|--------------------|--------------------|-----------|-------|
| task-001 | 0 | tests/test_memory_cli.py | N | No tests included in any context pack |
| task-002 | 0 | tests/test_memory_*.py | N | Same |
| task-003 | 0 | tests/test_memory_context_builder.py | N | Same |

**Test inclusion score**: 1 / 5
- No test files appear in any context pack.
- The context builder returns `relevant_tests` separately but the pack for this
  repo returns an empty list — likely because the memory module's test mapping
  is not populated.

---

## 4. `memory explain` Usefulness

**Automated score**: 1.0 (4/4 criteria on all 3 targets)

| Target | Usefulness (1–5) | Notes |
|--------|-----------------|-------|
| Cache  | 4 | Correct files, confidence shown, purpose statement present |
| Search | 2 | Spurious feature — explain output is present but describes wrong behavior |
| Models | 4 | Accurate for a single-file feature |

**Explain usefulness score**: 3.3 / 5
- Quality is good when the feature is real; degrades for spurious features.
- The automated metric (4/4 criteria) doesn't catch spurious classifications.

---

## 5. `memory changed` Usefulness

| File | Impacted areas shown | Accurate? | Useful for triage? | Notes |
|------|---------------------|-----------|-------------------|-------|
| code_review_graph/memory/commands.py | code_review_graph/memory module | Y | Y | Correctly identifies the enclosing module |
| code_review_graph/memory/signal_cache.py | Cache feature | Y | Partial | Feature is accurate; no test impact shown |

**Changed usefulness score**: 3 / 5
- Correctly identifies the enclosing area for memory/ files.
- No graph impact (graph not built) — cross-file blast radius missing.

---

## 6. Overfetch / Noise

Worst tasks: 001, 003, 005 (16–18 files, 87k–93k tokens).

| Noisy file | Why included | Impact on agent | Suggested fix |
|------------|-------------|----------------|---------------|
| code-review-graph-vscode/src/backend/cli.ts | "Cli" feature matches .ts CLI file | High — wrong language/layer | Intra-module file ranking; limit to top N files |
| code_review_graph/memory/__init__.py | Module contains it | Low — harmless but wastes tokens | Exclude __init__.py from context packs |
| All 15 memory/ files for each task | Module returned in full | High — 50k–93k tokens | Return only most-relevant files within matched area |

**Overfetch verdict**: HIGH
- Token budget exceeded on every single task (threshold: 40k; actual: 50k–93k).
- Root cause: context_builder returns ALL files in a matched module/feature.
  No intra-area file ranking or token-budget enforcement exists.

---

## 7. Refresh Correctness

| Changed files | Artifacts refreshed | Correct? | Notes |
|--------------|--------------------|---------|-|
| 3 files (signal_cache.py, commands.py, test file) | 3 artifact slugs (directly impacted) | Y | All 3 changed files mapped to the memory module |

**Refresh correctness score**: 4 / 5
- Direct impact is computed correctly.
- Graph-expanded impact = 0 (expected: graph not built).
- With a built graph, cross-module impact would be visible.

---

## 8. Token / Context Efficiency

| Task ID | Files returned | Tokens estimated | Within budget (<=40k)? |
|---------|---------------|------------------|------------------------|
| task-001 | 18 | 93,720 | N (2.3x over) |
| task-002 | 20 | 50,483 | N (1.3x over) |
| task-003 | 16 | 87,339 | N (2.2x over) |
| task-004 | 20 | 50,483 | N (1.3x over) |
| task-005 | 16 | 87,339 | N (2.2x over) |
| task-006 | 20 | 50,483 | N (1.3x over) |
| task-007 | 20 | 50,483 | N (1.3x over) |
| task-008 | 20 | 50,483 | N (1.3x over) |

**0/8 tasks within token budget.** All packs are 1.3x–2.3x over the 40k threshold.

---

## Overall Summary

| Dimension                  | Score    | Pass threshold | Pass? |
|---------------------------|----------|---------------|-------|
| Feature classification    | 2/5      | >= 3          | FAIL  |
| Module classification     | 4/5      | >= 3          | PASS  |
| Context pack relevance    | 2.1/5    | >= 3          | FAIL  |
| Test file inclusion        | 1/5      | >= 3          | FAIL  |
| Explain usefulness        | 3.3/5    | >= 3          | PASS  |
| Changed usefulness        | 3/5      | >= 3          | PASS  |
| Overfetch / noise         | HIGH     | Low or Medium | FAIL  |
| Refresh correctness       | 4/5      | >= 3          | PASS  |
| Token efficiency          | 0/8 pass | <= 40k        | FAIL  |

**Passes: 4/9 dimensions**

**Overall verdict**: NEEDS WORK

**Top 3 issues to address**:
1. Token overfetch — context builder must enforce a token/file budget per pack
2. Root package blind spot — files outside named submodules are invisible
3. Feature confidence is too low without a built graph.db — degrade gracefully or gate feature detection on graph availability

**Evaluator notes**:
This scorecard is for the repomind repo itself, which is a library — not the
intended use case (feature-oriented app repos).  The product will score better
on Django/Flask/Node.js app repos.  The token overfetch issue, however, is
real and repo-type-independent: it will affect any repo where a module has
more than ~8 files.
