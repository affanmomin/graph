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

## Evaluating four target repos

The full evaluation plan runs the harness on four repo types.  Task files for
all four are included in `benchmarks/tasks/`.

| Repo type       | Target repo        | Tasks file                    | Status (2026-03-27) |
|-----------------|--------------------|-------------------------------|---------------------|
| Own repo        | repomind (this)    | `tasks/sample_tasks.json`     | Run — see `results/graph-manual-2026-03-27.md` |
| Public app repo | pallets/flask      | `tasks/public_repo_tasks.json` | Pending |
| Messy/flat repo | pallets/werkzeug   | `tasks/messy_repo_tasks.json`  | Pending |
| Flat package    | psf/requests       | `tasks/flat_package_tasks.json` | Pending |

### Running a specific target repo

```bash
# Flask (public, well-structured app framework)
git clone https://github.com/pallets/flask /tmp/flask-eval
python -m repomind build --repo /tmp/flask-eval
python -m repomind memory init --repo /tmp/flask-eval
python benchmarks/run_benchmark.py \
    --repo /tmp/flask-eval \
    --tasks benchmarks/tasks/public_repo_tasks.json

# Werkzeug (messy: flat single-package, heterogeneous concerns)
git clone https://github.com/pallets/werkzeug /tmp/werkzeug-eval
python benchmarks/run_benchmark.py \
    --repo /tmp/werkzeug-eval \
    --tasks benchmarks/tasks/messy_repo_tasks.json

# requests (canonical flat-package: all files in one directory)
git clone https://github.com/psf/requests /tmp/requests-eval
python benchmarks/run_benchmark.py \
    --repo /tmp/requests-eval \
    --tasks benchmarks/tasks/flat_package_tasks.json
```

### Pass bars by repo type

| Repo type    | feature_count | avg_confidence | coverage_pct |
|--------------|--------------|---------------|-------------|
| Structured app | >= 3        | >= 0.6        | >= 60%      |
| Messy/single-pkg | >= 1      | >= 0.3        | >= 30%      |
| Flat package | >= 2 (via flat_rescue) | >= 0.3 | >= 40% |

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

### Results on this repo (repomind) — 2026-03-27

Actual run from `benchmarks/results/graph-2026-03-27.json`:

| Metric | Actual | Threshold | Pass? |
|--------|--------|-----------|-------|
| `feature_count` | 5 | >= 2 | PASS |
| `avg_feature_confidence` | 0.41 | >= 0.5 | FAIL (no graph.db) |
| `avg_module_confidence` | 0.85 | >= 0.5 | PASS |
| `coverage_pct` tasks 001,003,005 | 100% | >= 50% | PASS |
| `coverage_pct` tasks 002,004,006 | 0% | >= 50% | FAIL |
| `coverage_pct` tasks 007,008 | 0% | N/A | Expected (stress tests) |
| `tokens_estimated` | 50k–93k | <= 40k | FAIL (all tasks) |
| explain quality (automated) | 1.0 | >= 0.75 | PASS |
| refresh planning | 3/3 correct | — | PASS |
| init time | 0.96s | — | PASS |

Key known limitations on this repo (library layout):
- `code_review_graph/*.py` (parser.py, graph.py, tools.py, etc.) are in the root package and invisible to the classifier — **root-package blind spot**.
- All tasks exceed the 40k token budget because the context builder returns all files in a matched area without intra-area ranking — **token overfetch**.
- Tasks 007 and 008 are deliberate stress tests showing the root-package blind spot.

Full analysis and blockers: `benchmarks/release_gate.md`.

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
