"""Feature and module classifier for the memory subsystem.

Responsible for grouping the raw graph nodes (files, functions, classes) into
higher-level concepts — features and modules — that are meaningful to humans
and useful to AI agents.

Classification is deliberately heuristic: it must work on messy real-world
repos that lack clean package boundaries or inline documentation.

Planned responsibilities:
- infer feature groupings from directory structure, naming, and graph proximity
- infer module groupings from package structure and import relationships
- identify architectural zone boundaries (e.g. API layer, data layer, CLI)
- produce ``FeatureMemory`` and ``ModuleMemory`` model instances
- attach confidence scores reflecting how certain the grouping is

Inputs (when implemented):
- ``GraphStore`` instance (read-only) for symbol relationships
- ``RepoScan`` result from ``scanner.py``
- optional human overrides from ``overrides.py``

Outputs (when implemented):
- ``list[FeatureMemory]``
- ``list[ModuleMemory]``

TODO(classifier): implement ``classify_features(graph, scan)`` -> list[FeatureMemory]
TODO(classifier): implement ``classify_modules(graph, scan)`` -> list[ModuleMemory]
TODO(classifier): integrate override hints that force/block groupings
TODO(classifier): add confidence scoring heuristics
"""

from __future__ import annotations

# TODO(classifier): imports will be added when implementation begins
