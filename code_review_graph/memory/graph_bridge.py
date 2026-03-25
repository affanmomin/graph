"""Thin adapter between the memory subsystem and the graph engine.

Hides graph-specific access (GraphStore, SQLite, edge traversal) from the rest
of the memory code.  All public functions degrade gracefully: when graph.db is
absent or empty they return ``False`` / empty lists / ``None``, and the caller
falls back to heuristic-only behaviour.

Public API
----------
graph_available(repo_root) -> bool
get_related_files(seed_files, repo_root, max_files=10) -> list[str]
get_related_tests(seed_files, repo_root, max_tests=5) -> list[str]
get_structural_neighbors(seed_files, repo_root, max_neighbors=5) -> list[str]
get_task_symbol_files(task, repo_root, max_files=5) -> list[str]
get_explain_context(seed_files, repo_root, ...) -> ExplainGraphContext | None
get_change_impact(changed_files, repo_root, ...) -> ChangeImpactContext | None
get_graph_expanded_files(changed_files, repo_root, max_expansion=20) -> list[str]
ClassifierGraphSignals (dataclass)
get_all_classifier_signals(groups, repo_root) -> dict[str, ClassifierGraphSignals]

Graph capabilities reused
-------------------------
- ``GraphStore.get_stats()``            — availability check (node count)
- ``GraphStore.get_impact_radius()``    — BFS at depth-1 for related files/tests
- ``GraphStore.get_nodes_by_file()``    — seed-file node enumeration
- ``GraphStore.get_edges_by_source()``  — outgoing edge traversal (fan-out)
- ``GraphStore.get_edges_by_target()``  — incoming edge traversal (fan-in / TESTED_BY)
- ``GraphStore.get_node()``             — resolve target qualified name → file_path
- ``GraphStore.search_nodes()``         — keyword search for symbol-level task routing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Symbol-level task routing
# ---------------------------------------------------------------------------


def get_task_symbol_files(
    task: str,
    repo_root: str | Path,
    max_files: int = 5,
) -> list[str]:
    """Return source files containing symbols that match the task description.

    Uses ``GraphStore.search_nodes()`` — which already performs multi-word,
    case-insensitive keyword search across node names and qualified names — to
    find nodes whose names overlap with the task string.  The containing
    source files (non-test) are returned, sorted for determinism.

    This enables **symbol-level routing**: if the task mentions ``verify_token``
    or ``InvoiceExporter``, the files that define those symbols are surfaced as
    extra seeds even when they would not score above the heuristic threshold on
    feature/module names alone.

    Results are sorted for determinism and capped at *max_files*.
    Returns an empty list when graph data is unavailable, task is blank, or
    any error occurs.
    """
    if not task.strip():
        return []
    p = _db_path(Path(repo_root))
    if not p.exists():
        return []
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return []
            nodes = gs.search_nodes(task, limit=20)
            seen: set[str] = set()
            files: list[str] = []
            for node in nodes:
                fp = node.file_path
                if fp and fp not in seen and not _is_test_file(fp):
                    seen.add(fp)
                    files.append(fp)
            return sorted(files)[:max_files]
    except Exception as exc:
        logger.debug("get_task_symbol_files: graph query failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Explain context
# ---------------------------------------------------------------------------


@dataclass
class ExplainGraphContext:
    """Graph-backed structural context for ``memory explain``.

    Attributes:
        related_files:        Non-test source files reachable within 1 BFS hop.
        related_tests:        Test files linked via TESTED_BY edges or 1-hop impact.
        structural_neighbors: Files connected via IMPORTS_FROM in either direction.
        fan_in_count:         Total number of unique external files that import or
                              call into the seed area (coupling pressure indicator).
        fan_in_sample:        Up to *max_fan_sample* representative importer paths.
        fan_out_sample:       Up to *max_fan_sample* paths this area imports from.
    """

    related_files: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    structural_neighbors: list[str] = field(default_factory=list)
    fan_in_count: int = 0
    fan_in_sample: list[str] = field(default_factory=list)
    fan_out_sample: list[str] = field(default_factory=list)


def _file_from_qualified(qualified_name: str) -> str | None:
    """Extract the file_path portion of a qualified node name.

    Qualified names follow the pattern ``file_path::symbol`` for non-File
    nodes, or just ``file_path`` for File nodes.  Returns ``None`` when the
    name does not contain a recognisable file path component (e.g. bare
    unqualified call targets).
    """
    if "::" in qualified_name:
        return qualified_name.split("::")[0]
    # A plain file path (File node) or an unresolved bare name.
    # Accept it only if it looks like a path (contains a separator or extension).
    if "/" in qualified_name or "\\" in qualified_name or "." in qualified_name:
        return qualified_name
    return None


def get_explain_context(
    seed_files: list[str],
    repo_root: str | Path,
    max_related: int = 5,
    max_tests: int = 5,
    max_neighbors: int = 4,
    max_fan_sample: int = 3,
) -> ExplainGraphContext | None:
    """Return graph-backed structural context for *seed_files*.

    Aggregates data for ``memory explain`` from a single graph open:

    * 1-hop BFS via ``get_impact_radius`` for related source files and tests.
    * TESTED_BY edges for explicitly linked test files.
    * IMPORTS_FROM edges (both directions) for structural neighbours.
    * Incoming CALLS + IMPORTS_FROM edge counts for fan-in coupling notes.
    * Outgoing IMPORTS_FROM targets for fan-out dependency notes.

    Returns ``None`` when the graph is unavailable or any error occurs —
    callers must always handle the ``None`` case and fall back gracefully.
    """
    if not seed_files:
        return None
    p = _db_path(Path(repo_root))
    if not p.exists():
        return None

    seed_set = set(seed_files)
    _INCOMING_KINDS = frozenset({"CALLS", "IMPORTS_FROM"})

    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return None

            # --- 1-hop BFS: related source files and test files ---------------
            radius = gs.get_impact_radius(list(seed_files), max_depth=1, max_nodes=300)
            related_files = sorted(
                f for f in radius["impacted_files"]
                if f not in seed_set and not _is_test_file(f)
            )[:max_related]
            related_tests: set[str] = set(
                f for f in radius["impacted_files"]
                if f not in seed_set and _is_test_file(f)
            )
            for node in radius["impacted_nodes"]:
                if node.is_test:
                    related_tests.add(node.file_path)

            # --- Edge traversal: neighbors, fan-in, fan-out -------------------
            neighbor_files: set[str] = set()
            fan_in_files: set[str] = set()
            fan_out_files: set[str] = set()

            for fp in seed_files:
                for node in gs.get_nodes_by_file(fp):
                    # TESTED_BY: incoming edges from test nodes to this node.
                    for edge in gs.get_edges_by_target(node.qualified_name):
                        if edge.kind == "TESTED_BY" and edge.file_path not in seed_set:
                            related_tests.add(edge.file_path)
                        # Fan-in: other files that call or import from this area.
                        if edge.kind in _INCOMING_KINDS and edge.file_path not in seed_set:
                            fan_in_files.add(edge.file_path)
                            if edge.kind == "IMPORTS_FROM":
                                neighbor_files.add(edge.file_path)

                    # Outgoing edges: what this area depends on / imports.
                    for edge in gs.get_edges_by_source(node.qualified_name):
                        if edge.kind == "IMPORTS_FROM":
                            target_fp = _file_from_qualified(edge.target_qualified)
                            if target_fp and target_fp not in seed_set:
                                fan_out_files.add(target_fp)
                                neighbor_files.add(target_fp)

            structural_neighbors = sorted(
                f for f in neighbor_files if not _is_test_file(f)
            )[:max_neighbors]
            fan_in_sample = sorted(
                f for f in fan_in_files if not _is_test_file(f)
            )[:max_fan_sample]
            fan_out_sample = sorted(
                f for f in fan_out_files if not _is_test_file(f)
            )[:max_fan_sample]

            return ExplainGraphContext(
                related_files=related_files,
                related_tests=sorted(related_tests)[:max_tests],
                structural_neighbors=structural_neighbors,
                fan_in_count=len(fan_in_files),
                fan_in_sample=fan_in_sample,
                fan_out_sample=fan_out_sample,
            )
    except Exception as exc:
        logger.debug("get_explain_context: graph query failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Change impact context
# ---------------------------------------------------------------------------


@dataclass
class ChangeImpactContext:
    """Graph-backed structural impact analysis for ``memory changed``.

    Attributes:
        impacted_files:  Non-test source files structurally reachable from the
                         changed files within one BFS hop.  Excludes the changed
                         files themselves.
        impacted_tests:  Test files reachable from the changed files — candidates
                         for re-running after the changes.
        total_impacted:  Total count of impacted graph nodes (before capping),
                         useful as a coupling-pressure indicator.
        truncated:       True when the impact radius hit the node cap and further
                         neighbours were skipped.
    """

    impacted_files: list[str] = field(default_factory=list)
    impacted_tests: list[str] = field(default_factory=list)
    total_impacted: int = 0
    truncated: bool = False


def get_change_impact(
    changed_files: list[str],
    repo_root: str | Path,
    max_files: int = 6,
    max_tests: int = 4,
) -> ChangeImpactContext | None:
    """Return graph-backed structural impact for *changed_files*.

    Uses ``GraphStore.get_impact_radius`` at depth 1 to find files and tests
    that are structurally reachable from the changed files — the blast radius
    of the change.

    Separates results into non-test source files (*impacted_files*) and test
    files (*impacted_tests*).  Results are sorted for determinism and capped
    at *max_files* / *max_tests*.

    Returns ``None`` when graph data is unavailable or any error occurs.
    """
    if not changed_files:
        return None
    p = _db_path(Path(repo_root))
    if not p.exists():
        return None

    seed_set = set(changed_files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return None

            radius = gs.get_impact_radius(list(changed_files), max_depth=1, max_nodes=300)

            impacted_files = sorted(
                f for f in radius["impacted_files"]
                if f not in seed_set and not _is_test_file(f)
            )[:max_files]

            impacted_tests = sorted(
                f for f in radius["impacted_files"]
                if f not in seed_set and _is_test_file(f)
            )
            # Also surface test nodes flagged via is_test on impacted_nodes
            for node in radius["impacted_nodes"]:
                if node.is_test and node.file_path not in seed_set:
                    impacted_tests.append(node.file_path)
            impacted_tests = sorted(set(impacted_tests))[:max_tests]

            return ChangeImpactContext(
                impacted_files=impacted_files,
                impacted_tests=impacted_tests,
                total_impacted=radius["total_impacted"],
                truncated=radius["truncated"],
            )
    except Exception as exc:
        logger.debug("get_change_impact: graph query failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Refresh planning expansion
# ---------------------------------------------------------------------------


def get_graph_expanded_files(
    changed_files: list[str],
    repo_root: str | Path,
    max_expansion: int = 20,
) -> list[str]:
    """Return non-test source files structurally reachable from *changed_files*.

    Used by refresh planning to expand artifact impact beyond direct file
    membership.  One BFS hop only — keeps refresh bounded and avoids
    cascading refreshes across the entire repo.

    Excludes the seed files themselves and test files.  Capped at
    *max_expansion* to keep the refresh plan bounded.

    Returns an empty list when graph data is unavailable or any error occurs.
    """
    if not changed_files:
        return []
    p = _db_path(Path(repo_root))
    seed_set = set(changed_files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return []
            result = gs.get_impact_radius(list(changed_files), max_depth=1, max_nodes=300)
            related = sorted(
                f for f in result["impacted_files"]
                if f not in seed_set and not _is_test_file(f)
            )
            return related[:max_expansion]
    except Exception as exc:
        logger.debug("get_graph_expanded_files: graph query failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Classifier signals
# ---------------------------------------------------------------------------


@dataclass
class ClassifierGraphSignals:
    """Graph signals for a single feature/module classifier group.

    Used by the classifier to refine confidence scores, merge graph-found test
    associations, and populate module dependency edges.  All fields default to
    empty/zero so callers can use the dataclass even when the graph has no
    data for a particular group.

    Attributes:
        internal_edge_count:      Number of unique directed file-pairs connected
                                  by IMPORTS_FROM or CALLS edges where both the
                                  source and target file belong to this group.
                                  Higher → files are genuinely coupled → raises
                                  confidence in the grouping.
        external_dep_files:       Source files OUTSIDE this group that files in
                                  this group import from (fan-out dependencies).
                                  Used to populate ``ModuleMemory.dependencies``.
        external_dependent_files: Source files OUTSIDE this group that import
                                  FROM files in this group (fan-in dependents).
                                  Used to populate ``ModuleMemory.dependents``.
        test_files:               Test files structurally linked to this group's
                                  source files via TESTED_BY edges.  More precise
                                  than stem-name heuristic matching.
    """

    internal_edge_count: int = 0
    external_dep_files: list[str] = field(default_factory=list)
    external_dependent_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)

    def confidence_delta(self, group_size: int) -> float:
        """Return a confidence adjustment in [-0.05, +0.08] based on connectivity.

        Well-connected groups receive a boost; groups with zero internal edges
        get a small penalty (keyword match with no structural confirmation).
        Single-file groups are not adjusted — internal edges are impossible.
        """
        if group_size <= 1:
            return 0.0
        if self.internal_edge_count >= group_size:
            return 0.08   # every file has ≥1 internal connection → confirm grouping
        if self.internal_edge_count > 0:
            return 0.04   # partial connectivity → weak structural confirmation
        return -0.05       # no internal edges → keyword match only, lower confidence


def get_all_classifier_signals(
    groups: dict[str, list[str]],
    repo_root: str | Path,
) -> dict[str, ClassifierGraphSignals]:
    """Compute graph signals for all classifier groups in a single DB pass.

    Opens ``graph.db`` once and processes every group, returning a mapping of
    group name → :class:`ClassifierGraphSignals`.  Returns an empty dict when
    the graph is unavailable or any error occurs — the classifier falls back to
    filesystem heuristics automatically.

    Graph signals computed per group:

    * **Internal edges** — IMPORTS_FROM or CALLS between files within the group.
      Counted as unique directed file-pair (source, target) to avoid per-node
      double-counting.
    * **External dep files** — files outside the group that the group imports
      from (via outgoing IMPORTS_FROM edges only, for stability).
    * **External dependent files** — files outside the group that import from
      the group (incoming IMPORTS_FROM edges; source identified via
      ``edge.file_path``).
    * **Test files** — test files linked to group source files via incoming
      TESTED_BY edges (``edge.file_path`` is the test file).

    Args:
        groups:    Mapping of group name → list of repo-relative file paths.
        repo_root: Repository root containing ``.code-review-graph/graph.db``.

    Returns:
        Dict mapping group name → :class:`ClassifierGraphSignals`.  Missing
        keys indicate no graph data was found for that group.
    """
    if not groups:
        return {}
    p = _db_path(Path(repo_root))
    if not p.exists():
        return {}

    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return {}

            # Build reverse-lookup: file_path → group name (for target resolution)
            file_to_group: dict[str, str] = {}
            for name, files in groups.items():
                for fp in files:
                    file_to_group[fp] = name

            # Initialise an empty signals object for every group
            signals: dict[str, ClassifierGraphSignals] = {
                name: ClassifierGraphSignals() for name in groups
            }

            for group_name, group_files in groups.items():
                group_set = set(group_files)
                sig = signals[group_name]

                # Track unique file-pair edges for internal_edge_count
                internal_pairs: set[tuple[str, str]] = set()
                ext_deps: set[str] = set()
                ext_dependents: set[str] = set()
                test_files: set[str] = set()

                for fp in group_files:
                    for node in gs.get_nodes_by_file(fp):
                        qn = node.qualified_name

                        # --- Outgoing edges ---
                        for edge in gs.get_edges_by_source(qn):
                            if edge.kind in ("IMPORTS_FROM", "CALLS"):
                                target_fp = _file_from_qualified(edge.target_qualified)
                                if target_fp is None:
                                    continue
                                if target_fp in group_set:
                                    # Internal edge: both ends inside this group
                                    internal_pairs.add((fp, target_fp))
                                elif edge.kind == "IMPORTS_FROM" and not _is_test_file(target_fp):
                                    # External dependency (imports only, not calls)
                                    ext_deps.add(target_fp)

                        # --- Incoming edges ---
                        for edge in gs.get_edges_by_target(qn):
                            if edge.kind == "TESTED_BY":
                                # edge.file_path = file containing the test node
                                test_files.add(edge.file_path)
                            elif edge.kind == "IMPORTS_FROM":
                                src_fp = edge.file_path
                                if src_fp not in group_set and not _is_test_file(src_fp):
                                    ext_dependents.add(src_fp)

                sig.internal_edge_count = len(internal_pairs)
                sig.external_dep_files = sorted(ext_deps)
                sig.external_dependent_files = sorted(ext_dependents)
                sig.test_files = sorted(test_files)

            return signals
    except Exception as exc:
        logger.debug("get_all_classifier_signals: failed: %s", exc)
        return {}
