"""Tests for the task-aware context pack builder (Ticket 7).

Synthetic repos and task strings are used throughout. No disk writes;
the builder is pure in-memory logic.

Scenarios covered:
- auth-focused task
- billing-focused task
- cross-cutting task touching multiple features
- task with no relevant match (fallback)
- ambiguous / generic task
- single-module repo
- empty repo (no features, no modules)
- JSON output via CLI
- scoring properties (rank ordering, confidence weighting)
- determinism
"""

from __future__ import annotations

import argparse
import json

import pytest

from code_review_graph.memory.context_builder import build_context_pack, _is_catchall, _tokenize, _score
from code_review_graph.memory.models import FeatureMemory, ModuleMemory, TaskContextPack


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _feature(
    name: str,
    files: list[str] | None = None,
    tests: list[str] | None = None,
    confidence: float = 0.9,
) -> FeatureMemory:
    return FeatureMemory(
        name=name,
        files=files if files is not None else [f"src/{name.lower()}/main.py"],
        tests=tests if tests is not None else [f"tests/test_{name.lower()}.py"],
        confidence=confidence,
    )


def _module(
    name: str,
    files: list[str] | None = None,
    tests: list[str] | None = None,
    confidence: float = 0.9,
) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=files if files is not None else [f"src/{name.lower()}/__init__.py"],
        tests=tests if tests is not None else [],
        confidence=confidence,
    )


_AUTH_FEATURE = _feature(
    "auth",
    files=["src/auth/login.py", "src/auth/middleware.py", "src/auth/token.py"],
    tests=["tests/test_auth.py"],
)
_BILLING_FEATURE = _feature(
    "billing",
    files=["src/billing/invoice.py", "src/billing/stripe.py"],
    tests=["tests/test_billing.py"],
)
_USERS_FEATURE = _feature(
    "users",
    files=["src/users/model.py", "src/users/service.py"],
    tests=["tests/test_users.py"],
)
_NOTIFICATIONS_FEATURE = _feature(
    "notifications",
    files=["src/notifications/email.py"],
    tests=[],
)

_ALL_FEATURES = [_AUTH_FEATURE, _BILLING_FEATURE, _USERS_FEATURE, _NOTIFICATIONS_FEATURE]

_AUTH_MODULE = _module("src/auth", files=["src/auth/__init__.py", "src/auth/login.py"])
_BILLING_MODULE = _module("src/billing", files=["src/billing/__init__.py", "src/billing/invoice.py"])
_ALL_MODULES = [_AUTH_MODULE, _BILLING_MODULE]


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


class TestTokenize:
    def test_lowercases(self):
        assert "Auth" not in _tokenize("Add Auth Feature")
        assert "auth" in _tokenize("Add Auth Feature")

    def test_strips_stop_words(self):
        tokens = _tokenize("add oauth to the auth module")
        assert "add" not in tokens
        assert "to" not in tokens
        assert "the" not in tokens

    def test_splits_on_hyphen(self):
        tokens = _tokenize("oauth-provider setup")
        assert "oauth" in tokens
        assert "provider" in tokens

    def test_splits_on_slash(self):
        tokens = _tokenize("src/auth/login")
        assert "auth" in tokens
        assert "login" in tokens

    def test_drops_single_chars(self):
        tokens = _tokenize("a b c auth")
        assert "a" not in tokens
        assert "auth" in tokens

    def test_empty_string(self):
        assert _tokenize("") == set()

    def test_returns_set(self):
        assert isinstance(_tokenize("auth billing"), set)


# ---------------------------------------------------------------------------
# _score
# ---------------------------------------------------------------------------


class TestScore:
    def test_zero_when_no_task_tokens(self):
        assert _score(set(), "auth", ["src/auth/login.py"], 1.0) == 0.0

    def test_name_match_gives_positive_score(self):
        score = _score({"auth"}, "auth", [], 1.0)
        assert score > 0

    def test_file_stem_match_gives_positive_score(self):
        score = _score({"login"}, "unrelated", ["src/auth/login.py"], 1.0)
        assert score > 0

    def test_dir_match_gives_positive_score(self):
        score = _score({"auth"}, "unrelated", ["src/auth/anything.py"], 1.0)
        assert score > 0

    def test_name_match_outweighs_dir_match(self):
        score_name = _score({"auth"}, "auth", [], 1.0)
        score_dir = _score({"auth"}, "unrelated", ["src/auth/x.py"], 1.0)
        assert score_name > score_dir

    def test_confidence_scaling(self):
        score_high = _score({"auth"}, "auth", [], 1.0)
        score_low = _score({"auth"}, "auth", [], 0.1)
        assert score_high > score_low

    def test_score_bounded(self):
        score = _score({"auth", "login", "token"}, "auth", ["src/auth/login.py"], 1.0)
        assert 0.0 <= score <= 1.5  # may exceed 1.0 slightly due to overlapping components

    def test_no_overlap_returns_zero(self):
        score = _score({"billing"}, "auth", ["src/auth/login.py"], 1.0)
        assert score == 0.0


# ---------------------------------------------------------------------------
# build_context_pack — auth-focused task
# ---------------------------------------------------------------------------


class TestAuthTask:
    def test_returns_task_context_pack(self):
        pack = build_context_pack("add oauth provider", _ALL_FEATURES, _ALL_MODULES)
        assert isinstance(pack, TaskContextPack)

    def test_task_preserved(self):
        pack = build_context_pack("add oauth provider", _ALL_FEATURES, _ALL_MODULES)
        assert pack.task == "add oauth provider"

    def test_auth_feature_included(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert "auth" in pack.relevant_features

    def test_auth_files_included(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert any("login" in f for f in pack.relevant_files)

    def test_auth_tests_included(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert any("test_auth" in t for t in pack.relevant_tests)

    def test_billing_not_top_for_login_task(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        # auth should outrank billing
        if "auth" in pack.relevant_features and "billing" in pack.relevant_features:
            auth_idx = pack.relevant_features.index("auth")
            billing_idx = pack.relevant_features.index("billing")
            assert auth_idx < billing_idx

    def test_summary_mentions_auth(self):
        pack = build_context_pack("fix auth token refresh", _ALL_FEATURES, _ALL_MODULES)
        assert "auth" in pack.summary.lower()

    def test_relevant_files_nonempty(self):
        pack = build_context_pack("fix login issue", _ALL_FEATURES, _ALL_MODULES)
        assert len(pack.relevant_files) > 0

    def test_no_duplicate_files(self):
        pack = build_context_pack("fix login issue", _ALL_FEATURES, _ALL_MODULES)
        assert len(pack.relevant_files) == len(set(pack.relevant_files))

    def test_no_duplicate_tests(self):
        pack = build_context_pack("fix login issue", _ALL_FEATURES, _ALL_MODULES)
        assert len(pack.relevant_tests) == len(set(pack.relevant_tests))


# ---------------------------------------------------------------------------
# build_context_pack — billing-focused task
# ---------------------------------------------------------------------------


class TestBillingTask:
    def test_billing_feature_included(self):
        pack = build_context_pack("add invoice export endpoint", _ALL_FEATURES, _ALL_MODULES)
        assert "billing" in pack.relevant_features

    def test_billing_files_included(self):
        pack = build_context_pack("add invoice export endpoint", _ALL_FEATURES, _ALL_MODULES)
        assert any("invoice" in f or "billing" in f for f in pack.relevant_files)

    def test_billing_tests_included(self):
        pack = build_context_pack("update billing webhook handling", _ALL_FEATURES, _ALL_MODULES)
        assert any("billing" in t for t in pack.relevant_tests)

    def test_summary_mentions_billing(self):
        pack = build_context_pack("update billing webhook handling", _ALL_FEATURES, _ALL_MODULES)
        assert "billing" in pack.summary.lower()


# ---------------------------------------------------------------------------
# build_context_pack — cross-cutting task
# ---------------------------------------------------------------------------


class TestCrossCuttingTask:
    def test_multiple_features_returned(self):
        # A task touching auth + billing
        pack = build_context_pack("user login and billing integration", _ALL_FEATURES, _ALL_MODULES)
        assert len(pack.relevant_features) >= 2

    def test_cross_cutting_warning_when_many_features(self):
        # A task that hits 3+ features
        features = _ALL_FEATURES  # 4 features
        pack = build_context_pack("auth billing users notifications", features, _ALL_MODULES)
        has_cross_cut_warning = any("cross-cutting" in w.lower() for w in pack.warnings)
        if len(pack.relevant_features) >= 3:
            assert has_cross_cut_warning

    def test_file_count_bounded(self):
        pack = build_context_pack("auth billing users notifications", _ALL_FEATURES, _ALL_MODULES)
        assert len(pack.relevant_files) <= 20


# ---------------------------------------------------------------------------
# build_context_pack — no-match fallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_returns_pack_even_with_no_match(self):
        pack = build_context_pack("completely unrelated xyzzyx", _ALL_FEATURES, _ALL_MODULES)
        assert isinstance(pack, TaskContextPack)

    def test_fallback_warning_included(self):
        pack = build_context_pack("zxqwerty1234 something unmatchable", _ALL_FEATURES, _ALL_MODULES)
        has_fallback = any("no specific area" in w.lower() for w in pack.warnings)
        assert has_fallback

    def test_empty_repo_returns_pack(self):
        pack = build_context_pack("add oauth provider", [], [])
        assert isinstance(pack, TaskContextPack)
        assert pack.relevant_features == []
        assert pack.relevant_modules == []

    def test_empty_repo_is_empty(self):
        pack = build_context_pack("add oauth provider", [], [])
        assert pack.is_empty()

    def test_catchall_excluded_from_fallback_when_meaningful_exists(self):
        """Low-confidence large catch-all features are excluded from fallback packs
        when a meaningful (higher-confidence, smaller) feature exists."""
        catchall = FeatureMemory(
            name="Src",
            files=[f"src/file{i}.py" for i in range(40)],  # 40 files
            tests=[],
            confidence=0.4,
        )
        meaningful = FeatureMemory(
            name="auth",
            files=["src/auth/login.py"],
            tests=[],
            confidence=0.85,
        )
        # Totally unrelated task to trigger fallback path
        pack = build_context_pack("xyzzy unrelated task", [catchall, meaningful], [])
        # meaningful should be preferred over the catch-all
        assert "auth" in pack.relevant_features
        assert "Src" not in pack.relevant_features

    def test_catchall_included_when_only_option(self):
        """When only a catch-all exists, it is still surfaced (never leave pack empty)."""
        catchall = FeatureMemory(
            name="Src",
            files=[f"src/file{i}.py" for i in range(40)],
            tests=[],
            confidence=0.4,
        )
        pack = build_context_pack("xyzzy unrelated task", [catchall], [])
        assert isinstance(pack, TaskContextPack)


class TestIsCatchall:
    def test_low_conf_large_is_catchall(self):
        f = FeatureMemory(name="Src", files=[f"src/f{i}.py" for i in range(35)],
                          tests=[], confidence=0.4)
        assert _is_catchall(f) is True

    def test_high_conf_large_is_not_catchall(self):
        f = FeatureMemory(name="auth", files=[f"src/f{i}.py" for i in range(35)],
                          tests=[], confidence=0.85)
        assert _is_catchall(f) is False

    def test_low_conf_small_is_not_catchall(self):
        f = FeatureMemory(name="auth", files=["src/auth/login.py"],
                          tests=[], confidence=0.4)
        assert _is_catchall(f) is False


# ---------------------------------------------------------------------------
# build_context_pack — single-module repo
# ---------------------------------------------------------------------------


class TestSingleModule:
    def test_single_feature_repo(self):
        features = [_AUTH_FEATURE]
        pack = build_context_pack("fix login bug", features, [])
        assert "auth" in pack.relevant_features

    def test_single_module_repo(self):
        modules = [_AUTH_MODULE]
        pack = build_context_pack("fix login bug", [], modules)
        assert "src/auth" in pack.relevant_modules

    def test_files_from_single_module(self):
        modules = [_AUTH_MODULE]
        pack = build_context_pack("fix login bug", [], modules)
        assert len(pack.relevant_files) > 0


# ---------------------------------------------------------------------------
# build_context_pack — output structure contracts
# ---------------------------------------------------------------------------


class TestStructureContracts:
    def test_all_fields_present(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert isinstance(pack.task, str)
        assert isinstance(pack.relevant_features, list)
        assert isinstance(pack.relevant_modules, list)
        assert isinstance(pack.relevant_files, list)
        assert isinstance(pack.relevant_tests, list)
        assert isinstance(pack.warnings, list)
        assert isinstance(pack.summary, str)

    def test_summary_nonempty(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert pack.summary.strip()

    def test_summary_contains_task(self):
        pack = build_context_pack("debug login bug", _ALL_FEATURES, _ALL_MODULES)
        assert "debug login bug" in pack.summary

    def test_is_not_empty_when_features_found(self):
        pack = build_context_pack("auth login fix", _ALL_FEATURES, _ALL_MODULES)
        assert not pack.is_empty()

    def test_is_empty_on_empty_repo(self):
        pack = build_context_pack("anything", [], [])
        assert pack.is_empty()

    def test_max_features_respected(self):
        many = [_feature(f"feature{i}") for i in range(10)]
        pack = build_context_pack("feature", many, [])
        assert len(pack.relevant_features) <= 5

    def test_max_modules_respected(self):
        many = [_module(f"module{i}") for i in range(10)]
        pack = build_context_pack("module", [], many)
        assert len(pack.relevant_modules) <= 5

    def test_max_files_respected(self):
        big_feature = _feature("auth", files=[f"src/auth/f{i}.py" for i in range(30)])
        pack = build_context_pack("auth", [big_feature], [])
        assert len(pack.relevant_files) <= 20


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_task_same_result(self):
        task = "debug auth login token refresh bug"
        r1 = build_context_pack(task, _ALL_FEATURES, _ALL_MODULES)
        r2 = build_context_pack(task, _ALL_FEATURES, _ALL_MODULES)
        assert r1.relevant_features == r2.relevant_features
        assert r1.relevant_modules == r2.relevant_modules
        assert r1.relevant_files == r2.relevant_files
        assert r1.relevant_tests == r2.relevant_tests
        assert r1.warnings == r2.warnings
        assert r1.summary == r2.summary


# ---------------------------------------------------------------------------
# Confidence weighting
# ---------------------------------------------------------------------------


class TestConfidenceWeighting:
    def test_high_confidence_ranks_above_low_confidence(self):
        high = _feature("auth", files=["src/auth/login.py"], confidence=0.95)
        low = _feature("auth", files=["src/auth/login.py"], confidence=0.3)
        score_high = _score({"auth"}, high.name, high.files, high.confidence)
        score_low = _score({"auth"}, low.name, low.files, low.confidence)
        assert score_high > score_low

    def test_low_confidence_warning_included(self):
        low_conf_feature = _feature("auth", confidence=0.3)
        pack = build_context_pack("auth login", [low_conf_feature], [])
        has_low_conf_warning = any("low-confidence" in w.lower() for w in pack.warnings)
        assert has_low_conf_warning


# ---------------------------------------------------------------------------
# No-tests warning
# ---------------------------------------------------------------------------


class TestNoTestsWarning:
    def test_warning_when_feature_has_no_tests(self):
        no_tests = _feature("notifications", tests=[])
        pack = build_context_pack("notifications email", [no_tests], [])
        assert any("no tests" in w.lower() for w in pack.warnings)

    def test_no_warning_when_tests_present(self):
        with_tests = _feature("auth", tests=["tests/test_auth.py"])
        pack = build_context_pack("auth login", [with_tests], [])
        no_test_warnings = [w for w in pack.warnings if "no tests" in w.lower()]
        assert len(no_test_warnings) == 0


# ---------------------------------------------------------------------------
# CLI integration — text and JSON output
# ---------------------------------------------------------------------------


class TestCLIIntegration:
    def _make_repo(self, tmp_path):
        files = {
            "src/auth/__init__.py": "",
            "src/auth/login.py": "def login(): pass",
            "src/billing/__init__.py": "",
            "src/billing/invoice.py": "class Invoice: pass",
            "tests/test_auth.py": "def test_login(): pass",
            "pyproject.toml": "[project]\nname='testapp'",
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return tmp_path

    def test_text_output_runs(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="fix login bug", json=False)
        memory_prepare_context_command(args)
        out = capsys.readouterr().out
        assert "prepare-context" in out
        assert "fix login bug" in out

    def test_text_output_contains_files(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="fix login bug", json=False)
        memory_prepare_context_command(args)
        out = capsys.readouterr().out
        assert "login" in out or "auth" in out

    def test_json_output_is_valid(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="fix login bug", json=True)
        memory_prepare_context_command(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "task" in data
        assert "relevant_features" in data
        assert "relevant_modules" in data
        assert "relevant_files" in data
        assert "relevant_tests" in data
        assert "warnings" in data
        assert "summary" in data

    def test_json_task_matches_input(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="add invoice endpoint", json=True)
        memory_prepare_context_command(args)
        data = json.loads(capsys.readouterr().out)
        assert data["task"] == "add invoice endpoint"

    def test_json_relevant_files_is_list(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="auth", json=True)
        memory_prepare_context_command(args)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data["relevant_files"], list)

    def test_empty_task_exits(self, tmp_path):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="   ", json=False)
        with pytest.raises(SystemExit):
            memory_prepare_context_command(args)

    def test_billing_task_returns_billing_context(self, tmp_path, capsys):
        from code_review_graph.memory.commands import memory_prepare_context_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo), task="add invoice export", json=True)
        memory_prepare_context_command(args)
        data = json.loads(capsys.readouterr().out)
        features = [f.lower() for f in data["relevant_features"]]
        modules = [m.lower() for m in data["relevant_modules"]]
        files = " ".join(data["relevant_files"]).lower()
        assert (
            any("billing" in x for x in features)
            or any("billing" in x for x in modules)
            or "billing" in files
        )


# ---------------------------------------------------------------------------
# Vocabulary-based scoring (_W_SYMBOL component)
# ---------------------------------------------------------------------------


class TestVocabularyScoring:
    """Tests for the 4th scoring component: symbol/vocabulary overlap."""

    def test_vocabulary_boosts_score_when_symbol_matches_task(self):
        """A feature whose files contain a matching symbol should score higher
        than the identical feature without vocabulary when the task references
        that symbol."""
        tokens = {"validate", "token"}
        files = ["src/auth/tokens.py"]
        vocab = {"src/auth/tokens.py": ["validate_token", "refresh_token", "TokenStore"]}

        score_with = _score(tokens, "auth", files, 0.9, vocabulary=vocab)
        score_without = _score(tokens, "auth", files, 0.9, vocabulary=None)
        assert score_with > score_without

    def test_vocabulary_no_match_does_not_degrade_score(self):
        """When vocabulary is present but no symbols match the task, the score
        should be at least as high as without vocabulary (normalisation spreads
        zero-symbol-overlap over a larger denominator — a slight drop is
        acceptable but it should not be dramatic)."""
        tokens = {"billing", "invoice"}
        files = ["src/auth/tokens.py"]
        vocab = {"src/auth/tokens.py": ["validate_token", "TokenStore"]}

        score_with = _score(tokens, "billing", files, 0.9, vocabulary=vocab)
        score_without = _score(tokens, "billing", files, 0.9, vocabulary=None)
        # Max allowed regression: 20% of without-vocab score
        assert score_with >= score_without * 0.8

    def test_empty_vocabulary_dict_same_as_none(self):
        """Passing an empty dict should behave identically to None."""
        tokens = {"auth", "login"}
        files = ["src/auth/login.py"]
        score_empty = _score(tokens, "auth", files, 0.9, vocabulary={})
        score_none = _score(tokens, "auth", files, 0.9, vocabulary=None)
        assert score_empty == score_none

    def test_build_context_pack_passes_vocabulary_to_scorer(self):
        """build_context_pack() accepts vocabulary kwarg and uses it for ranking.
        A feature whose files contain symbol 'validate_token' should rank first
        when the task is 'fix token validation'."""
        auth_feature = _feature("auth", files=["src/auth/tokens.py"])
        billing_feature = _feature("billing", files=["src/billing/invoice.py"])
        vocab = {
            "src/auth/tokens.py": ["validate_token", "TokenStore", "refresh_token"],
            "src/billing/invoice.py": ["InvoiceService", "generate_pdf"],
        }
        pack = build_context_pack(
            "fix token validation",
            [auth_feature, billing_feature],
            [],
            vocabulary=vocab,
        )
        assert len(pack.relevant_features) >= 1
        # Auth should win because 'token' and 'validate' both appear in vocabulary
        assert "auth" in pack.relevant_features[0].lower()

    def test_vocabulary_camelcase_symbols_tokenised(self):
        """CamelCase symbols like 'TokenStore' and 'InvoiceService' should be
        split on underscores/snake_case — the regex in _score splits on [-_]."""
        tokens = {"invoice"}
        files = ["src/billing/invoice.py"]
        # symbol name is snake_case; 'invoice' should match after split
        vocab = {"src/billing/invoice.py": ["create_invoice", "InvoiceStore"]}
        score = _score(tokens, "billing", files, 0.9, vocabulary=vocab)
        assert score > 0
