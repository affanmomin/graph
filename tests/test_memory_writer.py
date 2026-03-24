"""Tests for code_review_graph.memory.writer (Ticket 3).

Covers:
- ensure_memory_dirs: directory tree creation and idempotency
- write_text_if_changed: create / update / unchanged detection
- write_json_if_changed: JSON serialisation, sort stability, unchanged detection
- write_override_if_absent: never overwrites existing file
- render_markdown_section: output format and edge cases
- atomic write: no .tmp files left behind
- _ensure_trailing_newline: normalisation behaviour
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_review_graph.memory.writer import (
    MEMORY_SUBDIRS,
    ensure_memory_dirs,
    render_markdown_section,
    write_json_if_changed,
    write_override_if_absent,
    write_text_if_changed,
    _ensure_trailing_newline,
)


# ---------------------------------------------------------------------------
# ensure_memory_dirs
# ---------------------------------------------------------------------------


class TestEnsureMemoryDirs:
    def test_creates_root(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        assert dirs["root"].is_dir()
        assert dirs["root"] == tmp_path / ".agent-memory"

    def test_creates_all_subdirs(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        for name in MEMORY_SUBDIRS:
            assert name in dirs
            assert dirs[name].is_dir(), f"missing subdir: {name}"

    def test_expected_subdirs_present(self, tmp_path):
        ensure_memory_dirs(tmp_path)
        root = tmp_path / ".agent-memory"
        for name in ("features", "modules", "tasks", "changes", "rules", "overrides", "metadata"):
            assert (root / name).is_dir()

    def test_idempotent(self, tmp_path):
        """Calling twice must not raise and must not disturb existing content."""
        dirs1 = ensure_memory_dirs(tmp_path)
        # Write a file in one of the dirs
        sentinel = dirs1["features"] / "sentinel.md"
        sentinel.write_text("keep me\n")
        # Call again
        dirs2 = ensure_memory_dirs(tmp_path)
        assert sentinel.read_text() == "keep me\n"
        assert dirs1["root"] == dirs2["root"]

    def test_returns_path_dict(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        assert isinstance(dirs, dict)
        assert "root" in dirs
        for name in MEMORY_SUBDIRS:
            assert name in dirs
            assert isinstance(dirs[name], Path)

    def test_nested_repo_root(self, tmp_path):
        """Works when repo_root itself does not yet exist (mkdir parents)."""
        deep = tmp_path / "a" / "b" / "repo"
        deep.mkdir(parents=True)
        dirs = ensure_memory_dirs(deep)
        assert dirs["root"].is_dir()


# ---------------------------------------------------------------------------
# write_text_if_changed
# ---------------------------------------------------------------------------


class TestWriteTextIfChanged:
    def test_creates_new_file(self, tmp_path):
        path = tmp_path / ".agent-memory" / "repo.md"
        path.parent.mkdir(parents=True)
        status = write_text_if_changed(path, "# Repo\n\nHello.")
        assert status == "created"
        assert path.exists()

    def test_content_is_correct(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "hello world")
        assert path.read_text(encoding="utf-8") == "hello world\n"

    def test_trailing_newline_normalised(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "no newline")
        assert path.read_text(encoding="utf-8").endswith("\n")

    def test_multiple_trailing_newlines_collapsed(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "text\n\n\n\n")
        assert path.read_text(encoding="utf-8") == "text\n"

    def test_unchanged_returns_unchanged(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "content")
        status = write_text_if_changed(path, "content")
        assert status == "unchanged"

    def test_unchanged_does_not_rewrite(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "content")
        mtime_before = path.stat().st_mtime_ns
        write_text_if_changed(path, "content")
        assert path.stat().st_mtime_ns == mtime_before

    def test_changed_content_returns_updated(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "version 1")
        status = write_text_if_changed(path, "version 2")
        assert status == "updated"

    def test_changed_content_updates_file(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "version 1")
        write_text_if_changed(path, "version 2")
        assert "version 2" in path.read_text(encoding="utf-8")

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "file.md"
        status = write_text_if_changed(path, "hello")
        assert status == "created"
        assert path.exists()

    def test_no_tmp_file_left_behind(self, tmp_path):
        path = tmp_path / "out.md"
        write_text_if_changed(path, "data")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_unicode_content(self, tmp_path):
        path = tmp_path / "out.md"
        content = "# こんにちは\n\nEmoji: 🚀"
        write_text_if_changed(path, content)
        assert "こんにちは" in path.read_text(encoding="utf-8")
        assert "🚀" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# write_json_if_changed
# ---------------------------------------------------------------------------


class TestWriteJsonIfChanged:
    def test_creates_new_file(self, tmp_path):
        path = tmp_path / "manifest.json"
        status = write_json_if_changed(path, {"a": 1})
        assert status == "created"
        assert path.exists()

    def test_content_is_valid_json(self, tmp_path):
        path = tmp_path / "manifest.json"
        write_json_if_changed(path, {"key": "value", "num": 42})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == {"key": "value", "num": 42}

    def test_keys_are_sorted(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"z": 3, "a": 1, "m": 2})
        raw = path.read_text(encoding="utf-8")
        # "a" must appear before "m" must appear before "z"
        assert raw.index('"a"') < raw.index('"m"') < raw.index('"z"')

    def test_nested_keys_are_sorted(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"outer": {"z": 1, "a": 2}})
        raw = path.read_text(encoding="utf-8")
        assert raw.index('"a"') < raw.index('"z"')

    def test_same_data_same_bytes(self, tmp_path):
        """Serialising the same dict twice must produce identical bytes."""
        path1 = tmp_path / "a.json"
        path2 = tmp_path / "b.json"
        data = {"version": "1", "languages": ["python", "js"], "count": 3}
        write_json_if_changed(path1, data)
        write_json_if_changed(path2, data)
        assert path1.read_bytes() == path2.read_bytes()

    def test_unchanged_returns_unchanged(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"x": 1})
        status = write_json_if_changed(path, {"x": 1})
        assert status == "unchanged"

    def test_unchanged_does_not_rewrite(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"x": 1})
        mtime_before = path.stat().st_mtime_ns
        write_json_if_changed(path, {"x": 1})
        assert path.stat().st_mtime_ns == mtime_before

    def test_changed_data_returns_updated(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"x": 1})
        status = write_json_if_changed(path, {"x": 2})
        assert status == "updated"

    def test_trailing_newline(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"k": "v"})
        assert path.read_text(encoding="utf-8").endswith("\n")

    def test_no_tmp_file_left_behind(self, tmp_path):
        path = tmp_path / "data.json"
        write_json_if_changed(path, {"k": "v"})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_list_values_preserved(self, tmp_path):
        path = tmp_path / "data.json"
        data = {"items": ["b", "a", "c"]}  # list order must be preserved
        write_json_if_changed(path, data)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["items"] == ["b", "a", "c"]

    def test_key_insertion_order_irrelevant(self, tmp_path):
        """Dict insertion order must not affect output — sorted_keys wins."""
        path1 = tmp_path / "a.json"
        path2 = tmp_path / "b.json"
        write_json_if_changed(path1, {"b": 2, "a": 1})
        write_json_if_changed(path2, {"a": 1, "b": 2})
        assert path1.read_bytes() == path2.read_bytes()


# ---------------------------------------------------------------------------
# write_override_if_absent
# ---------------------------------------------------------------------------


class TestWriteOverrideIfAbsent:
    def test_creates_when_absent(self, tmp_path):
        path = tmp_path / "overrides" / "rules.yaml"
        path.parent.mkdir(parents=True)
        status = write_override_if_absent(path, "always_include: []\n")
        assert status == "created"
        assert path.exists()

    def test_does_not_overwrite_existing(self, tmp_path):
        path = tmp_path / "overrides" / "rules.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("human content\n")
        status = write_override_if_absent(path, "new content\n")
        assert status == "unchanged"
        assert path.read_text() == "human content\n"

    def test_returns_unchanged_for_existing(self, tmp_path):
        path = tmp_path / "rules.yaml"
        path.write_text("existing\n")
        assert write_override_if_absent(path, "different") == "unchanged"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "overrides" / "rules.yaml"
        status = write_override_if_absent(path, "# overrides\n")
        assert status == "created"
        assert path.exists()


# ---------------------------------------------------------------------------
# render_markdown_section
# ---------------------------------------------------------------------------


class TestRenderMarkdownSection:
    def test_default_level_two(self):
        out = render_markdown_section("Overview", "Some text.")
        assert out.startswith("## Overview")

    def test_body_follows_heading(self):
        out = render_markdown_section("Title", "Body text.")
        assert "## Title\n\nBody text." == out

    def test_level_one(self):
        out = render_markdown_section("Title", "body", level=1)
        assert out.startswith("# Title")

    def test_level_three(self):
        out = render_markdown_section("Sub", "body", level=3)
        assert out.startswith("### Sub")

    def test_body_whitespace_stripped(self):
        out = render_markdown_section("T", "  padded  ")
        assert out.endswith("padded")

    def test_multiline_body(self):
        body = "Line one.\nLine two."
        out = render_markdown_section("T", body)
        assert "Line one." in out
        assert "Line two." in out

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError):
            render_markdown_section("T", "b", level=0)
        with pytest.raises(ValueError):
            render_markdown_section("T", "b", level=7)


# ---------------------------------------------------------------------------
# _ensure_trailing_newline (internal — tested for correctness)
# ---------------------------------------------------------------------------


class TestEnsureTrailingNewline:
    def test_adds_newline_when_missing(self):
        assert _ensure_trailing_newline("text") == "text\n"

    def test_keeps_single_newline(self):
        assert _ensure_trailing_newline("text\n") == "text\n"

    def test_collapses_multiple_newlines(self):
        assert _ensure_trailing_newline("text\n\n\n") == "text\n"

    def test_empty_string(self):
        assert _ensure_trailing_newline("") == "\n"


# ---------------------------------------------------------------------------
# Integration: full .agent-memory/ tree
# ---------------------------------------------------------------------------


class TestFullMemoryTreeIntegration:
    def test_write_multiple_artifact_types(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)

        # Markdown artifact
        s1 = write_text_if_changed(dirs["features"] / "auth.md", "# Auth\n\nHandles login.")
        assert s1 == "created"

        # JSON metadata
        s2 = write_json_if_changed(
            dirs["metadata"] / "manifest.json",
            {"version": "1", "artifact_count": 1},
        )
        assert s2 == "created"

        # Override scaffold
        s3 = write_override_if_absent(
            dirs["overrides"] / "rules.yaml",
            "always_include: []\nnever_edit: []\n",
        )
        assert s3 == "created"

        # Second pass: everything unchanged
        s4 = write_text_if_changed(dirs["features"] / "auth.md", "# Auth\n\nHandles login.")
        s5 = write_json_if_changed(
            dirs["metadata"] / "manifest.json",
            {"version": "1", "artifact_count": 1},
        )
        assert s4 == "unchanged"
        assert s5 == "unchanged"

    def test_no_tmp_files_after_full_run(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        write_text_if_changed(dirs["modules"] / "core.md", "# Core")
        write_json_if_changed(dirs["metadata"] / "freshness.json", {"stale": False})
        tmp_files = list((tmp_path / ".agent-memory").rglob("*.tmp"))
        assert tmp_files == []

    def test_all_subdirs_accessible(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        for name in MEMORY_SUBDIRS:
            test_file = dirs[name] / f"test_{name}.txt"
            status = write_text_if_changed(test_file, f"content for {name}")
            assert status == "created"
            assert test_file.exists()
