"""Thin adapter between the memory subsystem and the graph engine.

Hides graph-specific access (GraphStore, SQLite, edge traversal) from the rest
of the memory code.  All public functions degrade gracefully: when graph.db is
absent or empty they return ``False`` / empty lists, and the caller falls back
to heuristic-only behaviour.

Public API
----------
graph_available(repo_root) -> bool
get_related_files(seed_files, repo_root, max_files=10) -> list[str]
get_related_tests(seed_files, repo_root, max_tests=5) -> list[str]
get_structural_neighbors(seed_files, repo_root, max_neighbors=5) -> list[str]

Graph capabilities reused
-------------------------
- ``GraphStore.get_stats()``            — availability check (node count)
- ``GraphStore.get_impact_radius()``    — BFS at depth-1 for related files/tests
- ``GraphStore.get_nodes_by_file()``    — seed-file node enumeration
- ``GraphStore.get_edges_by_source()``  — outgoing IMPORTS_FROM traversal
- ``GraphStore.get_edges_by_target()``  — incoming TESTED_BY / IMPORTS_FROM traversal
- ``GraphStore.get_node()``             — resolve target qualified name → file_path
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_SUBDIR = ".code-review-graph"
_DB_NAME = "graph.db"

# File-path fragments used to detect test files.
# Uses both Unix and Windows separators for cross-platform correctness.
_TEST_PATH_FRAGMENTS = (
    "/tests/", "\\tests\\",
    "/test/", "\\test\\",
    "/spec/", "\\spec\\",
    "test_",        # prefix: test_auth.py
    "_test.",       # suffix: auth_test.py
    ".spec.",       # JS/TS: auth.spec.ts
    ".test.",       # JS/TS: auth.test.ts
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _db_path(repo_root: Path) -> Path:
    return repo_root / _DB_SUBDIR / _DB_NAME


def _is_test_file(file_path: str) -> bool:
    """Return True if *file_path* looks like a test file (heuristic)."""
    lp = file_path.lower()
    return any(frag in lp for frag in _TEST_PATH_FRAGMENTS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def graph_available(repo_root: str | Path) -> bool:
    """Return True if a populated graph.db exists under *repo_root*.

    Checks both file existence and that the graph contains at least one node.
    Swallows all exceptions — the memory layer must never crash due to a
    missing or corrupt graph.
    """
    p = _db_path(Path(repo_root))
    if not p.exists():
        return False
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            stats = gs.get_stats()
            return stats.total_nodes > 0
    except Exception as exc:
        logger.debug("graph_available: could not open graph.db: %s", exc)
        return False


def get_related_files(
    seed_files: list[str],
    repo_root: str | Path,
    max_files: int = 10,
) -> list[str]:
    """Return non-test source files structurally related to *seed_files*.

    Uses ``get_impact_radius`` at depth 1 — one edge hop away from the seed
    files in the graph.  Excludes seed files themselves and test files.

    Results are sorted for determinism and capped at *max_files*.
    Returns an empty list if graph data is unavailable.
    """
    if not seed_files:
        return []
    p = _db_path(Path(repo_root))
    seed_set = set(seed_files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            result = gs.get_impact_radius(list(seed_files), max_depth=1, max_nodes=300)
            related = sorted(
                f for f in result["impacted_files"]
                if f not in seed_set and not _is_test_file(f)
            )
            return related[:max_files]
    except Exception as exc:
        logger.debug("get_related_files: graph query failed: %s", exc)
        return []


def get_related_tests(
    seed_files: list[str],
    repo_root: str | Path,
    max_tests: int = 5,
) -> list[str]:
    """Return test files structurally related to *seed_files* via graph edges.

    Two strategies are combined and deduplicated:

    1. **TESTED_BY edges** — incoming edges on seed-file nodes where
       ``kind == "TESTED_BY"``.  These link a test node to the code it tests.
       ``edge.file_path`` is the test file.

    2. **Impact-radius test files** — test files reachable within 1 hop of
       the seed files in the full graph, including nodes where ``is_test`` is
       set.

    Results are sorted for determinism and capped at *max_tests*.
    Returns an empty list if graph data is unavailable.
    """
    if not seed_files:
        return []
    p = _db_path(Path(repo_root))
    seed_set = set(seed_files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            test_files: set[str] = set()

            # Strategy 1: TESTED_BY edges.
            # Edge convention: source=test_node, target=tested_node, kind=TESTED_BY.
            # file_path on the edge is the file containing the test node.
            for fp in seed_files:
                for node in gs.get_nodes_by_file(fp):
                    for edge in gs.get_edges_by_target(node.qualified_name):
                        if edge.kind == "TESTED_BY" and edge.file_path not in seed_set:
                            test_files.add(edge.file_path)

            # Strategy 2: impact-radius test files (depth 1).
            result = gs.get_impact_radius(list(seed_files), max_depth=1, max_nodes=300)
            for f in result["impacted_files"]:
                if f not in seed_set and _is_test_file(f):
                    test_files.add(f)
            for node in result["impacted_nodes"]:
                if node.is_test:
                    test_files.add(node.file_path)

            return sorted(test_files)[:max_tests]
    except Exception as exc:
        logger.debug("get_related_tests: graph query failed: %s", exc)
        return []


def get_structural_neighbors(
    seed_files: list[str],
    repo_root: str | Path,
    max_neighbors: int = 5,
) -> list[str]:
    """Return files that import from or are imported by *seed_files*.

    Walks ``IMPORTS_FROM`` edges in both directions:

    * **Outgoing** — this file imports from *neighbor*.  The target node's
      ``file_path`` is the neighbor.
    * **Incoming** — *neighbor* imports from this file.  ``edge.file_path``
      on an incoming edge is the importing file (the neighbor).

    Excludes seed files, test files, and duplicates.
    Results are sorted for determinism and capped at *max_neighbors*.
    Returns an empty list if graph data is unavailable.
    """
    if not seed_files:
        return []
    p = _db_path(Path(repo_root))
    seed_set = set(seed_files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            neighbor_files: set[str] = set()

            for fp in seed_files:
                for node in gs.get_nodes_by_file(fp):
                    # Outgoing IMPORTS_FROM: seed file imports from neighbor.
                    for edge in gs.get_edges_by_source(node.qualified_name):
                        if edge.kind == "IMPORTS_FROM":
                            target_node = gs.get_node(edge.target_qualified)
                            if target_node and target_node.file_path not in seed_set:
                                neighbor_files.add(target_node.file_path)

                    # Incoming IMPORTS_FROM: neighbor imports from seed file.
                    # edge.file_path is where the import statement lives (the neighbor).
                    for edge in gs.get_edges_by_target(node.qualified_name):
                        if edge.kind == "IMPORTS_FROM" and edge.file_path not in seed_set:
                            neighbor_files.add(edge.file_path)

            neighbors = sorted(f for f in neighbor_files if not _is_test_file(f))
            return neighbors[:max_neighbors]
    except Exception as exc:
        logger.debug("get_structural_neighbors: graph query failed: %s", exc)
        return []
