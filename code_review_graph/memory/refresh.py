"""Incremental memory refresh orchestrator.

Responsible for detecting which memory artifacts are affected by recent repo
changes and triggering regeneration of only those artifacts.

Full regeneration is expensive and produces noisy Git diffs. The refresh
orchestrator keeps memory current by doing the minimum work needed.

It integrates with the existing ``incremental.py`` change-detection
infrastructure (Layer A) to learn which files changed, then uses the graph
to determine which features/modules/artifacts are impacted.

Planned responsibilities:
- accept a set of changed file paths (from git diff or file watcher)
- use the graph's impact-radius queries to find affected nodes
- map affected nodes to affected memory artifacts via the manifest
- trigger regeneration of only affected artifacts
- update freshness and stale flags in metadata
- support a ``--full`` fallback mode that regenerates everything

Refresh modes:
- incremental (default): only impacted artifacts
- full: all artifacts (used after major structural changes)
- targeted: single feature or module by name

TODO(refresh): implement ``plan_refresh(changed_files, graph, manifest)`` -> RefreshPlan
TODO(refresh): implement ``execute_refresh(plan, graph, scan, overrides)``
TODO(refresh): wire into CLI ``refresh-memory`` command (later ticket)
TODO(refresh): wire into file watcher event loop (later ticket)
"""

from __future__ import annotations

# TODO(refresh): imports will be added when implementation begins
