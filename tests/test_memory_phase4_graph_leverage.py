"""Tests for Phase 4 — Deep Graph Leverage.

Ticket 4.1 — CALLS-driven responsibility and entry-point inference
Ticket 4.2 — Hotspots and risk notes via node size / centrality
Ticket 4.3 — CONTAINS / INHERITS / coupling signals

All tests use direct patching of graph_bridge module attributes so they work
without a real graph.db, identical to the pattern used in Phase 2/3 tests.

Coverage:
- CallGraphSignals dataclass defaults
- get_all_call_graph_signals: graph absent → empty dict
- _resolve_entry_points: prefers call_signals over heuristic
- _resolve_entry_points: fallback when call_signals empty
- generate_feature_doc: includes entry points + key helpers when call_signals present
- generate_feature_doc: no key helpers section when call_signals absent
- generate_module_doc: includes structural risks when structural_signals present
- generate_module_doc: inheritance note appears in risks
- generate_module_doc: coupling note appears when score ≥ 0.4
- generate_hotspots_doc: lists large symbols
- generate_hotspots_doc: stub when no hotspots
- generate_hotspots_doc: very large symbols trigger guidance
- HotspotNode dataclass fields
- StructuralDepthSignals dataclass defaults
- get_all_hotspot_nodes: graph absent → empty list
- get_hotspot_nodes: graph absent → empty list
- get_all_structural_depth_signals: graph absent → empty dict
- explain_match: entry points shown when call_sigs available
- explain_match: hotspot note shown when hotspots available
- explain_match: coupling note shown when score high
- explain_match: inheritance note shown when pairs available
- explain_match: graceful fallback when graph absent (no new lines)
- context_builder: entry-point reordering runs without error (graph absent)
- commands pipeline: hotspots.md written to changes/ dir
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import code_review_graph.memory.graph_bridge as _gb

from code_review_graph.memory.graph_bridge import (
    CallGraphSignals,
    HotspotNode,
    StructuralDepthSignals,
)
from code_review_graph.memory.generator import (
    _resolve_entry_points,
    generate_feature_doc,
    generate_hotspots_doc,
    generate_module_doc,
)
from code_review_graph.memory.lookup import TargetMatch, explain_match
from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.scanner import RepoScan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _feature(name: str, files: list[str] | None = None) -> FeatureMemory:
    return FeatureMemory(
        name=name,
        files=files or [f"src/{name}/main.py"],
        tests=[f"tests/test_{name}.py"],
        confidence=0.9,
    )


def _module(name: str, files: list[str] | None = None) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=files or [f"src/{name}/__init__.py", f"src/{name}/core.py"],
        tests=[],
        confidence=0.85,
    )


def _match(obj, kind="feature", score=1.0):
    return TargetMatch(
        kind=kind, name=obj.name, slug=obj.slug(), obj=obj, score=score,
    )


class _MockGB:
    """Context manager that patches graph_bridge for a single test."""

    def __init__(
        self,
        available: bool = True,
        node_summaries: dict | None = None,
        vocabulary: dict | None = None,
        call_signals_map: dict | None = None,
        structural_signals_map: dict | None = None,
        hotspots: list | None = None,
    ):
        self._available = available
        self._node_summaries = node_summaries or {}
        self._vocabulary = vocabulary or {}
        self._call_signals_map = call_signals_map or {}
        self._structural_signals_map = structural_signals_map or {}
        self._hotspots = hotspots or []
        self._saved: dict = {}

    def __enter__(self):
        attrs = [
            "graph_available",
            "get_file_node_summary",
            "get_file_vocabulary",
            "get_all_call_graph_signals",
            "get_all_structural_depth_signals",
            "get_hotspot_nodes",
        ]
        self._saved = {a: getattr(_gb, a) for a in attrs if hasattr(_gb, a)}
        _gb.graph_available = lambda *a, **kw: self._available
        _gb.get_file_node_summary = lambda *a, **kw: dict(self._node_summaries)
        _gb.get_file_vocabulary = lambda *a, **kw: dict(self._vocabulary)
        _gb.get_all_call_graph_signals = lambda *a, **kw: dict(self._call_signals_map)
        _gb.get_all_structural_depth_signals = lambda *a, **kw: dict(self._structural_signals_map)
        _gb.get_hotspot_nodes = lambda *a, **kw: list(self._hotspots)
        return self

    def __exit__(self, *_):
        for attr, val in self._saved.items():
            setattr(_gb, attr, val)


# ---------------------------------------------------------------------------
# Ticket 4.1 — CallGraphSignals dataclass
# ---------------------------------------------------------------------------


class TestCallGraphSignalsDataclass:
    def test_defaults(self):
        sig = CallGraphSignals()
        assert sig.entry_points == []
        assert sig.key_helpers == []
        assert sig.entry_point_symbols == []

    def test_graph_absent_returns_empty(self, tmp_path):
        result = _gb.get_all_call_graph_signals({"grp": ["a.py"]}, tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# Ticket 4.1 — _resolve_entry_points
# ---------------------------------------------------------------------------


class TestResolveEntryPoints:
    def test_prefers_call_signals_over_heuristic(self):
        """When call_signals has entry_points, they appear first."""
        sig = CallGraphSignals(entry_points=["src/auth/api.py"])
        files = ["src/auth/api.py", "src/auth/utils.py"]
        result = _resolve_entry_points(files, vocabulary=None, call_signals=sig)
        assert result[0] == "src/auth/api.py"

    def test_fallback_to_stem_heuristic_when_no_call_signals(self):
        """Without call_signals, heuristic stem matching is used."""
        files = ["src/auth/main.py", "src/auth/utils.py"]
        result = _resolve_entry_points(files, vocabulary=None, call_signals=None)
        assert "src/auth/main.py" in result

    def test_fallback_when_call_signals_empty(self):
        """Empty call_signals.entry_points → falls back to heuristic."""
        sig = CallGraphSignals(entry_points=[])
        files = ["src/auth/server.py", "src/auth/utils.py"]
        result = _resolve_entry_points(files, vocabulary=None, call_signals=sig)
        assert "src/auth/server.py" in result

    def test_call_signals_supplemented_by_heuristic(self):
        """Call-graph EPs are primary; heuristic EPs fill in extras."""
        sig = CallGraphSignals(entry_points=["src/auth/api.py"])
        files = ["src/auth/api.py", "src/auth/main.py", "src/auth/utils.py"]
        result = _resolve_entry_points(files, vocabulary=None, call_signals=sig)
        # api.py from call graph, main.py from heuristic
        assert "src/auth/api.py" in result
        assert len(result) <= 5

    def test_capped_at_five(self):
        sig = CallGraphSignals(
            entry_points=[f"src/ep{i}.py" for i in range(10)]
        )
        result = _resolve_entry_points(
            [f"src/ep{i}.py" for i in range(10)],
            vocabulary=None, call_signals=sig,
        )
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# Ticket 4.1 — generate_feature_doc with call_signals
# ---------------------------------------------------------------------------


class TestFeatureDocCallSignals:
    def test_entry_points_section_present(self):
        feature = _feature("auth", files=["src/auth/api.py", "src/auth/utils.py"])
        sig = CallGraphSignals(entry_points=["src/auth/api.py"])
        doc = generate_feature_doc(feature, call_signals=sig)
        assert "Likely entry points" in doc
        assert "src/auth/api.py" in doc

    def test_key_helpers_section_present(self):
        feature = _feature("auth", files=["src/auth/api.py", "src/auth/utils.py"])
        sig = CallGraphSignals(key_helpers=["src/auth/utils.py"])
        doc = generate_feature_doc(feature, call_signals=sig)
        assert "Key helpers" in doc
        assert "src/auth/utils.py" in doc

    def test_no_key_helpers_when_call_signals_absent(self):
        feature = _feature("auth")
        doc = generate_feature_doc(feature, call_signals=None)
        assert "Key helpers" not in doc

    def test_no_key_helpers_when_helpers_empty(self):
        feature = _feature("auth")
        sig = CallGraphSignals(key_helpers=[])
        doc = generate_feature_doc(feature, call_signals=sig)
        assert "Key helpers" not in doc

    def test_fallback_no_crash_without_call_signals(self):
        feature = _feature("billing")
        doc = generate_feature_doc(feature)
        assert "billing" in doc.lower()


# ---------------------------------------------------------------------------
# Ticket 4.1 — generate_module_doc with call_signals
# ---------------------------------------------------------------------------


class TestModuleDocCallSignals:
    def test_entry_points_shown_in_module_doc(self):
        mod = _module("payments", files=["src/payments/api.py", "src/payments/core.py"])
        sig = CallGraphSignals(entry_points=["src/payments/api.py"])
        doc = generate_module_doc(mod, call_signals=sig)
        assert "Likely entry points" in doc
        assert "src/payments/api.py" in doc

    def test_key_helpers_shown_in_module_doc(self):
        mod = _module("payments", files=["src/payments/api.py", "src/payments/core.py"])
        sig = CallGraphSignals(key_helpers=["src/payments/core.py"])
        doc = generate_module_doc(mod, call_signals=sig)
        assert "Key helpers" in doc
        assert "src/payments/core.py" in doc


# ---------------------------------------------------------------------------
# Ticket 4.3 — StructuralDepthSignals dataclass
# ---------------------------------------------------------------------------


class TestStructuralDepthSignalsDataclass:
    def test_defaults(self):
        sig = StructuralDepthSignals()
        assert sig.inheritance_pairs == []
        assert sig.coupling_files == []
        assert sig.coupling_score == 0.0

    def test_graph_absent_returns_empty(self, tmp_path):
        result = _gb.get_all_structural_depth_signals({"grp": ["a.py"]}, tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# Ticket 4.3 — generate_module_doc with structural_signals
# ---------------------------------------------------------------------------


class TestModuleDocStructuralSignals:
    def test_inheritance_note_in_risks(self):
        mod = _module("auth")
        sig = StructuralDepthSignals(
            inheritance_pairs=[("TokenStore", "BaseStore")],
            coupling_score=0.1,
        )
        doc = generate_module_doc(mod, structural_signals=sig)
        assert "TokenStore" in doc
        assert "BaseStore" in doc

    def test_coupling_note_shown_when_score_high(self):
        mod = _module("core", files=["src/core/a.py", "src/core/b.py", "src/core/c.py"])
        sig = StructuralDepthSignals(
            coupling_score=0.6,
            coupling_files=["src/core/b.py"],
        )
        doc = generate_module_doc(mod, structural_signals=sig)
        assert "coupling" in doc.lower() or "coupled" in doc.lower()

    def test_coupling_note_absent_when_score_low(self):
        mod = _module("utils")
        sig = StructuralDepthSignals(coupling_score=0.1, coupling_files=["src/utils/a.py"])
        doc = generate_module_doc(mod, structural_signals=sig)
        # Low coupling should not produce a coupling warning
        assert "60%" not in doc  # score is 0.1, not 0.6

    def test_no_structural_section_when_signals_absent(self):
        mod = _module("utils")
        doc = generate_module_doc(mod)
        # Default should not have inheritance notes
        assert "extends" not in doc

    def test_multiple_inheritance_pairs(self):
        mod = _module("models")
        sig = StructuralDepthSignals(
            inheritance_pairs=[
                ("TokenStore", "BaseStore"),
                ("UserStore", "BaseStore"),
            ],
        )
        doc = generate_module_doc(mod, structural_signals=sig)
        assert "TokenStore" in doc or "UserStore" in doc


# ---------------------------------------------------------------------------
# Ticket 4.2 — HotspotNode dataclass
# ---------------------------------------------------------------------------


class TestHotspotNodeDataclass:
    def test_fields(self):
        h = HotspotNode(name="process_batch", file_path="src/core.py", kind="Function", line_count=120)
        assert h.name == "process_batch"
        assert h.line_count == 120

    def test_graph_absent_returns_empty_all(self, tmp_path):
        result = _gb.get_all_hotspot_nodes(tmp_path)
        assert result == []

    def test_graph_absent_returns_empty_per_file(self, tmp_path):
        result = _gb.get_hotspot_nodes(["src/a.py"], tmp_path)
        assert result == []


# ---------------------------------------------------------------------------
# Ticket 4.2 — generate_hotspots_doc
# ---------------------------------------------------------------------------


class TestGenerateHotspotsDoc:
    def _scan(self, tmp_path: Path) -> RepoScan:
        return RepoScan(repo_root=tmp_path)

    def test_stub_when_no_hotspots(self, tmp_path):
        doc = generate_hotspots_doc([], self._scan(tmp_path))
        assert "hotspots.md" not in doc.lower() or "no functions" in doc.lower() or "good shape" in doc.lower()
        assert "Hotspot summary" in doc

    def test_lists_large_symbols(self, tmp_path):
        hotspots = [
            HotspotNode("process_batch", "src/core.py", "Function", 120),
            HotspotNode("execute_query", "src/db.py", "Function", 95),
        ]
        doc = generate_hotspots_doc(hotspots, self._scan(tmp_path))
        assert "process_batch" in doc
        assert "execute_query" in doc

    def test_shows_line_count(self, tmp_path):
        hotspots = [HotspotNode("big_fn", "src/a.py", "Function", 200)]
        doc = generate_hotspots_doc(hotspots, self._scan(tmp_path))
        assert "200" in doc

    def test_very_large_triggers_guidance(self, tmp_path):
        hotspots = [HotspotNode("huge_fn", "src/a.py", "Function", 300)]
        doc = generate_hotspots_doc(hotspots, self._scan(tmp_path))
        assert "150" in doc or "breaking" in doc.lower() or "150 lines" in doc

    def test_groups_by_file(self, tmp_path):
        hotspots = [
            HotspotNode("fn1", "src/core.py", "Function", 80),
            HotspotNode("fn2", "src/core.py", "Function", 60),
            HotspotNode("fn3", "src/other.py", "Function", 70),
        ]
        doc = generate_hotspots_doc(hotspots, self._scan(tmp_path))
        assert "src/core.py" in doc
        assert "src/other.py" in doc

    def test_returns_string(self, tmp_path):
        assert isinstance(generate_hotspots_doc([], self._scan(tmp_path)), str)

    def test_has_preamble(self, tmp_path):
        doc = generate_hotspots_doc([], self._scan(tmp_path))
        assert "Auto-generated" in doc

    def test_has_guidance_section(self, tmp_path):
        hotspots = [HotspotNode("big_fn", "src/a.py", "Function", 60)]
        doc = generate_hotspots_doc(hotspots, self._scan(tmp_path))
        assert "Guidance" in doc


# ---------------------------------------------------------------------------
# Ticket 4.2 + 4.3 — explain_match enrichment
# ---------------------------------------------------------------------------


class TestExplainMatchPhase4:
    def test_entry_points_in_explain(self, tmp_path):
        feature = _feature("auth", files=["src/auth/api.py", "src/auth/utils.py"])
        match = _match(feature)
        call_map = {"_exp": CallGraphSignals(entry_points=["src/auth/api.py"])}
        ns = {"src/auth/api.py": MagicMock(classes=["AuthAPI"], functions=[], total_nodes=1)}
        with _MockGB(available=True, node_summaries=ns, call_signals_map=call_map):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Entry points" in result
        assert "src/auth/api.py" in result

    def test_key_helpers_in_explain(self, tmp_path):
        feature = _feature("auth", files=["src/auth/api.py", "src/auth/utils.py"])
        match = _match(feature)
        call_map = {"_exp": CallGraphSignals(key_helpers=["src/auth/utils.py"])}
        ns = {"src/auth/api.py": MagicMock(classes=["AuthAPI"], functions=[], total_nodes=1)}
        with _MockGB(available=True, node_summaries=ns, call_signals_map=call_map):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Key helpers" in result
        assert "src/auth/utils.py" in result

    def test_hotspot_note_in_explain(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": MagicMock(classes=["TokenStore"], functions=[], total_nodes=1)}
        hs = [HotspotNode("validate_all", "src/auth/tokens.py", "Function", 120)]
        with _MockGB(available=True, node_summaries=ns, hotspots=hs):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Hotspots" in result
        assert "validate_all" in result
        assert "120" in result

    def test_inheritance_note_in_explain(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": MagicMock(classes=["TokenStore"], functions=[], total_nodes=1)}
        sd_map = {"_exp": StructuralDepthSignals(inheritance_pairs=[("TokenStore", "BaseStore")])}
        with _MockGB(available=True, node_summaries=ns, structural_signals_map=sd_map):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Inheritance" in result
        assert "TokenStore" in result

    def test_coupling_note_in_explain(self, tmp_path):
        feature = _feature("core", files=["src/core/a.py", "src/core/b.py"])
        match = _match(feature)
        ns = {"src/core/a.py": MagicMock(classes=["CoreA"], functions=[], total_nodes=1)}
        sd_map = {"_exp": StructuralDepthSignals(
            coupling_score=0.7,
            coupling_files=["src/core/b.py"],
        )}
        with _MockGB(available=True, node_summaries=ns, structural_signals_map=sd_map):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Coupling" in result or "coupling" in result

    def test_no_phase4_lines_when_graph_absent(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        with _MockGB(available=False):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Hotspots" not in result
        assert "Coupling" not in result
        assert "Inheritance" not in result

    def test_not_found_unaffected(self, tmp_path):
        match = TargetMatch(kind="not_found", name="billing")
        result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# context_builder — 4.1 entry-point seed reordering
# ---------------------------------------------------------------------------


class TestContextBuilderEntryPointSeeding:
    def test_no_crash_when_graph_absent(self, tmp_path):
        """_enrich_with_graph should not crash when call graph signals fail."""
        from code_review_graph.memory.context_builder import build_context_pack
        from code_review_graph.memory.models import FeatureMemory

        feat = FeatureMemory(
            name="Auth",
            files=["src/auth/main.py", "src/auth/utils.py"],
            tests=[],
            confidence=0.9,
        )
        with _MockGB(available=False):
            pack = build_context_pack(
                "add login endpoint",
                features=[feat],
                modules=[],
                repo_root=tmp_path,
            )
        # Should complete without raising
        assert pack is not None

    def test_entry_points_prepended_when_graph_available(self, tmp_path):
        """When graph provides entry points, they appear in relevant files."""
        from code_review_graph.memory.context_builder import build_context_pack

        feat = FeatureMemory(
            name="Auth",
            files=["src/auth/utils.py", "src/auth/api.py"],
            tests=[],
            confidence=0.9,
        )
        # Entry point is api.py; it should end up in the pack
        call_map = {"_ctx": CallGraphSignals(entry_points=["src/auth/api.py"])}

        orig_gra = _gb.get_all_call_graph_signals
        orig_ga = _gb.graph_available
        orig_grf = getattr(_gb, "get_related_files", None)
        orig_grt = getattr(_gb, "get_related_tests", None)
        orig_gsn = getattr(_gb, "get_structural_neighbors", None)
        orig_gts = getattr(_gb, "get_task_symbol_files", None)

        try:
            _gb.graph_available = lambda *a, **kw: True
            _gb.get_all_call_graph_signals = lambda *a, **kw: dict(call_map)
            if orig_grf:
                _gb.get_related_files = lambda *a, **kw: []
            if orig_grt:
                _gb.get_related_tests = lambda *a, **kw: []
            if orig_gsn:
                _gb.get_structural_neighbors = lambda *a, **kw: []
            if orig_gts:
                _gb.get_task_symbol_files = lambda *a, **kw: []

            pack = build_context_pack(
                "add login endpoint",
                features=[feat],
                modules=[],
                repo_root=tmp_path,
            )
        finally:
            _gb.graph_available = orig_ga
            _gb.get_all_call_graph_signals = orig_gra
            if orig_grf:
                _gb.get_related_files = orig_grf
            if orig_grt:
                _gb.get_related_tests = orig_grt
            if orig_gsn:
                _gb.get_structural_neighbors = orig_gsn
            if orig_gts:
                _gb.get_task_symbol_files = orig_gts

        assert pack is not None
        assert "src/auth/api.py" in pack.relevant_files


# ---------------------------------------------------------------------------
# commands pipeline — hotspots.md written during init
# ---------------------------------------------------------------------------


class TestCommandsHotspotsPipeline:
    def test_hotspots_md_written(self, tmp_path):
        """run_memory_init_pipeline writes changes/hotspots.md."""
        # Write a minimal Python file so scanner finds something
        src = tmp_path / "mylib"
        src.mkdir()
        (src / "auth.py").write_text("def login(): pass\n", encoding="utf-8")

        from code_review_graph.memory.commands import run_memory_init_pipeline
        with _MockGB(available=False):  # graph absent is fine — hotspots.md still written
            result = run_memory_init_pipeline(tmp_path)

        hotspots_path = tmp_path / ".agent-memory" / "changes" / "hotspots.md"
        assert hotspots_path.exists(), "hotspots.md should be written during init"

    def test_hotspots_in_write_statuses(self, tmp_path):
        src = tmp_path / "mylib"
        src.mkdir()
        (src / "auth.py").write_text("def login(): pass\n", encoding="utf-8")

        from code_review_graph.memory.commands import run_memory_init_pipeline
        with _MockGB(available=False):
            result = run_memory_init_pipeline(tmp_path)

        assert ".agent-memory/changes/hotspots.md" in result["write_statuses"]
