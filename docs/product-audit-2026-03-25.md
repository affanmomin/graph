# Product Audit — code-review-graph
**Date:** 2026-03-25
**Version:** v1.8.4
**Codebase:** 22 Python source files · ~10,200 lines · 800 tests (all passing)

---

## 1. Executive Summary

This is no longer primarily a code-review tool. It has become a **dual-layer product**: a mature, production-grade code intelligence engine (Layer A) with a functionally complete repo-memory subsystem (Layer B) built on top of it. The core promise — "stop re-explaining your repo to AI every session" — is implementable today.

The CLI surface is clean and complete for 5 of 6 memory commands. The MCP surface has 14 tools including 5 memory tools. Tests are thorough (800 passing, 12 memory test files totalling ~5,200 lines). The architecture is sound.

What is missing is not functionality — it is integration confidence and one specific divergence between the CLI path and the MCP path.

**This is a credible alpha. It is not a polished product.**

---

## 2. Product Surface

### CLI Commands (14 total)

| Command | Status | Notes |
|---|---|---|
| `install` / `init` | ✅ | Writes `.mcp.json` for Claude Code |
| `build` | ✅ | Full graph parse |
| `update` | ✅ | Incremental, git-diff based |
| `watch` | ✅ | Watchdog file watcher |
| `status` | ✅ | Graph stats |
| `visualize` | ✅ | D3.js interactive HTML |
| `serve` | ✅ | FastMCP stdio server |
| `memory init` | ✅ | Full pipeline: scan → classify → generate → write → metadata |
| `memory refresh` | ✅ | Incremental + `--full`; writes `freshness.json` |
| `memory explain <target>` | ✅ | 6-stage resolution, explains feature/module/path |
| `memory prepare-context "<task>"` | ✅ | Keyword-scored context pack; `--json` flag |
| `memory changed <target>` | ✅ | Change summary using `freshness.json` |
| `memory annotate` | ⚠️ **Stub** | Prints format docs, does nothing |

### MCP Tools (14 total, in `tools.py`)

**Layer A — Graph engine:**

| Tool | Purpose |
|---|---|
| `build_or_update_graph_tool` | Full or incremental graph build |
| `get_impact_radius_tool` | BFS blast-radius from changed files |
| `query_graph_tool` | callers_of, callees_of, imports_of, inheritors_of, tests_for, children_of, file_summary |
| `get_review_context_tool` | Token-efficient review context with structural summary |
| `semantic_search_nodes_tool` | Keyword + vector search |
| `list_graph_stats_tool` | Aggregate stats |
| `embed_graph_tool` | Compute vector embeddings |
| `get_docs_section_tool` | Doc section retrieval |
| `find_large_functions_tool` | Find oversized nodes by line count |

**Layer B — Memory engine:**

| Tool | Purpose |
|---|---|
| `memory_init` | Scan + generate `.agent-memory/` |
| `memory_prepare_context` | Task → context pack (structured dict) |
| `memory_explain_area` | Explain a named feature or module |
| `memory_recent_changes` | Change summary for an area |
| `memory_refresh` | Refresh artifacts (incremental) |

### User-Facing Workflows That Work End-to-End

1. **One-time bootstrap:** `memory init` → commit `.agent-memory/`
2. **Task start ritual:** `memory prepare-context "<task>"` → paste to Claude Code
3. **Area exploration:** `memory explain <feature>` or `memory changed <feature>`
4. **Post-change refresh:** `memory refresh` (incremental) or `memory refresh --full`
5. **Human override loop:** edit `overrides/global.yaml` → re-run `memory init`
6. **Structural code review:** `build` → MCP graph tools via Claude Code

### Durable Artifacts Generated

| File | Status |
|---|---|
| `.agent-memory/repo.md` | ✅ |
| `.agent-memory/architecture.md` | ✅ |
| `.agent-memory/features/<slug>.md` | ✅ One per detected feature |
| `.agent-memory/modules/<slug>.md` | ✅ One per detected module |
| `.agent-memory/rules/conventions.md` | ✅ CLI path only (see gap §6) |
| `.agent-memory/rules/safe-boundaries.md` | ✅ CLI path only (see gap §6) |
| `.agent-memory/metadata/manifest.json` | ✅ |
| `.agent-memory/metadata/sources.json` | ✅ |
| `.agent-memory/metadata/confidence.json` | ✅ |
| `.agent-memory/metadata/freshness.json` | ✅ Written on refresh |
| `.agent-memory/changes/recent.md` | ✅ Written on refresh; not surfaced in `memory changed` output |
| `.agent-memory/overrides/*.yaml` | ✅ Loaded and applied |
| `.agent-memory/tasks/*.md` | ❌ Not implemented |
| `.agent-memory/changes/hotspots.md` | ❌ Not implemented |

### Local-Only vs Git-Committed

| Data | Committed? |
|---|---|
| `.agent-memory/` (all .md, .json, .yaml) | **Yes — by design** |
| `.code-review-graph/graph.db` | No — local performance index |
| Embeddings cache | No — local |
| Parser caches (in-memory) | No |

---

## 3. Architecture

### Major Subsystems

```
code_review_graph/
├── parser.py          1,172 lines — Tree-sitter, 14 languages
├── graph.py             619 lines — SQLite store, BFS impact analysis
├── incremental.py       554 lines — Git diff, file watcher, _maybe_refresh_memory
├── tools.py           1,249 lines — 14 MCP tool implementations
├── cli.py               450 lines — argparse, dispatch
├── main.py                          FastMCP server entry point
├── visualization.py                 D3.js interactive HTML export
├── embeddings.py                    Optional vector search
└── memory/            3,564 lines — Layer B (12 modules)
    ├── scanner.py       396 lines — Filesystem walk, RepoScan, language/framework detection
    ├── classifier.py    553 lines — Group files → FeatureMemory / ModuleMemory
    ├── generator.py     901 lines — Render models → markdown strings
    ├── writer.py        242 lines — Atomic disk writes, skip-if-unchanged
    ├── metadata.py      238 lines — manifest/freshness/confidence/sources JSON
    ├── refresh.py       344 lines — plan_refresh + execute_refresh
    ├── context_builder.py 337 lines — task → TaskContextPack (keyword scoring)
    ├── overrides.py     370 lines — load + apply YAML overrides
    ├── lookup.py        564 lines — 6-stage target resolution for explain/changed
    ├── commands.py      453 lines — CLI handlers
    ├── models.py        279 lines — dataclasses
    └── __init__.py       75 lines — public API exports
```

### Existing Fork Functionality Reused (Untouched)

- Tree-sitter parsing (14 languages including Vue SFC and Solidity)
- SQLite graph store (nodes, edges, BFS)
- `incremental.py` `get_changed_files` / `get_staged_and_unstaged` — reused in `memory_refresh_command`
- `find_project_root` — reused for `--repo` auto-detection
- FastMCP / MCP server plumbing — extended with 5 new tools
- CI pipeline and test structure

### New Memory-Layer Components Added

All 12 files in `code_review_graph/memory/`. `incremental.py` was modified to add `_maybe_refresh_memory()` — a non-fatal hook that fires after graph update when `.agent-memory/` exists. `cli.py` was extended with the full `memory` subcommand group.

### Data Flow — Init Path

```
scan_repo(repo_root)
  → RepoScan{languages, source_dirs, test_dirs, framework_hints, config_files, confidence}
    → classify_features / classify_modules
      → [FeatureMemory, ...] / [ModuleMemory, ...]
        → generate_*_doc(feature/module)
          → markdown string
            → write_text_if_changed(path, content)
              → "created" / "updated" / "unchanged"
                → save_manifest / save_sources_json / save_confidence_json
```

### Data Flow — Refresh Path

```
get_changed_files(repo_root) → [changed file paths]
  → plan_refresh(changed_files, features, modules, full=False)
    → RefreshPlan{mode, impacted_feature_slugs, impacted_module_slugs, update_repo, reason}
      → execute_refresh(plan, ...)
        → regenerate only impacted feature/module docs
        → always write changes/recent.md + metadata/freshness.json
```

---

## 4. Repo-Memory Implementation Detail

### Scanner (`scanner.py`)

Produces a `RepoScan` dataclass: `top_level_dirs`, `source_dirs`, `test_dirs`, `docs_dirs`, `config_files`, `languages`, `framework_hints`, `file_counts`, `readme_path`, `confidence` [0–1], `notes`.

Heuristics: extension map for 14 languages; skips `.git`, `node_modules`, `.venv`, `__pycache__`, `.code-review-graph`, `.agent-memory`; framework detection by file presence (`manage.py` → Django, `next.config.js` → Next.js, etc.).

### Classifier (`classifier.py`)

Two parallel classification streams:

**Feature classification (domain/product):**
- Tier-1 keywords (confidence 0.8–1.0): auth, billing, payment, user, notification, search, admin, api, upload, storage, report, cache, task, health, …
- Tier-2 keywords (confidence 0.5–0.7): core, common, utils, middleware, model, view, route, controller, service, repository, …
- Cross-cutting detection: names appearing across multiple source subtrees → lower confidence (0.3–0.5)

**Module classification (structural/package):**
1. Sub-packages with `__init__` or index files inside known source dirs → confidence 0.9
2. Direct children of monorepo `apps/`, `packages/`, `services/` → confidence 0.8
3. Fallback: each top-level source dir → confidence 0.6

> **Critical note:** `classifier.py` never queries `graph.db`. Classification is purely filesystem-based. Call graphs, test coverage edges, and import dependency chains from Layer A are completely unused.

### Generator (`generator.py`)

| Function | Output |
|---|---|
| `generate_repo_summary(scan)` | Stack, top-level structure, key locations |
| `generate_architecture_doc(scan)` | Major boundaries, inferred layout, coupling notes |
| `generate_feature_doc(feature)` | Purpose, files, tests, dependencies, rationale |
| `generate_module_doc(module)` | Role, files, tests, dependencies, dependents |
| `generate_conventions_doc(scan, overrides)` | Language/framework conventions, patterns |
| `generate_safe_boundaries_doc(scan, overrides)` | Never-edit paths, migration notes, PCI notes |

All functions are deterministic (same input → same string), produce no disk I/O, and degrade gracefully on weak signals. No LLM calls anywhere.

### Context Builder (`context_builder.py`)

**Scoring algorithm (per feature/module):**

```
raw = (2.0 × name_overlap + 1.0 × stem_overlap + 1.5 × dir_overlap) / 4.5
score = raw × (0.4 + 0.6 × confidence)
```

- `name_overlap`: task tokens ∩ feature/module name tokens / task token count
- `stem_overlap`: task tokens ∩ file stems / task token count
- `dir_overlap`: task tokens ∩ directory path components / task token count
- Confidence soft-weighting: high-confidence classifications rank above speculative ones

Threshold: 0.05. Caps: 5 features, 5 modules, 20 files. Fallback: when nothing scores above threshold, returns top 2 features + 2 modules (pack is never empty). Overrides applied last — they always win.

### Overrides (`overrides.py`)

`Overrides` dataclass: `always_include`, `never_edit`, `notes`, `task_hints` (list of `TaskHint{pattern, hint}`), `source_files`.

Load strategy: `global.yaml` first, then alphabetical area files. Merge policy: first-occurrence-wins deduplication. `apply_overrides` called *last* in `build_context_pack` — overrides always win over inference. Task-hint matching is case-insensitive token overlap. Never-edit paths surface as warnings in the pack. Human-authored files are never auto-overwritten (`write_override_if_absent` enforces this).

### Target Resolution (`lookup.py`)

6 stages for `match_target(target, agent_memory_root, features, modules)`:
1. Exact name match (case-insensitive) against features, then modules
2. Slug match (handles "authentication", "src-auth", etc.)
3. Path match — linear scan of all feature/module files
4. Substring match — one area contains target as substring
5. Fuzzy score using `_score` / `_tokenize` reused from context_builder (threshold 0.08)
6. Not found — return `TargetMatch(kind="not_found")` with alternatives

### Metadata (`metadata.py`)

**`manifest.json`** — version, generated_at, repo_root, source_roots, discovered_languages, discovered_docs_dirs, discovered_test_dirs, config_files, framework_hints, scan_confidence, generated_artifacts list.

**`sources.json`** — file-path → [feature:slug, module:slug] index.

**`confidence.json`** — per-artifact: name, slug, type, confidence, file_count, test_count.

**`freshness.json`** — refreshed_at, mode, changed_files_count, changed_files list, impacted_features list, impacted_modules list, artifacts_refreshed list.

### Writer (`writer.py`)

`write_text_if_changed(path, content)` — normalizes trailing newline, encodes UTF-8, compares bytes to existing file, atomic temp+rename write. Returns `"created"` / `"updated"` / `"unchanged"`.

`write_json_if_changed(path, data)` — `json.dumps(sort_keys=True, indent=2)`, delegates to above. Same input → same bytes → clean Git diff.

`write_override_if_absent(path, content)` — writes only if file does not exist. Human edits are never overwritten.

---

## 5. Maturity Assessment

### Production-Like

- **`parser.py`** — 14 languages, Solidity event/modifier support, Vue SFC delegation, call resolution, TOCTOU-safe reads, 180-depth AST guard, LRU module cache (15,000 entry cap). Battle-hardened.
- **`graph.py`** — WAL-mode SQLite, parameterized queries throughout, atomic file-scoped writes, thread-safe BFS, name sanitization for MCP safety.
- **`writer.py`** — Correct. Atomic temp+rename, byte-level idempotency, stable JSON serialization. Git diffs are clean.
- **`tools.py`** — 14 tools with `_validate_repo_root` path-traversal defense and `_sanitize_name` prompt-injection defense.
- **Test suite** — 800 tests, 12 memory test files (~5,200 lines). High signal-to-noise. Execution: 1.8 seconds.

### Alpha / Heuristic-Heavy

- **`classifier.py`** — entirely filesystem heuristic. No graph engine integration. A repo with non-standard layout (flat `src/`, no subdirectories, monorepo with deeply nested packages) will produce weak or empty feature classification.
- **`scanner.py`** — lightweight filesystem walk. Framework detection is keyword-matching on config files. Confidence score is a simple formula, not a quality measurement.
- **`context_builder.py`** — keyword overlap scoring. Works well when task language matches directory/file naming. Fails quietly on abstract tasks ("improve performance", "refactor the service layer") that don't name specific areas.
- **`generator.py`** — template rendering. Generated feature docs list files and tests — they do not explain *what the feature does*. They are structured indexes, not explanations.

### Fragile in Messy Repos

- **Flat file layouts:** `classifier.py` looks for subdirectories under inferred source roots. A project with all files in `src/` root produces zero features. Fallback fires but quality is low.
- **Large unclassified repos:** `refresh.py` maps changed files to features by set intersection. If most files are unowned by any feature, changed files hit nothing and the plan is "no artifacts need updating" even when stale.
- **Slow first run:** Initial scan parses every source file with Tree-sitter. 5–15 seconds on 1,000-file repos.
- **`memory annotate` is a stub** — the primary entry point for human corrections does nothing.

---

## 6. Gap Analysis

### Gap 1 — MCP `memory_init` diverges from CLI `memory_init` (Correctness)

`commands.py:memory_init_command` generates `rules/conventions.md` and `rules/safe-boundaries.md` and applies overrides. `tools.py:memory_init` does **not** call `generate_conventions_doc`, `generate_safe_boundaries_doc`, or `load_overrides`. When Claude Code calls `memory_init` as an MCP tool, no rules files are written and overrides are ignored.

**Fix:** Extract a shared `_run_init_pipeline(root)` function called by both.

### Gap 2 — Automatic refresh not wired to `code-review-graph update` (Behavioral)

`_maybe_refresh_memory` was added to `incremental.py` but `refresh_memory` defaults to `False`. The `update` CLI command calls `incremental_update(repo_root, store, base=args.base)` without passing `refresh_memory=True`. Automatic memory refresh on git commit is not enabled.

**Fix:** One line in `cli.py` — pass `refresh_memory=True` to `incremental_update`.

### Gap 3 — `memory annotate` is a stub (UX)

This is the command that creates `overrides/global.yaml`. Every user who wants to add domain knowledge must manually create the file. The stub prints format docs and exits. It is the primary human correction entry point.

**Fix:** Scaffold `overrides/global.yaml` with commented template, open in `$EDITOR` (with fallback to printing the path).

### Gap 4 — Classifier does not use the graph engine (Architectural)

`classifier.py` does not query `graph.db`. The 9 existing Layer A tools — impact radius, callers, tests_for, imports_of, TESTED_BY edges, INHERITS edges — are never consulted during feature classification or context pack building. The memory system ignores the most valuable data available to it.

**Fix (medium-term):** Query `graph.db` if it exists to: use `TESTED_BY` edges for actual test coverage, `CALLS` edges for feature dependencies, file co-occurrence for grouping. This elevates classification from "filesystem guessing" to "structural understanding."

### Gap 5 — `changes/recent.md` is generated but not surfaced (Product)

`execute_refresh` writes `changes/recent.md`. `memory_changed_command` reads `freshness.json` and reports on impacted features — it does not read or display `changes/recent.md`. The file exists but is unused in user-facing output.

### Gap 6 — No Claude Code CLAUDE.md integration (Discovery)

There is no `.agent-memory/CLAUDE.md` generated. Claude Code picks up `CLAUDE.md` files automatically at session start. If `memory init` generated a `CLAUDE.md` from `repo.md` + `architecture.md` + `rules/conventions.md`, repo memory would be available to every Claude Code session without any user action.

### Gap 7 — Present in code but not in docs/README

- `--json` flag on `memory prepare-context` — implemented, untested in docs
- `memory explain` works on file paths, not just feature/module names — mentioned only in CLI `--help`
- Scoring weights are hardcoded and tunable only by editing source (`_W_NAME=2.0`, `_W_FILE_STEM=1.0`, `_W_PATH_DIR=1.5`)
- `scan.notes` field surfaces scanner warnings — visible only in `memory init` output, not elsewhere

### Gap 8 — No real-world validation

Every test fixture is a minimal `tmp_path` with hand-crafted files. The heuristics have never been run on a Django monorepo, a Next.js app-router project, or a Go service with `pkg/internal` layout. There is no benchmark for whether context packs actually improve Claude Code session quality vs. no context.

---

## 7. Product Identity

### Is this still a code-review tool or a repo-memory product?

It is structurally both. The codebase is split: ~6,600 lines of code intelligence engine (Layer A) and ~3,600 lines of memory engine (Layer B). Layer A is what the README leads with. Layer B is what PRODUCT.md describes as the mission. The CLI banner shows "Graph commands" first and "Repo-memory commands" second.

The tension is real and should be resolved editorially before public launch — not in code, but in README, docs, and positioning.

### Strongest one-line description of the current product

> A Git-native repo memory system for Claude Code that generates committed `.agent-memory/` artifacts from your codebase structure, so every AI session starts with full project context instead of none.

The code review graph engine is the implementation substrate — the reason the classifier and scanner work without LLMs. The product is the memory.

---

## 8. What We Have Achieved

1. **Complete Layer B memory engine** — 12 modules, all implemented. Scanner, classifier, generator, writer, metadata, refresh, overrides, context builder, lookup. No stubs except `memory annotate`.

2. **5 of 6 memory CLI commands working end-to-end** — `init`, `refresh`, `explain`, `prepare-context`, `changed`. All tested. All producing real output.

3. **5 MCP memory tools** — Claude Code can call `memory_init`, `memory_prepare_context`, `memory_explain_area`, `memory_recent_changes`, `memory_refresh` as tools directly.

4. **Human overrides that propagate** — `overrides/global.yaml` is applied in `prepare-context`, reflected in `rules/conventions.md` and `rules/safe-boundaries.md`, surfaced as warnings.

5. **Deterministic, idempotent, Git-friendly artifact generation** — running `memory init` twice produces no diff. Byte-level comparison in `writer.py` and `sort_keys=True` in JSON serialization make this reliable.

6. **800 passing tests** — 12 memory test files, 50% memory test LOC share. Covers unit, integration, and CLI flows. Integration tests prove the full product loop: init → refresh → prepare-context → explain → changed.

7. **Real documentation** — `docs/repo-memory-demo.md` shows a realistic end-to-end walkthrough. README has a Repo Memory section with commands, artifact layout, and override format.

8. **Security hardened** — no `eval`, `exec`, `pickle`, `yaml.unsafe_load`; parameterized SQL; path-traversal prevention; name sanitization; SRI hash on CDN script; API keys from environment only.

---

## 9. What Is Still Unproven

1. **Classifier quality on real repos** — heuristics have never faced a production codebase at scale.
2. **Context pack usefulness** — no benchmark exists for whether packs improve Claude Code session quality vs. no context.
3. **Graph + memory integration** — the graph engine and memory engine do not talk to each other. Classification ignores call graphs, test coverage, and import chains.
4. **`memory annotate`** — the primary human correction entry point is a stub.
5. **MCP `memory_init` correctness** — does not generate rules files or apply overrides (diverges from CLI path).
6. **`changes/recent.md` utility** — generated but never surfaced in output.
7. **Automatic refresh on `update`** — not wired up.

---

## 10. What Should Happen Next

### Priority 1 — Fix correctness gaps (before calling this alpha-complete)

1. **Sync MCP `memory_init` with CLI `memory_init`** — extract shared `_run_init_pipeline(root)` called by both `commands.py` and `tools.py`. This is the most important correctness gap.

2. **Wire automatic refresh in `update`** — pass `refresh_memory=True` to `incremental_update` in `cli.py`. One-line fix.

3. **Implement `memory annotate`** — scaffold `overrides/global.yaml` with commented template, open in `$EDITOR`. This is the primary human correction entry point.

### Priority 2 — Real-world validation

4. **Run `memory init` on this codebase** — then on one messy real-world repo. Observe what the classifier produces. Fix the most common failure mode (flat layouts, empty feature detection).

5. **Generate `.agent-memory/CLAUDE.md`** — assembled from `repo.md` + `architecture.md` + `rules/conventions.md`. Makes repo memory available to every Claude Code session automatically.

6. **Surface `changes/recent.md` in `memory changed` output** — the file exists; it just needs to be read and displayed.

### Priority 3 — Connect the layers

7. **Query `graph.db` in `classifier.py`** — use `TESTED_BY` edges for actual test coverage, `CALLS` edges for feature dependencies. Elevates classification from filesystem guessing to structural understanding.

### Priority 4 — Before public launch

8. **Benchmark task setup time** — measure "time from fresh session to first useful code edit" with and without `memory prepare-context`. This is the product's core claim and it has never been measured.

9. **Resolve product identity in README** — the README currently leads with code-review benchmarks. If the mission is repo memory, the README should lead with that.

10. **Test on 3 real repos** — one Django app, one Next.js monorepo, one Go service. Observe, fix, repeat.

---

## Appendix — Codebase Metrics

### Source Files

| File | Lines | Layer |
|---|---|---|
| `tools.py` | 1,249 | A + B |
| `parser.py` | 1,172 | A |
| `memory/generator.py` | 901 | B |
| `memory/lookup.py` | 564 | B |
| `incremental.py` | 554 | A |
| `memory/classifier.py` | 553 | B |
| `cli.py` | 450 | C |
| `memory/commands.py` | 453 | C |
| `memory/refresh.py` | 344 | B |
| `main.py` | 345 | C |
| `memory/context_builder.py` | 337 | B |
| `memory/overrides.py` | 370 | B |
| `memory/scanner.py` | 396 | B |
| `memory/writer.py` | 242 | B |
| `memory/metadata.py` | 238 | B |
| `memory/models.py` | 279 | B |
| `graph.py` | 619 | A |
| `visualization.py` | 659 | A |
| `embeddings.py` | 383 | A |
| `memory/__init__.py` | 75 | B |

### Test Files

| File | Lines |
|---|---|
| `test_memory_overrides.py` | 592 |
| `test_memory_refresh.py` | 547 |
| `test_memory_generator.py` | 546 |
| `test_memory_tools.py` | 521 |
| `test_memory_context_builder.py` | 508 |
| `test_memory_integration.py` | 495 |
| `test_memory_classifier.py` | 484 |
| `test_memory_lookup.py` | 465 |
| `test_memory_scanner.py` | 462 |
| `test_memory_writer.py` | 379 |
| `test_memory_cli.py` | 354 |
| `test_memory_imports.py` | 333 |
| `test_multilang.py` | 499 |
| `test_parser.py` | 275 |
| `test_tools.py` | 271 |
| `test_graph.py` | 180 |
| `test_visualization.py` | 200 |
| `test_incremental.py` | 238 |
| `test_embeddings.py` | 135 |
| **Total** | **~7,500** |

**All 800 tests passing. Execution time: 1.8 seconds.**
