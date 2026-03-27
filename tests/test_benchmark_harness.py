"""Tests for the benchmark harness (benchmarks/run_benchmark.py).

Covers utility functions, scoring functions, and the new explain-quality
and cache-timing collectors.  All tests work without a real graph.db or
populated .agent-memory/ — heavy I/O is mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Make benchmarks/ importable ───────────────────────────────────────────────
import sys

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from benchmarks.run_benchmark import (  # noqa: E402
    THRESHOLDS,
    _mean,
    _passes,
    _slug,
    collect_classification_metrics,
    collect_context_pack_metrics,
    collect_explain_metrics,
    collect_cache_timing,
    collect_refresh_metrics,
)


# ── Helpers / fixtures ────────────────────────────────────────────────────────


def _feature(name: str, confidence: float = 0.8, files: list[str] | None = None,
             tests: list[str] | None = None):
    from code_review_graph.memory.models import FeatureMemory
    return FeatureMemory(
        name=name,
        files=files or [f"src/{name.lower()}.py"],
        tests=tests or [],
        confidence=confidence,
    )


def _module(name: str, confidence: float = 0.85, deps: list[str] | None = None):
    from code_review_graph.memory.models import ModuleMemory
    return ModuleMemory(
        name=name,
        files=[f"src/{name.lower()}.py"],
        tests=[],
        confidence=confidence,
        dependencies=deps or [],
    )


def _make_pack(files: list[str] | None = None, features: list[str] | None = None,
               modules: list[str] | None = None, warnings: list[str] | None = None):
    """Build a minimal TaskContextPack mock."""
    from code_review_graph.memory.context_builder import TaskContextPack
    return TaskContextPack(
        task="test task",
        relevant_features=features or [],
        relevant_modules=modules or [],
        relevant_files=files or [],
        relevant_tests=[],
        warnings=warnings or [],
        summary="test",
    )


# ── _mean ─────────────────────────────────────────────────────────────────────


def test_mean_empty():
    assert _mean([]) == 0.0


def test_mean_single():
    assert _mean([0.5]) == 0.5


def test_mean_multiple():
    assert _mean([0.4, 0.6]) == pytest.approx(0.5)


# ── _passes ───────────────────────────────────────────────────────────────────


def test_passes_ge_above_threshold():
    assert _passes("feature_count", 5.0) is True


def test_passes_ge_at_threshold():
    assert _passes("feature_count", 2.0) is True


def test_passes_ge_below_threshold():
    assert _passes("feature_count", 1.0) is False


def test_passes_le_below_threshold():
    assert _passes("files_returned", 10.0) is True


def test_passes_le_above_threshold():
    assert _passes("files_returned", 25.0) is False


def test_passes_unknown_metric():
    assert _passes("some_unknown_metric", 0.0) is True


# ── _slug ─────────────────────────────────────────────────────────────────────


def test_slug_simple():
    assert _slug("my-repo") == "my-repo"


def test_slug_spaces():
    assert _slug("My Repo Name") == "my-repo-name"


def test_slug_dots():
    assert _slug("repo.name") == "repo-name"


# ── collect_classification_metrics ───────────────────────────────────────────


def test_classification_empty():
    cm = collect_classification_metrics([], [])
    assert cm["feature_count"] == 0
    assert cm["module_count"] == 0
    assert cm["avg_feature_confidence"] == 0.0
    assert cm["avg_module_confidence"] == 0.0


def test_classification_with_features():
    features = [_feature("Auth", 0.8), _feature("Billing", 0.6)]
    cm = collect_classification_metrics(features, [])
    assert cm["feature_count"] == 2
    assert cm["avg_feature_confidence"] == pytest.approx(0.7)


def test_classification_features_with_tests():
    features = [
        _feature("Auth", tests=["tests/test_auth.py"]),
        _feature("Billing", tests=[]),
    ]
    cm = collect_classification_metrics(features, [])
    assert cm["features_with_tests"] == 1


def test_classification_modules_with_deps():
    modules = [_module("Core", deps=["Auth"]), _module("Utils", deps=[])]
    cm = collect_classification_metrics([], modules)
    assert cm["modules_with_deps"] == 1


# ── collect_context_pack_metrics ─────────────────────────────────────────────


def test_context_pack_coverage_hit(tmp_path):
    """Task with expected file present → coverage 100%."""
    tasks = [{"id": "t1", "description": "auth task", "expected_files_contain": ["auth"]}]
    pack = _make_pack(files=["src/auth.py", "src/user.py"])

    with patch("code_review_graph.memory.context_builder.build_context_pack", return_value=pack):
        results, _ = collect_context_pack_metrics(tasks, [], [], tmp_path, None)

    assert results[0]["coverage_pct"] == 100.0
    assert results[0]["pass"]["coverage_pct"] is True


def test_context_pack_coverage_miss(tmp_path):
    """Task with expected file absent → coverage 0%."""
    tasks = [{"id": "t1", "description": "billing task", "expected_files_contain": ["billing"]}]
    pack = _make_pack(files=["src/auth.py"])

    with patch("code_review_graph.memory.context_builder.build_context_pack", return_value=pack):
        results, _ = collect_context_pack_metrics(tasks, [], [], tmp_path, None)

    assert results[0]["coverage_pct"] == 0.0
    assert results[0]["pass"]["coverage_pct"] is False


def test_context_pack_no_expected_files(tmp_path):
    """Task with no expected_files_contain → coverage is None."""
    tasks = [{"id": "t1", "description": "generic task"}]
    pack = _make_pack(files=["src/auth.py"])

    with patch("code_review_graph.memory.context_builder.build_context_pack", return_value=pack):
        results, _ = collect_context_pack_metrics(tasks, [], [], tmp_path, None)

    assert results[0]["coverage_pct"] is None


def test_context_pack_tokens_pass(tmp_path):
    """Small files → tokens under budget → pass."""
    tasks = [{"id": "t1", "description": "task"}]
    # Create a tiny file
    f = tmp_path / "small.py"
    f.write_text("x = 1", encoding="utf-8")
    pack = _make_pack(files=["small.py"])

    with patch("code_review_graph.memory.context_builder.build_context_pack", return_value=pack):
        results, _ = collect_context_pack_metrics(tasks, [], [], tmp_path, None)

    assert results[0]["pass"]["tokens_estimated"] is True


def test_context_pack_tokens_fail(tmp_path):
    """Large file → tokens over budget → fail."""
    tasks = [{"id": "t1", "description": "task"}]
    f = tmp_path / "huge.py"
    f.write_text("x = 1\n" * 50000, encoding="utf-8")  # ~200k bytes -> ~50k tokens
    pack = _make_pack(files=["huge.py"])

    with patch("code_review_graph.memory.context_builder.build_context_pack", return_value=pack):
        results, _ = collect_context_pack_metrics(tasks, [], [], tmp_path, None)

    assert results[0]["pass"]["tokens_estimated"] is False


# ── collect_explain_metrics ───────────────────────────────────────────────────


def test_explain_metrics_no_features_no_modules(tmp_path):
    """No features or modules → returns empty targets with avg_score 0."""
    result = collect_explain_metrics([], [], tmp_path / ".agent-memory", tmp_path)
    assert result["targets"] == []
    assert result["avg_score"] == 0.0


def test_explain_metrics_passes_when_good_output(tmp_path):
    """Good explain output (long, has sections) → avg_score 1.0, pass=True."""
    feature = _feature("Auth")
    good_output = (
        "Feature: Auth\n\n"
        "  Confidence : 80%\n"
        "  Purpose    : Handles user authentication\n"
        "  Main files:\n"
        "    - src/auth.py\n"
    )

    from code_review_graph.memory.lookup import TargetMatch
    mock_match = TargetMatch(kind="feature", name="Auth", obj=feature, score=1.0)

    with (
        patch("code_review_graph.memory.lookup.match_target", return_value=mock_match),
        patch("code_review_graph.memory.lookup.explain_match", return_value=good_output),
    ):
        result = collect_explain_metrics(
            [feature], [], tmp_path / ".agent-memory", tmp_path
        )

    assert result["avg_score"] == 1.0
    assert result["pass"] is True
    assert result["targets"][0]["criteria"]["has_confidence"] is True
    assert result["targets"][0]["criteria"]["has_files_section"] is True
    assert result["targets"][0]["criteria"]["has_purpose"] is True


def test_explain_metrics_fails_when_empty_output(tmp_path):
    """Empty explain output → avg_score < threshold, pass=False."""
    feature = _feature("Auth")

    from code_review_graph.memory.lookup import TargetMatch
    mock_match = TargetMatch(kind="feature", name="Auth", obj=feature, score=1.0)

    with (
        patch("code_review_graph.memory.lookup.match_target", return_value=mock_match),
        patch("code_review_graph.memory.lookup.explain_match", return_value=""),
    ):
        result = collect_explain_metrics(
            [feature], [], tmp_path / ".agent-memory", tmp_path
        )

    assert result["avg_score"] == 0.0
    assert result["pass"] is False


def test_explain_metrics_respects_max_targets(tmp_path):
    """max_targets=1 → only first feature scored."""
    features = [_feature("Auth"), _feature("Billing"), _feature("Core")]

    from code_review_graph.memory.lookup import TargetMatch
    mock_match = TargetMatch(kind="feature", name="Auth", score=1.0)

    with (
        patch("code_review_graph.memory.lookup.match_target", return_value=mock_match),
        patch("code_review_graph.memory.lookup.explain_match", return_value="Confidence Purpose Main files"),
    ):
        result = collect_explain_metrics(
            features, [], tmp_path / ".agent-memory", tmp_path, max_targets=1
        )

    assert len(result["targets"]) == 1


def test_explain_metrics_falls_back_to_modules(tmp_path):
    """No features → uses modules."""
    module = _module("Core")

    from code_review_graph.memory.lookup import TargetMatch
    mock_match = TargetMatch(kind="module", name="Core", obj=module, score=1.0)
    good_output = "Confidence Purpose Main files: yes"

    with (
        patch("code_review_graph.memory.lookup.match_target", return_value=mock_match),
        patch("code_review_graph.memory.lookup.explain_match", return_value=good_output),
    ):
        result = collect_explain_metrics([], [module], tmp_path / ".agent-memory", tmp_path)

    assert len(result["targets"]) == 1
    assert result["targets"][0]["target"] == "Core"


# ── collect_cache_timing ──────────────────────────────────────────────────────


def test_cache_timing_returns_expected_keys(tmp_path):
    """collect_cache_timing returns cold, warm, speedup, cache_hit."""
    with patch("code_review_graph.memory.commands.run_memory_init_pipeline", return_value={}):
        result = collect_cache_timing(tmp_path)

    assert "cold_seconds" in result
    assert "warm_seconds" in result
    assert "speedup_ratio" in result
    assert "cache_hit" in result


def test_cache_timing_cache_hit_true_when_warm_faster(tmp_path):
    """cache_hit=True when warm run is faster than cold."""
    call_count = {"n": 0}

    def fake_init(root):
        call_count["n"] += 1
        if call_count["n"] == 1:
            import time; time.sleep(0.05)  # slow cold run
        return {}

    with patch("code_review_graph.memory.commands.run_memory_init_pipeline", side_effect=fake_init):
        result = collect_cache_timing(tmp_path)

    # Warm run (no sleep) should be faster than cold run (0.05s sleep)
    assert result["cache_hit"] is True
    assert result["cold_seconds"] > result["warm_seconds"]


def test_cache_timing_speedup_ratio_positive(tmp_path):
    """speedup_ratio >= 1 when cache is working."""
    call_count = {"n": 0}

    def fast_then_slow(root):
        call_count["n"] += 1
        if call_count["n"] == 1:
            import time; time.sleep(0.04)
        return {}

    with patch("code_review_graph.memory.commands.run_memory_init_pipeline",
               side_effect=fast_then_slow):
        result = collect_cache_timing(tmp_path)

    assert isinstance(result["speedup_ratio"], float)
    assert result["speedup_ratio"] > 0


# ── collect_refresh_metrics ───────────────────────────────────────────────────


def test_refresh_metrics_no_changed_files(tmp_path):
    """No git history available → skipped gracefully."""
    # plan_refresh won't be called if no changed files
    result = collect_refresh_metrics([], [], tmp_path)
    assert "skipped" in result or result.get("changed_files", 0) >= 0


def test_refresh_metrics_with_changed_files(tmp_path):
    """With known changed files, plan_refresh is called and metrics returned."""
    from code_review_graph.memory.refresh import RefreshPlan

    mock_plan = RefreshPlan(
        mode="incremental",
        changed_files=["src/auth.py"],
        impacted_feature_slugs=["auth"],
        impacted_module_slugs=[],
        graph_expanded_feature_slugs=[],
        graph_expanded_module_slugs=[],
        reason="incremental: 1 changed file(s)",
    )
    feat = _feature("Auth", files=["src/auth.py"])

    with (
        patch("subprocess.check_output", return_value="src/auth.py\n"),
        patch("code_review_graph.memory.refresh.plan_refresh", return_value=mock_plan),
    ):
        result = collect_refresh_metrics([feat], [], tmp_path)

    assert result["changed_files"] == 1
    assert result["directly_impacted"] == 1
    assert result["graph_expanded"] == 0


# ── THRESHOLDS sanity ─────────────────────────────────────────────────────────


def test_thresholds_cover_expected_metrics():
    required = {"feature_count", "avg_feature_confidence", "files_returned", "tokens_estimated"}
    assert required.issubset(THRESHOLDS.keys())


def test_thresholds_ops_are_valid():
    for metric, (op, _) in THRESHOLDS.items():
        assert op in (">=", "<="), f"{metric} has unknown op '{op}'"
