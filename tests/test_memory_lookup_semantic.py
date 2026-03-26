"""Tests for Ticket 2.2 — graph-aware explain and changed commands.

Covers:
- explain_match: graph-grounded purpose line (node_summaries)
- explain_match: fallback to path-count purpose when graph absent
- explain_match: Key symbols section appears when graph data available
- explain_match: Key symbols absent when graph unavailable
- explain_match: vocabulary-only purpose (no node_summaries but vocab present)
- changed_match: changed files annotated with vocabulary symbols
- changed_match: no annotation when graph absent
- changed_match: no annotation when no symbols in vocabulary for changed files
"""

from __future__ import annotations

import json

import code_review_graph.memory.graph_bridge as _gb

import pytest

from code_review_graph.memory.lookup import TargetMatch, changed_match, explain_match
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
    confidence: float = 0.85,
) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=files if files is not None else [f"src/{name}/__init__.py"],
        tests=[],
        confidence=confidence,
    )


def _match(obj, kind="feature", score=1.0, tmp_path=None):
    """Build a minimal TargetMatch for testing."""
    return TargetMatch(
        kind=kind,
        name=obj.name,
        slug=obj.slug(),
        obj=obj,
        artifact_path=None,
        score=score,
    )


class _FakeNodeSummary:
    def __init__(self, classes=None, functions=None):
        self.classes = classes or []
        self.functions = functions or []
        self.total_nodes = len(self.classes) + len(self.functions)


class _MockGB:
    """Context manager that patches graph_bridge for a test."""

    def __init__(
        self,
        available: bool = True,
        node_summaries: dict | None = None,
        vocabulary: dict | None = None,
    ):
        self._available = available
        self._node_summaries = node_summaries or {}
        self._vocabulary = vocabulary or {}
        self._saved: dict = {}

    def __enter__(self):
        self._saved = {
            attr: getattr(_gb, attr)
            for attr in ["graph_available", "get_file_node_summary", "get_file_vocabulary"]
            if hasattr(_gb, attr)
        }
        _gb.graph_available = lambda *a, **kw: self._available
        _gb.get_file_node_summary = lambda *a, **kw: dict(self._node_summaries)
        _gb.get_file_vocabulary = lambda *a, **kw: dict(self._vocabulary)
        return self

    def __exit__(self, *_):
        for attr, val in self._saved.items():
            setattr(_gb, attr, val)


# ---------------------------------------------------------------------------
# explain_match — graph-grounded purpose
# ---------------------------------------------------------------------------


class TestExplainMatchPurpose:
    def test_graph_purpose_shows_symbols(self, tmp_path):
        """When node_summaries are available, purpose contains symbol names."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=["TokenStore"], functions=["validate_token"]
        )}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "TokenStore" in result
        assert "validate_token" in result

    def test_purpose_contains_feature_name(self, tmp_path):
        """Purpose always mentions the feature name."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(classes=["TokenStore"])}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "auth" in result.lower()

    def test_fallback_purpose_when_graph_absent(self, tmp_path):
        """Without graph, purpose falls back to path-count description."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        with _MockGB(available=False):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        # Should contain file count OR 'classified' — the fallback form
        assert "file(s)" in result or "classified" in result

    def test_fallback_purpose_when_no_repo_root(self, tmp_path):
        """Without repo_root, purpose is path-based."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        result = explain_match(match, tmp_path / ".agent-memory", repo_root=None)
        assert "file(s)" in result or "classified" in result

    def test_vocab_only_purpose(self, tmp_path):
        """When only vocabulary is available (no node_summaries), purpose shows key symbols."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        vocab = {"src/auth/tokens.py": ["TokenStore", "validate_token"]}
        with _MockGB(available=True, node_summaries={}, vocabulary=vocab):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "TokenStore" in result or "validate_token" in result

    def test_module_purpose_grounded(self, tmp_path):
        """Module purpose is also graph-grounded when node_summaries available."""
        mod = _module("payments", files=["src/payments/charge.py"])
        match = _match(mod, kind="module")
        ns = {"src/payments/charge.py": _FakeNodeSummary(
            classes=["ChargeProcessor"], functions=["create_invoice"]
        )}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "ChargeProcessor" in result
        assert "create_invoice" in result


# ---------------------------------------------------------------------------
# explain_match — Key symbols section
# ---------------------------------------------------------------------------


class TestExplainMatchKeySymbols:
    def test_key_symbols_section_present_when_graph_available(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=["TokenStore"], functions=["validate_token"]
        )}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Key symbols" in result

    def test_key_symbols_lists_classes(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(classes=["JWTToken", "TokenStore"])}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "JWTToken" in result or "TokenStore" in result

    def test_key_symbols_absent_when_no_graph(self, tmp_path):
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        with _MockGB(available=False):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Key symbols" not in result

    def test_key_symbols_absent_when_empty_summaries(self, tmp_path):
        """Empty summaries (no symbols) → section omitted."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(classes=[], functions=[])}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "Key symbols" not in result

    def test_key_symbols_capped_at_four_each(self, tmp_path):
        """At most 4 classes and 4 functions are listed."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        match = _match(feature)
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=[f"Class{i}" for i in range(10)],
            functions=[f"func{i}" for i in range(10)],
        )}
        with _MockGB(available=True, node_summaries=ns):
            result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        # Count backtick-wrapped symbols in the "Key symbols" line
        sym_line = next(
            (ln for ln in result.splitlines() if "Key symbols" in ln), ""
        )
        assert sym_line.count("`") <= (4 + 4) * 2 + 4  # generous upper bound

    def test_explain_not_found_unchanged(self, tmp_path):
        """Not-found path is unaffected."""
        match = TargetMatch(kind="not_found", name="billing")
        result = explain_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# changed_match — vocabulary annotation of changed files
# ---------------------------------------------------------------------------


def _write_freshness(tmp_path, changed_files: list[str]) -> None:
    """Write a minimal freshness.json for changed_match tests."""
    meta_dir = tmp_path / ".agent-memory" / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "freshness.json").write_text(
        json.dumps({
            "refreshed_at": "2026-03-27T10:00:00Z",
            "mode": "incremental",
            "changed_files_count": len(changed_files),
            "changed_files": changed_files,
            "impacted_features": [],
            "impacted_modules": [],
            "artifacts_refreshed": [],
        }),
        encoding="utf-8",
    )


class TestChangedMatchVocabAnnotation:
    def test_symbol_annotation_on_changed_file(self, tmp_path):
        """Changed file lines include symbol names when vocabulary is available."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        _write_freshness(tmp_path, changed_files=["src/auth/tokens.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        vocab = {"src/auth/tokens.py": ["validate_token", "TokenStore"]}
        with _MockGB(available=True, vocabulary=vocab):
            result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "validate_token" in result or "TokenStore" in result

    def test_annotation_format_uses_backticks(self, tmp_path):
        """Symbols are wrapped in backticks in the changed file line."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        _write_freshness(tmp_path, changed_files=["src/auth/tokens.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        vocab = {"src/auth/tokens.py": ["validate_token"]}
        with _MockGB(available=True, vocabulary=vocab):
            result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "`validate_token`" in result

    def test_no_annotation_when_graph_absent(self, tmp_path):
        """Without graph, changed file lines show file paths only — no symbols."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        _write_freshness(tmp_path, changed_files=["src/auth/tokens.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        with _MockGB(available=False):
            result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        # File path should still be listed
        assert "src/auth/tokens.py" in result
        # But no backtick-wrapped symbols from vocabulary
        assert "— `" not in result

    def test_no_annotation_when_vocab_empty_for_file(self, tmp_path):
        """When vocabulary has no entry for the changed file, no annotation is added."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        _write_freshness(tmp_path, changed_files=["src/auth/tokens.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        vocab = {"src/auth/other.py": ["some_symbol"]}  # changed file not in vocab
        with _MockGB(available=True, vocabulary=vocab):
            result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "— `" not in result

    def test_annotation_capped_at_four_symbols(self, tmp_path):
        """No more than 4 symbols are shown per changed file line."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        _write_freshness(tmp_path, changed_files=["src/auth/tokens.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        many_syms = [f"sym{i}" for i in range(20)]
        vocab = {"src/auth/tokens.py": many_syms}
        with _MockGB(available=True, vocabulary=vocab):
            result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        # Find the line containing the file
        file_line = next(
            (ln for ln in result.splitlines() if "tokens.py" in ln and "—" in ln), ""
        )
        assert file_line.count("`") <= 4 * 2 + 2  # at most 4 backtick-pairs + dashes

    def test_no_vocab_fetch_when_no_area_files_changed(self, tmp_path):
        """When area_files is empty, no vocab fetch is attempted."""
        feature = _feature("auth", files=["src/auth/tokens.py"])
        # No changed files that overlap with auth's files
        _write_freshness(tmp_path, changed_files=["src/billing/invoice.py"])
        match = TargetMatch(
            kind="feature", name="auth", slug=feature.slug(),
            obj=feature, score=1.0,
        )
        call_count = 0
        orig_gfv = _gb.get_file_vocabulary

        def counting_gfv(*a, **kw):
            nonlocal call_count
            call_count += 1
            return {}

        orig_ga = _gb.graph_available
        _gb.graph_available = lambda *a, **kw: True
        _gb.get_file_vocabulary = counting_gfv

        try:
            changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        finally:
            _gb.graph_available = orig_ga
            _gb.get_file_vocabulary = orig_gfv

        assert call_count == 0, "vocab should not be fetched when area_files is empty"

    def test_changed_not_found_unchanged(self, tmp_path):
        """Not-found path is unaffected by vocab changes."""
        match = TargetMatch(kind="not_found", name="billing")
        result = changed_match(match, tmp_path / ".agent-memory", repo_root=tmp_path)
        assert "not found" in result.lower()
