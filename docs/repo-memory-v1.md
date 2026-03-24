# Repo Memory V1 — Design Reference

> This document describes the architecture, artifact layout, and module responsibilities
> for the repo-memory subsystem being added to `code-review-graph`.
> It is intended for contributors and for AI coding sessions working in this area.

## Product goal

Stop re-explaining your repo to AI every session.

The repo-memory subsystem generates a `.agent-memory/` folder that is committed to Git.
It contains concise, grounded, human- and agent-readable memory artifacts that let
Claude Code start a fresh session with useful project context instead of rediscovering
the codebase from scratch.

---

## Architecture layers

| Layer | Location | Status |
|---|---|---|
| A — Code intelligence engine | `code_review_graph/` (existing) | Complete |
| B — Memory engine | `code_review_graph/memory/` | In progress |
| C — Agent interface (CLI + MCP) | `cli.py` + `tools.py` extensions | Planned |

Layer B reads from Layer A. It does not modify graph.py, parser.py, incremental.py, or tools.py.

---

## `.agent-memory/` artifact layout

```
.agent-memory/
  repo.md                      # high-level repo overview
  architecture.md              # system boundaries, flows, risky areas
  features/
    <slug>.md                  # one file per detected feature
  modules/
    <slug>.md                  # one file per detected module
  tasks/
    <slug>.md                  # task playbooks (later)
  changes/
    recent.md                  # recent meaningful changes
    hotspots.md                # high-churn files and areas
  rules/
    conventions.md             # detected coding conventions
    safe-boundaries.md         # areas to avoid or treat carefully
  overrides/
    *.yaml                     # human-authored corrections and hints
  metadata/
    manifest.json              # index of all artifacts + overall state
    freshness.json             # per-artifact last-generated + stale flags
    confidence.json            # per-artifact confidence scores
    sources.json               # per-artifact source file lists
```

**Git commit policy:**
- All files above are committed to Git (durable, shareable).
- The graph database (`.code-review-graph/graph.db`) stays local-only.
- Override files in `overrides/` are human-authored and must never be auto-overwritten.

---

## Module responsibilities

| Module | Responsibility |
|---|---|
| `models.py` | Core data models: `MemoryArtifact`, `FeatureMemory`, `ModuleMemory`, `TaskContextPack`, `MemoryManifest`, `ArtifactMetadata` |
| `scanner.py` | Walk the repo filesystem; detect languages, frameworks, docs, config |
| `classifier.py` | Group graph nodes into features and modules; attach confidence scores |
| `generator.py` | Render model objects into markdown artifact content (no disk I/O) |
| `writer.py` | Write artifact content to `.agent-memory/` (atomic, skip if unchanged) |
| `metadata.py` | Read/write manifest, freshness, confidence, sources JSON files |
| `refresh.py` | Incremental refresh orchestration: map changed files → affected artifacts |
| `context_builder.py` | Assemble `TaskContextPack` for a natural-language task description |
| `overrides.py` | Load and apply human override YAML files |

---

## CLI commands (planned — Layer C)

| Command | Description |
|---|---|
| `init-memory` | First-time memory generation for a repo |
| `refresh-memory` | Incremental (default) or full refresh |
| `prepare-context <task>` | Return a focused `TaskContextPack` for a task |
| `explain-area <path>` | Explain a feature, module, or file path |
| `what-changed <area>` | Show recent meaningful changes in an area |
| `annotate-memory` | Open or create an override file for human editing |

---

## Data flow

```
repo files
    │
    ▼
[scanner.py]  ──────────────────────────────────► RepoScan
    │
    ▼
[GraphStore (Layer A)] ◄── existing graph build
    │
    ▼
[classifier.py]  ────────────────────────────────► list[FeatureMemory], list[ModuleMemory]
    │
    ▼
[generator.py]  ─────────────────────────────────► artifact content strings
    │
    ▼
[writer.py]  ────────────────────────────────────► .agent-memory/ files on disk
    │
    ▼
[metadata.py]  ──────────────────────────────────► manifest.json, freshness.json, ...
```

For task context:

```
developer task (natural language)
    │
    ▼
[context_builder.py] + [overrides.py]
    │
    ▼
TaskContextPack  ──► CLI output / MCP tool response ──► Claude Code session
```

---

## Design constraints

- **Deterministic output**: same inputs must produce the same artifact bytes to avoid Git diff noise.
- **Incremental by default**: only regenerate artifacts whose source files changed.
- **Messy repo tolerance**: graceful degradation when docs, tests, or structure are absent.
- **No LLM calls in V1**: generation is template/heuristic-driven; confidence scores reflect this.
- **Human overrides are authoritative**: never auto-overwrite files in `overrides/`.
- **Confidence ∈ [0.0, 1.0]**: always expose how certain the system is about its summaries.

---

## Ticket history

| Ticket | Description | Status |
|---|---|---|
| T1 | Create memory package foundation (`models.py`, all stubs) | Done |
| T2 | Scanner implementation | Planned |
| T3 | Classifier implementation | Planned |
| T4 | Generator + writer implementation | Planned |
| T5 | Metadata management | Planned |
| T6 | Refresh orchestration | Planned |
| T7 | Context builder + overrides | Planned |
| T8 | CLI commands (Layer C) | Planned |
| T9 | MCP tool wiring (Layer C) | Planned |
