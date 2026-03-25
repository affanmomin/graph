"""Tests for the ``memory`` CLI command group (Ticket 2).

Verifies:
- the ``memory`` subparser is registered and reachable
- all six subcommands parse arguments correctly
- required arguments are validated (missing args → error)
- placeholder output is sensible
- existing CLI commands are not broken
- ``--help`` text is reachable without errors
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def run_cli(*argv: str, expect_exit: int | None = 0) -> str:
    """Invoke ``main()`` with *argv* and return stdout as a string.

    If *expect_exit* is not None, assert that SystemExit is raised with that
    code. Pass ``expect_exit=None`` to allow any exit code (e.g. for --help
    which exits with 0 via argparse).
    """
    from code_review_graph.cli import main

    buf = StringIO()
    with patch("sys.argv", ["code-review-graph", *argv]):
        with patch("sys.stdout", buf):
            if expect_exit is not None:
                with pytest.raises(SystemExit) as exc:
                    main()
                assert exc.value.code == expect_exit, (
                    f"Expected exit {expect_exit}, got {exc.value.code}\n"
                    f"stdout: {buf.getvalue()}"
                )
            else:
                try:
                    main()
                except SystemExit:
                    pass
    return buf.getvalue()


def run_cli_stdout(*argv: str) -> str:
    """Invoke ``main()`` and capture stdout; ignore SystemExit."""
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
# memory namespace is registered
# ---------------------------------------------------------------------------


class TestMemoryNamespaceRegistered:
    def test_memory_no_subcommand_prints_help(self):
        out = run_cli_stdout("memory")
        assert "memory" in out.lower()
        assert "init" in out
        assert "refresh" in out
        assert "explain" in out
        assert "prepare-context" in out
        assert "changed" in out
        assert "annotate" in out

    def test_memory_help_flag(self):
        out = run_cli_stdout("memory", "--help")
        # argparse --help prints to stdout and exits 0
        assert "memory" in out.lower() or "init" in out


# ---------------------------------------------------------------------------
# memory init
# ---------------------------------------------------------------------------


class TestMemoryInit:
    def test_runs_without_error(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert "init" in out
        assert "repo-memory" in out

    def test_shows_repo_root(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert str(tmp_path) in out

    def test_shows_target_folder(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert ".agent-memory" in out

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "init", "--help")
        assert "init" in out or "agent-memory" in out.lower()


# ---------------------------------------------------------------------------
# memory refresh
# ---------------------------------------------------------------------------


class TestMemoryRefresh:
    def test_runs_incremental_by_default(self, tmp_path):
        out = run_cli_stdout("memory", "refresh", "--repo", str(tmp_path))
        assert "incremental" in out
        # Command is now implemented; no longer prints stub message
        assert "not yet implemented" not in out

    def test_full_flag(self, tmp_path):
        out = run_cli_stdout("memory", "refresh", "--full", "--repo", str(tmp_path))
        assert "full" in out

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "refresh", "--help")
        assert "refresh" in out or "--full" in out


# ---------------------------------------------------------------------------
# memory explain
# ---------------------------------------------------------------------------


class TestMemoryExplain:
    def test_runs_with_target(self, tmp_path):
        out = run_cli_stdout("memory", "explain", "authentication", "--repo", str(tmp_path))
        assert "explain" in out
        assert "authentication" in out
        assert "not yet implemented" not in out

    def test_shows_target_in_output(self, tmp_path):
        out = run_cli_stdout("memory", "explain", "src/api/routes.py", "--repo", str(tmp_path))
        assert "src/api/routes.py" in out

    def test_missing_target_exits_nonzero(self):
        buf = StringIO()
        with patch("sys.argv", ["code-review-graph", "memory", "explain"]):
            with patch("sys.stdout", buf):
                with patch("sys.stderr", StringIO()):
                    with pytest.raises(SystemExit) as exc:
                        from code_review_graph.cli import main
                        main()
        assert exc.value.code != 0

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "explain", "--help")
        assert "explain" in out or "target" in out.lower()


# ---------------------------------------------------------------------------
# memory prepare-context
# ---------------------------------------------------------------------------


class TestMemoryPrepareContext:
    def test_runs_with_task(self, tmp_path):
        out = run_cli_stdout(
            "memory", "prepare-context", "add rate limiting", "--repo", str(tmp_path)
        )
        assert "prepare-context" in out
        assert "add rate limiting" in out

    def test_shows_task_in_output(self, tmp_path):
        task = "fix the payment webhook handler"
        out = run_cli_stdout("memory", "prepare-context", task, "--repo", str(tmp_path))
        assert task in out

    def test_empty_task_exits_nonzero(self, tmp_path):
        buf = StringIO()
        with patch("sys.argv", [
            "code-review-graph", "memory", "prepare-context", "   ",
            "--repo", str(tmp_path),
        ]):
            with patch("sys.stdout", buf):
                with pytest.raises(SystemExit) as exc:
                    from code_review_graph.cli import main
                    main()
        assert exc.value.code != 0

    def test_missing_task_exits_nonzero(self):
        buf = StringIO()
        with patch("sys.argv", ["code-review-graph", "memory", "prepare-context"]):
            with patch("sys.stdout", buf):
                with patch("sys.stderr", StringIO()):
                    with pytest.raises(SystemExit) as exc:
                        from code_review_graph.cli import main
                        main()
        assert exc.value.code != 0

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "prepare-context", "--help")
        assert "context" in out.lower() or "task" in out.lower()

    def test_overrides_applied_when_present(self, tmp_path):
        """Overrides from .agent-memory/overrides/ influence prepare-context output."""
        overrides_dir = tmp_path / ".agent-memory" / "overrides"
        overrides_dir.mkdir(parents=True)
        (overrides_dir / "global.yaml").write_text(
            "always_include:\n  - docs/architecture.md\n"
            "never_edit:\n  - migrations/\n"
        )
        out = run_cli_stdout(
            "memory", "prepare-context", "add a feature", "--repo", str(tmp_path)
        )
        assert "docs/architecture.md" in out
        assert "migrations/" in out

    def test_prepare_context_no_overrides_dir(self, tmp_path):
        """prepare-context runs cleanly when .agent-memory/ does not exist."""
        out = run_cli_stdout(
            "memory", "prepare-context", "add a feature", "--repo", str(tmp_path)
        )
        assert "prepare-context" in out


# ---------------------------------------------------------------------------
# memory init — rule doc generation
# ---------------------------------------------------------------------------


class TestMemoryInitRuleDocs:
    def test_init_writes_conventions_md(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert (tmp_path / ".agent-memory" / "rules" / "conventions.md").exists()

    def test_init_writes_safe_boundaries_md(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert (tmp_path / ".agent-memory" / "rules" / "safe-boundaries.md").exists()

    def test_init_output_mentions_rule_docs(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert "conventions.md" in out
        assert "safe-boundaries.md" in out

    def test_init_applies_overrides_to_rule_docs(self, tmp_path):
        """Overrides already in place when init runs are reflected in rule docs."""
        overrides_dir = tmp_path / ".agent-memory" / "overrides"
        overrides_dir.mkdir(parents=True)
        (overrides_dir / "global.yaml").write_text(
            "never_edit:\n  - legacy/\n"
            "notes:\n  - 'Legacy module is frozen.'\n"
        )
        run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        sb = (tmp_path / ".agent-memory" / "rules" / "safe-boundaries.md").read_text()
        assert "legacy/" in sb
        conv = (tmp_path / ".agent-memory" / "rules" / "conventions.md").read_text()
        assert "Legacy module is frozen." in conv


# ---------------------------------------------------------------------------
# memory changed
# ---------------------------------------------------------------------------


class TestMemoryChanged:
    def test_runs_with_target(self, tmp_path):
        out = run_cli_stdout("memory", "changed", "src/auth", "--repo", str(tmp_path))
        assert "changed" in out
        assert "src/auth" in out
        assert "not yet implemented" not in out

    def test_missing_target_exits_nonzero(self):
        buf = StringIO()
        with patch("sys.argv", ["code-review-graph", "memory", "changed"]):
            with patch("sys.stdout", buf):
                with patch("sys.stderr", StringIO()):
                    with pytest.raises(SystemExit) as exc:
                        from code_review_graph.cli import main
                        main()
        assert exc.value.code != 0

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "changed", "--help")
        assert "changed" in out or "target" in out.lower()


# ---------------------------------------------------------------------------
# memory annotate
# ---------------------------------------------------------------------------


class TestMemoryAnnotate:
    def test_runs_without_error(self, tmp_path):
        out = run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert "annotate" in out
        # Stub message has been replaced with real implementation
        assert "not yet implemented" not in out

    def test_shows_overrides_dir(self, tmp_path):
        out = run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert "overrides" in out

    def test_help_reachable(self):
        out = run_cli_stdout("memory", "annotate", "--help")
        assert "annotate" in out or "override" in out.lower()


# ---------------------------------------------------------------------------
# Existing commands are not broken
# ---------------------------------------------------------------------------


class TestExistingCommandsUnaffected:
    def test_no_command_prints_banner(self):
        out = run_cli_stdout()
        # Banner includes known commands
        assert "build" in out
        assert "serve" in out
        assert "status" in out

    def test_version_flag(self):
        out = run_cli_stdout("--version")
        assert "code-review-graph" in out

    def test_build_help(self):
        out = run_cli_stdout("build", "--help")
        assert "build" in out

    def test_update_help(self):
        out = run_cli_stdout("update", "--help")
        assert "update" in out

    def test_status_help(self):
        out = run_cli_stdout("status", "--help")
        assert "status" in out

    def test_watch_help(self):
        out = run_cli_stdout("watch", "--help")
        assert "watch" in out

    def test_serve_help(self):
        out = run_cli_stdout("serve", "--help")
        assert "serve" in out

    def test_visualize_help(self):
        out = run_cli_stdout("visualize", "--help")
        assert "visualize" in out

    def test_install_help(self):
        out = run_cli_stdout("install", "--help")
        assert "install" in out


# ---------------------------------------------------------------------------
# Fix 1: CLI memory init generates rules files and CLAUDE.md
# ---------------------------------------------------------------------------


def _make_minimal_repo(tmp_path):
    files = {
        "pyproject.toml": "[project]\nname='testrepo'",
        "src/auth/__init__.py": "",
        "src/auth/login.py": "def login(): pass",
        "tests/test_auth.py": "def test_login(): pass",
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


class TestMemoryInitArtifacts:
    """CLI memory init must produce rules/* and CLAUDE.md."""

    def test_generates_conventions_md(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert (tmp_path / ".agent-memory" / "rules" / "conventions.md").exists()

    def test_generates_safe_boundaries_md(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert (tmp_path / ".agent-memory" / "rules" / "safe-boundaries.md").exists()

    def test_generates_claude_md(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert (tmp_path / ".agent-memory" / "CLAUDE.md").exists()

    def test_claude_md_has_content(self, tmp_path):
        run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        content = (tmp_path / ".agent-memory" / "CLAUDE.md").read_text()
        assert len(content) > 50
        assert "Repo memory" in content

    def test_output_mentions_claude_md(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert "CLAUDE.md" in out

    def test_output_mentions_conventions(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert "conventions" in out

    def test_output_mentions_safe_boundaries(self, tmp_path):
        out = run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        assert "safe-boundaries" in out

    def test_idempotent_second_run(self, tmp_path):
        """Running init twice must not crash and second run should show unchanged."""
        run_cli_stdout("memory", "init", "--repo", str(_make_minimal_repo(tmp_path)))
        out2 = run_cli_stdout("memory", "init", "--repo", str(tmp_path))
        assert "unchanged" in out2


# ---------------------------------------------------------------------------
# Fix 3: memory annotate implementation
# ---------------------------------------------------------------------------


class TestMemoryAnnotateImplemented:
    """memory annotate must scaffold global.yaml and respect existing edits."""

    def test_creates_global_yaml(self, tmp_path):
        run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert (tmp_path / ".agent-memory" / "overrides" / "global.yaml").exists()

    def test_global_yaml_has_template_content(self, tmp_path):
        run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        content = (tmp_path / ".agent-memory" / "overrides" / "global.yaml").read_text()
        assert "always_include" in content
        assert "never_edit" in content
        assert "task_hints" in content

    def test_does_not_overwrite_existing_edits(self, tmp_path):
        """Running annotate twice must not overwrite user edits."""
        override_path = tmp_path / ".agent-memory" / "overrides" / "global.yaml"
        override_path.parent.mkdir(parents=True, exist_ok=True)
        override_path.write_text("never_edit:\n  - migrations/\n", encoding="utf-8")

        run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))

        content = override_path.read_text()
        assert "migrations/" in content  # human edit preserved
        # Template content should NOT have been written (file already existed)
        assert "always_include" not in content

    def test_output_includes_file_path(self, tmp_path):
        out = run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert "global.yaml" in out

    def test_output_says_created_on_first_run(self, tmp_path):
        out = run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert "Created" in out or "created" in out

    def test_output_says_preserved_on_second_run(self, tmp_path):
        run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        out2 = run_cli_stdout("memory", "annotate", "--repo", str(tmp_path))
        assert "preserved" in out2 or "already exists" in out2.lower()


# ---------------------------------------------------------------------------
# Fix 4: changes/recent.md surfaced in memory changed
# ---------------------------------------------------------------------------


class TestChangedSurfacesRecentMd:
    """memory changed must incorporate changes/recent.md when it exists."""

    def test_includes_recent_md_content_for_matching_area(self, tmp_path):
        from code_review_graph.memory.lookup import changed_match, match_target
        from code_review_graph.memory.scanner import scan_repo
        from code_review_graph.memory.classifier import classify_features, classify_modules

        repo = _make_minimal_repo(tmp_path)
        agent_memory = repo / ".agent-memory"
        changes_dir = agent_memory / "changes"
        changes_dir.mkdir(parents=True, exist_ok=True)
        (changes_dir / "recent.md").write_text(
            "## Recent changes\n- auth/login.py: fixed token expiry bug\n",
            encoding="utf-8",
        )

        scan = scan_repo(repo)
        features = classify_features(repo, scan)
        modules = classify_modules(repo, scan)
        match = match_target("auth", agent_memory, features, modules)
        output = changed_match(match, agent_memory)
        assert "recent.md" in output.lower() or "login.py" in output or "token" in output

    def test_no_crash_when_recent_md_absent(self, tmp_path):
        from code_review_graph.memory.lookup import changed_match, match_target
        from code_review_graph.memory.scanner import scan_repo
        from code_review_graph.memory.classifier import classify_features, classify_modules

        repo = _make_minimal_repo(tmp_path)
        agent_memory = repo / ".agent-memory"
        agent_memory.mkdir(parents=True, exist_ok=True)

        scan = scan_repo(repo)
        features = classify_features(repo, scan)
        modules = classify_modules(repo, scan)
        match = match_target("auth", agent_memory, features, modules)
        # Must not raise even without recent.md or freshness.json
        output = changed_match(match, agent_memory)
        assert isinstance(output, str)
