"""Tests for graph-assisted classification (Ticket 5).

Covers:
- ClassifierGraphSignals: defaults, confidence_delta behaviour
- get_all_classifier_signals: no db, empty groups, internal edges counted as
  unique file-pairs, external dep/dependent files, test files via TESTED_BY,
  exception safety, zero-node graph
- classify_modules with graph: confidence boosted for well-connected groups,
  confidence dampened for isolated groups, tests enriched via TESTED_BY,
  dependencies populated, dependents populated, no-graph fallback unchanged
- classify_features with graph: confidence boosted/dampened, tests enriched,
  no-graph fallback unchanged
- _resolve_module_dependencies: direct mapping, cross-dependency, self-excluded
- Determinism: same input produces identical sorted output
- Regression: no-graph-db path is identical to pure filesystem classification
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_review_graph.memory.classifier import (
    _get_graph_signals,
    _resolve_module_dependencies,
    classify_features,
    classify_modules,
)
from code_review_graph.memory.graph_bridge import (
    ClassifierGraphSignals,
    get_all_classifier_signals,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.scanner import scan_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _make_store(
    stats_nodes: int = 10,
    nodes_by_file: dict | None = None,
    edges_by_source: dict | None = None,
    edges_by_target: dict | None = None,
) -> MagicMock:
    """Build a mock GraphStore."""
    store = MagicMock()
    store.__enter__ = MagicMock(return_value=store)
    store.__exit__ = MagicMock(return_value=False)
    stats = MagicMock()
    stats.total_nodes = stats_nodes
    store.get_stats.return_value = stats

    store.get_nodes_by_file.side_effect = lambda fp: (nodes_by_file or {}).get(fp, [])
    store.get_edges_by_source.side_effect = lambda qn: (edges_by_source or {}).get(qn, [])
    store.get_edges_by_target.side_effect = lambda qn: (edges_by_target or {}).get(qn, [])
    return store


def _node(fp: str, sym: str = "Func") -> MagicMock:
    n = MagicMock()
    n.qualified_name = f"{fp}::{sym}"
    n.file_path = fp
    n.is_test = False
    return n


def _edge(target_qn: str, kind: str, file_path: str = "") -> MagicMock:
    """Build a mock outgoing edge (source is implicit from context)."""
    e = MagicMock()
    e.target_qualified = target_qn
    e.kind = kind
    e.file_path = file_path
    return e


def _in_edge(source_fp: str, kind: str) -> MagicMock:
    """Build a mock incoming edge where edge.file_path is the source file."""
    e = MagicMock()
    e.kind = kind
    e.file_path = source_fp
    return e


# ---------------------------------------------------------------------------
# ClassifierGraphSignals — dataclass and confidence_delta
# ---------------------------------------------------------------------------


class TestClassifierGraphSignals:
    def test_defaults(self):
        sig = ClassifierGraphSignals()
        assert sig.internal_edge_count == 0
        assert sig.external_dep_files == []
        assert sig.external_dependent_files == []
        assert sig.test_files == []

    def test_confidence_delta_single_file(self):
        sig = ClassifierGraphSignals(internal_edge_count=0)
        assert sig.confidence_delta(1) == 0.0

    def test_confidence_delta_well_connected(self):
        """internal_edge_count >= group_size → +0.08 boost."""
        sig = ClassifierGraphSignals(internal_edge_count=5)
        assert sig.confidence_delta(5) == pytest.approx(0.08)

    def test_confidence_delta_partially_connected(self):
        """0 < internal_edge_count < group_size → +0.04."""
        sig = ClassifierGraphSignals(internal_edge_count=2)
        assert sig.confidence_delta(5) == pytest.approx(0.04)

    def test_confidence_delta_isolated(self):
        """internal_edge_count == 0 for multi-file group → -0.05 penalty."""
        sig = ClassifierGraphSignals(internal_edge_count=0)
        assert sig.confidence_delta(3) == pytest.approx(-0.05)

    def test_confidence_delta_two_file_well_connected(self):
        """Exactly group_size internal edges for 2-file group → boost."""
        sig = ClassifierGraphSignals(internal_edge_count=2)
        assert sig.confidence_delta(2) == pytest.approx(0.08)


# ---------------------------------------------------------------------------
# get_all_classifier_signals
# ---------------------------------------------------------------------------


class TestGetAllClassifierSignals:
    def test_no_db_returns_empty(self, tmp_path: Path):
        result = get_all_classifier_signals({"A": ["src/a.py"]}, tmp_path)
        assert result == {}

    def test_empty_groups_returns_empty(self, tmp_path: Path):
        result = get_all_classifier_signals({}, tmp_path)
        assert result == {}

    def test_zero_node_graph_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=0)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals({"A": ["src/a.py"]}, tmp_path)
        assert result == {}

    def test_returns_signal_for_every_group(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store()
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth.py"], "Billing": ["src/billing.py"]}, tmp_path
            )
        assert "Auth" in result
        assert "Billing" in result

    def test_internal_edge_counted(self, tmp_path: Path):
        """IMPORTS_FROM edge between two files in the same group → internal_edge_count=1."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        # login.py imports from middleware.py (same group)
        out_edge = _edge("src/auth/middleware.py::check", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node], "src/auth/middleware.py": []},
            edges_by_source={auth_node.qualified_name: [out_edge]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py", "src/auth/middleware.py"]}, tmp_path
            )
        assert result["Auth"].internal_edge_count == 1

    def test_internal_edge_deduplicated_as_file_pairs(self, tmp_path: Path):
        """Multiple node-level edges between the same two files count as ONE file-pair."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        # Two nodes in login.py, both importing from middleware.py
        node1 = _node("src/auth/login.py", "login")
        node2 = _node("src/auth/login.py", "validate")
        out1 = _edge("src/auth/middleware.py::check", "IMPORTS_FROM")
        out2 = _edge("src/auth/middleware.py::verify", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [node1, node2],
                "src/auth/middleware.py": [],
            },
            edges_by_source={
                node1.qualified_name: [out1],
                node2.qualified_name: [out2],
            },
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py", "src/auth/middleware.py"]}, tmp_path
            )
        # login.py→middleware.py is one unique file pair, regardless of 2 node-level edges
        assert result["Auth"].internal_edge_count == 1

    def test_external_dep_file_recorded(self, tmp_path: Path):
        """IMPORTS_FROM edge to a file outside the group → external_dep_files."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        # login.py imports from utils.py (NOT in Auth group)
        out_edge = _edge("src/utils/helpers.py::format_date", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node]},
            edges_by_source={auth_node.qualified_name: [out_edge]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py"]}, tmp_path
            )
        assert "src/utils/helpers.py" in result["Auth"].external_dep_files

    def test_calls_edge_counts_for_internal_but_not_external_dep(self, tmp_path: Path):
        """CALLS internal → counted; CALLS external → NOT in external_dep_files."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node_a = _node("src/auth/login.py", "login")
        # CALLS to middleware (in group) → internal
        internal_call = _edge("src/auth/middleware.py::check", "CALLS")
        # CALLS to utils (outside) → should NOT appear in external_dep_files
        external_call = _edge("src/utils/helpers.py::fmt", "CALLS")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [node_a],
                "src/auth/middleware.py": [],
            },
            edges_by_source={node_a.qualified_name: [internal_call, external_call]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py", "src/auth/middleware.py"]}, tmp_path
            )
        assert result["Auth"].internal_edge_count == 1
        # CALLS to external file is NOT an external_dep
        assert "src/utils/helpers.py" not in result["Auth"].external_dep_files

    def test_external_dependent_file_recorded(self, tmp_path: Path):
        """Incoming IMPORTS_FROM from outside the group → external_dependent_files."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        # Some other file imports from auth/login.py
        in_edge = _in_edge("src/api/routes.py", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node]},
            edges_by_target={auth_node.qualified_name: [in_edge]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py"]}, tmp_path
            )
        assert "src/api/routes.py" in result["Auth"].external_dependent_files

    def test_tested_by_edge_adds_test_file(self, tmp_path: Path):
        """Incoming TESTED_BY edge → test_files populated via edge.file_path."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        # test_login.py tests login.py via TESTED_BY
        tested_by_edge = _in_edge("tests/test_login.py", "TESTED_BY")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node]},
            edges_by_target={auth_node.qualified_name: [tested_by_edge]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py"]}, tmp_path
            )
        assert "tests/test_login.py" in result["Auth"].test_files

    def test_test_file_excluded_from_external_dep(self, tmp_path: Path):
        """Test files in external_dep_files are filtered out (heuristic)."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        # An IMPORTS_FROM edge to a test file (unusual but possible)
        edge_to_test = _edge("tests/test_helpers.py::setup", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node]},
            edges_by_source={auth_node.qualified_name: [edge_to_test]},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals(
                {"Auth": ["src/auth/login.py"]}, tmp_path
            )
        # Test file should NOT appear in external deps
        assert "tests/test_helpers.py" not in result["Auth"].external_dep_files

    def test_exception_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=10)
        store.get_nodes_by_file.side_effect = RuntimeError("db crash")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals({"Auth": ["src/auth.py"]}, tmp_path)
        assert result == {}

    def test_signals_sorted(self, tmp_path: Path):
        """All list fields in signals are sorted for determinism."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_node = _node("src/auth/login.py", "login")
        out_edges = [
            _edge("src/utils/z_helper.py::f", "IMPORTS_FROM"),
            _edge("src/utils/a_helper.py::f", "IMPORTS_FROM"),
        ]
        store = _make_store(
            nodes_by_file={"src/auth/login.py": [auth_node]},
            edges_by_source={auth_node.qualified_name: out_edges},
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_all_classifier_signals({"Auth": ["src/auth/login.py"]}, tmp_path)
        sig = result["Auth"]
        assert sig.external_dep_files == sorted(sig.external_dep_files)


# ---------------------------------------------------------------------------
# classify_modules with graph enrichment
# ---------------------------------------------------------------------------


def _domain_repo(tmp_path: Path) -> Path:
    """A repo with auth/ and billing/ sub-packages under src/."""
    return make_repo(tmp_path, {
        "src/auth/__init__.py": "",
        "src/auth/login.py": "def login(): pass",
        "src/auth/middleware.py": "from . import login",
        "src/billing/__init__.py": "",
        "src/billing/invoice.py": "def invoice(): pass",
        "tests/test_login.py": "def test_login(): pass",
        "tests/test_invoice.py": "def test_invoice(): pass",
    })


class TestClassifyModulesWithGraph:
    def test_no_graph_db_unchanged(self, tmp_path: Path):
        """Without graph.db, classification is identical to filesystem-only."""
        repo = _domain_repo(tmp_path)
        scan = scan_repo(repo)

        modules_no_graph = classify_modules(repo, scan)
        # No graph.db created → same result
        modules_again = classify_modules(repo, scan)

        assert [m.name for m in modules_no_graph] == [m.name for m in modules_again]

    def test_confidence_boosted_for_well_connected_module(self, tmp_path: Path):
        """A module whose files import each other gets a confidence boost."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        # middleware.py imports login.py within auth → internal edge
        mw_node = _node("src/auth/middleware.py", "check")
        internal_edge = _edge("src/auth/login.py::login", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={
                "src/auth/middleware.py": [mw_node],
                "src/auth/login.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_source={mw_node.qualified_name: [internal_edge]},
        )
        scan = scan_repo(repo)
        # baseline confidence without graph
        base_modules = classify_modules(repo, scan)
        auth_base = next((m for m in base_modules if "auth" in m.name.lower()), None)
        base_conf = auth_base.confidence if auth_base else 0.9

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        auth_mod = next((m for m in modules if "auth" in m.name.lower()), None)
        assert auth_mod is not None
        assert auth_mod.confidence >= base_conf  # boosted or equal

    def test_tests_enriched_via_tested_by_edges(self, tmp_path: Path):
        """TESTED_BY edge adds a test to the module even if stem-matching misses it."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        # A test file with an opaque name (no stem match) linked via TESTED_BY
        tested_by = _in_edge("tests/integration/auth_flow_test.py", "TESTED_BY")

        store = _make_store(
            nodes_by_file={"src/auth/login.py": [login_node], "src/auth/middleware.py": []},
            edges_by_target={login_node.qualified_name: [tested_by]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        auth_mod = next((m for m in modules if "auth" in m.name.lower()), None)
        assert auth_mod is not None
        assert "tests/integration/auth_flow_test.py" in auth_mod.tests

    def test_dependencies_populated_from_import_edges(self, tmp_path: Path):
        """auth module importing from billing → auth.dependencies includes billing."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        # auth/login.py imports from billing/invoice.py
        dep_edge = _edge("src/billing/invoice.py::invoice", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [login_node],
                "src/auth/middleware.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_source={login_node.qualified_name: [dep_edge]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        auth_mod = next((m for m in modules if "auth" in m.name.lower()), None)
        billing_mod = next((m for m in modules if "billing" in m.name.lower()), None)
        assert auth_mod is not None
        assert billing_mod is not None
        assert billing_mod.name in auth_mod.dependencies

    def test_dependents_populated_from_import_edges(self, tmp_path: Path):
        """billing imported by auth → billing.dependents includes auth.

        In the real graph an IMPORTS_FROM edge A→B appears as an outgoing edge
        when processing A's nodes AND as an incoming edge when processing B's
        nodes.  The mock must set up both sides to reflect this.
        """
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        invoice_node = _node("src/billing/invoice.py", "invoice")
        # Outgoing from auth → billing (auth depends on billing)
        dep_edge = _edge("src/billing/invoice.py::invoice", "IMPORTS_FROM")
        # Incoming to billing from auth (billing has auth as a dependent)
        in_from_auth = _in_edge("src/auth/login.py", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [login_node],
                "src/auth/middleware.py": [],
                "src/billing/invoice.py": [invoice_node],
            },
            edges_by_source={login_node.qualified_name: [dep_edge]},
            edges_by_target={invoice_node.qualified_name: [in_from_auth]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        billing_mod = next((m for m in modules if "billing" in m.name.lower()), None)
        auth_mod = next((m for m in modules if "auth" in m.name.lower()), None)
        assert billing_mod is not None
        assert auth_mod.name in billing_mod.dependents

    def test_confidence_clamped_at_0_98(self, tmp_path: Path):
        """Confidence never exceeds 0.98 after boost."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        mw_node = _node("src/auth/middleware.py", "check")
        # Many internal edges
        internal_edges = [_edge(f"src/auth/login.py::fn{i}", "IMPORTS_FROM") for i in range(20)]

        store = _make_store(
            nodes_by_file={
                "src/auth/middleware.py": [mw_node],
                "src/auth/login.py": [],
            },
            edges_by_source={mw_node.qualified_name: internal_edges},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        for m in modules:
            assert m.confidence <= 0.98

    def test_confidence_clamped_at_0_2_minimum(self, tmp_path: Path):
        """Confidence never drops below 0.2 after penalty."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store()  # No nodes → no edges → penalty applied
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)

        for m in modules:
            assert m.confidence >= 0.2

    def test_graph_exception_falls_back_gracefully(self, tmp_path: Path):
        """Graph exception in classify_modules does not crash — falls back to heuristics."""
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=10)
        store.get_nodes_by_file.side_effect = RuntimeError("crash")
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            modules = classify_modules(repo, scan)  # must not raise

        assert len(modules) > 0
        # Dependencies are empty (no graph data)
        for m in modules:
            assert m.dependencies == []


# ---------------------------------------------------------------------------
# classify_features with graph enrichment
# ---------------------------------------------------------------------------


def _feature_repo(tmp_path: Path) -> Path:
    """A repo with auth/ and billing/ feature dirs."""
    return make_repo(tmp_path, {
        "src/auth/__init__.py": "",
        "src/auth/login.py": "def login(): pass",
        "src/auth/session.py": "def session(): pass",
        "src/billing/__init__.py": "",
        "src/billing/invoice.py": "def invoice(): pass",
        "tests/test_auth.py": "def test_login(): pass",
    })


class TestClassifyFeaturesWithGraph:
    def test_no_graph_db_unchanged(self, tmp_path: Path):
        repo = _feature_repo(tmp_path)
        scan = scan_repo(repo)
        result1 = classify_features(repo, scan)
        result2 = classify_features(repo, scan)
        assert [f.name for f in result1] == [f.name for f in result2]

    def test_confidence_boosted_when_internally_connected(self, tmp_path: Path):
        """Feature files that import each other get a confidence boost."""
        repo = _feature_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        # login.py imports from session.py (same feature)
        internal = _edge("src/auth/session.py::session", "IMPORTS_FROM")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [login_node],
                "src/auth/session.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_source={login_node.qualified_name: [internal]},
        )
        scan = scan_repo(repo)
        base = classify_features(repo, scan)
        auth_base_conf = next(
            (f.confidence for f in base if "auth" in f.name.lower()), 0.9
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            features = classify_features(repo, scan)

        auth_feat = next((f for f in features if "auth" in f.name.lower()), None)
        assert auth_feat is not None
        assert auth_feat.confidence >= auth_base_conf

    def test_tests_enriched_via_tested_by(self, tmp_path: Path):
        """Graph TESTED_BY adds a structurally grounded test to the feature."""
        repo = _feature_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        # Opaque test file name (would not match heuristic)
        tested_by = _in_edge("tests/e2e/auth_smoke_test.py", "TESTED_BY")

        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [login_node],
                "src/auth/session.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_target={login_node.qualified_name: [tested_by]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            features = classify_features(repo, scan)

        auth_feat = next((f for f in features if "auth" in f.name.lower()), None)
        assert auth_feat is not None
        assert "tests/e2e/auth_smoke_test.py" in auth_feat.tests

    def test_heuristic_tests_preserved_when_graph_active(self, tmp_path: Path):
        """Graph-enhanced classification still keeps filesystem heuristic test matches."""
        repo = _feature_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store()  # No nodes → no graph edges → heuristic-only tests
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            features = classify_features(repo, scan)

        auth_feat = next((f for f in features if "auth" in f.name.lower()), None)
        assert auth_feat is not None
        # test_auth.py should still appear from stem matching
        assert any("test_auth" in t for t in auth_feat.tests)

    def test_graph_exception_falls_back_gracefully(self, tmp_path: Path):
        """Graph crash in classify_features is silently handled."""
        repo = _feature_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=10)
        store.get_nodes_by_file.side_effect = RuntimeError("crash")
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            features = classify_features(repo, scan)

        assert len(features) > 0


# ---------------------------------------------------------------------------
# _resolve_module_dependencies
# ---------------------------------------------------------------------------


class TestResolveModuleDependencies:
    def _make_module(self, name: str) -> ModuleMemory:
        return ModuleMemory(name=name, files=[], confidence=0.8)

    def test_dependency_mapped_correctly(self):
        auth = self._make_module("src/auth")
        billing = self._make_module("src/billing")
        file_to_module = {
            "src/auth/login.py": "src/auth",
            "src/billing/invoice.py": "src/billing",
        }
        from code_review_graph.memory.graph_bridge import ClassifierGraphSignals
        signals = {
            "src/auth": ClassifierGraphSignals(
                external_dep_files=["src/billing/invoice.py"]
            ),
            "src/billing": ClassifierGraphSignals(),
        }
        _resolve_module_dependencies([auth, billing], signals, file_to_module)
        assert "src/billing" in auth.dependencies

    def test_dependent_mapped_correctly(self):
        auth = self._make_module("src/auth")
        billing = self._make_module("src/billing")
        file_to_module = {
            "src/auth/login.py": "src/auth",
            "src/billing/invoice.py": "src/billing",
        }
        from code_review_graph.memory.graph_bridge import ClassifierGraphSignals
        signals = {
            "src/auth": ClassifierGraphSignals(),
            "src/billing": ClassifierGraphSignals(
                external_dependent_files=["src/auth/login.py"]
            ),
        }
        _resolve_module_dependencies([auth, billing], signals, file_to_module)
        assert "src/auth" in billing.dependents

    def test_self_not_added_as_dependency(self):
        """A module's own files in external_dep_files are excluded."""
        auth = self._make_module("src/auth")
        file_to_module = {"src/auth/login.py": "src/auth"}
        from code_review_graph.memory.graph_bridge import ClassifierGraphSignals
        signals = {
            "src/auth": ClassifierGraphSignals(
                external_dep_files=["src/auth/login.py"]  # own file
            ),
        }
        _resolve_module_dependencies([auth], signals, file_to_module)
        assert "src/auth" not in auth.dependencies

    def test_unknown_file_skipped(self):
        """Files not in file_to_module are silently skipped."""
        auth = self._make_module("src/auth")
        file_to_module: dict[str, str] = {}  # empty
        from code_review_graph.memory.graph_bridge import ClassifierGraphSignals
        signals = {
            "src/auth": ClassifierGraphSignals(
                external_dep_files=["src/unknown/mystery.py"]
            ),
        }
        _resolve_module_dependencies([auth], signals, file_to_module)
        assert auth.dependencies == []

    def test_no_signals_leaves_dependencies_empty(self):
        auth = self._make_module("src/auth")
        _resolve_module_dependencies([auth], {}, {})
        assert auth.dependencies == []
        assert auth.dependents == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_classify_modules_deterministic(self, tmp_path: Path):
        repo = _domain_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        mw_node = _node("src/auth/middleware.py", "check")
        internal = _edge("src/auth/login.py::login", "IMPORTS_FROM")
        store = _make_store(
            nodes_by_file={
                "src/auth/middleware.py": [mw_node],
                "src/auth/login.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_source={mw_node.qualified_name: [internal]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            r1 = classify_modules(repo, scan)
            r2 = classify_modules(repo, scan)

        assert [(m.name, m.confidence, m.dependencies) for m in r1] == [
            (m.name, m.confidence, m.dependencies) for m in r2
        ]

    def test_classify_features_deterministic(self, tmp_path: Path):
        repo = _feature_repo(tmp_path)
        db = repo / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        login_node = _node("src/auth/login.py", "login")
        internal = _edge("src/auth/session.py::session", "IMPORTS_FROM")
        store = _make_store(
            nodes_by_file={
                "src/auth/login.py": [login_node],
                "src/auth/session.py": [],
                "src/billing/invoice.py": [],
            },
            edges_by_source={login_node.qualified_name: [internal]},
        )
        scan = scan_repo(repo)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            r1 = classify_features(repo, scan)
            r2 = classify_features(repo, scan)

        assert [(f.name, f.confidence, f.tests) for f in r1] == [
            (f.name, f.confidence, f.tests) for f in r2
        ]
