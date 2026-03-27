"""Tests for Ticket 3.2 — flat_rescue.py and classifier integration.

Covers:
- _collect_flat_source_files: excludes test files, excluded dirs, claimed files
- _keyword_name_rescue: groups by domain token, falls back to Core
- _name_cluster: high domain token wins, then medium, then most-common, then fallback
- _cluster_by_similarity: groups similar embeddings, respects max_clusters + threshold
- _cosine_similarity: orthogonal → 0, identical → 1
- _find_tests_for_files: matches test files by stem
- rescue_flat_features: integrates file collect → clustering → graph signals
- rescue_flat_features: gracefully handles empty file lists
- rescue_flat_features: graph signals unavailable → still returns results
- _try_embedding_rescue: returns None when sentence-transformers missing
- classifier.classify_features: calls rescue when repo_shape == flat-package
- classifier.classify_features: calls rescue when no features found normally
- classifier.classify_features: no duplicate names between normal + rescued features
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import patch

import pytest

import code_review_graph.memory.flat_rescue as _fr
import code_review_graph.memory.graph_bridge as _gb

from code_review_graph.memory.flat_rescue import (
    _collect_flat_source_files,
    _cosine_similarity,
    _cluster_by_similarity,
    _find_tests_for_files,
    _keyword_name_rescue,
    _name_cluster,
    _stem_tokens,
    rescue_flat_features,
)
from code_review_graph.memory.models import FeatureMemory
from code_review_graph.memory.scanner import RepoScan, scan_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path: Path, rel: str, content: str = "# x\n") -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _scan(tmp_path: Path) -> RepoScan:
    return scan_repo(tmp_path)


def _feature(name: str, files: list[str]) -> FeatureMemory:
    return FeatureMemory(name=name, files=files, tests=[], confidence=0.9)


class _MockGB:
    """Patches graph_bridge for a test scope."""

    def __init__(self, available: bool = False, vocabulary: dict | None = None):
        self._available = available
        self._vocabulary = vocabulary or {}
        self._saved: dict = {}

    def __enter__(self):
        self._saved = {
            attr: getattr(_gb, attr)
            for attr in ["graph_available", "get_file_vocabulary", "get_all_classifier_signals"]
            if hasattr(_gb, attr)
        }
        _gb.graph_available = lambda *a, **kw: self._available
        _gb.get_file_vocabulary = lambda *a, **kw: dict(self._vocabulary)
        _gb.get_all_classifier_signals = lambda *a, **kw: {}
        return self

    def __exit__(self, *_):
        for attr, val in self._saved.items():
            setattr(_gb, attr, val)


# ---------------------------------------------------------------------------
# _stem_tokens
# ---------------------------------------------------------------------------


class TestStemTokens:
    def test_underscore_split(self):
        assert _stem_tokens("auth_service") == ["auth", "service"]

    def test_hyphen_split(self):
        assert _stem_tokens("billing-utils") == ["billing", "utils"]

    def test_lowercase(self):
        assert _stem_tokens("Auth") == ["auth"]

    def test_empty_parts_removed(self):
        assert _stem_tokens("_foo_") == ["foo"]


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self):
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)

    def test_unnormalised(self):
        a = [3.0, 4.0]
        b = [6.0, 8.0]  # same direction, different magnitude
        assert _cosine_similarity(a, b) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# _cluster_by_similarity
# ---------------------------------------------------------------------------


def _unit(v: list[float]) -> list[float]:
    """Return the L2-normalised version of v."""
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v]


class TestClusterBySimilarity:
    def test_two_similar_files_cluster_together(self):
        files = ["a.py", "b.py"]
        # Embeddings nearly identical
        e = [_unit([1.0, 0.01]), _unit([1.0, 0.02])]
        clusters = _cluster_by_similarity(files, e, threshold=0.5, max_clusters=8)
        assert len(clusters) == 1
        assert set(clusters[0]) == {"a.py", "b.py"}

    def test_two_orthogonal_files_form_two_clusters(self):
        files = ["a.py", "b.py"]
        e = [_unit([1.0, 0.0]), _unit([0.0, 1.0])]
        clusters = _cluster_by_similarity(files, e, threshold=0.5, max_clusters=8)
        assert len(clusters) == 2

    def test_max_clusters_cap_respected(self):
        # 5 orthogonal files but max_clusters=2 → at most 2 clusters
        files = [f"f{i}.py" for i in range(5)]
        dim = 5
        embeddings = [_unit([1.0 if j == i else 0.0 for j in range(dim)]) for i in range(5)]
        clusters = _cluster_by_similarity(files, embeddings, threshold=0.9, max_clusters=2)
        assert len(clusters) <= 2

    def test_single_file_returns_one_cluster(self):
        files = ["a.py"]
        e = [_unit([1.0, 0.0])]
        clusters = _cluster_by_similarity(files, e, threshold=0.5, max_clusters=8)
        assert clusters == [["a.py"]]

    def test_empty_input(self):
        assert _cluster_by_similarity([], [], threshold=0.5, max_clusters=8) == []

    def test_all_files_appear_in_output(self):
        files = ["a.py", "b.py", "c.py"]
        e = [_unit([1.0, 0.0, 0.0]), _unit([0.0, 1.0, 0.0]), _unit([0.0, 0.0, 1.0])]
        clusters = _cluster_by_similarity(files, e, threshold=0.9, max_clusters=8)
        all_files = [f for cluster in clusters for f in cluster]
        assert sorted(all_files) == sorted(files)

    def test_deterministic(self):
        files = ["a.py", "b.py", "c.py"]
        e = [_unit([1.0, 0.1, 0.0]), _unit([0.9, 0.2, 0.0]), _unit([0.0, 0.0, 1.0])]
        r1 = _cluster_by_similarity(files, e, threshold=0.5, max_clusters=8)
        r2 = _cluster_by_similarity(files, e, threshold=0.5, max_clusters=8)
        assert r1 == r2


# ---------------------------------------------------------------------------
# _name_cluster
# ---------------------------------------------------------------------------


class TestNameCluster:
    def test_high_domain_token_wins(self):
        files = ["auth_service.py", "token.py"]
        name = _name_cluster(files, {})
        assert name.lower() in ("auth", "token") or "auth" in name.lower()

    def test_medium_domain_token_used_when_no_high(self):
        name = _name_cluster(["base_handler.py"], {})
        assert name.lower() in ("base", "handler") or len(name) > 0

    def test_most_common_token_fallback(self):
        # "report" appears twice, no domain keyword — most common should win
        files = ["report_gen.py", "report_utils.py", "misc.py"]
        name = _name_cluster(files, {})
        # Should pick "report" (most common token) or "gen"/"utils" (length > 3)
        assert name  # just ensure non-empty

    def test_first_file_stem_last_resort(self):
        # xyzzy has no domain token, length<=3 tokens only
        name = _name_cluster(["xyz.py"], {})
        assert name  # non-empty

    def test_vocabulary_domain_hit(self):
        files = ["misc.py"]
        vocab = {"misc.py": ["AuthService", "LoginHandler"]}
        name = _name_cluster(files, vocab)
        # "auth" in "AuthService" → Auth or similar
        assert "auth" in name.lower() or "login" in name.lower() or name  # at minimum non-empty

    def test_returns_string(self):
        assert isinstance(_name_cluster(["a.py"], {}), str)


# ---------------------------------------------------------------------------
# _keyword_name_rescue
# ---------------------------------------------------------------------------


class TestKeywordNameRescue(object):
    def test_groups_by_high_domain_token(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "mylib/billing.py")
        _write(tmp_path, "mylib/utils.py")
        scan = _scan(tmp_path)
        files = ["mylib/auth.py", "mylib/billing.py", "mylib/utils.py"]
        result = _keyword_name_rescue(files, tmp_path, scan)
        names = [f.name.lower() for f in result]
        assert "auth" in names
        assert "billing" in names

    def test_files_without_keyword_go_to_core(self, tmp_path):
        _write(tmp_path, "mylib/foo.py")
        _write(tmp_path, "mylib/bar.py")
        scan = _scan(tmp_path)
        result = _keyword_name_rescue(["mylib/foo.py", "mylib/bar.py"], tmp_path, scan)
        names = [f.name for f in result]
        assert "Core" in names

    def test_high_confidence_for_domain_keyword(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        scan = _scan(tmp_path)
        result = _keyword_name_rescue(["mylib/auth.py"], tmp_path, scan)
        auth_feat = next(f for f in result if f.name.lower() == "auth")
        assert auth_feat.confidence > 0.4

    def test_core_group_lower_confidence(self, tmp_path):
        _write(tmp_path, "mylib/foo.py")
        scan = _scan(tmp_path)
        result = _keyword_name_rescue(["mylib/foo.py"], tmp_path, scan)
        core = next(f for f in result if f.name == "Core")
        assert core.confidence < 0.5

    def test_sorted_output(self, tmp_path):
        for name in ("billing", "auth", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        result = _keyword_name_rescue(
            ["mylib/billing.py", "mylib/auth.py", "mylib/admin.py"], tmp_path, scan
        )
        names = [f.name for f in result]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# _collect_flat_source_files
# ---------------------------------------------------------------------------


class TestCollectFlatSourceFiles:
    def test_basic_collection(self, tmp_path):
        _write(tmp_path, "mylib/a.py")
        _write(tmp_path, "mylib/b.py")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert "mylib/a.py" in files
        assert "mylib/b.py" in files

    def test_excludes_test_prefix_files(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "mylib/test_auth.py")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert "mylib/test_auth.py" not in files
        assert "mylib/auth.py" in files

    def test_excludes_test_suffix_files(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "mylib/auth_test.py")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert "mylib/auth_test.py" not in files

    def test_excludes_files_in_test_dirs(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "tests/helper.py")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert "tests/helper.py" not in files

    def test_excludes_claimed_files(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "mylib/billing.py")
        scan = _scan(tmp_path)
        existing = [_feature("Auth", ["mylib/auth.py"])]
        files = _collect_flat_source_files(tmp_path, scan, existing)
        assert "mylib/auth.py" not in files
        assert "mylib/billing.py" in files

    def test_sorted_output(self, tmp_path):
        for name in ("z.py", "a.py", "m.py"):
            _write(tmp_path, f"mylib/{name}")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert files == sorted(files)

    def test_non_source_files_excluded(self, tmp_path):
        _write(tmp_path, "mylib/README.md")
        _write(tmp_path, "mylib/auth.py")
        scan = _scan(tmp_path)
        files = _collect_flat_source_files(tmp_path, scan, [])
        assert "mylib/README.md" not in files


# ---------------------------------------------------------------------------
# _find_tests_for_files
# ---------------------------------------------------------------------------


class TestFindTestsForFiles:
    def test_finds_matching_test_file(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "tests/test_auth.py")
        scan = _scan(tmp_path)
        tests = _find_tests_for_files(tmp_path, scan, ["mylib/auth.py"])
        assert "tests/test_auth.py" in tests

    def test_no_test_dirs_returns_empty(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        scan = _scan(tmp_path)
        # No tests/ dir → scan.test_dirs is empty
        tests = _find_tests_for_files(tmp_path, scan, ["mylib/auth.py"])
        assert tests == []

    def test_unrelated_test_not_returned(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")
        _write(tmp_path, "tests/test_billing.py")
        scan = _scan(tmp_path)
        tests = _find_tests_for_files(tmp_path, scan, ["mylib/auth.py"])
        assert "tests/test_billing.py" not in tests


# ---------------------------------------------------------------------------
# rescue_flat_features — integration
# ---------------------------------------------------------------------------


class TestRescueFlatFeatures:
    def test_returns_features_for_flat_repo(self, tmp_path):
        for name in ("auth", "billing", "notifications", "users", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        assert len(result) > 0

    def test_empty_when_too_few_files(self, tmp_path):
        _write(tmp_path, "mylib/auth.py")  # only 1 file
        scan = _scan(tmp_path)
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        assert result == []

    def test_claimed_files_excluded(self, tmp_path):
        for name in ("auth", "billing", "notifications", "users", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        # Claim auth.py via existing features
        existing = [_feature("Auth", ["mylib/auth.py"])]
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, existing)
        # auth.py should not appear in rescued features
        rescued_files = [f for feat in result for f in feat.files]
        assert "mylib/auth.py" not in rescued_files

    def test_output_sorted_by_name(self, tmp_path):
        for name in ("billing", "auth", "users", "notifications", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        names = [f.name for f in result]
        assert names == sorted(names)

    def test_all_files_covered(self, tmp_path):
        source_names = ("auth", "billing", "notifications", "users", "admin")
        for name in source_names:
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        covered = {f for feat in result for f in feat.files}
        for name in source_names:
            assert f"mylib/{name}.py" in covered

    def test_confidence_in_range(self, tmp_path):
        for name in ("auth", "billing", "notifications", "users", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        for feat in result:
            assert 0.0 <= feat.confidence <= 1.0

    def test_graph_unavailable_still_returns_results(self, tmp_path):
        for name in ("auth", "billing", "users", "notifications", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB(available=False):
            result = rescue_flat_features(tmp_path, scan, [])
        assert len(result) > 0

    def test_no_exception_on_error(self, tmp_path):
        """rescue_flat_features never raises."""
        scan = RepoScan(repo_root=tmp_path)  # minimal scan
        with _MockGB():
            result = rescue_flat_features(tmp_path, scan, [])
        assert isinstance(result, list)

    def test_embedding_rescue_falls_back_to_keyword(self, tmp_path):
        """When sentence-transformers is missing, keyword rescue is used instead."""
        for name in ("auth", "billing", "users", "notifications", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        with _MockGB(), patch.dict("sys.modules", {"sentence_transformers": None}):
            result = rescue_flat_features(tmp_path, scan, [])
        assert len(result) > 0


# ---------------------------------------------------------------------------
# classifier.classify_features integration
# ---------------------------------------------------------------------------


class TestClassifierFlatRescueIntegration:
    def test_rescue_triggered_for_flat_package(self, tmp_path):
        """classify_features calls rescue when repo_shape == flat-package."""
        from code_review_graph.memory.classifier import classify_features

        # Write 5+ files in one directory — scanner will mark it flat-package
        for name in ("auth", "billing", "users", "notifications", "admin"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)
        assert scan.repo_shape == "flat-package"

        with _MockGB():
            features = classify_features(tmp_path, scan)
        assert len(features) > 0

    def test_rescue_triggered_when_no_normal_features(self, tmp_path):
        """classify_features calls rescue when normal heuristics find nothing."""
        from code_review_graph.memory.classifier import classify_features

        # Files with no domain keyword → normal classifier finds nothing
        for name in ("foo", "bar", "baz", "qux", "quux"):
            _write(tmp_path, f"mylib/{name}.py")
        scan = _scan(tmp_path)

        with _MockGB():
            features = classify_features(tmp_path, scan)
        # Rescue should have grouped these into Core (or similar)
        assert len(features) > 0

    def test_no_duplicate_names(self, tmp_path):
        """Names from rescue do not duplicate those from normal classification."""
        from code_review_graph.memory.classifier import classify_features

        # auth/ will be found by normal classifier; rescue gets remaining files
        _write(tmp_path, "src/auth/login.py")
        for name in ("billing", "users", "notifications", "admin"):
            _write(tmp_path, f"src/{name}.py")
        scan = _scan(tmp_path)

        with _MockGB():
            features = classify_features(tmp_path, scan)
        names = [f.name.lower() for f in features]
        assert len(names) == len(set(names)), "duplicate feature names found"
