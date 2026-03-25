"""Tests for memory/graph_bridge.py.

Covers:
- graph_available: True / False (missing db, empty db, exception)
- get_related_files: graph path, fallback (no graph), bounds, test filtering
- get_related_tests: TESTED_BY strategy, impact-radius strategy, fallback
- get_structural_neighbors: outgoing + incoming IMPORTS_FROM, fallback
- Integration: build_context_pack with graph enrichment ON and OFF
- Two realistic task examples (auth token refresh, billing invoice export)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_review_graph.memory.graph_bridge import (
    _is_test_file,
    get_related_files,
    get_related_tests,
    get_structural_neighbors,
    graph_available,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stats(total_nodes: int = 5) -> MagicMock:
    s = MagicMock()
    s.total_nodes = total_nodes
    return s


def _make_store(
    stats_nodes: int = 5,
    impact_files: list[str] | None = None,
    impact_nodes: list[MagicMock] | None = None,
    nodes_by_file: list[MagicMock] | None = None,
    edges_by_target: list[MagicMock] | None = None,
    edges_by_source: list[MagicMock] | None = None,
    get_node_result: MagicMock | None = None,
) -> MagicMock:
    """Return a mock GraphStore context manager with sensible defaults."""
    store = MagicMock()
    store.__enter__ = MagicMock(return_value=store)
    store.__exit__ = MagicMock(return_value=False)

    store.get_stats.return_value = _make_stats(stats_nodes)
    store.get_impact_radius.return_value = {
        "impacted_files": impact_files or [],
        "impacted_nodes": impact_nodes or [],
        "changed_nodes": [],
        "edges": [],
        "truncated": False,
        "total_impacted": len(impact_files or []),
    }
    store.get_nodes_by_file.return_value = nodes_by_file or []
    store.get_edges_by_target.return_value = edges_by_target or []
    store.get_edges_by_source.return_value = edges_by_source or []
    store.get_node.return_value = get_node_result
    return store


def _make_node(
    qualified_name: str = "src/auth.py::verify_token",
    file_path: str = "src/auth.py",
    is_test: bool = False,
) -> MagicMock:
    n = MagicMock()
    n.qualified_name = qualified_name
    n.file_path = file_path
    n.is_test = is_test
    return n


def _make_edge(
    kind: str = "CALLS",
    source_qualified: str = "src/a.py::func_a",
    target_qualified: str = "src/b.py::func_b",
    file_path: str = "src/a.py",
) -> MagicMock:
    e = MagicMock()
    e.kind = kind
    e.source_qualified = source_qualified
    e.target_qualified = target_qualified
    e.file_path = file_path
    return e


# ---------------------------------------------------------------------------
# _is_test_file
# ---------------------------------------------------------------------------


class TestIsTestFile:
    def test_test_prefix(self):
        assert _is_test_file("test_auth.py")

    def test_test_dir(self):
        assert _is_test_file("tests/test_auth.py")
        assert _is_test_file("src/tests/unit.py")

    def test_suffix(self):
        assert _is_test_file("auth_test.py")

    def test_spec(self):
        assert _is_test_file("auth.spec.ts")
        assert _is_test_file("auth.test.ts")

    def test_non_test(self):
        assert not _is_test_file("src/auth.py")
        assert not _is_test_file("src/billing/invoice.py")

    def test_windows_sep(self):
        assert _is_test_file("src\\tests\\test_auth.py")


# ---------------------------------------------------------------------------
# graph_available
# ---------------------------------------------------------------------------


class TestGraphAvailable:
    def test_no_db_file(self, tmp_path: Path):
        assert graph_available(tmp_path) is False

    def test_db_exists_with_nodes(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=10)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            assert graph_available(tmp_path) is True

    def test_db_exists_but_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=0)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            assert graph_available(tmp_path) is False

    def test_db_exception_returns_false(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        broken = MagicMock()
        broken.__enter__ = MagicMock(side_effect=RuntimeError("corrupt db"))
        broken.__exit__ = MagicMock(return_value=False)
        with patch("code_review_graph.graph.GraphStore", return_value=broken):
            assert graph_available(tmp_path) is False


# ---------------------------------------------------------------------------
# get_related_files
# ---------------------------------------------------------------------------


class TestGetRelatedFiles:
    def test_no_graph_returns_empty(self, tmp_path: Path):
        # No graph.db — must return [] without raising
        result = get_related_files(["src/auth.py"], tmp_path)
        assert result == []

    def test_empty_seed_files(self, tmp_path: Path):
        result = get_related_files([], tmp_path)
        assert result == []

    def test_returns_related_non_test_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=5,
            impact_files=[
                "src/auth.py",          # seed — must be excluded
                "src/middleware.py",    # related source file
                "src/user.py",          # related source file
                "tests/test_auth.py",   # test — must be excluded
            ],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_files(["src/auth.py"], tmp_path, max_files=10)

        assert "src/middleware.py" in result
        assert "src/user.py" in result
        assert "src/auth.py" not in result          # seed excluded
        assert "tests/test_auth.py" not in result   # test excluded

    def test_max_files_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        many_files = [f"src/module_{i}.py" for i in range(20)]
        store = _make_store(stats_nodes=5, impact_files=many_files)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_files(["src/seed.py"], tmp_path, max_files=3)

        assert len(result) <= 3

    def test_result_is_sorted(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=5,
            impact_files=["src/z_module.py", "src/a_module.py", "src/m_module.py"],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_files(["src/seed.py"], tmp_path)

        assert result == sorted(result)

    def test_graph_exception_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=5)
        store.get_impact_radius.side_effect = RuntimeError("db locked")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_files(["src/auth.py"], tmp_path)

        assert result == []


# ---------------------------------------------------------------------------
# get_related_tests
# ---------------------------------------------------------------------------


class TestGetRelatedTests:
    def test_no_graph_returns_empty(self, tmp_path: Path):
        result = get_related_tests(["src/auth.py"], tmp_path)
        assert result == []

    def test_empty_seed_files(self, tmp_path: Path):
        result = get_related_tests([], tmp_path)
        assert result == []

    def test_tested_by_edges(self, tmp_path: Path):
        """TESTED_BY edges on seed-file nodes must surface the test file."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth.py::verify_token", "src/auth.py")
        tested_by_edge = _make_edge(
            kind="TESTED_BY",
            source_qualified="tests/test_auth.py::test_verify_token",
            target_qualified="src/auth.py::verify_token",
            file_path="tests/test_auth.py",
        )
        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[node],
            edges_by_target=[tested_by_edge],
            impact_files=[],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_tests(["src/auth.py"], tmp_path)

        assert "tests/test_auth.py" in result

    def test_impact_radius_test_files(self, tmp_path: Path):
        """Test files in the impact radius must be surfaced."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        test_node = _make_node(
            "tests/test_billing.py::TestInvoice",
            "tests/test_billing.py",
            is_test=True,
        )
        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[],
            edges_by_target=[],
            impact_files=["tests/test_billing.py"],
            impact_nodes=[test_node],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_tests(["src/billing.py"], tmp_path)

        assert "tests/test_billing.py" in result

    def test_max_tests_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        many_tests = [f"tests/test_mod_{i}.py" for i in range(20)]
        store = _make_store(stats_nodes=5, impact_files=many_tests, nodes_by_file=[])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_tests(["src/auth.py"], tmp_path, max_tests=3)

        assert len(result) <= 3

    def test_seed_files_excluded(self, tmp_path: Path):
        """Test files that are themselves seeds should not appear in results."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=5,
            impact_files=["tests/test_auth.py"],  # also a seed
            nodes_by_file=[],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_tests(["tests/test_auth.py"], tmp_path)

        assert "tests/test_auth.py" not in result

    def test_graph_exception_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=5)
        store.get_nodes_by_file.side_effect = RuntimeError("db error")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_related_tests(["src/auth.py"], tmp_path)

        assert result == []


# ---------------------------------------------------------------------------
# get_structural_neighbors
# ---------------------------------------------------------------------------


class TestGetStructuralNeighbors:
    def test_no_graph_returns_empty(self, tmp_path: Path):
        result = get_structural_neighbors(["src/auth.py"], tmp_path)
        assert result == []

    def test_empty_seed_files(self, tmp_path: Path):
        result = get_structural_neighbors([], tmp_path)
        assert result == []

    def test_outgoing_imports(self, tmp_path: Path):
        """Files that the seed imports from should appear as neighbors."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth.py::verify_token", "src/auth.py")
        import_edge = _make_edge(
            kind="IMPORTS_FROM",
            source_qualified="src/auth.py::verify_token",
            target_qualified="src/jwt_utils.py::decode_jwt",
            file_path="src/auth.py",
        )
        target_node = _make_node("src/jwt_utils.py::decode_jwt", "src/jwt_utils.py")

        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[node],
            edges_by_source=[import_edge],
            edges_by_target=[],
            get_node_result=target_node,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_structural_neighbors(["src/auth.py"], tmp_path)

        assert "src/jwt_utils.py" in result

    def test_incoming_imports(self, tmp_path: Path):
        """Files that import from the seed should appear as neighbors."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth.py::verify_token", "src/auth.py")
        incoming_edge = _make_edge(
            kind="IMPORTS_FROM",
            source_qualified="src/api/routes.py::login_route",
            target_qualified="src/auth.py::verify_token",
            file_path="src/api/routes.py",  # the importing file = neighbor
        )

        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[node],
            edges_by_source=[],
            edges_by_target=[incoming_edge],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_structural_neighbors(["src/auth.py"], tmp_path)

        assert "src/api/routes.py" in result

    def test_test_files_excluded(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth.py::verify_token", "src/auth.py")
        incoming_edge = _make_edge(
            kind="IMPORTS_FROM",
            source_qualified="tests/test_auth.py::test_verify",
            target_qualified="src/auth.py::verify_token",
            file_path="tests/test_auth.py",
        )

        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[node],
            edges_by_source=[],
            edges_by_target=[incoming_edge],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_structural_neighbors(["src/auth.py"], tmp_path)

        assert "tests/test_auth.py" not in result

    def test_max_neighbors_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth.py::func", "src/auth.py")
        many_edges = [
            _make_edge(
                kind="IMPORTS_FROM",
                source_qualified=f"src/module_{i}.py::func",
                target_qualified="src/auth.py::func",
                file_path=f"src/module_{i}.py",
            )
            for i in range(20)
        ]

        store = _make_store(
            stats_nodes=5,
            nodes_by_file=[node],
            edges_by_source=[],
            edges_by_target=many_edges,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_structural_neighbors(["src/auth.py"], tmp_path, max_neighbors=3)

        assert len(result) <= 3

    def test_graph_exception_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=5)
        store.get_nodes_by_file.side_effect = RuntimeError("db locked")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_structural_neighbors(["src/auth.py"], tmp_path)

        assert result == []


# ---------------------------------------------------------------------------
# Integration: build_context_pack with graph enrichment
# ---------------------------------------------------------------------------


class TestBuildContextPackGraphEnrichment:
    """Test that build_context_pack integrates graph enrichment correctly."""

    def _auth_feature(self) -> FeatureMemory:
        return FeatureMemory(
            name="Authentication",
            files=["src/auth/verify.py", "src/auth/token.py"],
            tests=["tests/test_auth.py"],
            confidence=0.9,
        )

    def _billing_feature(self) -> FeatureMemory:
        return FeatureMemory(
            name="Billing",
            files=["src/billing/invoice.py", "src/billing/export.py"],
            tests=[],
            confidence=0.8,
        )

    def test_no_repo_root_no_graph_enrichment(self):
        """Without repo_root, graph enrichment is skipped entirely."""
        from code_review_graph.memory.context_builder import build_context_pack

        features = [self._auth_feature()]
        pack = build_context_pack(
            "fix auth token refresh bug",
            features,
            [],
            repo_root=None,
        )
        # Pack still works — just heuristic-only
        assert "Authentication" in pack.relevant_features
        assert "Context enriched" not in pack.summary

    def test_graph_unavailable_fallback(self, tmp_path: Path):
        """When graph.db does not exist, pack is heuristic-only without errors."""
        from code_review_graph.memory.context_builder import build_context_pack

        features = [self._auth_feature()]
        # No graph.db created in tmp_path
        pack = build_context_pack(
            "fix auth token refresh bug",
            features,
            [],
            repo_root=tmp_path,
        )
        assert "Authentication" in pack.relevant_features
        assert "Context enriched" not in pack.summary

    def test_graph_enrichment_adds_related_files(self, tmp_path: Path):
        """When graph is available, related files are added to the pack."""
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=10,
            impact_files=[
                "src/auth/verify.py",      # already in heuristic seed
                "src/auth/middleware.py",   # NEW — should be added
            ],
        )
        features = [self._auth_feature()]
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack(
                "fix auth token refresh bug",
                features,
                [],
                repo_root=tmp_path,
            )

        assert "src/auth/middleware.py" in pack.relevant_files
        assert "Context enriched" in pack.summary

    def test_graph_enrichment_adds_related_tests(self, tmp_path: Path):
        """When graph is available, related tests are added to the pack."""
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        # The feature has no tests initially
        feature = FeatureMemory(
            name="Authentication",
            files=["src/auth/verify.py"],
            tests=[],
            confidence=0.9,
        )

        node = _make_node("src/auth/verify.py::verify_token", "src/auth/verify.py")
        tested_by = _make_edge(
            kind="TESTED_BY",
            source_qualified="tests/test_verify.py::test_verify_token",
            target_qualified="src/auth/verify.py::verify_token",
            file_path="tests/test_verify.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[tested_by],
            impact_files=[],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack(
                "fix auth token refresh bug",
                [feature],
                [],
                repo_root=tmp_path,
            )

        assert "tests/test_verify.py" in pack.relevant_tests

    def test_no_duplicates_in_enriched_pack(self, tmp_path: Path):
        """Graph enrichment must not duplicate files already in the heuristic list."""
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=10,
            impact_files=["src/auth/verify.py"],  # already in feature.files
        )
        features = [self._auth_feature()]
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack(
                "fix auth token refresh bug",
                features,
                [],
                repo_root=tmp_path,
            )

        assert pack.relevant_files.count("src/auth/verify.py") == 1

    def test_max_files_cap_respected(self, tmp_path: Path):
        """Total files must never exceed _MAX_FILES (20) even with graph enrichment."""
        from code_review_graph.memory.context_builder import build_context_pack, _MAX_FILES

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        # Feature with many files
        big_feature = FeatureMemory(
            name="Core",
            files=[f"src/core/module_{i}.py" for i in range(18)],
            tests=[],
            confidence=0.9,
        )
        # Graph returns even more
        extra = [f"src/extra/module_{i}.py" for i in range(10)]
        store = _make_store(stats_nodes=10, impact_files=extra)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack("fix core bug", [big_feature], [], repo_root=tmp_path)

        assert len(pack.relevant_files) <= _MAX_FILES

    def test_graph_exception_does_not_break_pack(self, tmp_path: Path):
        """If graph enrichment raises internally, the pack is still returned."""
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        broken_store = _make_store(stats_nodes=10)
        broken_store.get_impact_radius.side_effect = RuntimeError("crash")
        broken_store.get_nodes_by_file.side_effect = RuntimeError("crash")

        features = [self._auth_feature()]
        with patch("code_review_graph.graph.GraphStore", return_value=broken_store):
            pack = build_context_pack(
                "fix auth token refresh bug",
                features,
                [],
                repo_root=tmp_path,
            )

        # Pack is still valid and contains heuristic results
        assert "Authentication" in pack.relevant_features
        assert len(pack.relevant_files) > 0


# ---------------------------------------------------------------------------
# Realistic task examples
# ---------------------------------------------------------------------------


class TestRealisticTaskExamples:
    """Two end-to-end task scenarios with graph enrichment.

    These mimic real developer workflows: a concrete bug fix in auth and a new
    endpoint in billing.  Each validates that:
    - The right feature is selected by heuristics
    - Graph-added files/tests appear in the pack
    - Output stays concise and bounded
    """

    def test_auth_token_refresh_bug(self, tmp_path: Path):
        """Task: 'fix the JWT token refresh race condition in auth middleware'.

        Heuristics pick the Authentication feature. Graph adds middleware.py
        (reachable in 1 hop) and the integration test file (via TESTED_BY).
        """
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        auth_feature = FeatureMemory(
            name="Authentication",
            files=["src/auth/token.py", "src/auth/session.py"],
            tests=["tests/unit/test_token.py"],
            confidence=0.92,
        )
        api_feature = FeatureMemory(
            name="API",
            files=["src/api/routes.py"],
            tests=[],
            confidence=0.5,
        )

        # Graph: middleware is 1 hop from token.py; integration test via TESTED_BY
        node = _make_node("src/auth/token.py::refresh_token", "src/auth/token.py")
        tested_by = _make_edge(
            kind="TESTED_BY",
            source_qualified="tests/integration/test_token_refresh.py::test_race",
            target_qualified="src/auth/token.py::refresh_token",
            file_path="tests/integration/test_token_refresh.py",
        )
        store = _make_store(
            stats_nodes=20,
            impact_files=["src/auth/middleware.py"],   # 1 hop from token.py
            nodes_by_file=[node],
            edges_by_target=[tested_by],
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack(
                "fix the JWT token refresh race condition in auth middleware",
                [auth_feature, api_feature],
                [],
                repo_root=tmp_path,
            )

        # Correct feature selected
        assert pack.relevant_features[0] == "Authentication"
        # Graph-enriched files present
        assert "src/auth/middleware.py" in pack.relevant_files
        # Integration test discovered via TESTED_BY
        assert "tests/integration/test_token_refresh.py" in pack.relevant_tests
        # Heuristic unit test still present
        assert "tests/unit/test_token.py" in pack.relevant_tests
        # Pack is bounded
        assert len(pack.relevant_files) <= 20
        # Summary mentions enrichment
        assert "Context enriched" in pack.summary

    def test_billing_invoice_export_endpoint(self, tmp_path: Path):
        """Task: 'add CSV export endpoint for invoices in billing'.

        Heuristics pick Billing. Graph adds a shared export utility (structural
        neighbor) and an existing billing integration test.
        """
        from code_review_graph.memory.context_builder import build_context_pack

        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        billing_feature = FeatureMemory(
            name="Billing",
            files=["src/billing/invoice.py", "src/billing/models.py"],
            tests=[],
            confidence=0.85,
        )
        auth_feature = FeatureMemory(
            name="Authentication",
            files=["src/auth/token.py"],
            tests=["tests/test_auth.py"],
            confidence=0.4,
        )

        # Graph: csv_exporter.py is reachable; billing integration test via radius
        store = _make_store(
            stats_nodes=15,
            impact_files=[
                "src/utils/csv_exporter.py",       # shared utility
                "tests/integration/test_billing.py",  # test file via radius
            ],
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            pack = build_context_pack(
                "add CSV export endpoint for invoices in billing",
                [billing_feature, auth_feature],
                [],
                repo_root=tmp_path,
            )

        # Correct feature ranked first
        assert pack.relevant_features[0] == "Billing"
        # Graph-added utility present
        assert "src/utils/csv_exporter.py" in pack.relevant_files
        # Billing integration test discovered
        assert "tests/integration/test_billing.py" in pack.relevant_tests
        # Auth should NOT dominate (lower score)
        assert pack.relevant_features[0] != "Authentication"
        # Pack is bounded
        assert len(pack.relevant_files) <= 20
