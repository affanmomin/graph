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
CallGraphSignals (dataclass)                                                      [4.1]
get_all_call_graph_signals(groups, repo_root) -> dict[str, CallGraphSignals]     [4.1]
HotspotNode (dataclass)                                                           [4.2]
get_all_hotspot_nodes(repo_root, min_lines, max_nodes) -> list[HotspotNode]      [4.2]
get_hotspot_nodes(files, repo_root, min_lines, max_nodes) -> list[HotspotNode]   [4.2]
StructuralDepthSignals (dataclass)                                                [4.3]
get_all_structural_depth_signals(groups, repo_root) -> dict[str, StructuralDepthSignals] [4.3]

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
# File vocabulary
# ---------------------------------------------------------------------------


def get_file_vocabulary(
    files: list[str],
    repo_root: str | Path,
    max_per_file: int = 30,
) -> dict[str, list[str]]:
    """Return function and class names per file from the graph.

    For each file path in *files*, queries ``get_nodes_by_file()`` and
    collects all non-File node names (functions, classes, methods).  This
    vocabulary is used by the generator for accurate responsibility inference
    and by the context builder for semantic task scoring.

    Args:
        files:        Repo-relative file paths to look up.
        repo_root:    Repo root path — locates graph.db.
        max_per_file: Max symbol names to return per file (default 30).

    Returns:
        Dict mapping file_path -> list[symbol_name].
        Returns ``{}`` gracefully when graph is absent or any error occurs.
    """
    if not files:
        return {}
    p = _db_path(Path(repo_root))
    if not p.exists():
        return {}
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return {}
            vocab: dict[str, list[str]] = {}
            for fp in files:
                nodes = gs.get_nodes_by_file(fp)
                names = [
                    n.name for n in nodes
                    if n.kind not in ("File", "Import")
                    and n.name
                    and len(n.name) >= 3          # skip single-letter names
                    and not n.name.startswith("_")  # skip private symbols
                ]
                if names:
                    vocab[fp] = names[:max_per_file]
            return vocab
    except Exception as exc:
        logger.debug("get_file_vocabulary: graph query failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Structured file node summaries
# ---------------------------------------------------------------------------


@dataclass
class FileNodeSummary:
    """Structured symbol summary for a single source file.

    Separates class names from function/method names so generators can
    produce purpose statements grounded in actual code structure (e.g.
    "Defines ``AuthMiddleware``; provides ``verify_token``, ``login_required``")
    instead of inferring from file stems alone.

    Attributes:
        classes:     Public class names defined in this file.
        functions:   Public function/method names defined in this file.
        total_nodes: Total non-File, non-Import nodes found in this file.
    """

    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    total_nodes: int = 0


def get_file_node_summary(
    files: list[str],
    repo_root: str | Path,
    max_classes: int = 8,
    max_functions: int = 10,
) -> dict[str, "FileNodeSummary"]:
    """Return structured class/function summaries per file from the graph.

    Opens ``graph.db`` once and processes all requested files.  For each
    file, nodes are split into Class nodes and Function/Method nodes.
    Private symbols (names starting with ``_``) and names shorter than 3
    characters are excluded.

    Args:
        files:         Repo-relative file paths to look up.
        repo_root:     Repo root path — locates graph.db.
        max_classes:   Max class names returned per file (default 8).
        max_functions: Max function/method names returned per file (default 10).

    Returns:
        Dict mapping file_path → :class:`FileNodeSummary`.
        Returns ``{}`` gracefully when graph is absent or any error occurs.
    """
    if not files:
        return {}
    p = _db_path(Path(repo_root))
    if not p.exists():
        return {}
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return {}
            summaries: dict[str, FileNodeSummary] = {}
            for fp in files:
                nodes = gs.get_nodes_by_file(fp)
                classes: list[str] = []
                functions: list[str] = []
                for n in nodes:
                    if n.kind in ("File", "Import"):
                        continue
                    if not n.name or len(n.name) < 3 or n.name.startswith("_"):
                        continue
                    if n.kind == "Class":
                        classes.append(n.name)
                    elif n.kind in ("Function", "Method"):
                        functions.append(n.name)
                total = len(classes) + len(functions)
                if total > 0:
                    summaries[fp] = FileNodeSummary(
                        classes=classes[:max_classes],
                        functions=functions[:max_functions],
                        total_nodes=total,
                    )
            return summaries
    except Exception as exc:
        logger.debug("get_file_node_summary: graph query failed: %s", exc)
        return {}


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


# ---------------------------------------------------------------------------
# Call-graph signals — Ticket 4.1
# ---------------------------------------------------------------------------


@dataclass
class CallGraphSignals:
    """CALLS-derived structural signals for one feature/module group.

    Attributes:
        entry_points:        File paths that call into other group files but
                             are not themselves called by other group files —
                             the natural "entry points" of the area.
        key_helpers:         File paths called by 2+ other group files —
                             shared internal helpers.
        entry_point_symbols: Representative public symbol names at entry-point
                             files (up to 3, for doc generation).
    """

    entry_points: list[str] = field(default_factory=list)
    key_helpers: list[str] = field(default_factory=list)
    entry_point_symbols: list[str] = field(default_factory=list)


def get_all_call_graph_signals(
    groups: dict[str, list[str]],
    repo_root: str | Path,
    max_entry_points: int = 3,
    max_helpers: int = 3,
) -> dict[str, CallGraphSignals]:
    """Compute CALLS-based entry-point and helper signals for all groups in one DB pass.

    For each group, analyses internal CALLS edges to classify files as:
    - **entry points**: files that call into the group but are not called internally.
    - **key helpers**: files called by 2+ other files in the group.

    When no internal CALLS edges exist (e.g. the group has only 1 file), the
    result will have empty lists — callers should fall back to heuristics.

    Args:
        groups:    Mapping of group name → list of repo-relative file paths.
        repo_root: Repository root containing ``.code-review-graph/graph.db``.

    Returns:
        Dict of group name → :class:`CallGraphSignals`.  Returns ``{}`` when
        the graph is unavailable or any error occurs.
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

            result: dict[str, CallGraphSignals] = {n: CallGraphSignals() for n in groups}

            for group_name, group_files in groups.items():
                if len(group_files) < 2:
                    continue  # single-file group: no internal calls possible

                group_set = set(group_files)

                # Build qualified_name → file_path map for this group
                qn_to_file: dict[str, str] = {}
                file_nodes: dict[str, list] = {}   # fp → list[node]
                for fp in group_files:
                    nodes = gs.get_nodes_by_file(fp)
                    file_nodes[fp] = nodes
                    for node in nodes:
                        qn_to_file[node.qualified_name] = fp

                # Count internal fan-in and fan-out per file
                # fan_out[fp]: unique other group files this file calls
                # fan_in[fp]: unique other group files that call this file
                fan_out: dict[str, set[str]] = {fp: set() for fp in group_files}
                fan_in: dict[str, set[str]] = {fp: set() for fp in group_files}

                for fp in group_files:
                    for node in file_nodes[fp]:
                        qn = node.qualified_name
                        # Outgoing CALLS
                        for edge in gs.get_edges_by_source(qn):
                            if edge.kind == "CALLS":
                                target_fp = _file_from_qualified(edge.target_qualified)
                                if target_fp and target_fp in group_set and target_fp != fp:
                                    fan_out[fp].add(target_fp)
                                    fan_in[target_fp].add(fp)

                sig = result[group_name]

                # Entry points: fan_in == 0 AND fan_out >= 1
                entry_candidates = sorted(
                    fp for fp in group_files
                    if len(fan_in[fp]) == 0 and len(fan_out[fp]) >= 1
                )
                sig.entry_points = entry_candidates[:max_entry_points]

                # Key helpers: called by 2+ other group files
                helper_candidates = sorted(
                    (fp for fp in group_files if len(fan_in[fp]) >= 2),
                    key=lambda fp: -len(fan_in[fp]),  # most-called first
                )
                sig.key_helpers = helper_candidates[:max_helpers]

                # Entry-point symbols: public non-private function/class names
                ep_symbols: list[str] = []
                seen_syms: set[str] = set()
                for ep_fp in sig.entry_points:
                    for node in file_nodes.get(ep_fp, []):
                        if node.kind in ("File", "Import"):
                            continue
                        if not node.name or len(node.name) < 3 or node.name.startswith("_"):
                            continue
                        if node.name not in seen_syms:
                            seen_syms.add(node.name)
                            ep_symbols.append(node.name)
                sig.entry_point_symbols = ep_symbols[:3]

            return result
    except Exception as exc:
        logger.debug("get_all_call_graph_signals: failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Hotspot nodes — Ticket 4.2
# ---------------------------------------------------------------------------


@dataclass
class HotspotNode:
    """A large function or class that may be a complexity/risk hotspot.

    Attributes:
        name:       Symbol name (function or class).
        file_path:  Repo-relative file path containing this symbol.
        kind:       Node kind — ``"Function"``, ``"Class"``, ``"Method"``, etc.
        line_count: Number of lines in this symbol's body.
    """

    name: str
    file_path: str
    kind: str
    line_count: int


def get_all_hotspot_nodes(
    repo_root: str | Path,
    min_lines: int = 40,
    max_nodes: int = 20,
) -> list[HotspotNode]:
    """Return the largest functions/classes across the entire repo.

    Uses ``GraphStore.get_nodes_by_size()`` to find symbols above *min_lines*,
    ordered by line count descending.  Intended for generating
    ``.agent-memory/changes/hotspots.md``.

    Args:
        repo_root:  Repository root containing ``graph.db``.
        min_lines:  Minimum body size to qualify as a hotspot (default 40).
        max_nodes:  Maximum hotspot entries to return (default 20).

    Returns:
        Sorted list of :class:`HotspotNode`, largest first.  Empty when the
        graph is absent or no nodes exceed the threshold.
    """
    p = _db_path(Path(repo_root))
    if not p.exists():
        return []
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return []
            nodes = gs.get_nodes_by_size(min_lines=min_lines, limit=max_nodes)
            return [
                HotspotNode(
                    name=n.name,
                    file_path=n.file_path,
                    kind=n.kind,
                    line_count=(n.line_end - n.line_start) if n.line_end else 0,
                )
                for n in nodes
                if n.name and not n.name.startswith("_")
            ]
    except Exception as exc:
        logger.debug("get_all_hotspot_nodes: failed: %s", exc)
        return []


def get_hotspot_nodes(
    files: list[str],
    repo_root: str | Path,
    min_lines: int = 40,
    max_nodes: int = 5,
) -> list[HotspotNode]:
    """Return the largest symbols within the given *files*.

    Unlike :func:`get_all_hotspot_nodes`, this filters to a specific file set —
    used to surface per-feature/module hotspots in ``memory explain``.

    Args:
        files:      Repo-relative file paths to inspect.
        repo_root:  Repository root.
        min_lines:  Minimum body size to qualify (default 40).
        max_nodes:  Maximum entries to return (default 5).

    Returns:
        :class:`HotspotNode` list, largest first.
    """
    if not files:
        return []
    p = _db_path(Path(repo_root))
    if not p.exists():
        return []
    file_set = set(files)
    try:
        from ..graph import GraphStore
        with GraphStore(p) as gs:
            if gs.get_stats().total_nodes == 0:
                return []
            hotspots: list[HotspotNode] = []
            for fp in files:
                for node in gs.get_nodes_by_file(fp):
                    if node.kind in ("File", "Import"):
                        continue
                    if not node.name or node.name.startswith("_"):
                        continue
                    lc = (node.line_end - node.line_start) if node.line_end else 0
                    if lc >= min_lines:
                        hotspots.append(HotspotNode(
                            name=node.name,
                            file_path=fp,
                            kind=node.kind,
                            line_count=lc,
                        ))
            hotspots.sort(key=lambda h: -h.line_count)
            return hotspots[:max_nodes]
    except Exception as exc:
        logger.debug("get_hotspot_nodes: failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Structural depth signals — Ticket 4.3
# ---------------------------------------------------------------------------


@dataclass
class StructuralDepthSignals:
    """Containment, inheritance, and cross-file coupling signals for one group.

    Attributes:
        inheritance_pairs:  List of ``(child_class, parent_class)`` name pairs
                            for INHERITS edges found within the group's files.
        coupling_files:     Files with the most cross-file CALLS edges (fan-in
                            from other group files), sorted by edge count desc.
        coupling_score:     Density of cross-file CALLS within the group
                            (0 = fully decoupled, 1 = every pair is connected).
    """

    inheritance_pairs: list[tuple[str, str]] = field(default_factory=list)
    coupling_files: list[str] = field(default_factory=list)
    coupling_score: float = 0.0


def get_all_structural_depth_signals(
    groups: dict[str, list[str]],
    repo_root: str | Path,
    max_pairs: int = 5,
    max_coupling_files: int = 3,
) -> dict[str, StructuralDepthSignals]:
    """Compute INHERITS/coupling signals for all groups in one DB pass.

    For each group:
    - Scans INHERITS edges among nodes in the group's files to build an
      inheritance summary (e.g. ``[("TokenStore", "BaseStore")]``).
    - Counts cross-file CALLS edges to produce a coupling score and list
      the most-called files within the group.

    Args:
        groups:              Mapping of group name → repo-relative file paths.
        repo_root:           Repo root.
        max_pairs:           Max inheritance pairs to return per group.
        max_coupling_files:  Max coupling file paths to surface.

    Returns:
        Dict of group name → :class:`StructuralDepthSignals`.  Returns ``{}``
        when the graph is unavailable or any error occurs.
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

            result: dict[str, StructuralDepthSignals] = {n: StructuralDepthSignals() for n in groups}

            for group_name, group_files in groups.items():
                if not group_files:
                    continue

                group_set = set(group_files)

                # Build qn → (name, file_path) map for this group
                qn_to_info: dict[str, tuple[str, str]] = {}
                file_nodes: dict[str, list] = {}
                for fp in group_files:
                    nodes = gs.get_nodes_by_file(fp)
                    file_nodes[fp] = nodes
                    for node in nodes:
                        qn_to_info[node.qualified_name] = (node.name, fp)

                inh_pairs: list[tuple[str, str]] = []
                # fan_in_count[fp] = number of cross-file CALLS edges TO this file
                fan_in_count: dict[str, int] = {fp: 0 for fp in group_files}
                total_cross_calls = 0

                for fp in group_files:
                    for node in file_nodes[fp]:
                        qn = node.qualified_name

                        for edge in gs.get_edges_by_source(qn):
                            if edge.kind == "INHERITS":
                                # child (fp) INHERITS parent (target)
                                parent_info = qn_to_info.get(edge.target_qualified)
                                if parent_info:
                                    child_name = node.name or ""
                                    parent_name = parent_info[0] or ""
                                    if child_name and parent_name and child_name != parent_name:
                                        pair = (child_name, parent_name)
                                        if pair not in inh_pairs:
                                            inh_pairs.append(pair)

                            elif edge.kind == "CALLS":
                                target_fp = _file_from_qualified(edge.target_qualified)
                                if target_fp and target_fp in group_set and target_fp != fp:
                                    fan_in_count[target_fp] += 1
                                    total_cross_calls += 1

                sig = result[group_name]
                sig.inheritance_pairs = inh_pairs[:max_pairs]

                # Coupling score: cross-file calls / (files * files) rough density
                n = len(group_files)
                max_possible = n * (n - 1) if n > 1 else 1
                sig.coupling_score = round(min(1.0, total_cross_calls / max_possible), 2)

                # Most-called files (highest fan-in)
                sig.coupling_files = sorted(
                    (fp for fp in group_files if fan_in_count[fp] >= 1),
                    key=lambda fp: -fan_in_count[fp],
                )[:max_coupling_files]

            return result
    except Exception as exc:
        logger.debug("get_all_structural_depth_signals: failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Architecture graph signals  (Ticket D)
# ---------------------------------------------------------------------------


@dataclass
class ArchitectureGraphSignals:
    """Graph-derived signals for ``architecture.md`` "Inspect first" section.

    Attributes:
        key_files: List of ``(file_path, description)`` tuples for the most
                   structurally important files in the repo.  Files are ranked
                   by fan-in (number of other files that import or call them).
    """

    key_files: list[tuple[str, str]] = field(default_factory=list)


def get_architecture_graph_signals(
    repo_root: str | Path,
    max_files: int = 5,
) -> "ArchitectureGraphSignals | None":
    """Return key files from the graph for ``architecture.md`` "Inspect first".

    Finds the top *max_files* non-test source files by fan-in count (number of
    unique files that CALLS or IMPORTS_FROM them).  These are the structural
    entry points and shared utilities most worth understanding first.

    Returns ``None`` when the graph is unavailable or empty so callers always
    fall back gracefully to heuristic-only behaviour.

    Args:
        repo_root: Absolute path to the repo root.
        max_files: Maximum number of key files to return (default 5).

    Returns:
        :class:`ArchitectureGraphSignals` with populated ``key_files``, or
        ``None`` on any error.
    """
    p = _db_path(Path(repo_root))
    if not p.exists():
        return None
    try:
        from ..graph import GraphStore

        root = Path(repo_root)
        root_str = str(root)

        with GraphStore(p) as gs:
            stats = gs.get_stats()
            if stats.total_nodes == 0:
                return None

            # Universe of non-test source files inside this repo
            src_files: set[str] = {
                f for f in gs.get_all_files()
                if f.startswith(root_str) and not _is_test_file(f)
            }
            if not src_files:
                return None

            # Fan-in: for each source file, collect the set of other source files
            # that import or call something inside it.
            fan_in: dict[str, set[str]] = {f: set() for f in src_files}

            _FANIN_KINDS = frozenset({"CALLS", "IMPORTS_FROM"})
            for edge in gs.get_all_edges():
                if edge.kind not in _FANIN_KINDS:
                    continue
                # The file that owns the edge (source side)
                src_fp = edge.file_path
                if not src_fp or src_fp not in src_files:
                    continue
                # Extract the file path of the target symbol
                target_fp = _file_from_qualified(edge.target_qualified)
                if not target_fp or target_fp not in src_files or target_fp == src_fp:
                    continue
                fan_in[target_fp].add(src_fp)

            # Require fan-in >= 2 to avoid noise from small/single-file references.
            # A file with only 1 caller is not meaningfully "core" to the architecture.
            ranked = sorted(
                ((fp, callers) for fp, callers in fan_in.items() if len(callers) >= 2),
                key=lambda x: -len(x[1]),
            )[:max_files]

            if not ranked:
                return None

            key_files: list[tuple[str, str]] = []
            for fp, callers in ranked:
                try:
                    rel = str(Path(fp).relative_to(root))
                except ValueError:
                    rel = fp
                count = len(callers)
                desc = f"high fan-in — imported/called by {count} other file(s)"
                key_files.append((rel, desc))

            return ArchitectureGraphSignals(key_files=key_files)

    except Exception as exc:
        logger.debug("get_architecture_graph_signals: failed: %s", exc)
        return None
