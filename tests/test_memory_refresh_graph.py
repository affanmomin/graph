"""Tests for graph-expanded refresh planning (Ticket 4).

Covers:
- get_graph_expanded_files: no db, empty input, returns related files,
  excludes seeds/tests, capped at max_expansion, exception safety
- plan_refresh with repo_root: graph expansion adds extra artifacts,
  direct slugs unchanged, expansion bounded by _GRAPH_EXPAND_MAX_ARTIFACTS,
  graph-expanded slugs sorted and deduplicated, fallback without graph db,
  full=True skips graph expansion
- execute_refresh processes graph-expanded slugs: artifact written,
  graph_expanded_artifacts tracked, dedup with direct slugs
- freshness.json includes graph_expanded_* fields
- recent.md shows Graph-expanded areas section when expansion occurred
- _maybe_refresh_memory in incremental.py passes repo_root to plan_refresh
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from code_review_graph.memory.graph_bridge import get_graph_expanded_files
from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.refresh import (
    RefreshPlan,
    _generate_freshness_data,
    _generate_recent_changes_doc,
    execute_refresh,
    plan_refresh,
)
from code_review_graph.memory.scanner import RepoScan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scan(repo_root: Path) -> RepoScan:
    return RepoScan(
        repo_root=repo_root,
        languages=["python"],
        source_dirs=["src"],
        test_dirs=["tests"],
        confidence=0.9,
    )


def _feature(name: str, files: list[str]) -> FeatureMemory:
    return FeatureMemory(name=name, files=files, tests=[], confidence=0.8)


def _module(name: str, files: list[str]) -> ModuleMemory:
    return ModuleMemory(name=name, files=files, confidence=0.85)


def _make_store(
    stats_nodes: int = 10,
    impact_files: list[str] | None = None,
    truncated: bool = False,
    total_impacted: int = 0,
) -> MagicMock:
    store = MagicMock()
    store.__enter__ = MagicMock(return_value=store)
    store.__exit__ = MagicMock(return_value=False)
    stats = MagicMock()
    stats.total_nodes = stats_nodes
    store.get_stats.return_value = stats
    store.get_impact_radius.return_value = {
        "impacted_files": impact_files or [],
        "impacted_nodes": [],
        "changed_nodes": [],
        "edges": [],
        "truncated": truncated,
        "total_impacted": total_impacted,
    }
    return store


@pytest.fixture()
def features() -> list[FeatureMemory]:
    return [
        _feature("Authentication", ["src/auth/login.py", "src/auth/middleware.py"]),
        _feature("Billing", ["src/billing/invoice.py", "src/billing/payment.py"]),
        _feature("Search", ["src/search/engine.py"]),
    ]


@pytest.fixture()
def modules() -> list[ModuleMemory]:
    return [
        _module("src.auth", ["src/auth/login.py", "src/auth/middleware.py"]),
        _module("src.billing", ["src/billing/invoice.py"]),
        _module("src.search", ["src/search/engine.py"]),
    ]


# ---------------------------------------------------------------------------
# get_graph_expanded_files
# ---------------------------------------------------------------------------


class TestGetGraphExpandedFiles:
    def test_no_db_returns_empty(self, tmp_path: Path):
        result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert result == []

    def test_empty_changed_files_returns_empty(self, tmp_path: Path):
        result = get_graph_expanded_files([], tmp_path)
        assert result == []

    def test_empty_graph_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=0)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert result == []

    def test_returns_related_non_test_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            impact_files=["src/api/routes.py", "src/middleware/cors.py"],
            total_impacted=3,
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert "src/api/routes.py" in result
        assert "src/middleware/cors.py" in result

    def test_excludes_seed_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        # graph returns the seed itself — should be excluded
        store = _make_store(impact_files=["src/auth/login.py", "src/api/routes.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert "src/auth/login.py" not in result
        assert "src/api/routes.py" in result

    def test_excludes_test_files(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(
            impact_files=["src/api/routes.py", "tests/test_auth.py"]
        )
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert "tests/test_auth.py" not in result
        assert "src/api/routes.py" in result

    def test_capped_at_max_expansion(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        many = [f"src/module_{i}.py" for i in range(50)]
        store = _make_store(impact_files=many, total_impacted=50)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path, max_expansion=5)
        assert len(result) <= 5

    def test_result_sorted(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/z.py", "src/a.py", "src/m.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/seed.py"], tmp_path)
        assert result == sorted(result)

    def test_exception_returns_empty(self, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(stats_nodes=10)
        store.get_impact_radius.side_effect = RuntimeError("crash")
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            result = get_graph_expanded_files(["src/auth/login.py"], tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# plan_refresh with graph expansion
# ---------------------------------------------------------------------------


class TestPlanRefreshGraphExpansion:
    def test_no_repo_root_no_expansion(self, features, modules):
        """Without repo_root, graph_expanded fields are empty."""
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        assert plan.graph_expanded_feature_slugs == []
        assert plan.graph_expanded_module_slugs == []
        assert plan.graph_expanded_files == []

    def test_no_graph_db_no_expansion(self, features, modules, tmp_path: Path):
        """No graph.db → graph expansion silently skipped."""
        plan = plan_refresh(["src/auth/login.py"], features, modules, repo_root=tmp_path)

        assert plan.graph_expanded_feature_slugs == []
        assert plan.graph_expanded_module_slugs == []

    def test_graph_expansion_adds_related_feature(self, features, modules, tmp_path: Path):
        """Files from BFS that belong to Billing trigger it as a graph-expanded artifact."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        # auth/login.py changed → graph shows billing/invoice.py as related
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        # Direct: only Authentication and src.auth
        assert "authentication" in plan.impacted_feature_slugs
        assert "billing" not in plan.impacted_feature_slugs
        # Graph-expanded: Billing added
        assert "billing" in plan.graph_expanded_feature_slugs

    def test_direct_slugs_not_in_expanded(self, features, modules, tmp_path: Path):
        """Slugs already in the direct list are not duplicated in graph_expanded."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        # BFS returns another auth file — auth is already direct
        store = _make_store(impact_files=["src/auth/middleware.py", "src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        # Authentication is direct; must NOT appear in expanded
        assert "authentication" not in plan.graph_expanded_feature_slugs
        assert "billing" in plan.graph_expanded_feature_slugs

    def test_expansion_bounded_by_max_artifacts(self, tmp_path: Path):
        """Graph expansion never exceeds _GRAPH_EXPAND_MAX_ARTIFACTS (3) extra slugs."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        # 10 features, each with a unique file
        many_features = [_feature(f"Feature{i}", [f"src/feat_{i}/main.py"]) for i in range(10)]
        # BFS returns all their files
        impact_files = [f"src/feat_{i}/main.py" for i in range(10)]
        store = _make_store(impact_files=impact_files)
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], many_features, [], repo_root=tmp_path
            )

        total_expanded = (
            len(plan.graph_expanded_feature_slugs) + len(plan.graph_expanded_module_slugs)
        )
        assert total_expanded <= 3

    def test_expanded_module_added(self, features, modules, tmp_path: Path):
        """A module whose file appears in BFS expansion is added to graph_expanded_module_slugs."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/search/engine.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        assert "src-search" in plan.graph_expanded_module_slugs

    def test_graph_expanded_files_populated(self, features, modules, tmp_path: Path):
        """plan.graph_expanded_files contains the BFS-returned file paths."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        assert "src/billing/invoice.py" in plan.graph_expanded_files

    def test_full_refresh_skips_graph_expansion(self, features, modules, tmp_path: Path):
        """full=True returns all artifacts and does not call graph expansion."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store) as mock_gs:
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, full=True, repo_root=tmp_path
            )

        assert plan.mode == "full"
        # Graph expanded fields are empty for full refresh
        assert plan.graph_expanded_feature_slugs == []
        assert plan.graph_expanded_module_slugs == []

    def test_reason_mentions_expansion_count(self, features, modules, tmp_path: Path):
        """reason string includes graph-expanded count when expansion happened."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        assert "graph-expanded" in plan.reason

    def test_reason_no_expansion_note_when_no_expansion(self, features, modules):
        """When no graph expansion occurs, reason does not mention graph-expanded."""
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        assert "graph-expanded" not in plan.reason

    def test_expanded_slugs_sorted(self, features, modules, tmp_path: Path):
        """Graph-expanded slug lists are sorted for determinism."""
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py", "src/search/engine.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        assert plan.graph_expanded_feature_slugs == sorted(plan.graph_expanded_feature_slugs)
        assert plan.graph_expanded_module_slugs == sorted(plan.graph_expanded_module_slugs)


# ---------------------------------------------------------------------------
# execute_refresh processes graph-expanded slugs
# ---------------------------------------------------------------------------


class TestExecuteRefreshGraphExpanded:
    def test_graph_expanded_feature_artifact_written(self, features, modules, tmp_path: Path):
        """A graph-expanded feature has its .md written."""
        scan = _scan(tmp_path)
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        assert "billing" in plan.graph_expanded_feature_slugs

        result = execute_refresh(plan, tmp_path, features, modules, scan)

        billing_path = tmp_path / ".agent-memory" / "features" / "billing.md"
        assert billing_path.exists()
        assert any("billing" in a for a in result["artifacts_updated"])

    def test_graph_expanded_artifacts_tracked_separately(self, features, modules, tmp_path: Path):
        """graph_expanded_artifacts in result lists only expansion-triggered writes."""
        scan = _scan(tmp_path)
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )
        result = execute_refresh(plan, tmp_path, features, modules, scan)

        # graph_expanded_artifacts key exists and contains billing
        assert "graph_expanded_artifacts" in result
        assert any("billing" in a for a in result["graph_expanded_artifacts"])
        # authentication was direct, NOT in graph_expanded_artifacts
        assert not any("authentication" in a for a in result["graph_expanded_artifacts"])

    def test_no_double_write_for_overlap(self, features, modules, tmp_path: Path):
        """If a slug appears in both direct and graph-expanded, it is only written once."""
        scan = _scan(tmp_path)
        # Manually build a plan where billing is in both lists (defensive dedup test)
        plan = RefreshPlan(
            mode="incremental",
            changed_files=["src/billing/invoice.py"],
            impacted_feature_slugs=["billing"],
            impacted_module_slugs=[],
            graph_expanded_feature_slugs=["billing"],  # same slug
            graph_expanded_module_slugs=[],
            graph_expanded_files=["src/billing/invoice.py"],
            reason="test overlap",
        )
        result = execute_refresh(plan, tmp_path, features, modules, scan)

        # billing.md must exist but not be in graph_expanded_artifacts
        billing_path = tmp_path / ".agent-memory" / "features" / "billing.md"
        assert billing_path.exists()
        assert not any("billing" in a for a in result["graph_expanded_artifacts"])

    def test_graph_expanded_artifacts_empty_without_expansion(self, features, modules, tmp_path):
        """Without graph expansion, graph_expanded_artifacts is an empty list."""
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)  # no repo_root
        result = execute_refresh(plan, tmp_path, features, modules, scan)

        assert result["graph_expanded_artifacts"] == []

    def test_unknown_graph_expanded_slug_skipped_gracefully(self, features, modules, tmp_path):
        """A stale graph-expanded slug that no longer exists in classifier output is skipped."""
        scan = _scan(tmp_path)
        plan = RefreshPlan(
            mode="incremental",
            changed_files=["src/auth/login.py"],
            impacted_feature_slugs=["authentication"],
            impacted_module_slugs=[],
            graph_expanded_feature_slugs=["ghost-feature"],
            graph_expanded_module_slugs=[],
            graph_expanded_files=[],
            reason="test",
        )
        result = execute_refresh(plan, tmp_path, features, modules, scan)
        assert result["mode"] == "incremental"


# ---------------------------------------------------------------------------
# freshness.json graph fields
# ---------------------------------------------------------------------------


class TestFreshnessGraphFields:
    def test_freshness_has_graph_expanded_keys(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        data = _generate_freshness_data(plan, [])

        assert "graph_expanded_features" in data
        assert "graph_expanded_modules" in data
        assert "graph_expanded_files" in data

    def test_freshness_graph_fields_populated(self, features, modules, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )
        data = _generate_freshness_data(plan, [])

        assert "billing" in data["graph_expanded_features"]
        assert "src/billing/invoice.py" in data["graph_expanded_files"]

    def test_freshness_graph_fields_empty_without_expansion(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        data = _generate_freshness_data(plan, [])

        assert data["graph_expanded_features"] == []
        assert data["graph_expanded_modules"] == []
        assert data["graph_expanded_files"] == []

    def test_execute_refresh_writes_graph_fields_to_disk(self, features, modules, tmp_path: Path):
        """execute_refresh persists graph expansion data in freshness.json."""
        scan = _scan(tmp_path)
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )
        execute_refresh(plan, tmp_path, features, modules, scan)

        freshness_path = tmp_path / ".agent-memory" / "metadata" / "freshness.json"
        data = json.loads(freshness_path.read_text())
        assert "graph_expanded_features" in data
        assert "billing" in data["graph_expanded_features"]


# ---------------------------------------------------------------------------
# recent.md graph-expanded section
# ---------------------------------------------------------------------------


class TestRecentMdGraphExpanded:
    def test_graph_expanded_section_appears(self, features, modules, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/billing/invoice.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}
        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "Graph-expanded areas" in doc
        assert "Billing" in doc
        assert "graph-related" in doc

    def test_no_graph_section_without_expansion(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}
        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "Graph-expanded areas" not in doc

    def test_graph_section_shows_module_names(self, features, modules, tmp_path: Path):
        db = tmp_path / ".code-review-graph" / "graph.db"
        db.parent.mkdir(parents=True)
        db.touch()
        store = _make_store(impact_files=["src/search/engine.py"])
        with patch("code_review_graph.graph.GraphStore", return_value=store):
            plan = plan_refresh(
                ["src/auth/login.py"], features, modules, repo_root=tmp_path
            )

        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}
        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        # Either the feature "Search" or the module "src.search" should appear
        assert "Search" in doc or "src.search" in doc


# ---------------------------------------------------------------------------
# _maybe_refresh_memory passes repo_root to plan_refresh
# ---------------------------------------------------------------------------


class TestMaybeRefreshMemoryGraphIntegration:
    def test_plan_refresh_called_with_repo_root(self, tmp_path: Path, features, modules):
        """_maybe_refresh_memory passes repo_root to plan_refresh for graph expansion."""
        scan = _scan(tmp_path)
        (tmp_path / ".agent-memory").mkdir()

        with (
            patch(
                "code_review_graph.memory.scanner.scan_repo",
                return_value=scan,
            ),
            patch(
                "code_review_graph.memory.classifier.classify_features",
                return_value=features,
            ),
            patch(
                "code_review_graph.memory.classifier.classify_modules",
                return_value=modules,
            ),
            patch(
                "code_review_graph.memory.refresh.plan_refresh",
                return_value=MagicMock(
                    mode="incremental",
                    changed_files=[],
                    impacted_feature_slugs=[],
                    impacted_module_slugs=[],
                    graph_expanded_feature_slugs=[],
                    graph_expanded_module_slugs=[],
                    graph_expanded_files=[],
                    update_repo=False,
                    update_architecture=False,
                    reason="test",
                ),
            ) as mock_plan,
            patch("code_review_graph.memory.refresh.execute_refresh"),
        ):
            from code_review_graph.incremental import _maybe_refresh_memory
            _maybe_refresh_memory(tmp_path, ["src/auth/login.py"])

            # plan_refresh must receive repo_root=tmp_path
            call_kwargs = mock_plan.call_args.kwargs
            assert "repo_root" in call_kwargs
            assert call_kwargs["repo_root"] == tmp_path
