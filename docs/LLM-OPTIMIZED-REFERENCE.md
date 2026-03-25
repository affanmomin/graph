# LLM-OPTIMIZED REFERENCE — code-review-graph v1.8.4

Claude Code: Read ONLY the exact `<section>` you need. Never load the whole file.

<section name="usage">
Install: pip install code-review-graph
Setup: code-review-graph install && code-review-graph build && code-review-graph memory init
Load memory in CLAUDE.md: @.agent-memory/CLAUDE.md
Commit: git add .agent-memory/ && git commit -m "chore: add repo memory"
Daily use: code-review-graph memory prepare-context "<task>" before starting work.
Auto-refresh: memory refreshes automatically on code-review-graph update.
</section>

<section name="memory">
Primary workflows:
1. prepare-context "<task>" — focused context pack before starting a task (features, files, tests, summary)
2. explain <feature|module|path> — stored memory for an area (reads .agent-memory/)
3. changed <file|dir> — graph-expanded impact for changed files (callers, dependents, tests)
4. refresh — incremental refresh of .agent-memory/ after commits
5. annotate — open overrides/global.yaml to add human corrections

Memory artifacts committed to Git: .agent-memory/
  CLAUDE.md              — session bootstrap (load via @.agent-memory/CLAUDE.md in root CLAUDE.md)
  repo.md                — overview
  architecture.md        — module map
  features/<slug>.md     — per feature
  modules/<slug>.md      — per module
  rules/conventions.md   — coding conventions
  rules/safe-boundaries.md — never-edit paths
  overrides/global.yaml  — human corrections (never auto-overwritten)
  metadata/*.json        — freshness, confidence, sources

Graph database (.code-review-graph/graph.db) is local-only — gitignored, rebuilt from source.
</section>

<section name="review-delta">
Always call get_impact_radius on changed files first.
Then get_review_context (depth=2).
Generate review using ONLY changed nodes + 2-hop neighbors.
Target: <800 tokens total context.
</section>

<section name="review-pr">
Fetch PR diff -> get_impact_radius -> get_review_context -> structured review with blast-radius table.
Never include full files unless explicitly asked.
</section>

<section name="commands">
Memory CLI: memory init | refresh [--full] | prepare-context "<task>" | explain <target> | changed <target> | annotate
Graph CLI: build | update | status | watch | visualize | serve | install

Memory MCP tools: memory_init, memory_prepare_context, memory_explain, memory_changed, memory_annotate
Graph MCP tools: build_or_update_graph_tool, get_impact_radius_tool, query_graph_tool, get_review_context_tool, semantic_search_nodes_tool, embed_graph_tool, list_graph_stats_tool, get_docs_section_tool, find_large_functions_tool

Skills: build-graph, review-delta, review-pr
</section>

<section name="legal">
MIT license. 100% local. No telemetry. Graph DB: .code-review-graph/graph.db (gitignored). Memory: .agent-memory/ (committed to Git).
</section>

<section name="watch">
Run: code-review-graph watch (auto-updates graph on file save via watchdog, also refreshes memory)
Or use PostToolUse (Write|Edit|Bash) hooks for automatic background updates.
</section>

<section name="embeddings">
Optional: pip install code-review-graph[embeddings]
Then call embed_graph_tool to compute vectors.
semantic_search_nodes_tool auto-uses vectors when available, falls back to keyword.
Model: all-MiniLM-L6-v2 (384-dim, fast).
</section>

<section name="languages">
Supported: Python, TypeScript, JavaScript, Vue, Go, Rust, Java, C#, Ruby, Kotlin, Swift, PHP, Solidity, C/C++
Parser: Tree-sitter via tree-sitter-language-pack
</section>

<section name="troubleshooting">
DB lock: SQLite WAL mode, auto-recovers. Only one build at a time.
Large repos: First build 30-60s. Incremental <2s. Add patterns to .code-review-graphignore.
Stale graph: Run code-review-graph build (full rebuild).
Missing nodes: Check language support + ignore patterns. Use full_rebuild=True on build_or_update_graph_tool.
Memory stale: Run code-review-graph memory refresh --full.
Graph absent: memory commands work in heuristic-only mode but run code-review-graph build for richer context.
</section>

**Instruction to Claude Code (always follow):**
When user asks anything about "code-review-graph", "how to use", "commands", "memory", "prepare-context", etc.:
1. Call get_docs_section_tool with the exact section name (memory, commands, usage, etc.).
2. Use ONLY that content + current graph state.
3. Never include full docs or source code in your reasoning.
This guarantees 90%+ token savings.
