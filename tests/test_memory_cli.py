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
        assert "not yet implemented" in out

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


# ---------------------------------------------------------------------------
# memory changed
# ---------------------------------------------------------------------------


class TestMemoryChanged:
    def test_runs_with_target(self, tmp_path):
        out = run_cli_stdout("memory", "changed", "src/auth", "--repo", str(tmp_path))
        assert "changed" in out
        assert "src/auth" in out
        assert "not yet implemented" in out

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
        assert "not yet implemented" in out

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
