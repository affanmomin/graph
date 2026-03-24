# Product Context: Repo Memory for Claude Code

> This document captures the Founder Memo, PRD, RFC, and TRD for the product direction.
> The current codebase (`code-review-graph`) is the **code intelligence engine** (Layer A).
> The work ahead is building the **memory layer** (Layer B) and **agent interface** (Layer C) on top of it.

---

## 1. Founder Memo

### One-line Thesis
Stop re-explaining your repo to AI every session.

### What We Are Building
A Git-native memory layer for Claude Code that continuously turns a repository into durable, agent-readable and human-readable project memory.

Developers start fresh AI coding sessions with better context, lower token waste, less wandering, and stronger knowledge transfer across teammates and machines.

### Why This Matters Now
AI coding is no longer a novelty workflow. Every fresh session still pays the same tax:
- rediscovering the codebase
- re-reading architecture
- re-finding the right files
- re-learning conventions
- re-learning what changed recently

That cost compounds across tasks, sessions, teammates, and machines. Documentation is inconsistently maintained, often stale, and rarely structured for AI systems to use well.

**Core insight:** The future problem is not "AI needs more context." The real problem is "AI needs the right durable memory."

### What This Is Not
- a generic documentation generator
- a graph visualization product
- a team dashboard
- a broad enterprise knowledge platform
- a general MCP search tool

**The graph is the engine. The memory is the product.**

### Product Wedge → Retention → Expansion
- **Wedge**: fresh-session task intelligence for Claude Code
- **Retention**: durable repo memory shared through Git
- **Expansion**: KT acceleration, handoff between humans and agents, safety boundaries, change intelligence, eventually team memory

### Differentiation
Other tools focus on code graph, indexing, search, and impact radius. We focus on:
- durable project memory
- task-aware context packs
- Git-shared agent knowledge
- human-correctable memory
- onboarding and KT value
- survival of repo understanding across sessions and machines

---

## 2. Product Requirements Document (PRD)

### Product Goal
Create a system that gives every repository a durable memory layer that:
- improves fresh AI session quality
- persists project understanding via Git
- updates automatically with code changes
- supports human correction and guidance
- helps with both task execution and KT

### Target User
A developer in a team using Claude Code on a real codebase.

### User Pain Points
- fresh AI sessions are slow and context-poor
- AI often looks in the wrong places first
- repeated repo explanation wastes time and tokens
- project knowledge is fragmented or missing
- onboarding relies on humans manually giving KT
- recent changes and dangerous boundaries are hard to surface quickly

### V1 Features

**1. Initial Memory Bootstrap**
Scans a repository and generates `.agent-memory/` with:
- repo summary, architecture summary
- feature summaries, module summaries
- task playbooks, recent changes summary
- hotspot summary, rules and boundaries, metadata

**2. Task Context Preparation**
Accepts a natural-language task and returns a focused context pack:
- relevant features, modules, files, tests
- recent related changes
- applicable rules and warnings
- a concise task summary for Claude Code

**3. Incremental Refresh**
Detects repo changes and updates only impacted memory artifacts.

**4. Human Overrides**
Supports human annotations: always-include files, never-edit paths, notes, task hints.

**5. Metadata and Trust Signals**
Tracks freshness, confidence, and source traceability per artifact.

### Non-Goals for V1
- hosted SaaS
- enterprise RBAC
- cross-repo org graph
- Slack/Jira/Linear integrations
- fancy graph UI
- support for all IDEs and agents

### Core Product Principles
- Git-native
- agent-first, human-readable
- automatic by default
- incremental refresh first
- compatible with messy repos
- concise and grounded, not verbose

### MVP Acceptance Criteria
A developer can:
1. install the tool
2. generate `.agent-memory/`
3. ask for context on a real task
4. receive a noticeably useful task pack
5. commit memory artifacts to Git
6. see memory update after repo changes

---

## 3. Technical RFC (RFC-001)

### Architecture

**Layer A: Code Intelligence Engine** ← *this is the current `code-review-graph` codebase*
- parse source code, build graph/index
- map symbols, imports, references, dependencies
- compute impact radius, detect changed files
- support incremental refresh

**Layer B: Memory Engine** ← *to be built*
- classify repo areas into features/modules
- generate memory artifacts and task playbooks
- merge code structure + docs + Git change signals
- maintain confidence/freshness/source metadata
- merge human overrides

**Layer C: Agent Interface** ← *to be built*
- expose commands/tools through CLI and MCP
- serve task context packs
- explain repo areas, summarize changes
- enforce rule-aware responses

### Repository Artifact Layout
```
.agent-memory/
  repo.md
  architecture.md
  features/
  modules/
  tasks/
  changes/
  rules/
  overrides/
  metadata/
```

### Commands
| Command | Description |
|---|---|
| `init-memory` | Initialize memory and generate first artifacts |
| `refresh-memory` | Refresh memory artifacts (incremental or full) |
| `prepare-context <task>` | Map a task to relevant repo memory, return context bundle |
| `explain-area <feature\|module\|path>` | Explain a repo area with grounded context |
| `what-changed <feature\|module\|path>` | Show recent meaningful changes and likely impacts |
| `annotate-memory` | Add or edit human override guidance |

### Memory Artifact Types
- **Repo Summary** — high-level overview
- **Architecture Summary** — system boundaries, flows, risky areas
- **Feature Summaries** — purpose, entry points, key files, conventions
- **Module Summaries** — role, dependencies, risks, tests
- **Task Playbooks** — task-grounded instructions and context patterns
- **Change Summaries** — recent relevant changes and hotspots
- **Rules** — conventions and safe boundaries
- **Overrides** — human corrections and constraints

### Incremental Refresh Strategy
1. detect changed files
2. map changed files → impacted graph nodes/areas
3. map impacted areas → memory artifacts
4. regenerate only impacted artifacts
5. refresh freshness/confidence/source metadata

### Key Design Decisions
- **Commit memory to Git**: portable, visible in diffs, team-shared, lightweight handoff. Only durable lightweight artifacts, not heavy index state.
- **Use existing graph engine**: faster V1, avoids reinventing parser/index, focuses engineering on differentiation.

### Open Questions
- exact heuristics for feature grouping
- how aggressively to generate task playbooks automatically
- whether to use LLMs at generation time locally or through configured model backends
- how to handle very large monorepos in V1

---

## 4. Technical Requirements Document (TRD)

### System Components

| Component | Responsibilities |
|---|---|
| CLI Layer | init, refresh, explain, prepare-context, annotate |
| MCP/Agent Interface | expose task prep, area explanation, change summaries to Claude Code |
| Repo Scanner | detect structure, languages, frameworks, features, modules |
| Graph Adapter | interface with graph engine, query symbols/deps/impact |
| Memory Classifier | infer feature/module groupings, architecture zones, task archetypes |
| Memory Generator | generate markdown summaries, task playbooks, change summaries, metadata |
| Refresh Orchestrator | detect changes, compute impacted artifacts, queue regeneration |
| Override Manager | load override files, merge human guidance into outputs |

### Command Requirements
- **`init-memory`**: validate repo root, init config, init code intelligence engine, generate initial artifacts, create `.agent-memory/`
- **`refresh-memory`**: incremental by default, support full refresh, support area-targeted refresh
- **`prepare-context`**: output relevant features, modules, files, tests, recent changes, rules/warnings, summary
- **`explain-area`**: explain a feature, module, or path with grounded context
- **`what-changed`**: retrieve recent meaningful changes affecting an area
- **`annotate-memory`**: write human override files

### Artifact Requirements
- Markdown artifacts: concise, stable format, suitable for Git commit
- Metadata artifacts: manifest, freshness, confidence, sources
- Override files: `always_include`, `never_edit`, `notes`, `task_hints`

### Data Handling
- **Local heavy state**: graph/index DB, parser caches, vector caches — NOT committed to Git
- **Durable state**: `.agent-memory/` markdown + YAML + JSON — committed to Git
- **Traceability**: every artifact preserves source metadata linking back to source files

### Non-Functional Requirements
| NFR | Requirement |
|---|---|
| Performance | Incremental refresh substantially faster than full refresh |
| Stability | Artifact generation deterministic enough to avoid excessive Git diff churn |
| Extensibility | Graph layer abstracted to allow swapping/upgrading backend |
| Usability | Easy for a developer to install and operate locally |
| Messy Repo Tolerance | Graceful degradation on imperfect repos |
| Trustworthiness | Freshness/confidence signals exposed so memory is not falsely treated as always accurate |

### V1 Constraints
- Claude Code-first
- Local-first
- Git as the durable sharing mechanism
- No heavy setup complexity
- No cloud control plane required

### Acceptance Criteria
The system is technically ready for V1 when:
1. it can initialize memory in a real repo
2. generated artifacts are committed cleanly
3. task context preparation is meaningfully useful
4. incremental refresh works for normal repo changes
5. human overrides are respected
