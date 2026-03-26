# Benchmark Harness

Lightweight evaluation workflow for the repo-memory system.  Combines an
automated metric runner with a manual scorecard to measure classification
quality, context-pack relevance, and refresh correctness.

---

## What this measures

| Layer          | Metric                          | How measured     |
|----------------|---------------------------------|-----------------|
| Classification | Feature/module count            | Automated        |
| Classification | Average confidence              | Automated        |
| Classification | Test file mapping rate          | Automated        |
| Classification | Module dependency resolution    | Automated        |
| Context packs  | Files returned per task         | Automated        |
| Context packs  | Estimated token cost            | Automated        |
| Context packs  | Expected-file coverage %        | Automated        |
| Context packs  | Relevance (1–5)                 | Manual scorecard |
| Context packs  | Overfetch / noise               | Manual scorecard |
| Context packs  | Related test inclusion          | Manual scorecard |
| Lookup         | `explain` usefulness (1–5)      | Manual scorecard |
| Lookup         | `changed` usefulness (1–5)      | Manual scorecard |
| Refresh        | Direct + graph-expanded slugs   | Automated        |
| Refresh        | Artifact correctness            | Manual scorecard |
| Efficiency     | Tokens ≤ 40 k per task          | Automated        |

---

## File layout

```
benchmarks/
  run_benchmark.py        Automated metric runner
  scorecard_template.md   Manual scoring template
  tasks/
    sample_tasks.json     8 sample tasks for this repo
  results/
    .gitkeep
    <repo>-<ts>.json      Auto-generated JSON reports (git-ignored)
    <repo>-manual-*.md    Filled-in scorecards (commit these)
```

> **Note**: `results/*.json` files are not committed.  Only manually completed
> `*-manual-*.md` scorecards should be committed so reviewers can see human
> evaluation history.

---

## Quick start

### 1. Run automated metrics on this repo

```bash
# From the project root
uv run python benchmarks/run_benchmark.py --repo .
```

This runs the full init pipeline and then tests context packs against
`benchmarks/tasks/sample_tasks.json`.  Results land in
`benchmarks/results/graph-<timestamp>.json`.

### 2. Skip init if `.agent-memory/` already exists

```bash
uv run python benchmarks/run_benchmark.py --repo . --skip-init
```

### 3. Run on a different repo

```bash
uv run python benchmarks/run_benchmark.py --repo /path/to/other-repo \
    --tasks /path/to/your-tasks.json
```

You'll need to write a custom tasks file for repos other than this one
(see "Writing task files" below).

### 4. Fill in the manual scorecard

```bash
cp benchmarks/scorecard_template.md \
   benchmarks/results/graph-manual-$(date +%Y%m%d).md
# Open and fill in the manual sections
```

---

## Pass/fail thresholds

The runner applies these automatically.  Manual scores use ≥ 3 as the
passing bar.

| Metric                   | Threshold |
|--------------------------|-----------|
| `feature_count`          | ≥ 2       |
| `avg_feature_confidence` | ≥ 0.5     |
| `avg_module_confidence`  | ≥ 0.5     |
| `files_returned`         | ≤ 20      |
| `tokens_estimated`       | ≤ 40 000  |
| `coverage_pct`           | ≥ 50 %    |

---

## Writing task files

A task file is a JSON object with a `"tasks"` array.  Each entry:

```json
{
  "id": "task-001",
  "description": "Add a new CLI command to the memory subsystem",
  "expected_features": ["memory", "cli"],
  "expected_modules": ["code_review_graph.memory"],
  "expected_files_contain": ["commands.py", "cli.py"],
  "notes": "Human-readable note for scorecard review"
}
```

- `expected_features` / `expected_modules` are informational only (not
  auto-checked); they guide the manual scorecard.
- `expected_files_contain` lists file *stems* (no path, no extension) that
  must appear in the returned pack.  The runner computes `coverage_pct`
  from these.

---

## Evaluating three target repos

The full evaluation plan runs the harness on:

| Repo         | Why                                       | Custom tasks? |
|--------------|-------------------------------------------|--------------|
| This repo    | Known ground truth; ideal smoke test      | Yes (included) |
| Messy repo   | Tests robustness on weak/absent docs      | Write custom  |
| Public repo  | Real-world signal outside our own codebase | Write custom  |

For each repo:
1. Run `run_benchmark.py`.
2. Fill in a copy of `scorecard_template.md`.
3. Commit the filled scorecard.

---

## Interpreting results

### JSON report shape

```json
{
  "classification": {
    "feature_count": 6,
    "avg_feature_confidence": 0.74,
    ...
  },
  "context_packs": [
    {
      "task_id": "task-001",
      "files_returned": 7,
      "coverage_pct": 100.0,
      "tokens_estimated": 12400,
      "pass": { "files_returned": true, "tokens_estimated": true, "coverage_pct": true }
    }
  ],
  "refresh": {
    "directly_impacted": 2,
    "graph_expanded": 1,
    "total_plan_size": 3
  },
  "timing": { "init_seconds": 3.4, "context_pack_seconds": 0.1 }
}
```

### Common failure patterns

| Symptom                        | Likely cause                               |
|-------------------------------|---------------------------------------------|
| `feature_count < 2`           | Repo has no domain-keyword directories (flat layout), or scanner excluded too much |
| `avg_confidence < 0.5`        | Filesystem heuristics weak; graph not built |
| `coverage_pct < 50`           | Task description tokens don't match names, or target file is outside classified modules |
| `files_returned > 20`         | Scoring threshold too low (`_MIN_SCORE`)    |
| `tokens_estimated > 40 000`   | Module has many large files; pack returns all of them in fallback mode |
| No `graph_expanded` slugs     | `.repomind/graph.db` not built     |

### Results on this repo (repomind)

This repo is itself a good stress-test case for the product. Its layout is **module-dominant with no domain-keyword feature directories**:

- `code_review_graph/memory/` — the only classified module (13 files)
- `code_review_graph/*.py` (parser.py, graph.py, tools.py, etc.) — root package files, **not captured by any classified module**

Expected benchmark behavior on this repo:

| Metric | Expected | Why |
|--------|----------|-----|
| `feature_count` | 0 | No domain-keyword subdirectories (`auth/`, `billing/`, etc.) |
| `module_count` | 1 | Only `code_review_graph/memory` is a named subpackage |
| `coverage_pct` tasks 001-006 | 100% | Target files are all in the memory module |
| `coverage_pct` tasks 007-008 | 0% | Deliberate stress tests; target files are in root package |
| `tokens_estimated` | ~58k | 13 files × large file size; exceeds 40k budget in fallback mode |

Tasks 007 and 008 in `sample_tasks.json` are intentionally failing stress tests. They demonstrate the **root-package blind spot**: files in `code_review_graph/*.py` that are not inside a named subpackage are invisible to the current classifier.

---

## Running the full CI check

```bash
# Lint + type-check + tests (as usual)
uv run ruff check code_review_graph/
uv run mypy code_review_graph/ --ignore-missing-imports --no-strict-optional
uv run pytest tests/ --tb=short -q

# Then benchmark
uv run python benchmarks/run_benchmark.py --repo .
```
