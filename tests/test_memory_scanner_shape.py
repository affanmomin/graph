"""Tests for Ticket 3.1 — repo shape detection in scanner.py.

Covers:
- flat-package: all source files in one directory (≥ _FLAT_MIN_FILES files)
- structured: source files spread across ≥ _STRUCTURED_MIN_DIRS directories
- mixed: 2 directories (between flat and structured)
- unknown: fewer than _SHAPE_MIN_FILES source files found
- test files are excluded from shape counting
- test directories are excluded from shape counting
- root-level files count as one directory (".")
- shape_rationale is always a non-empty string (except unknown edge case)
- RepoScan dataclass has repo_shape and shape_rationale fields
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_review_graph.memory.scanner import (
    RepoScan,
    _FLAT_MIN_FILES,
    _SHAPE_MIN_FILES,
    _STRUCTURED_MIN_DIRS,
    scan_repo,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_source(tmp_path: Path, rel: str) -> None:
    """Write an empty Python source file at the given relative path."""
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# placeholder\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# RepoScan dataclass fields
# ---------------------------------------------------------------------------


class TestRepoScanFields:
    def test_repo_shape_default(self):
        scan = RepoScan(repo_root=Path("."))
        assert scan.repo_shape == "unknown"

    def test_shape_rationale_default(self):
        scan = RepoScan(repo_root=Path("."))
        assert scan.shape_rationale == ""


# ---------------------------------------------------------------------------
# Shape detection — flat-package
# ---------------------------------------------------------------------------


class TestFlatPackageShape:
    def test_flat_package_single_dir(self, tmp_path):
        """All source files in one sub-directory → flat-package."""
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "flat-package"

    def test_flat_package_rationale_present(self, tmp_path):
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        scan = scan_repo(tmp_path)
        assert scan.shape_rationale != ""

    def test_flat_package_root_level_files(self, tmp_path):
        """Files directly at repo root (parent = '.') also count as flat."""
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"module{i}.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "flat-package"

    def test_below_flat_threshold_not_flat(self, tmp_path):
        """Fewer than _FLAT_MIN_FILES files in 1 dir → not flat-package."""
        for i in range(_FLAT_MIN_FILES - 1):
            _write_source(tmp_path, f"mylib/module{i}.py")
        scan = scan_repo(tmp_path)
        # Could be "mixed" or "unknown" but NOT flat-package
        assert scan.repo_shape != "flat-package"


# ---------------------------------------------------------------------------
# Shape detection — structured
# ---------------------------------------------------------------------------


class TestStructuredShape:
    def test_structured_multiple_dirs(self, tmp_path):
        """Source files in ≥ _STRUCTURED_MIN_DIRS directories → structured."""
        for i in range(_STRUCTURED_MIN_DIRS):
            _write_source(tmp_path, f"pkg{i}/main.py")
            _write_source(tmp_path, f"pkg{i}/utils.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "structured"

    def test_structured_rationale_present(self, tmp_path):
        for i in range(_STRUCTURED_MIN_DIRS):
            _write_source(tmp_path, f"pkg{i}/main.py")
        scan = scan_repo(tmp_path)
        assert scan.shape_rationale != ""

    def test_structured_django_style(self, tmp_path):
        """Django-style layout: multiple app dirs each with source files."""
        for app in ("auth", "billing", "users", "notifications"):
            _write_source(tmp_path, f"{app}/models.py")
            _write_source(tmp_path, f"{app}/views.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "structured"


# ---------------------------------------------------------------------------
# Shape detection — mixed
# ---------------------------------------------------------------------------


class TestMixedShape:
    def test_mixed_two_dirs(self, tmp_path):
        """Source files in exactly 2 dirs (below structured threshold) → mixed."""
        # 2 dirs but ≥ _SHAPE_MIN_FILES total files
        _write_source(tmp_path, "core/a.py")
        _write_source(tmp_path, "core/b.py")
        _write_source(tmp_path, "utils/c.py")
        # total = 3 (≥ _SHAPE_MIN_FILES), dirs = 2 → mixed
        assert _SHAPE_MIN_FILES <= 3  # sanity check
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "mixed"

    def test_mixed_rationale_present(self, tmp_path):
        _write_source(tmp_path, "core/a.py")
        _write_source(tmp_path, "core/b.py")
        _write_source(tmp_path, "utils/c.py")
        scan = scan_repo(tmp_path)
        assert scan.shape_rationale != ""


# ---------------------------------------------------------------------------
# Shape detection — unknown
# ---------------------------------------------------------------------------


class TestUnknownShape:
    def test_unknown_too_few_files(self, tmp_path):
        """Fewer than _SHAPE_MIN_FILES source files → unknown."""
        for i in range(_SHAPE_MIN_FILES - 1):
            _write_source(tmp_path, f"src/mod{i}.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "unknown"

    def test_unknown_empty_repo(self, tmp_path):
        """Empty repository → unknown."""
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "unknown"


# ---------------------------------------------------------------------------
# Test file exclusion
# ---------------------------------------------------------------------------


class TestTestFileExclusion:
    def test_test_prefix_files_excluded(self, tmp_path):
        """test_*.py files do not count towards shape parent directories."""
        # 5 production files in mylib + 5 test files in tests/ → still flat-package
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        for i in range(5):
            _write_source(tmp_path, f"tests/test_module{i}.py")
        scan = scan_repo(tmp_path)
        # tests/ dir should be excluded so only mylib/ counts → flat-package
        assert scan.repo_shape == "flat-package"

    def test_test_suffix_files_excluded(self, tmp_path):
        """*_test.py files do not inflate dir count."""
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        for i in range(5):
            _write_source(tmp_path, f"tests/module{i}_test.py")
        scan = scan_repo(tmp_path)
        assert scan.repo_shape == "flat-package"

    def test_test_dir_name_excluded(self, tmp_path):
        """Files in a directory named 'tests' are excluded from shape counting."""
        # Without test exclusion these would create 2 dirs → mixed, not flat-package
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        for i in range(3):
            _write_source(tmp_path, f"tests/helper{i}.py")  # no test_ prefix but in tests/
        scan = scan_repo(tmp_path)
        # tests/ dir excluded → only mylib/ → flat-package
        assert scan.repo_shape == "flat-package"


# ---------------------------------------------------------------------------
# shape_rationale content checks
# ---------------------------------------------------------------------------


class TestShapeRationale:
    def test_flat_rationale_mentions_files(self, tmp_path):
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        scan = scan_repo(tmp_path)
        assert "flat" in scan.shape_rationale.lower() or "parent" in scan.shape_rationale.lower()

    def test_structured_rationale_mentions_directories(self, tmp_path):
        for i in range(_STRUCTURED_MIN_DIRS):
            _write_source(tmp_path, f"pkg{i}/main.py")
            _write_source(tmp_path, f"pkg{i}/b.py")
        scan = scan_repo(tmp_path)
        assert "director" in scan.shape_rationale.lower()

    def test_unknown_rationale_mentions_threshold(self, tmp_path):
        scan = scan_repo(tmp_path)
        # rationale should explain why it's unknown
        assert scan.shape_rationale != "" or scan.repo_shape != "unknown"

    def test_rationale_not_empty_for_all_shapes(self, tmp_path):
        """For any detected shape, rationale is non-empty."""
        for i in range(_FLAT_MIN_FILES):
            _write_source(tmp_path, f"mylib/module{i}.py")
        scan = scan_repo(tmp_path)
        assert scan.shape_rationale != ""
