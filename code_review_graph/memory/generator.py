"""Memory artifact generator for the memory subsystem.

Responsible for turning structured ``FeatureMemory``, ``ModuleMemory``, and
related data objects into human- and agent-readable markdown content.

The generator produces the text content of each ``.agent-memory/`` artifact.
It does NOT write files to disk — that is the responsibility of ``writer.py``.

Design constraints:
- output must be concise (optimised for agent context windows, not humans skimming)
- output must be stable and deterministic to avoid noisy Git diffs
- output must degrade gracefully when source signals are weak or absent
- no LLM calls in V1 — generation is template/heuristic-driven

Planned responsibilities:
- generate ``repo.md`` content from scan + graph stats
- generate ``architecture.md`` content from module/layer analysis
- generate ``features/<slug>.md`` content from ``FeatureMemory``
- generate ``modules/<slug>.md`` content from ``ModuleMemory``
- generate ``changes/recent.md`` content from git log signals
- generate ``changes/hotspots.md`` content from churn analysis
- generate ``rules/conventions.md`` content from config/style signals
- generate ``rules/safe-boundaries.md`` from override hints

TODO(generator): implement ``generate_repo_summary(scan, graph)`` -> str
TODO(generator): implement ``generate_feature_doc(feature: FeatureMemory)`` -> str
TODO(generator): implement ``generate_module_doc(module: ModuleMemory)`` -> str
TODO(generator): implement ``generate_architecture_doc(modules, scan)`` -> str
TODO(generator): implement ``generate_recent_changes(repo_root)`` -> str
"""

from __future__ import annotations

# TODO(generator): imports will be added when implementation begins
