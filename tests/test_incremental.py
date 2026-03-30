"""Tests for the incremental graph update module."""

import subprocess
from unittest.mock import MagicMock, patch

from code_review_graph.graph import GraphStore
from code_review_graph.incremental import (
    _extra_skip_dirs,
    _is_binary,
    _load_ignore_patterns,
    _should_ignore,
    _WALK_SKIP_DIRS,
    collect_all_files,
    find_project_root,
    find_repo_root,
    full_build,
    get_all_tracked_files,
    get_changed_files,
    get_db_path,
    get_staged_and_unstaged,
    incremental_update,
)


class TestFindRepoRoot:
    def test_finds_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert find_repo_root(tmp_path) == tmp_path

    def test_finds_parent_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        assert find_repo_root(sub) == tmp_path

    def test_returns_none_without_git(self, tmp_path):
        sub = tmp_path / "no_git"
        sub.mkdir()
        assert find_repo_root(sub) is None


class TestFindProjectRoot:
    def test_returns_git_root(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert find_project_root(tmp_path) == tmp_path

    def test_falls_back_to_start(self, tmp_path):
        sub = tmp_path / "no_git"
        sub.mkdir()
        assert find_project_root(sub) == sub


class TestGetDbPath:
    def test_creates_directory_and_db_path(self, tmp_path):
        db_path = get_db_path(tmp_path)
        assert db_path == tmp_path / ".code-review-graph" / "graph.db"
        assert (tmp_path / ".code-review-graph").is_dir()

    def test_creates_gitignore(self, tmp_path):
        get_db_path(tmp_path)
        gi = tmp_path / ".code-review-graph" / ".gitignore"
        assert gi.exists()
        assert "*\n" in gi.read_text()

    def test_migrates_legacy_db(self, tmp_path):
        legacy = tmp_path / ".code-review-graph.db"
        legacy.write_text("legacy data")
        db_path = get_db_path(tmp_path)
        assert db_path.exists()
        assert not legacy.exists()
        assert db_path.read_text() == "legacy data"

    def test_cleans_legacy_side_files(self, tmp_path):
        legacy = tmp_path / ".code-review-graph.db"
        legacy.write_text("data")
        for suffix in ("-wal", "-shm", "-journal"):
            (tmp_path / f".code-review-graph.db{suffix}").write_text("side")
        get_db_path(tmp_path)
        for suffix in ("-wal", "-shm", "-journal"):
            assert not (tmp_path / f".code-review-graph.db{suffix}").exists()


class TestIgnorePatterns:
    def test_default_patterns_loaded(self, tmp_path):
        patterns = _load_ignore_patterns(tmp_path)
        assert "node_modules/**" in patterns
        assert ".git/**" in patterns
        assert "__pycache__/**" in patterns

    def test_custom_ignore_file(self, tmp_path):
        ignore = tmp_path / ".code-review-graphignore"
        ignore.write_text("custom/**\n# comment\n\nvendor/**\n")
        patterns = _load_ignore_patterns(tmp_path)
        assert "custom/**" in patterns
        assert "vendor/**" in patterns
        # Comments and blanks should be skipped
        assert "# comment" not in patterns
        assert "" not in patterns

    def test_repomindignore_loaded(self, tmp_path):
        """User-facing .repomindignore file is recognised."""
        (tmp_path / ".repomindignore").write_text("my-venv/**\n")
        patterns = _load_ignore_patterns(tmp_path)
        assert "my-venv/**" in patterns

    def test_both_ignore_files_merged(self, tmp_path):
        """Patterns from both ignore files are combined."""
        (tmp_path / ".repomindignore").write_text("from-repomind/**\n")
        (tmp_path / ".code-review-graphignore").write_text("from-crg/**\n")
        patterns = _load_ignore_patterns(tmp_path)
        assert "from-repomind/**" in patterns
        assert "from-crg/**" in patterns

    def test_should_ignore_matches(self):
        patterns = ["node_modules/**", "*.pyc", ".git/**"]
        assert _should_ignore("node_modules/foo/bar.js", patterns)
        assert _should_ignore("test.pyc", patterns)
        assert _should_ignore(".git/HEAD", patterns)
        assert not _should_ignore("src/main.py", patterns)


class TestExtraSkipDirs:
    def test_extracts_single_component(self):
        dirs = _extra_skip_dirs(["my-venv/**", "dist/**", "*.min.js"])
        assert "my-venv" in dirs
        assert "dist" in dirs
        # Glob-only patterns should not produce directory names
        assert "*.min.js" not in dirs

    def test_does_not_extract_multi_component(self):
        """Multi-component paths like BE/src/generated/** must not be pruned at root."""
        dirs = _extra_skip_dirs(["BE/src/generated/**"])
        assert "BE" not in dirs

    def test_empty_patterns(self):
        assert _extra_skip_dirs([]) == frozenset()


class TestWalkSkipDirs:
    def test_contains_node_modules(self):
        assert "node_modules" in _WALK_SKIP_DIRS

    def test_contains_venv(self):
        assert ".venv" in _WALK_SKIP_DIRS and "venv" in _WALK_SKIP_DIRS


class TestCollectAllFilesWalk:
    """collect_all_files fallback walk must prune ignored directories."""

    def test_does_not_enter_node_modules(self, tmp_path):
        """node_modules is never entered even without a git repo."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        nm = tmp_path / "node_modules" / "react"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")

        files = collect_all_files(tmp_path)
        assert any("main.py" in f for f in files)
        assert not any("node_modules" in f for f in files)

    def test_does_not_enter_nested_node_modules(self, tmp_path):
        """node_modules inside a subdirectory is also pruned."""
        (tmp_path / "BE" / "src").mkdir(parents=True)
        (tmp_path / "BE" / "src" / "app.ts").write_text("const x = 1")
        nm = tmp_path / "BE" / "node_modules" / "express"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")

        files = collect_all_files(tmp_path)
        assert any("app.ts" in f for f in files)
        assert not any("node_modules" in f for f in files)

    def test_respects_repomindignore_directory_exclusion(self, tmp_path):
        """Directories listed in .repomindignore are excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1")
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "model.py").write_text("# auto-generated")
        (tmp_path / ".repomindignore").write_text("generated/**\n")

        files = collect_all_files(tmp_path)
        assert any("main.py" in f for f in files)
        assert not any("generated" in f for f in files)


class TestIsBinary:
    def test_text_file_is_not_binary(self, tmp_path):
        f = tmp_path / "text.py"
        f.write_text("print('hello')\n")
        assert not _is_binary(f)

    def test_binary_file_is_binary(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"header\x00binary data")
        assert _is_binary(f)

    def test_missing_file_is_binary(self, tmp_path):
        f = tmp_path / "missing.txt"
        assert _is_binary(f)


class TestGitOperations:
    @patch("code_review_graph.incremental.subprocess.run")
    def test_get_changed_files(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="src/a.py\nsrc/b.py\n",
        )
        result = get_changed_files(tmp_path)
        assert result == ["src/a.py", "src/b.py"]
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert "git" in call_args[0][0]
        assert call_args[1].get("timeout") == 30

    @patch("code_review_graph.incremental.subprocess.run")
    def test_get_changed_files_fallback(self, mock_run, tmp_path):
        # First call fails, second succeeds
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout=""),
            MagicMock(returncode=0, stdout="staged.py\n"),
        ]
        result = get_changed_files(tmp_path)
        assert result == ["staged.py"]
        assert mock_run.call_count == 2

    @patch("code_review_graph.incremental.subprocess.run")
    def test_get_changed_files_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired("git", 30)
        result = get_changed_files(tmp_path)
        assert result == []

    @patch("code_review_graph.incremental.subprocess.run")
    def test_get_staged_and_unstaged(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/a.py\n?? new.py\nR  old.py -> new_name.py\n",
        )
        result = get_staged_and_unstaged(tmp_path)
        assert "src/a.py" in result
        assert "new.py" in result
        assert "new_name.py" in result
        # old.py should NOT be in results (renamed away)
        assert "old.py" not in result

    @patch("code_review_graph.incremental.subprocess.run")
    def test_get_all_tracked_files(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="a.py\nb.py\nc.go\n",
        )
        result = get_all_tracked_files(tmp_path)
        assert result == ["a.py", "b.py", "c.go"]


class TestFullBuild:
    def test_full_build_parses_files(self, tmp_path):
        # Create a simple Python file
        py_file = tmp_path / "sample.py"
        py_file.write_text("def hello():\n    pass\n")
        (tmp_path / ".git").mkdir()

        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)
        try:
            mock_target = "code_review_graph.incremental.get_all_tracked_files"
            with patch(mock_target, return_value=["sample.py"]):
                result = full_build(tmp_path, store)
            assert result["files_parsed"] == 1
            assert result["total_nodes"] > 0
            assert result["errors"] == []
            assert store.get_metadata("last_build_type") == "full"
        finally:
            store.close()


class TestIncrementalUpdate:
    def test_incremental_with_no_changes(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)
        try:
            result = incremental_update(tmp_path, store, changed_files=[])
            assert result["files_updated"] == 0
        finally:
            store.close()

    def test_incremental_with_changed_file(self, tmp_path):
        py_file = tmp_path / "mod.py"
        py_file.write_text("def greet():\n    return 'hi'\n")

        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)
        try:
            result = incremental_update(
                tmp_path, store, changed_files=["mod.py"]
            )
            assert result["files_updated"] >= 1
            assert result["total_nodes"] > 0
        finally:
            store.close()

    def test_incremental_deleted_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        store = GraphStore(db_path)
        try:
            # Pre-populate with a file
            py_file = tmp_path / "old.py"
            py_file.write_text("x = 1\n")
            result = incremental_update(tmp_path, store, changed_files=["old.py"])
            assert result["total_nodes"] > 0

            # Now delete the file and run incremental
            py_file.unlink()
            incremental_update(tmp_path, store, changed_files=["old.py"])
            # File should have been removed from graph
            nodes = store.get_nodes_by_file(str(tmp_path / "old.py"))
            assert len(nodes) == 0
        finally:
            store.close()
