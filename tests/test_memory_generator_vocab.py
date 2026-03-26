"""Tests for Ticket 1.1 — graph-grounded purpose statements and vocabulary-aware entry points.

Covers:
- _feature_purpose() with vocabulary and node_summaries
- _module_purpose() with vocabulary and node_summaries
- _infer_entry_points() with vocabulary
- _collect_symbols_from_summaries() helper
- _format_purpose_with_symbols() helper
- generate_feature_doc() / generate_module_doc() with node_summaries
"""

from __future__ import annotations

import pytest

from code_review_graph.memory.generator import (
    _collect_symbols_from_summaries,
    _feature_purpose,
    _format_purpose_with_symbols,
    _infer_entry_points,
    _module_purpose,
    generate_feature_doc,
    generate_module_doc,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_feature(
    name="auth",
    files=None,
    tests=None,
    confidence=0.9,
):
    return FeatureMemory(
        name=name,
        files=["src/auth/login.py", "src/auth/tokens.py"] if files is None else files,
        tests=["tests/test_auth.py"] if tests is None else tests,
        confidence=confidence,
    )


def make_module(
    name="src/auth",
    files=None,
    tests=None,
    confidence=0.9,
):
    return ModuleMemory(
        name=name,
        files=["src/auth/__init__.py", "src/auth/login.py"] if files is None else files,
        tests=["tests/test_auth.py"] if tests is None else tests,
        confidence=confidence,
    )


class _FakeNodeSummary:
    """Minimal stand-in for FileNodeSummary — avoids graph DB dependency in tests."""
    def __init__(self, classes=None, functions=None):
        self.classes = classes or []
        self.functions = functions or []
        self.total_nodes = len(self.classes) + len(self.functions)


# ---------------------------------------------------------------------------
# _feature_purpose
# ---------------------------------------------------------------------------


class TestFeaturePurpose:
    def test_fallback_when_no_vocab_and_no_summaries(self):
        feature = make_feature(name="billing", confidence=0.9)
        result = _feature_purpose(feature)
        assert "billing" in result
        assert "file(s)" in result or "classified" in result

    def test_vocab_produces_key_symbols_text(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        vocab = {"src/auth/tokens.py": ["TokenStore", "validate_token"]}
        result = _feature_purpose(feature, vocabulary=vocab)
        assert "auth" in result
        assert "TokenStore" in result or "validate_token" in result
        assert "Key symbols" in result

    def test_node_summaries_produce_defines_provides(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=["TokenStore"],
            functions=["validate_token", "refresh_token"],
        )}
        result = _feature_purpose(feature, node_summaries=ns)
        assert "auth" in result
        assert "TokenStore" in result
        assert "validate_token" in result or "refresh_token" in result
        assert "Defines" in result

    def test_node_summaries_take_priority_over_vocab(self):
        """node_summaries are preferred over flat vocabulary."""
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        vocab = {"src/auth/tokens.py": ["FlatSymbol"]}
        ns = {"src/auth/tokens.py": _FakeNodeSummary(classes=["FromSummary"])}
        result = _feature_purpose(feature, vocabulary=vocab, node_summaries=ns)
        assert "FromSummary" in result

    def test_empty_node_summaries_falls_back_to_vocab(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        vocab = {"src/auth/tokens.py": ["FallbackSym"]}
        result = _feature_purpose(feature, vocabulary=vocab, node_summaries={})
        assert "FallbackSym" in result

    def test_deterministic_with_summaries(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=["AuthMiddleware"],
            functions=["verify_token"],
        )}
        r1 = _feature_purpose(feature, node_summaries=ns)
        r2 = _feature_purpose(feature, node_summaries=ns)
        assert r1 == r2


# ---------------------------------------------------------------------------
# _module_purpose
# ---------------------------------------------------------------------------


class TestModulePurpose:
    def test_fallback_when_no_vocab(self):
        module = make_module(name="src/payments")
        result = _module_purpose(module)
        assert "payments" in result
        assert "file(s)" in result or "classified" in result

    def test_vocab_produces_key_symbols_text(self):
        module = make_module(name="src/payments", files=["src/payments/charge.py"])
        vocab = {"src/payments/charge.py": ["ChargeProcessor", "create_invoice"]}
        result = _module_purpose(module, vocabulary=vocab)
        assert "payments" in result
        assert "ChargeProcessor" in result or "create_invoice" in result

    def test_node_summaries_produce_defines_provides(self):
        module = make_module(name="src/payments", files=["src/payments/charge.py"])
        ns = {"src/payments/charge.py": _FakeNodeSummary(
            classes=["ChargeProcessor"],
            functions=["create_invoice"],
        )}
        result = _module_purpose(module, node_summaries=ns)
        assert "ChargeProcessor" in result
        assert "create_invoice" in result

    def test_classes_only_uses_defines_form(self):
        module = make_module(name="src/models", files=["src/models/user.py"])
        ns = {"src/models/user.py": _FakeNodeSummary(classes=["User", "Profile"], functions=[])}
        result = _module_purpose(module, node_summaries=ns)
        assert "Defines" in result
        # "Provides" only appears in the prefix, not in the symbol section
        assert "Defines `User`" in result or "Defines `Profile`" in result

    def test_functions_only_uses_provides_form(self):
        module = make_module(name="src/utils", files=["src/utils/helpers.py"])
        ns = {"src/utils/helpers.py": _FakeNodeSummary(classes=[], functions=["slugify", "format_date"])}
        result = _module_purpose(module, node_summaries=ns)
        assert "Provides `slugify`" in result or "Provides `format_date`" in result
        assert "Defines" not in result


# ---------------------------------------------------------------------------
# _infer_entry_points with vocabulary
# ---------------------------------------------------------------------------


class TestInferEntryPointsVocabulary:
    def test_stem_match_still_works(self):
        result = _infer_entry_points(["src/auth/views.py", "src/auth/models.py"])
        assert "src/auth/views.py" in result
        assert "src/auth/models.py" not in result

    def test_vocab_detects_handle_function(self):
        vocab = {"src/auth/service.py": ["handle_request", "internal_helper"]}
        result = _infer_entry_points(["src/auth/service.py", "src/auth/models.py"], vocabulary=vocab)
        assert "src/auth/service.py" in result

    def test_vocab_detects_run_function(self):
        vocab = {"src/worker.py": ["run_job", "process_task"]}
        result = _infer_entry_points(["src/worker.py"], vocabulary=vocab)
        assert "src/worker.py" in result

    def test_models_not_detected_as_entry_point(self):
        vocab = {"src/models.py": ["UserModel", "BaseModel"]}
        result = _infer_entry_points(["src/models.py"], vocabulary=vocab)
        assert "src/models.py" not in result

    def test_no_false_positives_without_vocab(self):
        result = _infer_entry_points(["src/auth/models.py", "src/auth/schemas.py"])
        assert result == []

    def test_result_capped_at_five(self):
        files = [f"src/handler{i}.py" for i in range(10)]
        result = _infer_entry_points(files)
        assert len(result) <= 5

    def test_stem_match_deduped_with_vocab_match(self):
        """A file matching both stem and vocab appears only once."""
        vocab = {"src/api/views.py": ["handle_request"]}
        result = _infer_entry_points(["src/api/views.py"], vocabulary=vocab)
        assert result.count("src/api/views.py") == 1


# ---------------------------------------------------------------------------
# _collect_symbols_from_summaries
# ---------------------------------------------------------------------------


class TestCollectSymbolsFromSummaries:
    def test_collects_classes_and_functions(self):
        ns = {
            "a.py": _FakeNodeSummary(classes=["ClassA"], functions=["func_a"]),
            "b.py": _FakeNodeSummary(classes=["ClassB"], functions=["func_b"]),
        }
        classes, functions = _collect_symbols_from_summaries(ns, ["a.py", "b.py"])
        assert "ClassA" in classes
        assert "ClassB" in classes
        assert "func_a" in functions
        assert "func_b" in functions

    def test_deduplicates_across_files(self):
        ns = {
            "a.py": _FakeNodeSummary(classes=["SharedClass"]),
            "b.py": _FakeNodeSummary(classes=["SharedClass"]),
        }
        classes, _ = _collect_symbols_from_summaries(ns, ["a.py", "b.py"])
        assert classes.count("SharedClass") == 1

    def test_respects_max_classes(self):
        ns = {"a.py": _FakeNodeSummary(classes=[f"C{i}" for i in range(10)])}
        classes, _ = _collect_symbols_from_summaries(ns, ["a.py"], max_classes=3)
        assert len(classes) <= 3

    def test_respects_max_functions(self):
        ns = {"a.py": _FakeNodeSummary(functions=[f"f{i}" for i in range(10)])}
        _, functions = _collect_symbols_from_summaries(ns, ["a.py"], max_functions=2)
        assert len(functions) <= 2

    def test_missing_file_in_summaries_is_skipped(self):
        ns = {"a.py": _FakeNodeSummary(classes=["Present"])}
        classes, _ = _collect_symbols_from_summaries(ns, ["a.py", "missing.py"])
        assert classes == ["Present"]

    def test_empty_summaries_returns_empty_lists(self):
        classes, functions = _collect_symbols_from_summaries({}, ["a.py"])
        assert classes == []
        assert functions == []


# ---------------------------------------------------------------------------
# _format_purpose_with_symbols
# ---------------------------------------------------------------------------


class TestFormatPurposeWithSymbols:
    def test_defines_and_provides(self):
        result = _format_purpose_with_symbols("Implements **auth**", ["AuthMiddleware"], ["verify_token"])
        assert "Defines" in result
        assert "provides" in result  # lowercase in mid-sentence
        assert "AuthMiddleware" in result
        assert "verify_token" in result

    def test_classes_only_uses_defines(self):
        result = _format_purpose_with_symbols("Implements **auth**", ["AuthMiddleware"], [])
        assert "Defines `AuthMiddleware`" in result
        assert "Provides" not in result

    def test_functions_only_uses_provides(self):
        result = _format_purpose_with_symbols("Implements **auth**", [], ["verify_token"])
        assert "Provides `verify_token`" in result
        assert "Defines" not in result

    def test_preserves_class_name_case(self):
        result = _format_purpose_with_symbols("prefix", ["AuthMiddleware", "JWTToken"], [])
        assert "AuthMiddleware" in result
        assert "JWTToken" in result

    def test_ends_with_period(self):
        result = _format_purpose_with_symbols("prefix", ["Foo"], ["bar"])
        assert result.endswith(".")

    def test_prefix_present_in_output(self):
        result = _format_purpose_with_symbols("My **prefix**", ["Cls"], [])
        assert "My **prefix**" in result


# ---------------------------------------------------------------------------
# generate_feature_doc / generate_module_doc with node_summaries
# ---------------------------------------------------------------------------


class TestFeatureDocWithNodeSummaries:
    def test_purpose_grounded_when_summaries_present(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        ns = {"src/auth/tokens.py": _FakeNodeSummary(
            classes=["TokenStore"],
            functions=["validate_token"],
        )}
        doc = generate_feature_doc(feature, node_summaries=ns)
        assert "TokenStore" in doc
        assert "validate_token" in doc

    def test_purpose_falls_back_when_summaries_empty(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        doc = generate_feature_doc(feature, node_summaries={})
        assert "auth" in doc
        assert "# Feature: auth" in doc

    def test_backward_compat_no_node_summaries_arg(self):
        """Callers that don't pass node_summaries should still work."""
        feature = make_feature(name="auth")
        doc = generate_feature_doc(feature)
        assert "# Feature: auth" in doc

    def test_deterministic_with_summaries(self):
        feature = make_feature(name="auth", files=["src/auth/tokens.py"])
        ns = {"src/auth/tokens.py": _FakeNodeSummary(classes=["TokenStore"], functions=["verify"])}
        doc1 = generate_feature_doc(feature, node_summaries=ns)
        doc2 = generate_feature_doc(feature, node_summaries=ns)
        assert doc1 == doc2


class TestModuleDocWithNodeSummaries:
    def test_purpose_grounded_when_summaries_present(self):
        module = make_module(name="src/payments", files=["src/payments/charge.py"])
        ns = {"src/payments/charge.py": _FakeNodeSummary(
            classes=["ChargeProcessor"],
            functions=["create_invoice"],
        )}
        doc = generate_module_doc(module, node_summaries=ns)
        assert "ChargeProcessor" in doc
        assert "create_invoice" in doc

    def test_purpose_falls_back_when_no_summaries(self):
        module = make_module(name="src/payments", files=["src/payments/charge.py"])
        doc = generate_module_doc(module)
        assert "payments" in doc
        assert "# Module: src/payments" in doc

    def test_backward_compat_no_node_summaries_arg(self):
        module = make_module(name="src/core")
        doc = generate_module_doc(module)
        assert "# Module: src/core" in doc
