# 30-Day Roadmap — April 2026

> CTO framing. Grounded in actual product state as of the 2026-03-25 audit.
> Phases 1–8 are committed. 1026 tests pass. The pipeline works end-to-end.
> The gap now is output quality, validation, and first-time UX — not architecture.

---

## Executive State of Play

**What you have:** A working alpha. 6 CLI commands, 5 MCP tools, graph enrichment wired, human overrides, auto-refresh, 1026 tests, docs rewritten, 7 launch blockers fixed.

**What it can't do yet:** Produce trustworthy artifacts on flat-package repos (including its own). The benchmark runs all-FAIL on this repo. "Responsibilities: data models / ORM definitions" for the memory engine. Zero features detected. The graph's AST node vocabulary — the real differentiator — is sitting unused in `graph.db`.

**The 30-day goal:** Move from "it runs" to "it impresses on 3 real-world repos." Ship a public alpha you'd hand to another developer without apologising.

---

## What NOT to Work On

Stop immediately. Don't let these in the door this month.

| Topic | Why to stop |
|---|---|
| Cloud / SaaS / multi-repo | Not in V1 scope. One distraction kills the alpha window. |
| VS Code extension updates | Extension works. Memory is the product now. |
| GUI / visualisation improvements | D3 viz exists. Not the thing that makes memory better. |
| More MCP tools | 5 tools cover all commands. Don't add before quality is fixed. |
| Supporting more languages | 14 languages already. Edge case fixing, not expansion. |
| Semantic search via embeddings | High cost, optional. Node-vocabulary scoring achieves 80% of the value first. |
| Multi-agent orchestration | Post-V1. |
| Paid tier / monetisation | Post-validation. |

---

## What to Postpone (After Month 1)

Do these — but not yet. They matter. They just don't unblock the alpha.

- `prepare-context` embedding-based semantic matching (node vocabulary gets you 80% of the way first)
- Monorepo multi-root classification (validate on simpler cases first)
- `memory init --watch` auto-regeneration mode
- Benchmark CI gate (add after you have 3 real-world repos with correct results)
- PyPI publish and install UX polish (do after first external tester validates end-to-end)

---

## Week 1 — Product Hardening (Days 1–7)

**Goal:** Fix the three output-quality failures that destroy trust on first use.

### Task 1.1 — Read graph node names in generator and classifier

**What:** Add `get_file_vocabulary(files, repo_root) -> dict[str, list[str]]` to `graph_bridge.py`.
Returns function and class names per file from the graph (using `get_nodes_by_file()`).
Wire into:
- `generator.py` responsibility inference: replace stem heuristics ("models.py → data models") with actual function/class names from the graph ("authenticate, validate_token, issue_jwt → authentication and token management")
- `context_builder.py` scoring: expand `_tokenize` to include node name vocabulary so "fix token expiry" matches files containing `validate_token()` even with no directory keyword match

**Output:**
- `graph_bridge.get_file_vocabulary()` function with graceful fallback when graph absent
- `generator.py` `_infer_responsibilities()` rewritten to use vocabulary first, stem heuristic as fallback
- `context_builder.py` scoring uses node vocabulary when `repo_root` is provided
- Tests for both paths (graph-present and graph-absent)

**Success criteria:**
- Running `memory init` on this repo: module description no longer says "data models / ORM definitions"
- Running `prepare-context "fix token expiry handling"` on a flat auth module: returns the auth file even without a matching directory name

---

### Task 1.2 — Flat-package file-stem feature detection

**What:** Add a fallback classification pass in `classifier.py` for repos with zero directory-based features.
Strategy: after Tier-1/Tier-2 keyword directory scan comes up empty, scan individual file stems for domain keywords (`auth.py`, `billing.py`, `payments.py`, `users.py`, `notifications.py`). Treat matching files as implied flat features. Confidence: 0.5 (lower than directory signal).

**Output:**
- New `_classify_flat_file_features()` function in `classifier.py`, called only when `classify_features()` returns an empty list
- Tests covering: flat repo with domain-keyword files, flat repo with no matches (still empty), mixed repo (directory wins over flat)

**Success criteria:**
- `memory init` on this repo detects `parser.py`, `graph.py`, `tools.py`, `incremental.py` as implied flat modules
- Benchmark `feature_count` moves from 0 to ≥ 2 on this repo
- No regression on directory-structured repos

---

### Task 1.3 — Quality verdict in `memory init` output + build-first enforcement

**What:**
1. After `memory init` completes, print a quality verdict line:
   - `Good: 3 features + 5 modules detected` (green if color supported)
   - `Sparse: 0 features detected — add domain corrections in .agent-memory/overrides/ or run code-review-graph build first` (yellow)
2. At the start of `memory init`, check if `graph.db` exists. If not, print a prominent notice *before* scanning:
   ```
   Tip: run `code-review-graph build` first for graph-assisted classification.
   Continuing with heuristic-only mode.
   ```
   This should be the first printed line, not buried in output.

**Output:**
- `commands.py` `memory_init_command()` updated with graph-present check and quality verdict
- Tests asserting the quality verdict and graph-missing notice appear correctly

**Success criteria:**
- First-time user running `memory init` before `build` sees the tip immediately
- Running on a sparse repo: explicit "0 features detected" verdict with guidance

---

### Week 1 — Expected output at end

- Responsibility descriptions are accurate (node vocabulary wired)
- Flat repos produce at least some features
- First-time UX is guided, not silent
- All existing 1026+ tests still pass

---

## Week 2 — Validation (Days 8–14)

**Goal:** Test on 3 real-world repos before calling this alpha-ready. No new features. Diagnosis only — fix the top issues found.

### Task 2.1 — Select and run on 3 target repos

Run the full pipeline (`build` → `memory init` → `prepare-context` × 3 tasks → `explain` → `changed`) on:

| Repo | Why |
|---|---|
| A domain-structured web app (e.g. a Django/FastAPI app with `auth/`, `billing/`, `api/` dirs) | Happy-path test — this is the intended use case |
| A flat Python library (e.g. `requests`, `httpx`, or any single-package lib) | Stress test for the flat-package blind spot |
| A monorepo (e.g. any `packages/` or `apps/` structure) | Boundary test for module detection |

For each: run benchmark (`run_benchmark.py`), fill in `scorecard_template.md`, commit the scorecard.

**Output:**
- 3 filled manual scorecards in `benchmarks/results/`
- Written notes on top-2 failures per repo

**Success criteria:**
- At least 2/3 repos: `avg_feature_confidence ≥ 0.5`, `coverage_pct ≥ 50%` for tasks that match domain dirs
- No repo produces an artifact with visibly wrong responsibility descriptions

---

### Task 2.2 — Fix top-3 issues found in validation

After running on 3 repos, rank all failures by "would a developer lose trust if they saw this" (yes/maybe/no).
Fix the top 3 yes-trust-destroying failures. These are unknown until 2.1 runs — time-box to 3 days.

**Output:** Bug fixes with tests. Changelog entries.

**Success criteria:** Re-running `run_benchmark.py` on the same repos: no more trust-destroying artifacts.

---

### Task 2.3 — Conventions bleed fix

**What:** `rules/conventions.md` lists Go, Java, Rust, TypeScript conventions for a Python-dominant project because test fixture files in other languages influence language detection.

Fix: weight language detection by lines-of-code contribution, not file count. Files under `tests/fixtures/` that are clearly sample files (< 50 lines, depth ≥ 3 from repo root) should not influence the primary language stack.

**Output:**
- `scanner.py` `_detect_languages()` updated with LOC weighting and fixture filtering
- Tests asserting Python-dominant project detects Python as primary language even with fixture files in other languages

**Success criteria:**
- `memory init` on this repo: `rules/conventions.md` lists Python conventions only, not Go/Java/Rust/TypeScript

---

### Week 2 — Expected output at end

- 3 manual scorecards committed
- Conventions bleed fixed
- Validation-discovered bugs fixed
- You can describe product quality from evidence, not intuition

---

## Week 3 — Launch Prep (Days 15–21)

**Goal:** Polish the install flow, cut the alpha tag, make it shippable to the first external testers.

### Task 3.1 — `prepare-context` match transparency

**What:** When `prepare-context` returns a pack, print which features/modules matched and at what score, not just the file list.

Current output:
```
Task: add rate limiting to auth middleware
Inspect first: code_review_graph/memory/context_builder.py, ...
```

Target output:
```
Task: add rate limiting to auth middleware
Matched: Auth (score: 0.72), Api (score: 0.41)
Inspect first: src/auth/middleware.py, src/auth/views.py, src/api/routes.py
```

When fallback fires (score 0 for everything):
```
Task: add rate limiting to auth middleware
No area matched — showing broadest context.
For better results: run `memory annotate` to add task hints.
```

**Output:**
- `context_builder.py` `_build_summary()` updated to include match names + scores
- CLI and MCP output includes this transparency line
- Tests covering matched, fallback, and partial-match cases

**Success criteria:**
- Developer can immediately tell whether the result is "high confidence" or "I couldn't find it"

---

### Task 3.2 — Install and setup flow audit

Walk through the complete first-time setup as a new user on a clean machine:

```bash
pip install code-review-graph
cd my-project
code-review-graph build
code-review-graph memory init
# Add @.agent-memory/CLAUDE.md to your Claude Code config
code-review-graph memory prepare-context "add a new user registration endpoint"
```

For each step: is the output clear? Is the error message (if any) actionable? Is the next step obvious?

Fix every point of confusion. Do not add features — only improve messages, error text, and guidance output.

**Output:**
- Updated `--help` text for `memory init`, `prepare-context`, `explain`, `changed`, `refresh`
- Improved error messages for the most common failure modes (wrong path, missing build, no features classified)
- Updated `docs/USAGE.md` quick-start section to match exact current command output

**Success criteria:**
- A developer unfamiliar with the product can complete the setup flow without reading source code

---

### Task 3.3 — Cut the alpha tag and commit Phase 8 fixes

**What:**
1. Commit the Phase 8 launch fixes (7 blockers: banner, dead-end message, test file leak, fallback warning, path validation, CHANGELOG, README CTA) — currently local changes
2. Tag `v1.9.0-alpha` in git
3. Write a one-page "Alpha Release Notes" document (`docs/ALPHA-RELEASE.md`) covering: what it does, what it doesn't do yet, known limitations (flat packages, lexical-only matching without graph), how to report issues

**Output:**
- Committed Phase 8 fixes
- `v1.9.0-alpha` git tag
- `docs/ALPHA-RELEASE.md`

**Success criteria:**
- Tag exists. Release notes are honest about limitations.

---

### Task 3.4 — First external tester handoff

Identify 1–3 developers outside the team. Hand them the alpha with the install guide. Ask them to run it on their own repo and fill out a structured feedback form (5 questions: did it run, was the output useful, what was wrong, what was missing, would you use it).

**Output:** Written feedback from ≥ 1 external tester.

**Success criteria:** At least one external tester runs end-to-end without hand-holding from the team.

---

### Week 3 — Expected output at end

- Alpha tag cut and committed
- External testers running
- `prepare-context` output is transparent about what matched and why
- Setup flow is guided and self-explanatory

---

## Week 4 — Post-Launch Learning (Days 22–28)

**Goal:** Listen. Fix the top-2 friction points reported by external testers. Do not build new features.

### Task 4.1 — Triage external feedback

Categorise all reported issues as:
- **Trust-destroying** (wrong output, misleading artifacts) → fix immediately
- **Friction** (confusing UX, missing guidance) → fix this week
- **Missing feature** (would be nice) → add to V2 list
- **Working as intended** → document and close

**Output:** Prioritised issue list. Fix the top 2 trust-destroying and top 2 friction issues.

---

### Task 4.2 — Benchmark CI gate (scoped)

Add a CI step that runs `run_benchmark.py --repo . --skip-init` on this repo and asserts:
- `module_count ≥ 1`
- `avg_module_confidence ≥ 0.5`
- No task pack has `files_returned > 20`

This is not a full quality gate yet — just a regression guard. The all-FAIL on feature_count is documented and expected for this repo.

**Output:**
- `.github/workflows/ci.yml` updated with benchmark step
- `benchmarks/tasks/sample_tasks.json` updated to reflect post-week-1 quality expectations

**Success criteria:**
- CI passes. No regressions in classification quality.

---

### Task 4.3 — Hotspot and freshness UX

The `changes/recent.md` and `changes/hotspots.md` artifacts are generated but underused. After a `memory refresh`, the user sees the files but not a clear "what to pay attention to" summary.

Add a `changed --summary` flag that prints a one-paragraph digest:
- N files changed in the last refresh
- Top 3 hotspot files by change frequency
- Any areas with changes that have no related tests

**Output:**
- `lookup.py` `changed_match()` extended with summary mode
- `commands.py` `memory_changed_command()` updated
- Tests for summary output

**Success criteria:**
- `memory changed --summary` produces a single short digest useful for daily standup / code review prep

---

### Week 4 — Expected output at end

- External tester feedback triaged and top issues fixed
- Benchmark CI gate in place
- `changed --summary` digest exists
- V2 feature list written based on real feedback (not guesses)

---

## V2 Deeper Work (Day 29–30: Planning Only)

Do not implement these this month. Use the last 2 days to write the V2 spec with specifics from month-1 learnings.

### V2.1 — Embedding-based semantic task matching

Replace lexical `_score()` in `context_builder.py` with a two-stage approach:
1. Lexical score (current — fast, zero-dependency)
2. Embedding similarity score using `semantic_search_nodes_tool` (optional, high-quality)

`prepare-context "fix the token expiry edge case"` → semantic match → `authenticate()`, `validate_token()` → auth module.

**Why defer:** Node-vocabulary scoring (week 1) achieves 70–80% of this benefit at zero runtime cost. Validate whether the gap is worth the embedding dependency.

---

### V2.2 — Graph-cluster-based module detection for flat repos

Use IMPORTS_FROM edges in `graph.db` to cluster highly-connected files into implied modules. A flat repo with `auth.py` importing `tokens.py` and `sessions.py` → one cluster. `billing.py` importing `invoices.py` and `stripe_client.py` → another.

**Why defer:** File-stem flat detection (week 1) gives a reasonable baseline. Validate whether graph-cluster detection improves precision meaningfully on real repos before implementing.

---

### V2.3 — Confidence-gated artifact regeneration

Today `memory refresh` regenerates all artifacts for changed files. V2 should: only regenerate an artifact if the new classification confidence differs from the stored confidence by more than a threshold. Prevents Git noise from minor changes that don't affect substance.

---

### V2.4 — Structured task feedback loop

When a developer corrects a `prepare-context` result ("this was wrong, the right area is auth"), capture that as a training signal in the overrides YAML. Over time, the overrides file becomes a self-improving fine-tuning layer for the lexical scorer.

---

## Risk Register

### Biggest Product Risk

**Risk:** Output quality on flat-package repos is too weak to earn trust, and most mature Python/Go/Rust projects are flat.

**Evidence:** This repo — a working developer tool — produces 0 features, 1 module, wrong responsibility description, and a benchmark full-fail. If a developer's first `memory init` produces this, they close the terminal and never come back.

**Mitigation:** Week 1 flat-file detection + node-vocabulary reading. If these two changes don't fix the output quality to "mostly right" level, you need to reconsider whether heuristic classification is the right first layer at all — semantic search might need to come sooner.

**Signal to watch:** After week 1, re-run on this repo. If responsibility descriptions are still wrong and feature count is still 0, the product needs a different classification approach before external release.

---

### Biggest Technical Risk

**Risk:** Graph node vocabulary reading (`get_file_vocabulary`) adds a runtime coupling between the memory layer and `graph.db` that currently doesn't exist. If the graph schema changes (tree-sitter upstream change, SQLite migration), memory quality silently degrades.

**Mitigation:**
1. Keep the vocabulary path strictly optional — `graph_bridge.get_file_vocabulary()` returns `{}` when graph absent. Memory works without it, just at lower quality.
2. Add a test that asserts `get_file_vocabulary()` returns `{}` when repo_root is None or graph.db absent.
3. Do not store vocabulary in `.agent-memory/` artifacts — re-derive it from the live graph each time. This keeps artifacts stable even when the graph changes.

---

### Highest-Leverage Investment

**Single best use of time in month 1:**

Wiring graph node names (function and class names per file) into both the responsibility inference (`generator.py`) and the relevance scorer (`context_builder.py`).

Why this wins:
1. Fixes the worst visible trust problem (wrong responsibility labels) in one place
2. Makes `prepare-context` semantic without any embedding infrastructure
3. Uses data already in `graph.db` — zero new parsing, zero new dependencies
4. Benefits every command: `explain`, `changed`, `prepare-context`, and `memory init` output
5. Creates a clean abstraction (`get_file_vocabulary`) that V2 semantic matching builds on top of

No other single change produces this breadth of improvement.

---

## 30-Day Summary Table

| Week | Theme | Goal | Key output |
|---|---|---|---|
| 1 | Product hardening | Fix output quality | Node vocabulary wired, flat-file detection, quality verdict |
| 2 | Validation | Test on 3 real repos | 3 filled scorecards, validation bugs fixed, conventions bleed fixed |
| 3 | Launch prep | Cut alpha, ship to testers | v1.9.0-alpha tag, match transparency, install flow polished |
| 4 | Post-launch learning | Listen and fix | Tester feedback triaged, top 2 frictions fixed, CI gate added |
| V2 planning | Next bets | Spec V2 | Written V2 plan grounded in month-1 evidence |

---

## Definition of Done for This Roadmap

The 30-day roadmap is complete when:

1. **Quality bar met**: Running `memory init` on this repo, a Django web app, and a flat Python library all produce artifacts where the responsibility descriptions are accurate (verified by reading the source)
2. **Benchmark regression-free**: `run_benchmark.py --repo .` shows `module_count ≥ 1`, `avg_module_confidence ≥ 0.5`, no pack > 20 files
3. **External validation**: At least 1 developer outside the team ran end-to-end without hand-holding and reported results
4. **Alpha shipped**: `v1.9.0-alpha` tag exists; release notes are honest about limitations
5. **V2 plan written**: Feature decisions are backed by tester feedback, not guesses

---

*Roadmap written 2026-03-25. Review at end of week 2 after validation results.*
