"""Tests for compute_quality_verdict() — Ticket 1.2.

Covers:
- All four verdict levels: rich, good, sparse, weak
- graph_used / vocabulary_used signals
- guidance messages for degraded states
- avg_confidence computation
- edge cases: empty features/modules
"""

from __future__ import annotations

import pytest

from code_review_graph.memory.commands import compute_quality_verdict
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_features(n: int, confidence: float = 0.9) -> list[FeatureMemory]:
    return [FeatureMemory(name=f"feature{i}", confidence=confidence) for i in range(n)]


def make_modules(n: int, confidence: float = 0.9) -> list[ModuleMemory]:
    return [ModuleMemory(name=f"module{i}", confidence=confidence) for i in range(n)]


# ---------------------------------------------------------------------------
# Weak verdict
# ---------------------------------------------------------------------------


class TestWeakVerdict:
    def test_zero_features_no_graph(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "weak"

    def test_zero_features_with_graph(self):
        """Graph being present doesn't rescue a weak verdict when there are no features."""
        result = compute_quality_verdict([], [], graph_used=True, vocabulary_used=True)
        assert result["verdict"] == "weak"

    def test_zero_features_one_module(self):
        result = compute_quality_verdict([], make_modules(1), graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "weak"

    def test_weak_guidance_is_not_empty(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert len(result["guidance"]) > 0

    def test_weak_no_graph_guidance_mentions_build(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        all_guidance = " ".join(result["guidance"])
        assert "repomind build" in all_guidance or "build" in all_guidance

    def test_weak_message_mentions_zero_features(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert "0 features" in result["message"] or "Weak" in result["message"]

    def test_weak_avg_confidence_zero_when_no_items(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert result["avg_confidence"] == 0.0


# ---------------------------------------------------------------------------
# Sparse verdict
# ---------------------------------------------------------------------------


class TestSparseVerdict:
    def test_one_feature_no_modules(self):
        result = compute_quality_verdict(make_features(1), [], graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "sparse"

    def test_one_feature_two_modules(self):
        result = compute_quality_verdict(make_features(1), make_modules(2), graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "sparse"

    def test_sparse_no_graph_guidance_mentions_build(self):
        result = compute_quality_verdict(make_features(1), [], graph_used=False, vocabulary_used=False)
        all_guidance = " ".join(result["guidance"])
        assert "build" in all_guidance

    def test_sparse_low_confidence_guidance(self):
        """Low-confidence areas should be flagged in guidance."""
        result = compute_quality_verdict(
            make_features(1, confidence=0.4), [],
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "sparse"
        all_guidance = " ".join(result["guidance"])
        assert "low confidence" in all_guidance.lower() or "confidence" in all_guidance.lower()

    def test_sparse_with_graph_no_build_guidance(self):
        """When graph IS available, the 'run build' guidance should not appear."""
        result = compute_quality_verdict(make_features(1), [], graph_used=True, vocabulary_used=True)
        assert result["verdict"] == "sparse"
        all_guidance = " ".join(result["guidance"])
        assert "repomind build" not in all_guidance


# ---------------------------------------------------------------------------
# Good verdict
# ---------------------------------------------------------------------------


class TestGoodVerdict:
    def test_two_features(self):
        result = compute_quality_verdict(make_features(2), [], graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "good"

    def test_three_modules(self):
        result = compute_quality_verdict([], make_modules(3), graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "good"

    def test_two_features_two_modules(self):
        result = compute_quality_verdict(make_features(2), make_modules(2), graph_used=True, vocabulary_used=True)
        assert result["verdict"] == "good"

    def test_good_heuristic_guidance_mentions_build(self):
        """Without graph, good verdict should suggest running build."""
        result = compute_quality_verdict(make_features(2), [], graph_used=False, vocabulary_used=False)
        assert result["verdict"] == "good"
        all_guidance = " ".join(result["guidance"])
        assert "build" in all_guidance

    def test_good_with_graph_no_guidance(self):
        """With graph present, good verdict needs no extra guidance."""
        result = compute_quality_verdict(make_features(2), make_modules(2), graph_used=True, vocabulary_used=True)
        assert result["verdict"] == "good"
        assert result["guidance"] == []

    def test_good_not_upgraded_to_rich_without_graph(self):
        """Many features but no graph → still good, not rich."""
        result = compute_quality_verdict(
            make_features(5), make_modules(5),
            graph_used=False, vocabulary_used=False,
        )
        assert result["verdict"] != "rich"

    def test_good_not_upgraded_to_rich_with_low_confidence(self):
        """Graph present but low confidence → still good, not rich."""
        result = compute_quality_verdict(
            make_features(4, confidence=0.5), make_modules(5, confidence=0.5),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "good"


# ---------------------------------------------------------------------------
# Rich verdict
# ---------------------------------------------------------------------------


class TestRichVerdict:
    def test_rich_requires_graph_vocabulary_and_enough_areas(self):
        result = compute_quality_verdict(
            make_features(3), make_modules(4),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "rich"

    def test_rich_three_features_four_modules(self):
        result = compute_quality_verdict(
            make_features(3, confidence=0.9), make_modules(4, confidence=0.9),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "rich"

    def test_rich_requires_vocabulary(self):
        """Graph present but no vocabulary → cannot be rich."""
        result = compute_quality_verdict(
            make_features(4), make_modules(5),
            graph_used=True, vocabulary_used=False,
        )
        assert result["verdict"] != "rich"

    def test_rich_message_mentions_graph_grounded(self):
        result = compute_quality_verdict(
            make_features(4), make_modules(4),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "rich"
        assert "graph" in result["message"].lower() or "Rich" in result["message"]

    def test_rich_no_guidance(self):
        result = compute_quality_verdict(
            make_features(4), make_modules(4),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "rich"
        assert result["guidance"] == []

    def test_rich_min_feature_threshold(self):
        """Exactly 3 features and 0 modules → does not reach 4-module threshold, needs 3+ features."""
        result = compute_quality_verdict(
            make_features(3), [],
            graph_used=True, vocabulary_used=True,
        )
        # 3 features qualifies (>= 3 features is sufficient for rich)
        assert result["verdict"] == "rich"

    def test_rich_min_module_threshold(self):
        """Exactly 4 modules and 0 features → qualifies for rich via module threshold."""
        result = compute_quality_verdict(
            [], make_modules(4),
            graph_used=True, vocabulary_used=True,
        )
        assert result["verdict"] == "rich"


# ---------------------------------------------------------------------------
# Return structure
# ---------------------------------------------------------------------------


class TestVerdictStructure:
    def test_returns_all_required_keys(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        for key in ("verdict", "message", "guidance", "avg_confidence", "graph_used", "vocabulary_used"):
            assert key in result

    def test_verdict_is_one_of_four_levels(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert result["verdict"] in ("rich", "good", "sparse", "weak")

    def test_guidance_is_list(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=False)
        assert isinstance(result["guidance"], list)

    def test_avg_confidence_reflects_input(self):
        result = compute_quality_verdict(
            make_features(2, confidence=0.8), [],
            graph_used=True, vocabulary_used=True,
        )
        assert result["avg_confidence"] == pytest.approx(0.8, abs=0.01)

    def test_avg_confidence_mixed_features_modules(self):
        """avg_confidence is computed over both features AND modules."""
        features = make_features(2, confidence=1.0)
        modules = make_modules(2, confidence=0.6)
        result = compute_quality_verdict(features, modules, graph_used=True, vocabulary_used=True)
        # avg of [1.0, 1.0, 0.6, 0.6] = 0.8
        assert result["avg_confidence"] == pytest.approx(0.8, abs=0.01)

    def test_graph_used_propagated(self):
        result = compute_quality_verdict([], [], graph_used=True, vocabulary_used=False)
        assert result["graph_used"] is True

    def test_vocabulary_used_propagated(self):
        result = compute_quality_verdict([], [], graph_used=False, vocabulary_used=True)
        assert result["vocabulary_used"] is True

    def test_deterministic(self):
        """Same inputs always produce same output."""
        features = make_features(2)
        modules = make_modules(2)
        r1 = compute_quality_verdict(features, modules, graph_used=True, vocabulary_used=True)
        r2 = compute_quality_verdict(features, modules, graph_used=True, vocabulary_used=True)
        assert r1 == r2
