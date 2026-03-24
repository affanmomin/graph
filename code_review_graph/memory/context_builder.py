"""Task-aware context pack builder.

This is the killer feature of the repo memory system.

Given a natural-language task description, the context builder assembles a
focused ``TaskContextPack`` containing exactly the repo context an AI agent
needs to start working — without requiring the developer to manually point it
at files or explain the codebase.

The context pack is the primary output of the ``prepare-context`` CLI command
and the corresponding MCP tool. It is designed to be injected into a Claude
Code session at startup.

Context selection strategy (planned):
1. Tokenise / keyword-extract the task description.
2. Match task tokens against feature and module names and summaries.
3. Use graph proximity (BFS from matched nodes) to expand to related files.
4. Score and rank candidates by relevance to the task.
5. Apply override hints (always_include, never_edit) from ``overrides.py``.
6. Trim to a target token budget (configurable, default ~4 k tokens of paths).
7. Attach applicable warnings from ``rules/safe-boundaries.md``.
8. Produce a one-paragraph ``summary`` framing the task for Claude Code.

Planned responsibilities:
- accept a natural-language task string
- return a populated ``TaskContextPack``
- handle tasks that match no known feature gracefully (low-confidence fallback)
- support MCP and CLI as equal consumers (no logic duplication)

TODO(context_builder): implement ``build_context(task, graph, manifest, overrides)``
TODO(context_builder): implement keyword extraction and feature matching
TODO(context_builder): implement graph-proximity file expansion
TODO(context_builder): implement warning injection from safe-boundaries
TODO(context_builder): expose as MCP tool in tools.py (later ticket)
"""

from __future__ import annotations

# TODO(context_builder): imports will be added when implementation begins
