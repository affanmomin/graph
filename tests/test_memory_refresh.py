"""Tests for the incremental memory refresh orchestrator (Ticket 9).

Covers:
- plan_refresh: one file changed in a feature
- plan_refresh: multiple related files changed
- plan_refresh: deleted file (path still in changed_files, absent on disk)
- plan_refresh: full=True marks everything impacted
- plan_refresh: no changed files → empty plan
- plan_refresh: structural file triggers repo/architecture update
- plan_refresh: large batch of files triggers repo/architecture update
- execute_refresh: writes recent.md and freshness.json
- execute_refresh: only impacted feature artifacts are regenerated
- execute_refresh: non-impacted feature artifacts are not regenerated
- execute_refresh: unknown slug is skipped gracefully
- freshness metadata reflects the correct plan data
- incremental_update refresh_memory=True hook calls _maybe_refresh_memory
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
# Fixtures
# ---------------------------------------------------------------------------


def _scan(repo_root: Path) -> RepoScan:
    """Minimal RepoScan for testing."""
    return RepoScan(
        repo_root=repo_root,
        languages=["python"],
        source_dirs=["src"],
        test_dirs=["tests"],
        confidence=0.9,
    )


def _feature(name: str, files: list[str], tests: list[str] | None = None) -> FeatureMemory:
    return FeatureMemory(name=name, files=files, tests=tests or [], confidence=0.8)


def _module(name: str, files: list[str]) -> ModuleMemory:
    return ModuleMemory(name=name, files=files, confidence=0.85)


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
    ]


# ---------------------------------------------------------------------------
# plan_refresh — incremental mode
# ---------------------------------------------------------------------------


class TestPlanRefreshIncremental:
    def test_one_file_in_feature(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        assert plan.mode == "incremental"
        assert plan.changed_files == ["src/auth/login.py"]
        assert "authentication" in plan.impacted_feature_slugs
        assert "billing" not in plan.impacted_feature_slugs
        assert "src-auth" in plan.impacted_module_slugs

    def test_multiple_related_files(self, features, modules):
        plan = plan_refresh(
            ["src/auth/login.py", "src/billing/invoice.py"],
            features,
            modules,
        )

        assert plan.mode == "incremental"
        assert set(plan.impacted_feature_slugs) == {"authentication", "billing"}
        assert set(plan.impacted_module_slugs) == {"src-auth", "src-billing"}

    def test_unclassified_file_no_impact(self, features, modules):
        """A changed file not in any feature/module causes no artifact impact."""
        plan = plan_refresh(["src/utils/helpers.py"], features, modules)

        assert plan.mode == "incremental"
        assert plan.impacted_feature_slugs == []
        assert plan.impacted_module_slugs == []

    def test_deleted_file_still_appears_in_changed(self, features, modules, tmp_path):
        """A deleted file path is still valid in changed_files (file doesn't need to exist)."""
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        assert "src/auth/login.py" in plan.changed_files

    def test_empty_changed_files(self, features, modules):
        plan = plan_refresh([], features, modules)

        assert plan.mode == "incremental"
        assert plan.changed_files == []
        assert plan.impacted_feature_slugs == []
        assert plan.impacted_module_slugs == []
        assert not plan.update_repo

    def test_no_features_or_modules(self):
        plan = plan_refresh(["src/foo.py"], [], [])

        assert plan.impacted_feature_slugs == []
        assert plan.impacted_module_slugs == []

    def test_changed_files_sorted(self, features, modules):
        plan = plan_refresh(["src/billing/invoice.py", "src/auth/login.py"], features, modules)

        assert plan.changed_files == sorted(plan.changed_files)

    def test_impacted_slugs_sorted(self, features, modules):
        plan = plan_refresh(
            ["src/auth/login.py", "src/billing/invoice.py"],
            features,
            modules,
        )

        assert plan.impacted_feature_slugs == sorted(plan.impacted_feature_slugs)
        assert plan.impacted_module_slugs == sorted(plan.impacted_module_slugs)

    def test_reason_contains_counts(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        assert "1 changed file" in plan.reason
        assert "directly impacted" in plan.reason

    # --- structural triggers ---

    def test_top_level_file_triggers_repo_update(self, features, modules):
        plan = plan_refresh(["README.md"], features, modules)

        assert plan.update_repo is True
        assert plan.update_architecture is True

    def test_config_toml_triggers_repo_update(self, features, modules):
        plan = plan_refresh(["pyproject.toml"], features, modules)

        assert plan.update_repo is True

    def test_config_json_triggers_repo_update(self, features, modules):
        plan = plan_refresh(["package.json"], features, modules)

        assert plan.update_repo is True

    def test_deep_source_file_no_repo_update(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        # auth/login.py is not at repo root and not a config file → no repo update
        assert plan.update_repo is False

    def test_large_batch_triggers_repo_update(self, features, modules):
        """10+ changed files trigger a top-level doc refresh."""
        many = [f"src/file_{i}.py" for i in range(10)]
        plan = plan_refresh(many, features, modules)

        assert plan.update_repo is True


# ---------------------------------------------------------------------------
# plan_refresh — full mode
# ---------------------------------------------------------------------------


class TestPlanRefreshFull:
    def test_full_marks_everything(self, features, modules):
        plan = plan_refresh([], features, modules, full=True)

        assert plan.mode == "full"
        assert plan.update_repo is True
        assert plan.update_architecture is True
        assert set(plan.impacted_feature_slugs) == {
            "authentication", "billing", "search"
        }
        assert set(plan.impacted_module_slugs) == {"src-auth", "src-billing"}

    def test_full_with_changed_files(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules, full=True)

        assert plan.mode == "full"
        assert plan.changed_files == ["src/auth/login.py"]
        # still all artifacts
        assert "search" in plan.impacted_feature_slugs

    def test_full_reason(self, features, modules):
        plan = plan_refresh([], features, modules, full=True)

        assert "full refresh" in plan.reason


# ---------------------------------------------------------------------------
# execute_refresh
# ---------------------------------------------------------------------------


class TestExecuteRefresh:
    def test_creates_recent_md(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        execute_refresh(plan, tmp_path, features, modules, scan)

        recent = tmp_path / ".agent-memory" / "changes" / "recent.md"
        assert recent.exists()
        content = recent.read_text()
        assert "# Recent changes" in content
        assert "src/auth/login.py" in content

    def test_creates_freshness_json(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        execute_refresh(plan, tmp_path, features, modules, scan)

        freshness_path = tmp_path / ".agent-memory" / "metadata" / "freshness.json"
        assert freshness_path.exists()
        data = json.loads(freshness_path.read_text())
        assert data["mode"] == "incremental"
        assert "src/auth/login.py" in data["changed_files"]
        assert "refreshed_at" in data

    def test_only_impacted_feature_regenerated(self, features, modules, tmp_path):
        """Only the auth feature is regenerated; billing is untouched."""
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        result = execute_refresh(plan, tmp_path, features, modules, scan)

        updated = result["artifacts_updated"]
        # authentication feature should be regenerated
        assert any("authentication" in a for a in updated)
        # billing feature should NOT be regenerated
        assert not any("billing" in a for a in updated)

    def test_non_impacted_feature_not_written(self, features, modules, tmp_path):
        """Billing feature .md is not created when only auth changed."""
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        execute_refresh(plan, tmp_path, features, modules, scan)

        billing_path = tmp_path / ".agent-memory" / "features" / "billing.md"
        assert not billing_path.exists()

    def test_full_refresh_writes_all_features(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh([], features, modules, full=True)

        result = execute_refresh(plan, tmp_path, features, modules, scan)

        updated = result["artifacts_updated"]
        assert any("authentication" in a for a in updated)
        assert any("billing" in a for a in updated)
        assert any("search" in a for a in updated)

    def test_unknown_slug_skipped_gracefully(self, features, modules, tmp_path):
        """A stale slug in the plan that doesn't match any classifier output is skipped."""
        scan = _scan(tmp_path)
        plan = RefreshPlan(
            mode="incremental",
            changed_files=["src/ghost.py"],
            impacted_feature_slugs=["ghost-feature-that-does-not-exist"],
            impacted_module_slugs=[],
            update_repo=False,
            update_architecture=False,
            reason="test",
        )

        # Should not raise
        result = execute_refresh(plan, tmp_path, features, modules, scan)
        assert result["mode"] == "incremental"

    def test_empty_plan_still_writes_recent_and_freshness(self, features, modules, tmp_path):
        """Even a no-op plan writes recent.md and freshness.json."""
        scan = _scan(tmp_path)
        plan = plan_refresh([], features, modules)

        execute_refresh(plan, tmp_path, features, modules, scan)

        assert (tmp_path / ".agent-memory" / "changes" / "recent.md").exists()
        assert (tmp_path / ".agent-memory" / "metadata" / "freshness.json").exists()

    def test_result_dict_keys(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        result = execute_refresh(plan, tmp_path, features, modules, scan)

        assert set(result.keys()) == {
            "mode", "changed_files", "artifacts_updated", "artifacts_skipped",
            "graph_expanded_artifacts", "reason"
        }

    def test_repo_architecture_updated_when_flagged(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh(["pyproject.toml"], features, modules)

        assert plan.update_repo is True
        result = execute_refresh(plan, tmp_path, features, modules, scan)

        updated = result["artifacts_updated"]
        assert ".agent-memory/repo.md" in updated
        assert ".agent-memory/architecture.md" in updated

    def test_repo_not_updated_for_deep_source_file(self, features, modules, tmp_path):
        scan = _scan(tmp_path)
        plan = plan_refresh(["src/auth/login.py"], features, modules)

        assert plan.update_repo is False
        result = execute_refresh(plan, tmp_path, features, modules, scan)

        updated = result["artifacts_updated"]
        assert ".agent-memory/repo.md" not in updated

    def test_idempotent_write_skips_unchanged(self, features, modules, tmp_path):
        """Running execute_refresh twice with the same inputs skips unchanged artifacts."""
        scan = _scan(tmp_path)
        plan = plan_refresh([], features, modules, full=True)

        result1 = execute_refresh(plan, tmp_path, features, modules, scan)
        result2 = execute_refresh(plan, tmp_path, features, modules, scan)

        # Second run: feature/module docs should be unchanged (same content)
        skipped2 = result2["artifacts_skipped"]
        assert any("authentication" in a for a in skipped2)


# ---------------------------------------------------------------------------
# _generate_recent_changes_doc
# ---------------------------------------------------------------------------


class TestGenerateRecentChangesDoc:
    def test_contains_header(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}

        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "# Recent changes" in doc

    def test_lists_changed_files(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}

        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "src/auth/login.py" in doc

    def test_lists_impacted_feature_name(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}

        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "Authentication" in doc

    def test_no_impact_message(self, features, modules):
        plan = plan_refresh(["src/utils/helpers.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}

        doc = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        assert "No classified features or modules" in doc

    def test_deterministic(self, features, modules):
        """Same plan always produces same doc (no random IDs or unstable timestamps
        within a single call — we freeze the content comparison by checking structure)."""
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        feature_by_slug = {f.slug(): f for f in features}
        module_by_slug = {m.slug(): m for m in modules}

        doc1 = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)
        doc2 = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)

        # Structure (everything except the timestamp line) is identical
        lines1 = [l for l in doc1.splitlines() if "Last refresh" not in l]
        lines2 = [l for l in doc2.splitlines() if "Last refresh" not in l]
        assert lines1 == lines2


# ---------------------------------------------------------------------------
# _generate_freshness_data
# ---------------------------------------------------------------------------


class TestGenerateFreshnessData:
    def test_keys_present(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        data = _generate_freshness_data(plan, [".agent-memory/features/authentication.md"])

        assert "refreshed_at" in data
        assert "mode" in data
        assert "changed_files" in data
        assert "artifacts_refreshed" in data
        assert "impacted_features" in data
        assert "impacted_modules" in data
        assert "graph_expanded_features" in data
        assert "graph_expanded_modules" in data
        assert "graph_expanded_files" in data
        assert "reason" in data

    def test_mode_matches_plan(self, features, modules):
        plan = plan_refresh([], features, modules, full=True)
        data = _generate_freshness_data(plan, [])

        assert data["mode"] == "full"

    def test_changed_files_count(self, features, modules):
        plan = plan_refresh(["src/auth/login.py", "src/billing/invoice.py"], features, modules)
        data = _generate_freshness_data(plan, [])

        assert data["changed_files_count"] == 2

    def test_artifacts_refreshed_sorted(self, features, modules):
        plan = plan_refresh(["src/auth/login.py"], features, modules)
        arts = [
            ".agent-memory/changes/recent.md",
            ".agent-memory/features/authentication.md",
        ]
        data = _generate_freshness_data(plan, arts)

        assert data["artifacts_refreshed"] == sorted(arts)


# ---------------------------------------------------------------------------
# incremental_update refresh_memory hook
# ---------------------------------------------------------------------------


class TestIncrementalUpdateRefreshHook:
    def test_refresh_memory_false_does_not_call_hook(self, tmp_path):
        """refresh_memory=False (default) must not call _maybe_refresh_memory."""
        with patch(
            "code_review_graph.incremental._maybe_refresh_memory"
        ) as mock_refresh:
            from code_review_graph.incremental import incremental_update
            from code_review_graph.graph import GraphStore

            db_path = tmp_path / ".code-review-graph" / "graph.db"
            db_path.parent.mkdir(parents=True)
            store = GraphStore(db_path)
            try:
                incremental_update(
                    tmp_path, store, changed_files=[], refresh_memory=False
                )
            finally:
                store.close()

            mock_refresh.assert_not_called()

    def test_refresh_memory_true_calls_hook(self, tmp_path):
        """refresh_memory=True triggers _maybe_refresh_memory after graph update."""
        with patch(
            "code_review_graph.incremental._maybe_refresh_memory"
        ) as mock_refresh:
            from code_review_graph.incremental import incremental_update
            from code_review_graph.graph import GraphStore

            db_path = tmp_path / ".code-review-graph" / "graph.db"
            db_path.parent.mkdir(parents=True)
            store = GraphStore(db_path)
            try:
                incremental_update(
                    tmp_path, store, changed_files=["src/foo.py"], refresh_memory=True
                )
            finally:
                store.close()

            mock_refresh.assert_called_once_with(tmp_path, ["src/foo.py"])

    def test_maybe_refresh_memory_skips_when_no_agent_memory(self, tmp_path):
        """_maybe_refresh_memory is a no-op when .agent-memory/ doesn't exist."""
        from code_review_graph.incremental import _maybe_refresh_memory

        # No .agent-memory/ at tmp_path
        _maybe_refresh_memory(tmp_path, ["src/foo.py"])  # should not raise

    def test_maybe_refresh_memory_runs_when_agent_memory_exists(self, tmp_path, features, modules):
        """_maybe_refresh_memory runs the refresh pipeline when .agent-memory/ exists."""
        from code_review_graph.incremental import _maybe_refresh_memory

        (tmp_path / ".agent-memory").mkdir()

        # Patch at the source modules — _maybe_refresh_memory uses lazy relative imports.
        with (
            patch(
                "code_review_graph.memory.scanner.scan_repo",
                return_value=_scan(tmp_path),
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
                return_value=MagicMock(),
            ) as mock_plan,
            patch("code_review_graph.memory.refresh.execute_refresh") as mock_exec,
        ):
            _maybe_refresh_memory(tmp_path, ["src/auth/login.py"])

            mock_plan.assert_called_once()
            mock_exec.assert_called_once()

    def test_maybe_refresh_memory_non_fatal_on_error(self, tmp_path):
        """Errors inside _maybe_refresh_memory are caught and logged, not raised."""
        from code_review_graph.incremental import _maybe_refresh_memory

        (tmp_path / ".agent-memory").mkdir()

        with patch(
            "code_review_graph.memory.scanner.scan_repo",
            side_effect=RuntimeError("scan exploded"),
        ):
            # Must not raise
            _maybe_refresh_memory(tmp_path, ["src/foo.py"])
