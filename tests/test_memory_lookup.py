"""Tests for ``memory/lookup.py`` — target resolution and output formatting.

Covers:
- match_target: exact feature/module name, slug, path, substring, fuzzy, not-found
- explain_match: found (feature/module), not-found, with/without artifact file
- changed_match: found with/without freshness.json, not-found
- CLI integration: memory explain and memory changed commands
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.lookup import (
    TargetMatch,
    match_target,
    explain_match,
    changed_match,
    _make_match,
    _confidence_label,
    _format_timestamp,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _feature(name: str, files=None, tests=None, confidence=0.9) -> FeatureMemory:
    return FeatureMemory(
        name=name,
        files=files or [f"src/{name.lower()}/main.py"],
        tests=tests or [f"tests/test_{name.lower()}.py"],
        confidence=confidence,
    )


def _module(name: str, files=None, tests=None, confidence=0.85) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=files or [f"src/{name.replace('.', '/')}/mod.py"],
        tests=tests or [f"tests/test_{name.replace('.', '_')}.py"],
        confidence=confidence,
    )


@pytest.fixture()
def features() -> list[FeatureMemory]:
    return [
        _feature("Authentication", files=["src/auth/login.py", "src/auth/middleware.py"],
                 tests=["tests/test_auth.py"]),
        _feature("Payments", files=["src/payments/webhook.py"], tests=["tests/test_payments.py"]),
        _feature("Rate Limiting", files=["src/ratelimit/limiter.py"]),
    ]


@pytest.fixture()
def modules() -> list[ModuleMemory]:
    return [
        _module("code_review_graph.memory", files=["code_review_graph/memory/models.py"],
                tests=["tests/test_memory_models.py"]),
        _module("code_review_graph.parser", files=["code_review_graph/parser.py"],
                tests=["tests/test_parser.py"]),
    ]


# ---------------------------------------------------------------------------
# match_target — exact name
# ---------------------------------------------------------------------------


class TestMatchTargetExactName:
    def test_exact_feature_name_case_insensitive(self, tmp_path, features, modules):
        m = match_target("authentication", tmp_path, features, modules)
        assert m.found()
        assert m.kind == "feature"
        assert m.name == "Authentication"
        assert m.score == 1.0

    def test_exact_feature_name_with_original_case(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        assert m.found()
        assert m.name == "Authentication"

    def test_exact_module_name(self, tmp_path, features, modules):
        m = match_target("code_review_graph.memory", tmp_path, features, modules)
        assert m.found()
        assert m.kind == "module"
        assert m.name == "code_review_graph.memory"

    def test_exact_module_name_case_insensitive(self, tmp_path, features, modules):
        m = match_target("CODE_REVIEW_GRAPH.MEMORY", tmp_path, features, modules)
        assert m.found()
        assert m.kind == "module"


# ---------------------------------------------------------------------------
# match_target — slug match
# ---------------------------------------------------------------------------


class TestMatchTargetSlug:
    def test_slug_match_feature(self, tmp_path, features, modules):
        m = match_target("rate-limiting", tmp_path, features, modules)
        assert m.found()
        assert m.name == "Rate Limiting"
        assert m.score == 1.0

    def test_slug_match_module(self, tmp_path, features, modules):
        m = match_target("code-review-graph-memory", tmp_path, features, modules)
        assert m.found()
        assert m.name == "code_review_graph.memory"


# ---------------------------------------------------------------------------
# match_target — path match
# ---------------------------------------------------------------------------


class TestMatchTargetPath:
    def test_path_match_feature_file(self, tmp_path, features, modules):
        m = match_target("src/auth/login.py", tmp_path, features, modules)
        assert m.found()
        assert m.kind == "path"
        assert "Authentication" in m.name

    def test_path_match_module_file(self, tmp_path, features, modules):
        m = match_target("code_review_graph/memory/models.py", tmp_path, features, modules)
        assert m.found()
        assert m.kind == "path"
        assert "code_review_graph.memory" in m.name

    def test_path_match_score(self, tmp_path, features, modules):
        m = match_target("src/payments/webhook.py", tmp_path, features, modules)
        assert m.found()
        assert m.score >= 0.9


# ---------------------------------------------------------------------------
# match_target — substring and fuzzy
# ---------------------------------------------------------------------------


class TestMatchTargetSubstringAndFuzzy:
    def test_substring_match_feature(self, tmp_path, features, modules):
        m = match_target("auth", tmp_path, features, modules)
        assert m.found()
        assert "Authentication" in m.name

    def test_substring_match_module(self, tmp_path, features, modules):
        m = match_target("parser", tmp_path, features, modules)
        assert m.found()
        assert "parser" in m.name.lower()

    def test_fuzzy_match_returns_something_for_close_target(self, tmp_path, features, modules):
        # "payment" is close to "Payments"
        m = match_target("payment", tmp_path, features, modules)
        assert m.found()
        assert "Payments" in m.name

    def test_not_found_for_completely_unrelated(self, tmp_path, features, modules):
        m = match_target("xyzzy_nonexistent_zzz", tmp_path, features, modules)
        assert not m.found()
        assert m.kind == "not_found"


# ---------------------------------------------------------------------------
# match_target — artifact_path
# ---------------------------------------------------------------------------


class TestMatchTargetArtifactPath:
    def test_artifact_path_none_when_file_missing(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        # tmp_path has no .agent-memory/, so no artifact file exists
        assert m.artifact_path is None

    def test_artifact_path_set_when_file_exists(self, tmp_path, features, modules):
        feat_dir = tmp_path / "features"
        feat_dir.mkdir(parents=True)
        (feat_dir / "authentication.md").write_text("# Authentication\n")
        m = match_target("Authentication", tmp_path, features, modules)
        assert m.artifact_path is not None
        assert m.artifact_path.name == "authentication.md"


# ---------------------------------------------------------------------------
# explain_match — not found
# ---------------------------------------------------------------------------


class TestExplainMatchNotFound:
    def test_not_found_mentions_target(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing")
        out = explain_match(match, tmp_path)
        assert "billing" in out

    def test_not_found_suggests_init(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing")
        out = explain_match(match, tmp_path)
        assert "memory init" in out

    def test_not_found_with_alternatives(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing", alternatives=["Payments"])
        out = explain_match(match, tmp_path)
        assert "Payments" in out


# ---------------------------------------------------------------------------
# explain_match — found feature
# ---------------------------------------------------------------------------


class TestExplainMatchFoundFeature:
    def test_shows_feature_name(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "Authentication" in out

    def test_shows_confidence(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "Confidence" in out or "%" in out

    def test_shows_main_files(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "src/auth/login.py" in out or "Main files" in out

    def test_shows_related_tests(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "test_auth.py" in out or "Related tests" in out

    def test_freshness_hint_when_no_metadata(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        # No freshness.json → shows "unknown" hint
        assert "Freshness" in out


# ---------------------------------------------------------------------------
# explain_match — found module
# ---------------------------------------------------------------------------


class TestExplainMatchFoundModule:
    def test_shows_module_name(self, tmp_path, features, modules):
        m = match_target("code_review_graph.memory", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "code_review_graph.memory" in out

    def test_kind_label_is_module(self, tmp_path, features, modules):
        m = match_target("code_review_graph.memory", tmp_path, features, modules)
        out = explain_match(m, tmp_path)
        assert "Module" in out


# ---------------------------------------------------------------------------
# changed_match — not found
# ---------------------------------------------------------------------------


class TestChangedMatchNotFound:
    def test_not_found_mentions_target(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing")
        out = changed_match(match, tmp_path)
        assert "billing" in out

    def test_not_found_suggests_refresh(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing")
        out = changed_match(match, tmp_path)
        assert "memory refresh" in out or "memory init" in out


# ---------------------------------------------------------------------------
# changed_match — found but no freshness data
# ---------------------------------------------------------------------------


class TestChangedMatchNoFreshness:
    def test_no_freshness_data_message(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "refresh" in out.lower()

    def test_still_shows_area_name(self, tmp_path, features, modules):
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "Authentication" in out


# ---------------------------------------------------------------------------
# changed_match — found with freshness data
# ---------------------------------------------------------------------------


class TestChangedMatchWithFreshness:
    def _write_freshness(self, tmp_path: Path, changed_files=None, impacted_features=None):
        metadata_dir = tmp_path / "metadata"
        metadata_dir.mkdir(parents=True)
        data = {
            "refreshed_at": "2026-03-24T10:00:00+00:00",
            "mode": "incremental",
            "changed_files_count": len(changed_files or []),
            "changed_files": changed_files or [],
            "impacted_features": impacted_features or [],
            "impacted_modules": [],
            "artifacts_refreshed": [],
        }
        (metadata_dir / "freshness.json").write_text(json.dumps(data))

    def test_shows_last_refresh_timestamp(self, tmp_path, features, modules):
        self._write_freshness(tmp_path)
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "2026-03-24" in out

    def test_shows_refresh_mode(self, tmp_path, features, modules):
        self._write_freshness(tmp_path)
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "incremental" in out

    def test_shows_no_recent_changes_when_empty(self, tmp_path, features, modules):
        self._write_freshness(tmp_path, changed_files=[])
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "No recent changes" in out

    def test_shows_area_files_when_changed(self, tmp_path, features, modules):
        self._write_freshness(
            tmp_path,
            changed_files=["src/auth/login.py"],
            impacted_features=["authentication"],
        )
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "src/auth/login.py" in out

    def test_impacted_status_shown(self, tmp_path, features, modules):
        self._write_freshness(
            tmp_path,
            changed_files=["src/auth/login.py"],
            impacted_features=["authentication"],
        )
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "refreshed" in out.lower()

    def test_not_impacted_status_shown(self, tmp_path, features, modules):
        self._write_freshness(tmp_path, changed_files=["src/payments/webhook.py"])
        m = match_target("Authentication", tmp_path, features, modules)
        out = changed_match(m, tmp_path)
        assert "NOT refreshed" in out or "not impacted" in out.lower() or "not refreshed" in out.lower()


# ---------------------------------------------------------------------------
# TargetMatch.found()
# ---------------------------------------------------------------------------


class TestTargetMatchFound:
    def test_found_for_feature(self):
        m = TargetMatch(kind="feature", name="Foo")
        assert m.found() is True

    def test_found_for_module(self):
        m = TargetMatch(kind="module", name="Bar")
        assert m.found() is True

    def test_found_for_path(self):
        m = TargetMatch(kind="path", name="src/foo.py")
        assert m.found() is True

    def test_not_found(self):
        m = TargetMatch(kind="not_found", name="unknown")
        assert m.found() is False


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_confidence_label_high(self):
        assert _confidence_label(0.9) == "high"

    def test_confidence_label_medium(self):
        assert _confidence_label(0.7) == "medium"

    def test_confidence_label_low(self):
        assert _confidence_label(0.5) == "low"

    def test_format_timestamp_valid(self):
        result = _format_timestamp("2026-03-24T10:00:00+00:00")
        assert "2026-03-24" in result

    def test_format_timestamp_empty(self):
        result = _format_timestamp("")
        assert result == "unknown"

    def test_format_timestamp_unknown(self):
        result = _format_timestamp("unknown")
        assert result == "unknown"

    def test_format_timestamp_invalid_falls_back(self):
        result = _format_timestamp("not-a-date")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# CLI integration — memory explain
# ---------------------------------------------------------------------------


def _run_cli_stdout(*argv: str) -> str:
    from code_review_graph.cli import main

    buf = StringIO()
    with patch("sys.argv", ["code-review-graph", *argv]):
        with patch("sys.stdout", buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


class TestCLIExplain:
    def test_explain_shows_header(self, tmp_path):
        out = _run_cli_stdout("memory", "explain", "authentication", "--repo", str(tmp_path))
        assert "explain" in out
        assert "authentication" in out

    def test_explain_not_found_shows_helpful_message(self, tmp_path):
        out = _run_cli_stdout("memory", "explain", "xyzzy_zzz", "--repo", str(tmp_path))
        assert "not found" in out.lower() or "memory init" in out

    def test_explain_no_longer_shows_not_yet_implemented(self, tmp_path):
        out = _run_cli_stdout("memory", "explain", "anything", "--repo", str(tmp_path))
        assert "not yet implemented" not in out


class TestCLIChanged:
    def test_changed_shows_header(self, tmp_path):
        out = _run_cli_stdout("memory", "changed", "authentication", "--repo", str(tmp_path))
        assert "changed" in out
        assert "authentication" in out

    def test_changed_not_found_shows_helpful_message(self, tmp_path):
        out = _run_cli_stdout("memory", "changed", "xyzzy_zzz", "--repo", str(tmp_path))
        assert "not found" in out.lower() or "memory init" in out or "memory refresh" in out

    def test_changed_no_longer_shows_not_yet_implemented(self, tmp_path):
        out = _run_cli_stdout("memory", "changed", "anything", "--repo", str(tmp_path))
        assert "not yet implemented" not in out
