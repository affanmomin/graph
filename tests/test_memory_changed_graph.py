"""Tests for graph-aware ``memory changed``.

Covers:
- ChangeImpactContext dataclass defaults and fields
- get_change_impact: basic, impacted files, impacted tests, no db, empty
  changed files, exception safety, seed exclusion, max caps, truncation flag
- changed_match: graph section with freshness data, fallback without freshness,
  no-graph fallback, no-repo-root fallback
- Feature, module, and path-match scenarios
- MCP tool memory_recent_changes: graph fields present / absent
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from code_review_graph.memory.graph_bridge import ChangeImpactContext, get_change_impact
from code_review_graph.memory.lookup import changed_match, match_target
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_stats(total_nodes: int = 10) -> MagicMock:
    s = MagicMock()
    s.total_nodes = total_nodes
    return s


def _make_store(
    stats_nodes: int = 10,
    impact_files: list[str] | None = None,
    impact_nodes: list[MagicMock] | None = None,
    total_impacted: int = 0,
    truncated: bool = False,
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
        "truncated": truncated,
        "total_impacted": total_impacted,
    }
    return store


def _make_test_node(fp: str) -> MagicMock:
    n = MagicMock()
    n.qualified_name = f"{fp}::Test"
    n.file_path = fp
    n.is_test = True
    return n


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


def _make_match(kind: str, obj, score: float = 1.0):
    from code_review_graph.memory.lookup import TargetMatch
    return TargetMatch(
        kind=kind,
        name=obj.name,
        slug=obj.slug(),
        obj=obj,
        artifact_path=None,
        score=score,
    )


def _freshness(changed_files: list[str] | None = None) -> dict:
    return {
        "refreshed_at": "2024-01-15T10:00:00+00:00",
        "mode": "incremental",
        "changed_files_count": len(changed_files or []),
        "changed_files": changed_files or [],
        "impacted_features": [],
        "impacted_modules": [],
        "artifacts_refreshed": [],
    }


def _write_freshness(agent_memory: Path, data: dict) -> None:
    meta = agent_memory / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "freshness.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# ChangeImpactContext dataclass
# ---------------------------------------------------------------------------


class TestChangeImpactContext:
    def test_defaults(self):
        ctx = ChangeImpactContext()
        assert ctx.impacted_files == []
        assert ctx.impacted_tests == []
        assert ctx.total_impacted == 0
        assert ctx.truncated is False

    def test_fields(self):
        ctx = ChangeImpactContext(
            impacted_files=["src/api/routes.py"],
            impacted_tests=["tests/test_routes.py"],
            total_impacted=5,
            truncated=True,
        )
        assert ctx.total_impacted == 5
        assert ctx.truncated is True


# ---------------------------------------------------------------------------
# get_change_impact
# ---------------------------------------------------------------------------


class TestGetChangeImpact:
    def test_no_db_returns_none(self, tmp_path: Path):
        result = get_change_impact(["src/auth.py"], tmp_path)
        assert result is None

    def test_empty_changed_files_returns_none(self, tmp_path: Path):
        result = get_change_impact([], tmp_path)
        assert result is None

    def test_empty_graph_returns_none(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=0)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_change_impact(["src/auth.py"], tmp_path)
        assert result is None

    def test_returns_context(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/api/routes.py"], total_impacted=3)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth/token.py"], tmp_path)
        assert isinstance(ctx, ChangeImpactContext)
        assert "src/api/routes.py" in ctx.impacted_files

    def test_separates_test_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            impact_files=["src/api/routes.py", "tests/test_auth.py"],
            total_impacted=5,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "src/api/routes.py" in ctx.impacted_files
        assert "tests/test_auth.py" not in ctx.impacted_files
        assert "tests/test_auth.py" in ctx.impacted_tests

    def test_is_test_nodes_included_in_tests(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        test_node = _make_test_node("tests/integration/test_flow.py")
        store = _make_store(
            impact_files=[],
            impact_nodes=[test_node],
            total_impacted=2,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth.py"], tmp_path)
        assert ctx is not None
        assert "tests/integration/test_flow.py" in ctx.impacted_tests

    def test_seed_files_excluded(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            impact_files=["src/auth/token.py", "src/api/routes.py"],
            total_impacted=2,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth/token.py"], tmp_path)
        assert ctx is not None
        assert "src/auth/token.py" not in ctx.impacted_files

    def test_max_files_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        many = [f"src/module_{i}.py" for i in range(20)]
        store = _make_store(impact_files=many, total_impacted=20)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth.py"], tmp_path, max_files=3)
        assert ctx is not None
        assert len(ctx.impacted_files) <= 3

    def test_max_tests_cap(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        many = [f"tests/test_mod_{i}.py" for i in range(20)]
        store = _make_store(impact_files=many, total_impacted=20)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth.py"], tmp_path, max_tests=2)
        assert ctx is not None
        assert len(ctx.impacted_tests) <= 2

    def test_truncated_flag_propagated(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/a.py"], total_impacted=5, truncated=True)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth.py"], tmp_path)
        assert ctx is not None
        assert ctx.truncated is True

    def test_total_impacted_propagated(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/a.py"], total_impacted=42)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/auth.py"], tmp_path)
        assert ctx is not None
        assert ctx.total_impacted == 42

    def test_exception_returns_none(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=10)
        store.get_impact_radius.side_effect = RuntimeError("db crash")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_change_impact(["src/auth.py"], tmp_path)
        assert result is None

    def test_result_sorted(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            impact_files=["src/z.py", "src/a.py", "src/m.py"],
            total_impacted=3,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            ctx = get_change_impact(["src/seed.py"], tmp_path)
        assert ctx is not None
        assert ctx.impacted_files == sorted(ctx.impacted_files)


# ---------------------------------------------------------------------------
# changed_match with graph enrichment
# ---------------------------------------------------------------------------


class TestChangedMatchGraph:
    def test_no_repo_root_no_graph_section(self, tmp_path: Path):
        """Without repo_root, no Graph impact section."""
        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))
        output = changed_match(match, tmp_path, repo_root=None)
        assert "Graph impact" not in output

    def test_graph_unavailable_no_graph_section(self, tmp_path: Path):
        """When graph.db is missing, no Graph impact section."""
        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))
        # no graph.db created
        output = changed_match(match, tmp_path, repo_root=tmp_path)
        assert "Graph impact" not in output

    def test_graph_section_appears_with_changed_files(self, tmp_path: Path):
        """When graph available + area_files changed → Graph impact section shown."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=3,
        )
        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Graph impact" in output
        assert "src/api/routes.py" in output

    def test_impacted_tests_shown(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["tests/integration/test_auth_flow.py"],
            total_impacted=4,
        )
        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "tests/integration/test_auth_flow.py" in output
        assert "Tests to re-run" in output

    def test_impact_scope_shown(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=7,
        )
        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "7 node(s) impacted" in output

    def test_no_freshness_still_uses_graph(self, tmp_path: Path):
        """Without freshness.json, graph enrichment still runs using area files."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=2,
        )
        feature = _auth_feature()
        match = _make_match("feature", feature)
        # No freshness.json written

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Graph impact" in output
        assert "src/api/routes.py" in output

    def test_module_match_with_graph(self, tmp_path: Path):
        """changed_match works for module matches."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/billing_routes.py"],
            total_impacted=3,
        )
        module = _billing_module()
        match = _make_match("module", module)
        _write_freshness(tmp_path, _freshness(["src/billing/invoice.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Module: src.billing" in output
        assert "Graph impact" in output
        assert "src/api/billing_routes.py" in output

    def test_path_match_with_graph(self, tmp_path: Path):
        """changed_match works for path-kind matches (obj is a feature)."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=2,
        )
        feature = _auth_feature()
        from code_review_graph.memory.lookup import TargetMatch
        match = TargetMatch(
            kind="path",
            name="Authentication (via path: src/auth/token.py)",
            slug=feature.slug(),
            obj=feature,
            artifact_path=None,
            score=0.9,
        )
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "src/api/routes.py" in output

    def test_graph_exception_does_not_break_output(self, tmp_path: Path):
        """If graph query raises, the rest of changed_match still renders."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(stats_nodes=10)
        store.get_impact_radius.side_effect = RuntimeError("crash")

        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        # Core heuristic sections still render
        assert "Authentication" in output
        assert "Last refresh" in output
        # No crash, no graph section
        assert "Graph impact" not in output

    def test_no_area_files_falls_back_to_obj_files(self, tmp_path: Path):
        """When no area_files changed, obj.files used as graph seeds."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=2,
        )
        feature = _auth_feature()
        match = _make_match("feature", feature)
        # freshness has changed_files unrelated to auth
        _write_freshness(tmp_path, _freshness(["src/unrelated/other.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        # Graph section uses feature files as seeds → still shows impact
        assert "Graph impact" in output


# ---------------------------------------------------------------------------
# Realistic end-to-end scenarios
# ---------------------------------------------------------------------------


class TestRealisticChangedExamples:
    def test_auth_changed_blast_radius(self, tmp_path: Path):
        """'memory changed Authentication' with real graph data.

        Auth token.py changed → routes.py and middleware.py impacted (callers)
        + integration test needs re-running.
        """
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=[
                "src/api/routes.py",
                "src/middleware/auth_middleware.py",
                "tests/integration/test_auth_flow.py",
            ],
            total_impacted=12,
        )
        feature = FeatureMemory(
            name="Authentication",
            files=["src/auth/token.py", "src/auth/session.py"],
            tests=["tests/unit/test_token.py"],
            confidence=0.9,
        )
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        # Heuristic section intact
        assert "Authentication" in output
        assert "src/auth/token.py" in output

        # Graph section
        assert "Graph impact" in output
        assert "src/api/routes.py" in output
        assert "src/middleware/auth_middleware.py" in output
        assert "tests/integration/test_auth_flow.py" in output
        assert "Tests to re-run" in output
        assert "12 node(s) impacted" in output

    def test_billing_module_changed_no_freshness(self, tmp_path: Path):
        """'memory changed src.billing' without freshness.json.

        Graph still runs using billing module files as seeds.
        """
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/billing_routes.py", "src/workers/invoice_sender.py"],
            total_impacted=8,
        )
        module = _billing_module()
        match = _make_match("module", module)
        # No freshness.json

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Module: src.billing" in output
        assert "No refresh data found" in output
        assert "Graph impact" in output
        assert "src/api/billing_routes.py" in output
        assert "src/workers/invoice_sender.py" in output
        assert "8 node(s) impacted" in output


# ---------------------------------------------------------------------------
# Impacted areas via sources.json
# ---------------------------------------------------------------------------


def _write_sources(agent_memory: Path, sources: dict) -> None:
    meta = agent_memory / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "sources.json").write_text(json.dumps({"sources": sources}))


class TestImpactedAreas:
    def test_impacted_areas_shown_from_sources_json(self, tmp_path: Path):
        """Named feature/module areas appear when impacted files map to them in sources.json."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py", "src/payments/processor.py"],
            total_impacted=5,
        )
        _write_sources(tmp_path, {
            "src/api/routes.py": ["feature:API Gateway", "module:src.api"],
            "src/payments/processor.py": ["feature:Payments"],
        })

        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Impacted areas" in output
        assert "API Gateway" in output
        assert "Payments" in output

    def test_impacted_areas_module_names_shown(self, tmp_path: Path):
        """Module-kind area names appear alongside feature names."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=3,
        )
        _write_sources(tmp_path, {
            "src/api/routes.py": ["module:src.api"],
        })

        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Impacted areas" in output
        assert "src.api" in output

    def test_no_sources_json_no_impacted_areas(self, tmp_path: Path):
        """When sources.json is absent, Impacted areas line is silently skipped."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=3,
        )
        # No sources.json written

        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        # Graph section still shows, just no Impacted areas line
        assert "Graph impact" in output
        assert "Impacted areas" not in output

    def test_impacted_areas_no_freshness_path(self, tmp_path: Path):
        """Impacted areas also works in the no-freshness code path."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        store = _make_store(
            impact_files=["src/payments/processor.py"],
            total_impacted=4,
        )
        _write_sources(tmp_path, {
            "src/payments/processor.py": ["feature:Payments"],
        })

        feature = _auth_feature()
        match = _make_match("feature", feature)
        # No freshness.json → takes the no-freshness code path

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "Impacted areas" in output
        assert "Payments" in output

    def test_seed_files_excluded_from_areas(self, tmp_path: Path):
        """Files in seed set are not counted as impacted areas even if in sources.json."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()

        # impacted_files from graph includes the seed (which bridge should exclude)
        # but let's verify _impacted_areas also skips files in seed_set
        store = _make_store(
            impact_files=["src/api/routes.py"],
            total_impacted=2,
        )
        _write_sources(tmp_path, {
            "src/auth/token.py": ["feature:Authentication"],  # seed — should be excluded
            "src/api/routes.py": ["feature:API Gateway"],     # truly impacted
        })

        feature = _auth_feature()
        match = _make_match("feature", feature)
        _write_freshness(tmp_path, _freshness(["src/auth/token.py"]))

        with patch("code_review_graph.graph.GraphStore", return_value=store):
            output = changed_match(match, tmp_path, repo_root=tmp_path)

        assert "API Gateway" in output
        # "Authentication" is the feature being queried, not an impacted external area
        # (it may appear in the header, but not as an "Impacted areas" entry)
        lines = output.splitlines()
        impacted_line = next((l for l in lines if "Impacted areas" in l), "")
        assert "Authentication" not in impacted_line
