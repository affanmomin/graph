"""Tests for Ticket 2.1 — graph-anchored semantic prepare-context.

Covers:
- _graph_symbol_boost() with mocked get_task_symbol_files
- Symbol boost lifts a zero-heuristic item above _MIN_SCORE
- Symbol boost reranks features correctly
- Symbol boost skipped when graph returns no files
- Boost is proportional to fraction of matched files / confidence
- _enrich_with_graph() uses extended seeds (heuristic + symbol) for test discovery
- _enrich_with_graph() accepts precomputed symbol_files without re-querying
- build_context_pack() summary notes symbol routing
- build_context_pack() end-to-end with mocked graph returns correct feature
"""

from __future__ import annotations

import code_review_graph.memory.graph_bridge as _gb

import pytest

from code_review_graph.memory.context_builder import (
    _build_summary,
    _enrich_with_graph,
    _graph_symbol_boost,
    build_context_pack,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feature(
    name: str,
    files: list[str] | None = None,
    confidence: float = 0.9,
) -> FeatureMemory:
    return FeatureMemory(
        name=name,
        files=files if files is not None else [f"src/{name}/main.py"],
        tests=[f"tests/test_{name}.py"],
        confidence=confidence,
    )


def _module(
    name: str,
    files: list[str] | None = None,
    confidence: float = 0.9,
) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=files if files is not None else [f"src/{name}/__init__.py"],
        tests=[],
        confidence=confidence,
    )


class _MockGB:
    """Context manager that monkey-patches graph_bridge for a test."""

    def __init__(
        self,
        symbol_files: list[str] | None = None,
        related_tests: list[str] | None = None,
        available: bool = True,
    ):
        self._symbol_files = symbol_files or []
        self._related_tests = related_tests or []
        self._available = available
        self._saved: dict = {}

    def __enter__(self):
        self._saved = {
            attr: getattr(_gb, attr)
            for attr in [
                "graph_available",
                "get_file_vocabulary",
                "get_task_symbol_files",
                "get_related_files",
                "get_related_tests",
                "get_structural_neighbors",
                "get_file_node_summary",
            ]
            if hasattr(_gb, attr)
        }
        _gb.graph_available = lambda *a, **kw: self._available
        _gb.get_file_vocabulary = lambda *a, **kw: {}
        _gb.get_task_symbol_files = lambda *a, **kw: list(self._symbol_files)
        _gb.get_related_files = lambda *a, **kw: []
        _gb.get_related_tests = lambda *a, **kw: list(self._related_tests)
        _gb.get_structural_neighbors = lambda *a, **kw: []
        return self

    def __exit__(self, *_):
        for attr, val in self._saved.items():
            setattr(_gb, attr, val)


# ---------------------------------------------------------------------------
# _graph_symbol_boost
# ---------------------------------------------------------------------------


class TestGraphSymbolBoost:
    def test_positive_boost_when_file_matches(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            boosts, sym_files = _graph_symbol_boost("fix token expiry", [feature], [], tmp_path)
        assert id(feature) in boosts
        assert boosts[id(feature)] > 0
        assert "src/auth/tokens.py" in sym_files

    def test_no_boost_for_unmatched_feature(self, tmp_path):
        auth = _feature("auth", files=["src/auth/tokens.py"])
        billing = _feature("billing", files=["src/billing/invoice.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            boosts, _ = _graph_symbol_boost("fix token expiry", [auth, billing], [], tmp_path)
        assert id(auth) in boosts
        assert id(billing) not in boosts

    def test_empty_when_no_symbol_files_returned(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        with _MockGB(symbol_files=[]):
            boosts, sym_files = _graph_symbol_boost("fix auth bug", [feature], [], tmp_path)
        assert boosts == {}
        assert sym_files == []

    def test_boost_proportional_to_match_fraction(self, tmp_path):
        """Feature with 2/3 files matched boosts more than one with 1/2 matched."""
        big = _feature("big", files=["src/big/a.py", "src/big/b.py", "src/big/c.py"])
        small = _feature("small", files=["src/small/a.py", "src/small/b.py"])
        with _MockGB(symbol_files=["src/big/a.py", "src/big/b.py", "src/small/a.py"]):
            boosts, _ = _graph_symbol_boost("task", [big, small], [], tmp_path)
        assert boosts[id(big)] > boosts[id(small)]

    def test_boost_respects_confidence(self, tmp_path):
        """Low-confidence feature gets a smaller boost than high-confidence one."""
        high = _feature("high", files=["src/x/y.py"], confidence=0.9)
        low = _feature("low", files=["src/x/y.py"], confidence=0.3)
        with _MockGB(symbol_files=["src/x/y.py"]):
            boosts, _ = _graph_symbol_boost("task", [high, low], [], tmp_path)
        assert boosts[id(high)] > boosts[id(low)]

    def test_boost_capped_below_030(self, tmp_path):
        """Boost never exceeds ~0.30 (confidence=1.0 ceiling)."""
        feature = _feature("auth", files=["src/auth/tokens.py"], confidence=1.0)
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            boosts, _ = _graph_symbol_boost("task", [feature], [], tmp_path)
        assert boosts[id(feature)] <= 0.31  # small floating-point tolerance

    def test_returns_symbol_files_for_caller(self, tmp_path):
        """Second return value is the raw symbol_files list (avoids second DB open)."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        expected = ["src/auth/tokens.py", "src/auth/utils.py"]
        with _MockGB(symbol_files=expected):
            _, sym_files = _graph_symbol_boost("task", [feature], [], tmp_path)
        assert sym_files == expected

    def test_module_also_boosted(self, tmp_path):
        """Modules receive a boost when their files are in the symbol file set."""
        mod = _module("payments", files=["src/payments/charge.py"])
        with _MockGB(symbol_files=["src/payments/charge.py"]):
            boosts, _ = _graph_symbol_boost("create invoice", [], [mod], tmp_path)
        assert id(mod) in boosts
        assert boosts[id(mod)] > 0

    def test_graceful_on_exception(self, tmp_path):
        """Returns empty dicts/lists even if the bridge raises."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        orig = _gb.get_task_symbol_files
        _gb.get_task_symbol_files = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("db gone"))
        try:
            boosts, sym_files = _graph_symbol_boost("fix token", [feature], [], tmp_path)
        finally:
            _gb.get_task_symbol_files = orig
        assert boosts == {}
        assert sym_files == []


# ---------------------------------------------------------------------------
# _enrich_with_graph — symbol files as extended seeds for test discovery
# ---------------------------------------------------------------------------


class TestEnrichWithGraphSymbolSeeds:
    def test_precomputed_symbol_files_not_re_queried(self, tmp_path):
        """When symbol_files is passed in, get_task_symbol_files is NOT called."""
        files = ["src/auth/login.py"]
        tests: list[str] = []
        call_count = 0

        def counting_gtsf(*a, **kw):
            nonlocal call_count
            call_count += 1
            return []

        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            _gb.get_task_symbol_files = counting_gtsf  # override inside context
            _enrich_with_graph(
                files, tests, tmp_path, task="fix token",
                symbol_files=["src/auth/tokens.py"],
            )

        assert call_count == 0, "get_task_symbol_files should not be called when symbol_files given"

    def test_extended_seeds_include_symbol_file(self, tmp_path):
        """get_related_tests is called with heuristic seed + symbol file combined."""
        seeds_used: list[list[str]] = []

        orig_grt = _gb.get_related_tests
        orig_ga = _gb.graph_available
        orig_grf = _gb.get_related_files
        orig_gsn = _gb.get_structural_neighbors

        def capturing_grt(seed_files, *a, **kw):
            seeds_used.append(list(seed_files))
            return []

        _gb.graph_available = lambda *a, **kw: True
        _gb.get_related_files = lambda *a, **kw: []
        _gb.get_related_tests = capturing_grt
        _gb.get_structural_neighbors = lambda *a, **kw: []

        try:
            _enrich_with_graph(
                ["src/auth/login.py"], [], tmp_path,
                task="fix token",
                symbol_files=["src/auth/tokens.py"],
            )
        finally:
            _gb.graph_available = orig_ga
            _gb.get_related_files = orig_grf
            _gb.get_related_tests = orig_grt
            _gb.get_structural_neighbors = orig_gsn

        assert len(seeds_used) == 1
        assert "src/auth/login.py" in seeds_used[0]
        assert "src/auth/tokens.py" in seeds_used[0]

    def test_symbol_file_appended_to_files(self, tmp_path):
        """A symbol file not already in files gets added."""
        files = ["src/auth/login.py"]
        tests: list[str] = []
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            enriched = _enrich_with_graph(
                files, tests, tmp_path,
                task="fix token",
                symbol_files=["src/auth/tokens.py"],
            )
        assert enriched is True
        assert "src/auth/tokens.py" in files

    def test_no_enrichment_when_graph_unavailable(self, tmp_path):
        files = ["src/auth/login.py"]
        with _MockGB(available=False):
            result = _enrich_with_graph(files, [], tmp_path, task="fix auth")
        assert result is False
        assert files == ["src/auth/login.py"]

    def test_symbol_file_not_duplicated(self, tmp_path):
        """If symbol file is already in files, it is not appended again."""
        files = ["src/auth/tokens.py"]
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            _enrich_with_graph(files, [], tmp_path, task="fix token",
                               symbol_files=["src/auth/tokens.py"])
        assert files.count("src/auth/tokens.py") == 1


# ---------------------------------------------------------------------------
# _build_summary — symbol routing note
# ---------------------------------------------------------------------------


class TestBuildSummarySymbolRouting:
    def _base(self):
        return dict(
            task="fix token expiry",
            relevant_features=[_feature("auth", files=["src/auth/tokens.py"])],
            relevant_modules=[],
            files=["src/auth/tokens.py"],
            warnings=[],
            fallback=False,
        )

    def test_symbol_routing_note_present(self):
        result = _build_summary(**self._base(), graph_symbol_routed=True)
        assert "symbol" in result.lower()

    def test_symbol_routing_suppresses_generic_enriched_note(self):
        """When both flags are True, only symbol routing note appears."""
        result = _build_summary(**self._base(), graph_enriched=True, graph_symbol_routed=True)
        assert "symbol" in result.lower()
        assert "structural relationships" not in result

    def test_enriched_note_when_not_symbol_routed(self):
        result = _build_summary(**self._base(), graph_enriched=True, graph_symbol_routed=False)
        assert "structural relationships" in result

    def test_no_enrichment_note_when_neither_flag(self):
        result = _build_summary(**self._base(), graph_enriched=False, graph_symbol_routed=False)
        assert "symbol" not in result.lower()
        assert "structural" not in result


# ---------------------------------------------------------------------------
# build_context_pack — end-to-end semantic routing
# ---------------------------------------------------------------------------


class TestBuildContextPackSemanticRouting:
    def test_symbol_match_lifts_zero_heuristic_feature(self, tmp_path):
        """Task 'fix validate_token' has no directory overlap with the 'auth' feature
        but tokens.py defines validate_token — auth should still be selected."""
        auth = _feature("auth", files=["src/auth/tokens.py"])
        billing = _feature("billing", files=["src/billing/invoice.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack = build_context_pack("fix validate_token", [auth, billing], [], repo_root=tmp_path)
        assert "auth" in pack.relevant_features

    def test_unrelated_feature_stays_out(self, tmp_path):
        """Feature with no lexical or symbol match is excluded."""
        auth = _feature("auth", files=["src/auth/tokens.py"])
        notifs = _feature("notifications", files=["src/notifications/email.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack = build_context_pack("fix validate_token", [auth, notifs], [], repo_root=tmp_path)
        assert "notifications" not in pack.relevant_features

    def test_symbol_file_appears_in_relevant_files(self, tmp_path):
        auth = _feature("auth", files=["src/auth/tokens.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack = build_context_pack("fix validate_token", [auth], [], repo_root=tmp_path)
        assert "src/auth/tokens.py" in pack.relevant_files

    def test_symbol_routing_reflected_in_summary(self, tmp_path):
        auth = _feature("auth", files=["src/auth/tokens.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack = build_context_pack("fix validate_token", [auth], [], repo_root=tmp_path)
        assert "symbol" in pack.summary.lower()

    def test_symbol_test_discovery_via_extended_seeds(self, tmp_path):
        """Tests discovered via symbol-matched seeds appear in relevant_tests."""
        misc = _feature("misc", files=["src/misc/utils.py"])
        seeds_used: list[list[str]] = []

        orig_ga = _gb.graph_available
        orig_gfv = getattr(_gb, "get_file_vocabulary", None)
        orig_gtsf = _gb.get_task_symbol_files
        orig_grf = _gb.get_related_files
        orig_grt = _gb.get_related_tests
        orig_gsn = _gb.get_structural_neighbors

        def capturing_grt(seed_files, *a, **kw):
            seeds_used.append(list(seed_files))
            return ["tests/test_tokens.py"]

        _gb.graph_available = lambda *a, **kw: True
        if orig_gfv is not None:
            _gb.get_file_vocabulary = lambda *a, **kw: {}
        _gb.get_task_symbol_files = lambda *a, **kw: ["src/auth/tokens.py"]
        _gb.get_related_files = lambda *a, **kw: []
        _gb.get_related_tests = capturing_grt
        _gb.get_structural_neighbors = lambda *a, **kw: []

        try:
            pack = build_context_pack("fix validate_token", [misc], [], repo_root=tmp_path)
        finally:
            _gb.graph_available = orig_ga
            if orig_gfv is not None:
                _gb.get_file_vocabulary = orig_gfv
            _gb.get_task_symbol_files = orig_gtsf
            _gb.get_related_files = orig_grf
            _gb.get_related_tests = orig_grt
            _gb.get_structural_neighbors = orig_gsn

        assert "tests/test_tokens.py" in pack.relevant_tests
        if seeds_used:
            assert "src/auth/tokens.py" in seeds_used[0]

    def test_no_symbol_routing_note_when_no_graph(self, tmp_path):
        """When graph is unavailable, summary does not claim symbol routing."""
        auth = _feature("auth", files=["src/auth/tokens.py"])
        billing = _feature("billing", files=["src/billing/invoice.py"])
        with _MockGB(available=False, symbol_files=[]):
            pack = build_context_pack("fix validate_token", [auth, billing], [], repo_root=tmp_path)
        assert "symbol" not in pack.summary.lower()

    def test_deterministic_with_symbol_boost(self, tmp_path):
        """Same inputs always yield the same pack."""
        auth = _feature("auth", files=["src/auth/tokens.py"])
        billing = _feature("billing", files=["src/billing/invoice.py"])
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack1 = build_context_pack("fix validate_token", [auth, billing], [], repo_root=tmp_path)
        with _MockGB(symbol_files=["src/auth/tokens.py"]):
            pack2 = build_context_pack("fix validate_token", [auth, billing], [], repo_root=tmp_path)
        assert pack1.relevant_features == pack2.relevant_features
        assert pack1.relevant_files == pack2.relevant_files

    def test_no_graph_no_crash(self, tmp_path):
        """Pack is still returned when repo_root is None."""
        auth = _feature("auth", files=["src/auth/tokens.py"])
        pack = build_context_pack("fix validate_token", [auth], [], repo_root=None)
        assert pack is not None
