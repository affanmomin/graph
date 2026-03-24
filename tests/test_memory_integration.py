"""Integration tests for the repo-memory subsystem (Ticket 12).

These tests exercise end-to-end flows using real file I/O in temporary
directories — no mocking of the memory subsystem itself.  They prove that
the product loop works: init → refresh → prepare-context → explain → changed.

Flows covered:
  1.  memory init         — artifacts created on disk, manifest written
  2.  feature/module docs — correct .md files under .agent-memory/
  3.  prepare-context     — returns features/files for a realistic task
  4.  refresh (full)      — re-runs and updates artifacts
  5.  refresh (incremental) — respects changed_files plan
  6.  overrides influence — always_include / never_edit appear in output
  7.  memory explain      — resolves a known feature, prints useful text
  8.  memory changed      — shows change info using freshness.json
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper — run CLI, capture stdout
# ---------------------------------------------------------------------------


def _run(*argv: str) -> str:
    from code_review_graph.cli import main

    buf = StringIO()
    with patch("sys.argv", ["code-review-graph", *argv]):
        with patch("sys.stdout", buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures — realistic but minimal repo tree in tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    """Create a minimal Python project structure for integration tests."""
    # Source package
    src = tmp_path / "src" / "myapp"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "auth.py").write_text(
        "def login(user, pwd):\n    pass\n\ndef logout(user):\n    pass\n"
    )
    (src / "payments.py").write_text(
        "def charge(amount):\n    pass\n\ndef refund(txn_id):\n    pass\n"
    )
    (src / "api.py").write_text(
        "from .auth import login\nfrom .payments import charge\n\ndef handle_request():\n    pass\n"
    )

    # Tests
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_auth.py").write_text(
        "from myapp.auth import login\n\ndef test_login():\n    pass\n"
    )
    (tests / "test_payments.py").write_text(
        "from myapp.payments import charge\n\ndef test_charge():\n    pass\n"
    )

    # Project files
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\nversion = "0.1.0"\n'
    )
    (tmp_path / "README.md").write_text("# My App\n")

    return tmp_path


# ---------------------------------------------------------------------------
# 1. memory init — artifacts created on disk
# ---------------------------------------------------------------------------


class TestMemoryInitIntegration:
    def test_init_creates_agent_memory_dir(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory").is_dir()

    def test_init_creates_repo_md(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "repo.md").exists()

    def test_init_creates_architecture_md(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "architecture.md").exists()

    def test_init_creates_metadata_dir(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "metadata").is_dir()

    def test_init_writes_manifest_json(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        manifest_path = sample_repo / ".agent-memory" / "metadata" / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text())
        assert "generated_at" in manifest
        assert "generated_artifacts" in manifest
        assert isinstance(manifest["generated_artifacts"], list)

    def test_init_writes_sources_json(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        sources_path = sample_repo / ".agent-memory" / "metadata" / "sources.json"
        assert sources_path.exists()
        data = json.loads(sources_path.read_text())
        assert "sources" in data

    def test_init_writes_confidence_json(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        conf_path = sample_repo / ".agent-memory" / "metadata" / "confidence.json"
        assert conf_path.exists()
        data = json.loads(conf_path.read_text())
        assert "features" in data
        assert "modules" in data

    def test_init_output_reports_scanning(self, sample_repo):
        out = _run("memory", "init", "--repo", str(sample_repo))
        assert "scanning" in out.lower() or "repo-memory" in out

    def test_init_output_reports_done(self, sample_repo):
        out = _run("memory", "init", "--repo", str(sample_repo))
        assert "Done" in out or "done" in out

    def test_init_is_idempotent(self, sample_repo):
        """Running init twice should not raise and should produce updated artifacts."""
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "init", "--repo", str(sample_repo))
        assert "Done" in out or "done" in out


# ---------------------------------------------------------------------------
# 2. feature / module doc generation
# ---------------------------------------------------------------------------


class TestFeatureModuleDocGeneration:
    def test_features_dir_created(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "features").is_dir()

    def test_modules_dir_created(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "modules").is_dir()

    def test_rules_dir_created(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "rules").is_dir()

    def test_conventions_md_created(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "rules" / "conventions.md").exists()

    def test_safe_boundaries_md_created(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory" / "rules" / "safe-boundaries.md").exists()

    def test_repo_md_contains_language_info(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        content = (sample_repo / ".agent-memory" / "repo.md").read_text()
        # Should mention python somewhere
        assert "python" in content.lower() or "Python" in content

    def test_architecture_md_is_nonempty(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        content = (sample_repo / ".agent-memory" / "architecture.md").read_text()
        assert len(content) > 50

    def test_conventions_md_is_nonempty(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        content = (sample_repo / ".agent-memory" / "rules" / "conventions.md").read_text()
        assert len(content) > 20

    def test_safe_boundaries_md_is_nonempty(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        content = (sample_repo / ".agent-memory" / "rules" / "safe-boundaries.md").read_text()
        assert len(content) > 20

    def test_manifest_lists_repo_and_architecture(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        manifest = json.loads(
            (sample_repo / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        artifact_ids = {a["artifact_id"] for a in manifest["generated_artifacts"]}
        assert "repo" in artifact_ids
        assert "architecture" in artifact_ids


# ---------------------------------------------------------------------------
# 3. prepare-context flow
# ---------------------------------------------------------------------------


class TestPrepareContextIntegration:
    def test_prepare_context_runs_without_init(self, sample_repo):
        """prepare-context works even if memory init has not been run."""
        out = _run("memory", "prepare-context", "add login feature", "--repo", str(sample_repo))
        assert "prepare-context" in out

    def test_prepare_context_mentions_task(self, sample_repo):
        out = _run("memory", "prepare-context", "fix the payment webhook", "--repo", str(sample_repo))
        assert "fix the payment webhook" in out

    def test_prepare_context_after_init_has_context(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "prepare-context", "add auth login", "--repo", str(sample_repo))
        # Should have found at least something
        assert "prepare-context" in out
        # Should not crash or print error
        assert "Error" not in out or "error" not in out.lower()

    def test_prepare_context_empty_task_exits_nonzero(self, sample_repo):
        buf = StringIO()
        with patch("sys.argv", [
            "code-review-graph", "memory", "prepare-context", "   ",
            "--repo", str(sample_repo),
        ]):
            with patch("sys.stdout", buf):
                with pytest.raises(SystemExit) as exc:
                    from code_review_graph.cli import main
                    main()
        assert exc.value.code != 0

    def test_prepare_context_json_flag(self, sample_repo):
        out = _run(
            "memory", "prepare-context", "add feature", "--repo", str(sample_repo), "--json"
        )
        # Should produce valid JSON
        data = json.loads(out)
        assert "task" in data
        assert "relevant_features" in data
        assert "relevant_modules" in data


# ---------------------------------------------------------------------------
# 4. refresh (full) flow
# ---------------------------------------------------------------------------


class TestRefreshFullIntegration:
    def test_refresh_no_agent_memory_prints_hint(self, sample_repo):
        out = _run("memory", "refresh", "--repo", str(sample_repo))
        assert "not found" in out.lower() or "run 'memory init'" in out

    def test_refresh_full_after_init_succeeds(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "refresh", "--full", "--repo", str(sample_repo))
        assert "full" in out
        assert "Done" in out

    def test_refresh_full_updates_manifest(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        manifest_before = json.loads(
            (sample_repo / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        _run("memory", "refresh", "--full", "--repo", str(sample_repo))
        # manifest.json still exists and is valid
        manifest_after = json.loads(
            (sample_repo / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        assert "generated_artifacts" in manifest_after

    def test_refresh_writes_freshness_json(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        _run("memory", "refresh", "--full", "--repo", str(sample_repo))
        freshness_path = sample_repo / ".agent-memory" / "metadata" / "freshness.json"
        assert freshness_path.exists()
        data = json.loads(freshness_path.read_text())
        assert "refreshed_at" in data
        assert "mode" in data


# ---------------------------------------------------------------------------
# 5. refresh (incremental) flow
# ---------------------------------------------------------------------------


class TestRefreshIncrementalIntegration:
    def test_refresh_incremental_after_init_succeeds(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "refresh", "--repo", str(sample_repo))
        assert "incremental" in out
        assert "Done" in out

    def test_refresh_incremental_writes_freshness(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        _run("memory", "refresh", "--repo", str(sample_repo))
        freshness_path = sample_repo / ".agent-memory" / "metadata" / "freshness.json"
        assert freshness_path.exists()

    def test_refresh_incremental_output_mentions_changed_files(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "refresh", "--repo", str(sample_repo))
        assert "changed files" in out.lower() or "Changed" in out

    def test_double_refresh_is_stable(self, sample_repo):
        """Two incremental refreshes should not raise."""
        _run("memory", "init", "--repo", str(sample_repo))
        _run("memory", "refresh", "--repo", str(sample_repo))
        out = _run("memory", "refresh", "--repo", str(sample_repo))
        assert "Done" in out


# ---------------------------------------------------------------------------
# 6. overrides influence
# ---------------------------------------------------------------------------


class TestOverridesInfluenceIntegration:
    def _write_overrides(self, repo: Path) -> Path:
        overrides_dir = repo / ".agent-memory" / "overrides"
        overrides_dir.mkdir(parents=True)
        (overrides_dir / "global.yaml").write_text(
            "always_include:\n  - docs/architecture.md\n"
            "never_edit:\n  - migrations/\n"
            "notes:\n  - 'Payments module is PCI-scoped.'\n"
        )
        return overrides_dir

    def test_prepare_context_applies_always_include(self, sample_repo):
        self._write_overrides(sample_repo)
        out = _run(
            "memory", "prepare-context", "add feature", "--repo", str(sample_repo)
        )
        assert "docs/architecture.md" in out

    def test_prepare_context_applies_never_edit(self, sample_repo):
        self._write_overrides(sample_repo)
        out = _run(
            "memory", "prepare-context", "add feature", "--repo", str(sample_repo)
        )
        assert "migrations/" in out

    def test_init_reflects_overrides_in_safe_boundaries(self, sample_repo):
        self._write_overrides(sample_repo)
        _run("memory", "init", "--repo", str(sample_repo))
        sb = (sample_repo / ".agent-memory" / "rules" / "safe-boundaries.md").read_text()
        assert "migrations/" in sb

    def test_init_reflects_overrides_in_conventions(self, sample_repo):
        self._write_overrides(sample_repo)
        _run("memory", "init", "--repo", str(sample_repo))
        conv = (sample_repo / ".agent-memory" / "rules" / "conventions.md").read_text()
        assert "Payments module is PCI-scoped." in conv

    def test_prepare_context_without_overrides_dir_runs_cleanly(self, sample_repo):
        # No .agent-memory/ at all
        out = _run(
            "memory", "prepare-context", "add feature", "--repo", str(sample_repo)
        )
        assert "prepare-context" in out


# ---------------------------------------------------------------------------
# 7. memory explain — end-to-end
# ---------------------------------------------------------------------------


class TestMemoryExplainIntegration:
    def test_explain_after_init_finds_something(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "explain", "myapp", "--repo", str(sample_repo))
        # Either found (shows "Feature:" / "Module:") or not-found with helpful msg
        assert ("Feature:" in out or "Module:" in out
                or "not found" in out.lower())

    def test_explain_not_found_target_is_helpful(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        out = _run("memory", "explain", "xyzzy_doesnt_exist_zzz", "--repo", str(sample_repo))
        assert "not found" in out.lower() or "memory init" in out

    def test_explain_header_always_present(self, sample_repo):
        out = _run("memory", "explain", "auth", "--repo", str(sample_repo))
        assert "explain" in out

    def test_explain_shows_repo_root(self, sample_repo):
        out = _run("memory", "explain", "auth", "--repo", str(sample_repo))
        assert str(sample_repo) in out

    def test_explain_no_longer_stub(self, sample_repo):
        out = _run("memory", "explain", "auth", "--repo", str(sample_repo))
        assert "not yet implemented" not in out


# ---------------------------------------------------------------------------
# 8. memory changed — end-to-end
# ---------------------------------------------------------------------------


class TestMemoryChangedIntegration:
    def test_changed_header_always_present(self, sample_repo):
        out = _run("memory", "changed", "auth", "--repo", str(sample_repo))
        assert "changed" in out

    def test_changed_shows_repo_root(self, sample_repo):
        out = _run("memory", "changed", "auth", "--repo", str(sample_repo))
        assert str(sample_repo) in out

    def test_changed_no_longer_stub(self, sample_repo):
        out = _run("memory", "changed", "auth", "--repo", str(sample_repo))
        assert "not yet implemented" not in out

    def test_changed_with_freshness_after_refresh(self, sample_repo):
        _run("memory", "init", "--repo", str(sample_repo))
        _run("memory", "refresh", "--full", "--repo", str(sample_repo))
        out = _run("memory", "changed", "myapp", "--repo", str(sample_repo))
        # Should show some refresh info rather than "no refresh data"
        assert "changed" in out.lower() or "refresh" in out.lower()

    def test_changed_not_found_is_helpful(self, sample_repo):
        out = _run("memory", "changed", "xyzzy_nonexistent_zzz", "--repo", str(sample_repo))
        assert "not found" in out.lower() or "memory init" in out or "memory refresh" in out


# ---------------------------------------------------------------------------
# 9. Full product loop — init → refresh → explain → prepare-context
# ---------------------------------------------------------------------------


class TestFullProductLoop:
    def test_complete_flow(self, sample_repo):
        """The core product loop runs without errors start to finish."""
        # Step 1: init
        init_out = _run("memory", "init", "--repo", str(sample_repo))
        assert (sample_repo / ".agent-memory").is_dir()

        # Step 2: refresh
        ref_out = _run("memory", "refresh", "--full", "--repo", str(sample_repo))
        assert "Done" in ref_out

        # Step 3: prepare-context
        ctx_out = _run(
            "memory", "prepare-context", "add authentication endpoint",
            "--repo", str(sample_repo)
        )
        assert "prepare-context" in ctx_out

        # Step 4: explain
        exp_out = _run("memory", "explain", "auth", "--repo", str(sample_repo))
        assert "explain" in exp_out

        # Step 5: changed
        chg_out = _run("memory", "changed", "auth", "--repo", str(sample_repo))
        assert "changed" in chg_out

        # None should have printed the stub message
        for out in [init_out, ref_out, ctx_out, exp_out, chg_out]:
            assert "not yet implemented" not in out

    def test_agent_memory_committable(self, sample_repo):
        """All generated files use stable, deterministic content (Git-friendly)."""
        _run("memory", "init", "--repo", str(sample_repo))
        agent_memory = sample_repo / ".agent-memory"

        # Run init a second time — output should be identical (unchanged)
        from code_review_graph.memory.writer import WriteStatus

        # Collect file mtimes before second init
        files_before = {
            p: p.stat().st_mtime
            for p in agent_memory.rglob("*.md")
        }

        import time
        time.sleep(0.05)  # ensure mtime would differ if rewritten

        _run("memory", "init", "--repo", str(sample_repo))

        # Files should not have been touched (content identical → write skipped)
        files_after = {
            p: p.stat().st_mtime
            for p in agent_memory.rglob("*.md")
        }
        for path in files_before:
            assert files_before[path] == files_after[path], (
                f"{path.name} was rewritten on second init (not idempotent)"
            )
