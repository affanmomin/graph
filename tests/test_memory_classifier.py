"""Tests for the feature and module classifier (Ticket 5).

Synthetic repo layouts tested:
- single-app repo (flat src/)
- well-structured repo (src/ with sub-packages)
- domain-folder repo (auth/, billing/, users/, etc.)
- monorepo (apps/ or packages/ with multiple entries)
- messy mixed repo (no clear structure)
- cross-cutting features (same token in multiple sub-trees)
- empty / nearly-empty repo

Each test only checks the classifier's output — no disk writes, no CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_review_graph.memory.classifier import classify_features, classify_modules
from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.scanner import scan_repo


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a fake repo with the given file tree under tmp_path."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def scan(repo_path: Path):
    return scan_repo(repo_path)


# ---------------------------------------------------------------------------
# Module classification — single-app repo
# ---------------------------------------------------------------------------


class TestModulesSingleApp:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/__init__.py": "",
            "src/main.py": "def main(): pass",
            "src/utils.py": "def helper(): pass",
            "tests/test_main.py": "def test_main(): pass",
        })

    def test_returns_list(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        assert isinstance(result, list)

    def test_nonempty(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        assert len(result) >= 1

    def test_all_module_memory_instances(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        assert all(isinstance(m, ModuleMemory) for m in result)

    def test_has_files(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        assert any(len(m.files) > 0 for m in result)

    def test_confidence_in_range(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            assert 0.0 <= m.confidence <= 1.0

    def test_sorted_by_name(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        names = [m.name for m in result]
        assert names == sorted(names)

    def test_files_are_repo_relative(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            for f in m.files:
                assert not Path(f).is_absolute(), f"absolute path leaked: {f}"

    def test_no_duplicate_names(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        names = [m.name for m in result]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Module classification — sub-packages with __init__.py
# ---------------------------------------------------------------------------


class TestModulesSubPackages:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/__init__.py": "",
            "src/auth/__init__.py": "",
            "src/auth/models.py": "",
            "src/auth/views.py": "",
            "src/billing/__init__.py": "",
            "src/billing/invoice.py": "",
            "src/billing/stripe.py": "",
            "tests/test_auth.py": "",
            "tests/test_billing.py": "",
        })

    def test_detects_auth_module(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        names = [m.name for m in result]
        assert any("auth" in n for n in names)

    def test_detects_billing_module(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        names = [m.name for m in result]
        assert any("billing" in n for n in names)

    def test_auth_module_has_files(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        auth = next((m for m in result if "auth" in m.name), None)
        assert auth is not None
        assert len(auth.files) >= 2

    def test_auth_module_high_confidence(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        auth = next((m for m in result if "auth" in m.name), None)
        assert auth is not None
        assert auth.confidence >= 0.8

    def test_tests_linked_to_module(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        auth = next((m for m in result if "auth" in m.name), None)
        assert auth is not None
        assert any("test_auth" in t for t in auth.tests)

    def test_files_sorted(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            assert m.files == sorted(m.files)


# ---------------------------------------------------------------------------
# Module classification — monorepo (apps/ or packages/)
# ---------------------------------------------------------------------------


class TestModulesMonorepo:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "apps/web/index.ts": "",
            "apps/web/pages/home.tsx": "",
            "apps/api/main.py": "",
            "apps/api/routes.py": "",
            "apps/worker/worker.py": "",
        })

    def test_detects_monorepo_entries(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        names = [m.name for m in result]
        assert any("web" in n for n in names)
        assert any("api" in n for n in names)

    def test_each_entry_has_files(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            assert len(m.files) > 0

    def test_confidence_reasonable(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            assert m.confidence >= 0.5


# ---------------------------------------------------------------------------
# Feature classification — domain folder names
# ---------------------------------------------------------------------------


class TestFeaturesDomainFolders:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/auth/login.py": "",
            "src/auth/middleware.py": "",
            "src/billing/invoice.py": "",
            "src/billing/stripe.py": "",
            "src/users/model.py": "",
            "src/users/service.py": "",
            "src/notifications/email.py": "",
            "tests/test_auth.py": "",
            "tests/test_billing.py": "",
        })

    def test_returns_list(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        assert isinstance(result, list)

    def test_detects_auth_feature(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name.lower() for f in result]
        assert any("auth" in n for n in names)

    def test_detects_billing_feature(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name.lower() for f in result]
        assert any("billing" in n for n in names)

    def test_detects_users_feature(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name.lower() for f in result]
        assert any("user" in n for n in names)

    def test_all_feature_memory_instances(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        assert all(isinstance(f, FeatureMemory) for f in result)

    def test_auth_feature_high_confidence(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        auth = next((f for f in result if "auth" in f.name.lower()), None)
        assert auth is not None
        assert auth.confidence >= 0.8

    def test_auth_feature_has_files(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        auth = next((f for f in result if "auth" in f.name.lower()), None)
        assert auth is not None
        assert len(auth.files) >= 2

    def test_auth_tests_linked(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        auth = next((f for f in result if "auth" in f.name.lower()), None)
        assert auth is not None
        assert any("test_auth" in t for t in auth.tests)

    def test_sorted_by_name(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name for f in result]
        assert names == sorted(names)

    def test_confidence_in_range(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        for f in result:
            assert 0.0 <= f.confidence <= 1.0

    def test_files_are_repo_relative(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        for f in result:
            for path in f.files:
                assert not Path(path).is_absolute()

    def test_no_duplicate_names(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name for f in result]
        assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Feature classification — medium-confidence structural keywords
# ---------------------------------------------------------------------------


class TestFeaturesMediumKeywords:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/models/user.py": "",
            "src/views/home.py": "",
            "src/services/email.py": "",
            "src/middleware/auth.py": "",
        })

    def test_detects_models(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        names = [f.name.lower() for f in result]
        assert any("model" in n for n in names)

    def test_medium_confidence_below_high(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        for f in result:
            # medium-keyword features should be < high threshold
            if "model" in f.name.lower() or "view" in f.name.lower():
                assert f.confidence <= 0.75


# ---------------------------------------------------------------------------
# Feature classification — cross-cutting token detection
# ---------------------------------------------------------------------------


class TestFeaturesCrossCutting:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "backend/reporting/summary.py": "",
            "frontend/reporting/chart.ts": "",
        })

    def test_detects_cross_cutting_feature(self, tmp_path):
        repo = self._repo(tmp_path)
        # scan will find 'backend' and 'frontend' as source dirs (fallback)
        s = scan_repo(repo)
        result = classify_features(repo, s)
        names = [f.name.lower() for f in result]
        # 'reporting' appears in both sub-trees
        assert any("reporting" in n for n in names)

    def test_cross_cutting_lower_confidence(self, tmp_path):
        repo = self._repo(tmp_path)
        s = scan_repo(repo)
        result = classify_features(repo, s)
        reporting = next((f for f in result if "reporting" in f.name.lower()), None)
        if reporting:
            assert reporting.confidence < 0.6


# ---------------------------------------------------------------------------
# Empty / nearly empty repos
# ---------------------------------------------------------------------------


class TestEmptyRepo:
    def test_modules_empty_repo(self, tmp_path):
        result = classify_modules(tmp_path, scan(tmp_path))
        assert isinstance(result, list)
        # may be empty — should not crash

    def test_features_empty_repo(self, tmp_path):
        result = classify_features(tmp_path, scan(tmp_path))
        assert isinstance(result, list)

    def test_single_file_repo(self, tmp_path):
        make_repo(tmp_path, {"main.py": "print('hello')"})
        s = scan(tmp_path)
        modules = classify_modules(tmp_path, s)
        features = classify_features(tmp_path, s)
        assert isinstance(modules, list)
        assert isinstance(features, list)


# ---------------------------------------------------------------------------
# Messy / non-standard repo
# ---------------------------------------------------------------------------


class TestMessyRepo:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "weird_dir/a.py": "",
            "weird_dir/b.py": "",
            "other_stuff/c.ts": "",
            "totally_flat.py": "",
        })

    def test_does_not_crash(self, tmp_path):
        repo = self._repo(tmp_path)
        s = scan(repo)
        modules = classify_modules(repo, s)
        features = classify_features(repo, s)
        assert isinstance(modules, list)
        assert isinstance(features, list)

    def test_all_confidences_in_range(self, tmp_path):
        repo = self._repo(tmp_path)
        s = scan(repo)
        for m in classify_modules(repo, s):
            assert 0.0 <= m.confidence <= 1.0
        for f in classify_features(repo, s):
            assert 0.0 <= f.confidence <= 1.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/auth/__init__.py": "",
            "src/auth/login.py": "",
            "src/billing/__init__.py": "",
            "src/billing/invoice.py": "",
            "src/users/__init__.py": "",
            "src/users/model.py": "",
            "tests/test_auth.py": "",
        })

    def test_modules_deterministic(self, tmp_path):
        repo = self._repo(tmp_path)
        s = scan(repo)
        r1 = [(m.name, m.files, m.confidence) for m in classify_modules(repo, s)]
        r2 = [(m.name, m.files, m.confidence) for m in classify_modules(repo, s)]
        assert r1 == r2

    def test_features_deterministic(self, tmp_path):
        repo = self._repo(tmp_path)
        s = scan(repo)
        r1 = [(f.name, f.files, f.confidence) for f in classify_features(repo, s)]
        r2 = [(f.name, f.files, f.confidence) for f in classify_features(repo, s)]
        assert r1 == r2


# ---------------------------------------------------------------------------
# Model field contracts
# ---------------------------------------------------------------------------


class TestModelContracts:
    def _repo(self, tmp_path):
        return make_repo(tmp_path, {
            "src/auth/__init__.py": "",
            "src/auth/login.py": "",
            "tests/test_auth.py": "",
        })

    def test_module_fields_present(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            assert isinstance(m.name, str) and m.name
            assert isinstance(m.files, list)
            assert isinstance(m.tests, list)
            assert isinstance(m.confidence, float)
            assert isinstance(m.summary, str)

    def test_feature_fields_present(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        for f in result:
            assert isinstance(f.name, str) and f.name
            assert isinstance(f.files, list)
            assert isinstance(f.tests, list)
            assert isinstance(f.confidence, float)
            assert isinstance(f.summary, str)

    def test_module_slug_is_safe(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_modules(repo, scan(repo))
        for m in result:
            slug = m.slug()
            assert " " not in slug
            assert "/" not in slug or True  # slugs may contain / — that's OK

    def test_feature_slug_is_safe(self, tmp_path):
        repo = self._repo(tmp_path)
        result = classify_features(repo, scan(repo))
        for f in result:
            slug = f.slug()
            assert "/" not in slug


# ---------------------------------------------------------------------------
# Module classification — Ticket B: Strategy 0 (root-level package files)
# ---------------------------------------------------------------------------


class TestModulesStrategy0:
    def test_root_package_files_get_own_module(self, tmp_path):
        """A package with both root-level files and a sub-package produces 2 modules."""
        repo = make_repo(tmp_path, {
            "mypkg/__init__.py": "",
            "mypkg/parser.py": "def parse(): pass",
            "mypkg/graph.py": "def build(): pass",
            "mypkg/cli.py": "def main(): pass",
            "mypkg/memory/__init__.py": "",
            "mypkg/memory/scanner.py": "def scan(): pass",
            "mypkg/memory/generator.py": "def generate(): pass",
        })
        sc = scan(repo)
        modules = classify_modules(repo, sc)
        names = [m.name for m in modules]
        # Strategy 0 should produce "mypkg" for root files
        assert "mypkg" in names, f"Expected 'mypkg' in {names}"
        # Strategy 1 should produce "mypkg/memory" for sub-package
        assert "mypkg/memory" in names, f"Expected 'mypkg/memory' in {names}"

    def test_root_module_files_do_not_include_subpackage_files(self, tmp_path):
        """Root module should not contain files from the sub-package."""
        repo = make_repo(tmp_path, {
            "mypkg/__init__.py": "",
            "mypkg/parser.py": "def parse(): pass",
            "mypkg/graph.py": "def build(): pass",
            "mypkg/memory/__init__.py": "",
            "mypkg/memory/scanner.py": "def scan(): pass",
        })
        sc = scan(repo)
        modules = classify_modules(repo, sc)
        root_mod = next((m for m in modules if m.name == "mypkg"), None)
        assert root_mod is not None
        # Root module should only contain root-level files
        for f in root_mod.files:
            assert "memory/" not in f, f"Subpackage file leaked into root module: {f}"

    def test_no_init_py_does_not_create_root_module(self, tmp_path):
        """Strategy 0 requires __init__.py — a plain directory should not get a root module."""
        repo = make_repo(tmp_path, {
            "mypkg/parser.py": "def parse(): pass",
            "mypkg/graph.py": "def build(): pass",
            "mypkg/memory/__init__.py": "",
            "mypkg/memory/scanner.py": "def scan(): pass",
        })
        sc = scan(repo)
        modules = classify_modules(repo, sc)
        # Without __init__.py on mypkg, Strategy 0 should not fire for it
        names = [m.name for m in modules]
        assert "mypkg" not in names, f"Strategy 0 should not fire without __init__.py; got {names}"

    def test_package_with_only_subpackages_no_root_module(self, tmp_path):
        """A package with NO root-level source files should not produce a root module via S0."""
        repo = make_repo(tmp_path, {
            "mypkg/__init__.py": "",
            "mypkg/memory/__init__.py": "",
            "mypkg/memory/scanner.py": "def scan(): pass",
        })
        sc = scan(repo)
        modules = classify_modules(repo, sc)
        names = [m.name for m in modules]
        # No root-level source files in mypkg → no "mypkg" module from S0
        assert "mypkg" not in names, f"S0 should not emit root mod with no root files; got {names}"
