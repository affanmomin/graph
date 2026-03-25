"""Tests for graph-enriched ``memory explain``.

Covers:
- ExplainGraphContext dataclass fields
- get_explain_context: feature with graph available, module with graph available,
  path-matched target, unavailable fallback, exception safety
- explain_match: graph section rendered, heuristic-only fallback, no duplicates
- _graph_explain_section: each field (tests, neighbors, fan-in, fan-out, related files)
- memory_explain_area MCP tool: graph fields present / absent
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_review_graph.memory.graph_bridge import ExplainGraphContext, get_explain_context
from code_review_graph.memory.lookup import explain_match, match_target
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Shared mock helpers (mirrors test_memory_graph_bridge.py conventions)
# ---------------------------------------------------------------------------


def _make_stats(total_nodes: int = 10) -> MagicMock:
    s = MagicMock()
    s.total_nodes = total_nodes
    return s


def _make_store(
    stats_nodes: int = 10,
    impact_files: list[str] | None = None,
    impact_nodes: list[MagicMock] | None = None,
    nodes_by_file: list[MagicMock] | None = None,
    edges_by_target: list[MagicMock] | None = None,
    edges_by_source: list[MagicMock] | None = None,
) -> MagicMock:
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
        "total_impacted": 0,
    }
    store.get_nodes_by_file.return_value = nodes_by_file or []
    store.get_edges_by_target.return_value = edges_by_target or []
    store.get_edges_by_source.return_value = edges_by_source or []
    return store


def _make_node(qn: str = "src/auth.py::verify", fp: str = "src/auth.py") -> MagicMock:
    n = MagicMock()
    n.qualified_name = qn
    n.file_path = fp
    n.is_test = False
    return n


def _make_edge(
    kind: str = "IMPORTS_FROM",
    source: str = "src/a.py::func",
    target: str = "src/b.py::func",
    file_path: str = "src/a.py",
) -> MagicMock:
    e = MagicMock()
    e.kind = kind
    e.source_qualified = source
    e.target_qualified = target
    e.file_path = file_path
    return e


def _auth_feature() -> FeatureMemory:
    return FeatureMemory(
        name="Authentication",
        files=["src/auth/token.py", "src/auth/session.py"],
        tests=["tests/unit/test_token.py"],
        confidence=0.9,
    )


def _billing_module() -> ModuleMemory:
    return ModuleMemory(
        name="src.billing",
        files=["src/billing/invoice.py", "src/billing/models.py"],
        tests=[],
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# ExplainGraphContext dataclass
# ---------------------------------------------------------------------------


class TestExplainGraphContext:
    def test_defaults(self):
        ctx = ExplainGraphContext()
        assert ctx.related_files == []
        assert ctx.related_tests == []
        assert ctx.structural_neighbors == []
        assert ctx.fan_in_count == 0
        assert ctx.fan_in_sample == []
        assert ctx.fan_out_sample == []

    def test_fields_set(self):
        ctx = ExplainGraphContext(
            related_files=["src/a.py"],
            related_tests=["tests/test_a.py"],
            structural_neighbors=["src/b.py"],
            fan_in_count=3,
            fan_in_sample=["src/c.py"],
            fan_out_sample=["src/d.py"],
        )
        assert ctx.fan_in_count == 3
        assert ctx.fan_in_sample == ["src/c.py"]


# ---------------------------------------------------------------------------
# get_explain_context
# ---------------------------------------------------------------------------


class TestGetExplainContext:
    def test_no_db_returns_none(self, tmp_path: Path):
        result = get_explain_context(["src/auth.py"], tmp_path)
        assert result is None

    def test_empty_seed_files_returns_none(self, tmp_path: Path):
        result = get_explain_context([], tmp_path)
        assert result is None

    def test_empty_graph_returns_none(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=0)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_explain_context(["src/auth.py"], tmp_path)
        assert result is None

    def test_returns_explain_context(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            stats_nodes=10,
            impact_files=["src/auth/middleware.py"],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert isinstance(ctx, ExplainGraphContext)
        assert "src/auth/middleware.py" in ctx.related_files

    def test_related_tests_from_impact_radius(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        test_node = _make_node("tests/test_auth.py::TestToken", "tests/test_auth.py")
        test_node.is_test = True
        store = _make_store(
            stats_nodes=10,
            impact_files=["tests/integration/test_token.py"],
            impact_nodes=[test_node],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "tests/integration/test_token.py" in ctx.related_tests
        assert "tests/test_auth.py" in ctx.related_tests

    def test_tested_by_edges_populate_related_tests(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        node = _make_node("src/auth/token.py::verify_token", "src/auth/token.py")
        tested_by = _make_edge(
            kind="TESTED_BY",
            source="tests/integration/test_flow.py::test_verify",
            target="src/auth/token.py::verify_token",
            file_path="tests/integration/test_flow.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[tested_by],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "tests/integration/test_flow.py" in ctx.related_tests

    def test_fan_in_from_imports_from_edges(self, tmp_path: Path):
        """Incoming IMPORTS_FROM edges must populate fan_in_count and fan_in_sample."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        importer_edge = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/routes.py::login_route",
            target="src/auth/token.py::verify",
            file_path="src/api/routes.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[importer_edge],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert ctx.fan_in_count >= 1
        assert "src/api/routes.py" in ctx.fan_in_sample

    def test_fan_out_from_outgoing_imports(self, tmp_path: Path):
        """Outgoing IMPORTS_FROM edges must populate fan_out_sample."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        import_edge = _make_edge(
            kind="IMPORTS_FROM",
            source="src/auth/token.py::verify",
            target="src/utils/crypto.py::hash_token",  # has ::  → file = src/utils/crypto.py
            file_path="src/auth/token.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_source=[import_edge],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "src/utils/crypto.py" in ctx.fan_out_sample

    def test_structural_neighbors_populated(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        neighbor_edge = _make_edge(
            kind="IMPORTS_FROM",
            source="src/middleware.py::check",
            target="src/auth/token.py::verify",
            file_path="src/middleware.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[neighbor_edge],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "src/middleware.py" in ctx.structural_neighbors

    def test_exception_returns_none(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=10)
        store.get_impact_radius.side_effect = RuntimeError("db locked")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_explain_context(["src/auth.py"], tmp_path)
        assert result is None

    def test_seed_files_excluded_from_related_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            stats_nodes=10,
            impact_files=["src/auth/token.py", "src/auth/middleware.py"],
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "src/auth/token.py" not in ctx.related_files

    def test_max_related_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        many = [f"src/module_{i}.py" for i in range(20)]
        store = _make_store(stats_nodes=10, impact_files=many)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_explain_context(["src/seed.py"], tmp_path, max_related=3)
        assert ctx is not None
        assert len(ctx.related_files) <= 3


# ---------------------------------------------------------------------------
# explain_match with graph enrichment
# ---------------------------------------------------------------------------


class TestExplainMatchGraph:
    """Test that explain_match renders the Graph structure section correctly."""

    def _match(self, tmp_path: Path, feature: FeatureMemory) -> object:
        """Create a TargetMatch for *feature* using tmp_path as agent_memory_root."""
        from code_review_graph.memory.lookup import TargetMatch
        return TargetMatch(
            kind="feature",
            name=feature.name,
            slug=feature.slug(),
            obj=feature,
            artifact_path=None,
            score=1.0,
        )

    def test_no_repo_root_no_graph_section(self, tmp_path: Path):
        """Without repo_root, no Graph structure section in output."""
        feature = _auth_feature()
        match = self._match(tmp_path, feature)
        output = explain_match(match, tmp_path, repo_root=None)
        assert "Graph structure" not in output

    def test_graph_unavailable_no_graph_section(self, tmp_path: Path):
        """When graph.db is missing, no Graph structure section."""
        feature = _auth_feature()
        match = self._match(tmp_path, feature)
        # no graph.db created in tmp_path
        output = explain_match(match, tmp_path, repo_root=tmp_path)
        assert "Graph structure" not in output

    def test_graph_available_renders_section(self, tmp_path: Path):
        """When graph is available and returns data, section appears."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        import_edge = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/routes.py::login",
            target="src/auth/token.py::verify",
            file_path="src/api/routes.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[import_edge],
        )
        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "Graph structure" in output

    def test_fan_in_appears_in_output(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        importer = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/routes.py::login",
            target="src/auth/token.py::verify",
            file_path="src/api/routes.py",
        )
        store = _make_store(stats_nodes=10, nodes_by_file=[node], edges_by_target=[importer])
        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "Imported/called by" in output
        assert "src/api/routes.py" in output

    def test_fan_out_appears_in_output(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        outgoing = _make_edge(
            kind="IMPORTS_FROM",
            source="src/auth/token.py::verify",
            target="src/utils/crypto.py::hash_token",
            file_path="src/auth/token.py",
        )
        store = _make_store(stats_nodes=10, nodes_by_file=[node], edges_by_source=[outgoing])
        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "Depends on" in output
        assert "src/utils/crypto.py" in output

    def test_graph_linked_tests_not_in_heuristic_list(self, tmp_path: Path):
        """Graph-linked tests NOT already in heuristic list must appear in output."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        tested_by = _make_edge(
            kind="TESTED_BY",
            source="tests/integration/test_flow.py::test_verify",
            target="src/auth/token.py::verify",
            file_path="tests/integration/test_flow.py",
        )
        store = _make_store(stats_nodes=10, nodes_by_file=[node], edges_by_target=[tested_by])
        feature = _auth_feature()  # heuristic tests: only tests/unit/test_token.py
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "tests/integration/test_flow.py" in output

    def test_heuristic_tests_not_duplicated_in_graph_section(self, tmp_path: Path):
        """Tests already in the heuristic list must NOT appear again in Graph section."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        # Graph finds the same test already in heuristic list
        tested_by = _make_edge(
            kind="TESTED_BY",
            source="tests/unit/test_token.py::test_verify",
            target="src/auth/token.py::verify",
            file_path="tests/unit/test_token.py",  # already in _auth_feature().tests
        )
        store = _make_store(stats_nodes=10, nodes_by_file=[node], edges_by_target=[tested_by])
        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        # test_token.py should appear exactly once in the Related tests section
        count = output.count("tests/unit/test_token.py")
        assert count == 1

    def test_related_files_in_output(self, tmp_path: Path):
        """New related files (not in feature.files) must appear in output."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=10,
            impact_files=["src/auth/middleware.py"],  # not in auth_feature.files
        )
        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "src/auth/middleware.py" in output

    def test_module_explain_with_graph(self, tmp_path: Path):
        """explain_match works for module matches, not just feature matches."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/billing/invoice.py::Invoice", "src/billing/invoice.py")
        importer = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/billing_routes.py::create_invoice",
            target="src/billing/invoice.py::Invoice",
            file_path="src/api/billing_routes.py",
        )
        store = _make_store(stats_nodes=10, nodes_by_file=[node], edges_by_target=[importer])

        from code_review_graph.memory.lookup import TargetMatch
        module = _billing_module()
        match = TargetMatch(
            kind="module",
            name=module.name,
            slug=module.slug(),
            obj=module,
            artifact_path=None,
            score=1.0,
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "Module: src.billing" in output
        assert "src/api/billing_routes.py" in output

    def test_path_match_with_graph(self, tmp_path: Path):
        """explain_match works for path-kind matches (obj is a feature/module)."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            stats_nodes=10,
            impact_files=["src/auth/middleware.py"],
        )

        from code_review_graph.memory.lookup import TargetMatch
        feature = _auth_feature()
        match = TargetMatch(
            kind="path",
            name="Authentication (via path: src/auth/token.py)",
            slug=feature.slug(),
            obj=feature,
            artifact_path=None,
            score=0.9,
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        # Graph section appears
        assert "src/auth/middleware.py" in output

    def test_path_match_to_feature_shows_feature_label(self, tmp_path: Path):
        """A path match whose underlying obj is a FeatureMemory must show 'Feature:'."""
        from code_review_graph.memory.lookup import TargetMatch
        feature = _auth_feature()
        match = TargetMatch(
            kind="path",
            name="Authentication (via path: src/auth/token.py)",
            slug=feature.slug(),
            obj=feature,
            artifact_path=None,
            score=0.9,
        )
        # No graph needed — just checking the header label
        output = explain_match(match, tmp_path, repo_root=None)
        assert output.startswith("Feature:")

    def test_path_match_to_module_shows_module_label(self, tmp_path: Path):
        """A path match whose underlying obj is a ModuleMemory must show 'Module:'."""
        from code_review_graph.memory.lookup import TargetMatch
        module = _billing_module()
        match = TargetMatch(
            kind="path",
            name="src.billing (via path: src/billing/invoice.py)",
            slug=module.slug(),
            obj=module,
            artifact_path=None,
            score=0.9,
        )
        output = explain_match(match, tmp_path, repo_root=None)
        assert output.startswith("Module:")

    def test_graph_section_uses_per_line_bullets(self, tmp_path: Path):
        """Multiple structural neighbors must each appear on their own line."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify", "src/auth/token.py")
        # Two importers → fan-in count = 2, sample has two files
        importer_a = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/routes.py::login",
            target="src/auth/token.py::verify",
            file_path="src/api/routes.py",
        )
        importer_b = _make_edge(
            kind="IMPORTS_FROM",
            source="src/workers/job.py::run",
            target="src/auth/token.py::verify",
            file_path="src/workers/job.py",
        )
        store = _make_store(
            stats_nodes=10,
            nodes_by_file=[node],
            edges_by_target=[importer_a, importer_b],
        )
        feature = _auth_feature()
        from code_review_graph.memory.lookup import TargetMatch
        match = TargetMatch(
            kind="feature", name=feature.name, slug=feature.slug(),
            obj=feature, artifact_path=None, score=1.0,
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        lines = output.splitlines()
        # Each file must be on its own line (bullet format), not comma-joined
        file_lines = [l for l in lines if "src/api/routes.py" in l or "src/workers/job.py" in l]
        assert len(file_lines) >= 2, (
            "Each file should be on its own line, not comma-separated"
        )
        # Must not appear on a single line together
        combined = [l for l in lines if "src/api/routes.py" in l and "src/workers/job.py" in l]
        assert combined == [], "Files must not be comma-joined on one line"

    def test_graph_exception_does_not_break_output(self, tmp_path: Path):
        """If graph query raises, the rest of explain_match output is still correct."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=10)
        store.get_impact_radius.side_effect = RuntimeError("crash")

        feature = _auth_feature()
        match = self._match(tmp_path, feature)

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        # Core heuristic sections still present
        assert "Authentication" in output
        assert "src/auth/token.py" in output
        # No crash propagated
        assert "Graph structure" not in output


# ---------------------------------------------------------------------------
# Realistic task examples
# ---------------------------------------------------------------------------


class TestRealisticExplainExamples:
    """Two end-to-end explain scenarios showing full graph-enriched output."""

    def test_explain_auth_feature(self, tmp_path: Path):
        """Explain 'Authentication' feature with rich graph data.

        Graph adds: an integration test via TESTED_BY, a middleware neighbor,
        fan-in from routes.py, fan-out to crypto.py.
        """
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/auth/token.py::verify_token", "src/auth/token.py")
        tested_by = _make_edge(
            kind="TESTED_BY",
            source="tests/integration/test_auth_flow.py::test_verify",
            target="src/auth/token.py::verify_token",
            file_path="tests/integration/test_auth_flow.py",
        )
        import_from_routes = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/routes.py::login",
            target="src/auth/token.py::verify_token",
            file_path="src/api/routes.py",
        )
        outgoing_crypto = _make_edge(
            kind="IMPORTS_FROM",
            source="src/auth/token.py::verify_token",
            target="src/utils/crypto.py::hash_token",
            file_path="src/auth/token.py",
        )
        store = _make_store(
            stats_nodes=20,
            impact_files=["src/auth/middleware.py"],
            nodes_by_file=[node],
            edges_by_target=[tested_by, import_from_routes],
            edges_by_source=[outgoing_crypto],
        )

        feature = FeatureMemory(
            name="Authentication",
            files=["src/auth/token.py", "src/auth/session.py"],
            tests=["tests/unit/test_token.py"],  # heuristic test
            confidence=0.92,
        )

        from code_review_graph.memory.lookup import TargetMatch
        match = TargetMatch(
            kind="feature", name="Authentication", slug="authentication",
            obj=feature, artifact_path=None, score=1.0,
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        # Heuristic sections intact
        assert "Authentication" in output
        assert "90%" in output or "92%" in output
        assert "src/auth/token.py" in output
        assert "tests/unit/test_token.py" in output

        # Graph section enrichment
        assert "Graph structure" in output
        assert "tests/integration/test_auth_flow.py" in output  # TESTED_BY
        assert "src/api/routes.py" in output                    # fan-in
        assert "src/utils/crypto.py" in output                  # fan-out
        assert "src/auth/middleware.py" in output               # 1-hop related

        # No duplication of heuristic test
        assert output.count("tests/unit/test_token.py") == 1

    def test_explain_billing_module(self, tmp_path: Path):
        """Explain 'src.billing' module — graph shows 2 importers (high coupling).

        Verifies fan-in count and module-level output correctness.
        """
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        node = _make_node("src/billing/invoice.py::Invoice", "src/billing/invoice.py")
        importer_1 = _make_edge(
            kind="IMPORTS_FROM",
            source="src/api/billing_routes.py::create_invoice",
            target="src/billing/invoice.py::Invoice",
            file_path="src/api/billing_routes.py",
        )
        importer_2 = _make_edge(
            kind="IMPORTS_FROM",
            source="src/workers/invoice_sender.py::send",
            target="src/billing/invoice.py::Invoice",
            file_path="src/workers/invoice_sender.py",
        )
        store = _make_store(
            stats_nodes=15,
            impact_files=["src/billing/pdf_export.py"],
            nodes_by_file=[node],
            edges_by_target=[importer_1, importer_2],
        )

        from code_review_graph.memory.lookup import TargetMatch
        module = _billing_module()
        match = TargetMatch(
            kind="module", name=module.name, slug=module.slug(),
            obj=module, artifact_path=None, score=1.0,
        )

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = explain_match(match, tmp_path, repo_root=tmp_path)

        assert "Module: src.billing" in output
        assert "Graph structure" in output
        assert "2 file(s)" in output   # fan-in count
        assert "src/api/billing_routes.py" in output
        assert "src/billing/pdf_export.py" in output
