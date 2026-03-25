# Benchmark Scorecard — Manual Evaluation

> Copy this file to `benchmarks/results/<repo-slug>-manual-<date>.md` before filling in.
> The automated runner (`run_benchmark.py`) measures counts and coverage.
> This scorecard captures the human judgement layer that automation cannot.

---

## Repo Under Test

| Field          | Value |
|----------------|-------|
| Repo name      |       |
| Repo URL/path  |       |
| Evaluator      |       |
| Date           |       |
| Runner results | `benchmarks/results/<file>.json` |

---

## 1. Classification Quality

Rate the overall feature and module segmentation.

### Features

| Feature name | Makes sense? | Files correct? | Confidence plausible? | Notes |
|--------------|-------------|---------------|----------------------|-------|
|              | Y / N / ?   | Y / N / ?     | Y / N / ?            |       |
|              |             |               |                      |       |
|              |             |               |                      |       |

**Feature classification score**: __ / 5
- 5 = All features are distinct, correctly scoped, and intuitively named
- 4 = Mostly correct; 1–2 minor grouping issues
- 3 = Recognizable but noisy; some files misplaced or features merged/split incorrectly
- 2 = Significant grouping issues; would mislead an agent
- 1 = Useless; almost no correct groupings

### Modules

| Module name | Makes sense? | Dependencies correct? | Dependents correct? | Notes |
|-------------|-------------|----------------------|--------------------|-------|
|             | Y / N / ?   | Y / N / ?            | Y / N / ?          |       |
|             |             |                      |                    |       |

**Module classification score**: __ / 5

---

## 2. Context Pack Relevance (per task)

For each task in `sample_tasks.json`, rate how relevant the returned context pack was.

### Scoring guide

| Score | Meaning |
|-------|---------|
| 5     | Perfect — all the right files, nothing irrelevant |
| 4     | Good — core files present; 1–2 minor noise files |
| 3     | Adequate — most important files present but some noise or gaps |
| 2     | Poor — key files missing OR significant noise |
| 1     | Useless — would not help an agent start the task |

| Task ID | Task description (short) | Relevance (1–5) | Key files present? | Noisy files? | Notes |
|---------|--------------------------|----------------|--------------------|--------------|-------|
| task-001 |                         |                | Y / N              | Y / N        |       |
| task-002 |                         |                | Y / N              | Y / N        |       |
| task-003 |                         |                | Y / N              | Y / N        |       |
| task-004 |                         |                | Y / N              | Y / N        |       |
| task-005 |                         |                | Y / N              | Y / N        |       |
| task-006 |                         |                | Y / N              | Y / N        |       |
| task-007 |                         |                | Y / N              | Y / N        |       |
| task-008 |                         |                | Y / N              | Y / N        |       |

**Average context pack relevance**: __ / 5

---

## 3. Test File Inclusion

For tasks that involve code changes, were the relevant test files included in the pack?

| Task ID | Test files returned | Expected test files | Included? | Notes |
|---------|--------------------|--------------------|-----------|-------|
|         |                    |                    | Y / N     |       |
|         |                    |                    | Y / N     |       |

**Test inclusion score**: __ / 5

---

## 4. `memory explain` Usefulness

Run `uv run code-review-graph memory explain <feature>` on 2–3 features.
Rate how useful the explain output would be to an AI agent about to work on that feature.

| Target | Command run | Usefulness (1–5) | Notes |
|--------|-------------|-----------------|-------|
|        |             |                 |       |
|        |             |                 |       |

**Explain usefulness score**: __ / 5
- 5 = Clear, accurate, actionable — an agent could start work immediately
- 3 = Present but generic or incomplete
- 1 = Missing, wrong, or would mislead

---

## 5. `memory changed` Usefulness

Run `uv run code-review-graph memory changed <file>` on 1–2 recently changed files.
Rate the quality of the impacted-areas analysis.

| File | Impacted areas shown | Accurate? | Useful for triage? | Notes |
|------|---------------------|-----------|-------------------|-------|
|      |                     | Y / N     | Y / N             |       |
|      |                     | Y / N     | Y / N             |       |

**Changed usefulness score**: __ / 5

---

## 6. Overfetch / Noise

For the worst-scoring task in section 2, list the noisy files and why they were included.

| Noisy file | Why it was included (guess) | Impact on agent | Suggested fix |
|------------|----------------------------|----------------|---------------|
|            |                            | Low / Med / High |             |

**Overfetch verdict**: Low / Medium / High

---

## 7. Refresh Correctness

After making a small change to one file, run:
```
uv run code-review-graph memory refresh
```

| Changed file | Artifacts refreshed | Correct? | Graph-expanded artifacts | Expansion makes sense? |
|--------------|--------------------|---------|--------------------------|-----------------------|
|              |                    | Y / N   |                          | Y / N                 |

**Refresh correctness score**: __ / 5

---

## 8. Token / Context Efficiency

Automated results from `run_benchmark.py` (copy from JSON):

| Task ID | Files returned | Tokens estimated | Within budget (≤40k)? |
|---------|---------------|------------------|-----------------------|
|         |               |                  | Y / N                 |
|         |               |                  | Y / N                 |

**Notes on token efficiency**:

---

## Overall Summary

| Dimension                  | Score (1–5) | Pass threshold | Pass? |
|---------------------------|-------------|---------------|-------|
| Feature classification    |             | ≥ 3           |       |
| Module classification     |             | ≥ 3           |       |
| Context pack relevance    |             | ≥ 3           |       |
| Test file inclusion        |             | ≥ 3           |       |
| Explain usefulness        |             | ≥ 3           |       |
| Changed usefulness        |             | ≥ 3           |       |
| Overfetch / noise         |             | Low or Medium |       |
| Refresh correctness       |             | ≥ 3           |       |
| Token efficiency          |             | ≤ 40k tokens  |       |

**Overall verdict**: PASS / FAIL / NEEDS WORK

**Top 3 issues to address**:
1.
2.
3.

**Evaluator notes**:
