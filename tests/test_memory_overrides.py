"""Tests for the human override system (Ticket 10).

Covers:
- valid override parsing (global.yaml and area-specific files)
- load order: global.yaml before area-specific files
- deduplication across multiple files
- invalid YAML handling (skipped with warning, not raised)
- non-dict YAML top-level (skipped)
- missing 'pattern' or 'hint' in task_hints (item skipped)
- empty override directory → Overrides.empty()
- apply_overrides: always_include prepended to relevant_files
- apply_overrides: never_edit as warnings
- apply_overrides: task_hints injected when matched
- apply_overrides: notes injected as warnings (capped at 3)
- task_hint_match: pattern matches
- task_hint_match: no match
- task_hint_match: case-insensitive
- build_context_pack with overrides
- generate_conventions_doc with overrides notes
- generate_conventions_doc language/framework conventions
- generate_safe_boundaries_doc with never_edit
- generate_safe_boundaries_doc inferred boundaries
- precedence: global + area-specific with overlapping keys
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_review_graph.memory.context_builder import build_context_pack
from code_review_graph.memory.generator import (
    generate_conventions_doc,
    generate_safe_boundaries_doc,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory
from code_review_graph.memory.overrides import (
    Overrides,
    TaskHint,
    apply_overrides,
    load_overrides,
    task_hint_match,
)
from code_review_graph.memory.scanner import RepoScan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _overrides_dir(tmp_path: Path) -> Path:
    d = tmp_path / "overrides"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scan(repo_root: Path, **kwargs) -> RepoScan:
    return RepoScan(repo_root=repo_root, **kwargs)


def _feature(name: str, files: list[str]) -> FeatureMemory:
    return FeatureMemory(name=name, files=files, confidence=0.8)


def _module(name: str, files: list[str]) -> ModuleMemory:
    return ModuleMemory(name=name, files=files, confidence=0.85)


# ---------------------------------------------------------------------------
# load_overrides — basic parsing
# ---------------------------------------------------------------------------


class TestLoadOverridesBasic:
    def test_missing_dir_returns_empty(self, tmp_path):
        overrides = load_overrides(tmp_path / ".agent-memory")
        assert overrides.is_empty()

    def test_empty_dir_returns_empty(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _overrides_dir(agent_memory)
        overrides = load_overrides(agent_memory)
        assert overrides.is_empty()

    def test_valid_global_yaml(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            """
always_include:
  - src/auth/middleware.py
  - docs/architecture.md
never_edit:
  - migrations/
  - src/vendor/
notes:
  - "JWT secret is in env-var only."
  - "Migrations managed by infra team."
task_hints:
  - pattern: "add endpoint"
    hint: "New endpoints go in src/api/routes/."
""",
        )

        overrides = load_overrides(agent_memory)

        assert overrides.always_include == ["src/auth/middleware.py", "docs/architecture.md"]
        assert overrides.never_edit == ["migrations/", "src/vendor/"]
        assert "JWT secret is in env-var only." in overrides.notes
        assert len(overrides.task_hints) == 1
        assert overrides.task_hints[0].pattern == "add endpoint"
        assert overrides.source_files == ["global.yaml"]

    def test_empty_yaml_file_is_valid(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(agent_memory / "overrides" / "global.yaml", "")
        overrides = load_overrides(agent_memory)
        assert overrides.is_empty()

    def test_partial_keys_only_always_include(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include:\n  - src/core.py\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.always_include == ["src/core.py"]
        assert overrides.never_edit == []
        assert overrides.notes == []
        assert overrides.task_hints == []


# ---------------------------------------------------------------------------
# load_overrides — invalid YAML handling
# ---------------------------------------------------------------------------


class TestLoadOverridesInvalidYAML:
    def test_invalid_yaml_is_skipped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "key: [unclosed bracket\n",
        )
        # Must not raise
        overrides = load_overrides(agent_memory)
        assert overrides.is_empty()

    def test_non_dict_top_level_is_skipped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(agent_memory / "overrides" / "global.yaml", "- item1\n- item2\n")
        overrides = load_overrides(agent_memory)
        assert overrides.is_empty()

    def test_non_list_always_include_is_skipped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include: not-a-list\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.always_include == []

    def test_invalid_file_does_not_block_valid_file(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "bad.yaml",
            "key: [unclosed\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include:\n  - src/good.py\n",
        )
        overrides = load_overrides(agent_memory)
        assert "src/good.py" in overrides.always_include

    def test_task_hints_missing_pattern_is_skipped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "task_hints:\n  - hint: 'missing pattern'\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.task_hints == []

    def test_task_hints_missing_hint_is_skipped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "task_hints:\n  - pattern: 'fix auth'\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.task_hints == []


# ---------------------------------------------------------------------------
# load_overrides — merge strategy
# ---------------------------------------------------------------------------


class TestLoadOverridesMerge:
    def test_global_loaded_before_area_specific(self, tmp_path):
        """Entries from global.yaml precede those from area files."""
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include:\n  - src/global.py\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "auth.yaml",
            "always_include:\n  - src/auth.py\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.always_include[0] == "src/global.py"
        assert overrides.always_include[1] == "src/auth.py"

    def test_deduplication_across_files(self, tmp_path):
        """Duplicate entries across files are deduplicated (first occurrence wins)."""
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include:\n  - src/shared.py\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "auth.yaml",
            "always_include:\n  - src/shared.py\n  - src/auth.py\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.always_include.count("src/shared.py") == 1
        assert "src/auth.py" in overrides.always_include

    def test_task_hints_deduped_on_pattern_and_hint(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        hint_yaml = "task_hints:\n  - pattern: 'fix auth'\n    hint: 'Check middleware.'\n"
        _write_yaml(agent_memory / "overrides" / "global.yaml", hint_yaml)
        _write_yaml(agent_memory / "overrides" / "auth.yaml", hint_yaml)
        overrides = load_overrides(agent_memory)
        assert len(overrides.task_hints) == 1

    def test_multiple_area_files_sorted_alphabetically(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "notes:\n  - 'global'\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "zebra.yaml",
            "notes:\n  - 'zebra'\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "auth.yaml",
            "notes:\n  - 'auth'\n",
        )
        overrides = load_overrides(agent_memory)
        # global first, then auth, then zebra
        assert overrides.notes == ["global", "auth", "zebra"]

    def test_source_files_recorded(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(agent_memory / "overrides" / "global.yaml", "notes:\n  - 'hi'\n")
        _write_yaml(agent_memory / "overrides" / "auth.yaml", "notes:\n  - 'there'\n")
        overrides = load_overrides(agent_memory)
        assert "global.yaml" in overrides.source_files
        assert "auth.yaml" in overrides.source_files


# ---------------------------------------------------------------------------
# task_hint_match
# ---------------------------------------------------------------------------


class TestTaskHintMatch:
    def _overrides(self, hints: list[tuple[str, str]]) -> Overrides:
        return Overrides(
            task_hints=[TaskHint(pattern=p, hint=h) for p, h in hints]
        )

    def test_exact_pattern_match(self):
        ov = self._overrides([("add endpoint", "Put it in src/api/routes/")])
        matched = task_hint_match("add a new endpoint to the API", ov)
        assert "Put it in src/api/routes/" in matched

    def test_case_insensitive(self):
        ov = self._overrides([("fix auth", "Check middleware.py")])
        matched = task_hint_match("FIX the AUTH bug", ov)
        assert matched

    def test_no_match(self):
        ov = self._overrides([("add endpoint", "Hint A"), ("fix auth", "Hint B")])
        matched = task_hint_match("update the billing logic", ov)
        assert matched == []

    def test_empty_task_hints(self):
        ov = Overrides()
        assert task_hint_match("any task", ov) == []

    def test_multiple_hints_can_match(self):
        ov = self._overrides([
            ("add endpoint", "Hint A"),
            ("add route", "Hint B"),
        ])
        matched = task_hint_match("add a new endpoint and route", ov)
        assert "Hint A" in matched
        assert "Hint B" in matched

    def test_duplicate_hints_deduped(self):
        ov = Overrides(task_hints=[
            TaskHint(pattern="auth", hint="Same hint"),
            TaskHint(pattern="login", hint="Same hint"),
        ])
        matched = task_hint_match("fix auth login", ov)
        assert matched.count("Same hint") == 1


# ---------------------------------------------------------------------------
# apply_overrides
# ---------------------------------------------------------------------------


class TestApplyOverrides:
    def _pack(self, files: list[str] | None = None):
        from code_review_graph.memory.models import TaskContextPack
        return TaskContextPack(
            task="fix the auth bug",
            relevant_files=files or ["src/auth/login.py"],
        )

    def test_always_include_prepended(self):
        pack = self._pack()
        overrides = Overrides(always_include=["src/auth/middleware.py"])
        result = apply_overrides(pack, overrides)
        assert result.relevant_files[0] == "src/auth/middleware.py"
        assert "src/auth/login.py" in result.relevant_files

    def test_always_include_deduped(self):
        pack = self._pack(files=["src/auth/middleware.py", "src/auth/login.py"])
        overrides = Overrides(always_include=["src/auth/middleware.py"])
        result = apply_overrides(pack, overrides)
        assert result.relevant_files.count("src/auth/middleware.py") == 1

    def test_never_edit_becomes_warning(self):
        pack = self._pack()
        overrides = Overrides(never_edit=["migrations/", "src/vendor/"])
        result = apply_overrides(pack, overrides)
        assert any("Never edit" in w and "migrations/" in w for w in result.warnings)
        assert any("Never edit" in w and "src/vendor/" in w for w in result.warnings)

    def test_matched_task_hint_injected(self):
        pack = self._pack()
        overrides = Overrides(task_hints=[
            TaskHint(pattern="auth", hint="Check middleware.py first.")
        ])
        result = apply_overrides(pack, overrides)
        assert any("Check middleware.py first." in w for w in result.warnings)

    def test_unmatched_task_hint_not_injected(self):
        pack = self._pack()
        overrides = Overrides(task_hints=[
            TaskHint(pattern="billing invoice", hint="Check payment flow.")
        ])
        result = apply_overrides(pack, overrides)
        assert not any("Check payment flow." in w for w in result.warnings)

    def test_notes_injected_as_warnings(self):
        pack = self._pack()
        overrides = Overrides(notes=["JWT secret is env-var only."])
        result = apply_overrides(pack, overrides)
        assert any("JWT secret" in w for w in result.warnings)

    def test_notes_capped_at_three(self):
        pack = self._pack()
        overrides = Overrides(notes=[f"Note {i}" for i in range(6)])
        result = apply_overrides(pack, overrides)
        note_warnings = [w for w in result.warnings if w.startswith("Note:")]
        assert len(note_warnings) <= 3

    def test_empty_overrides_noop(self):
        pack = self._pack()
        original_files = list(pack.relevant_files)
        result = apply_overrides(pack, Overrides.empty())
        assert result.relevant_files == original_files
        assert result.warnings == []

    def test_existing_warnings_preserved(self):
        pack = self._pack()
        pack.warnings = ["Existing warning."]
        overrides = Overrides(never_edit=["migrations/"])
        result = apply_overrides(pack, overrides)
        assert "Existing warning." in result.warnings
        assert any("Never edit" in w for w in result.warnings)

    def test_duplicate_warnings_not_added(self):
        pack = self._pack()
        pack.warnings = ["Never edit: `migrations/` (marked in overrides)"]
        overrides = Overrides(never_edit=["migrations/"])
        result = apply_overrides(pack, overrides)
        assert result.warnings.count("Never edit: `migrations/` (marked in overrides)") == 1


# ---------------------------------------------------------------------------
# build_context_pack with overrides
# ---------------------------------------------------------------------------


class TestBuildContextPackWithOverrides:
    def test_always_include_in_pack(self):
        features = [_feature("Auth", ["src/auth/login.py"])]
        overrides = Overrides(always_include=["docs/architecture.md"])
        pack = build_context_pack("fix auth bug", features, [], overrides=overrides)
        assert "docs/architecture.md" in pack.relevant_files

    def test_never_edit_warning_in_pack(self):
        features = [_feature("Auth", ["src/auth/login.py"])]
        overrides = Overrides(never_edit=["migrations/"])
        pack = build_context_pack("fix auth bug", features, [], overrides=overrides)
        assert any("migrations/" in w for w in pack.warnings)

    def test_task_hint_injected(self):
        features = [_feature("Auth", ["src/auth/login.py"])]
        overrides = Overrides(task_hints=[
            TaskHint(pattern="auth", hint="Use JWT middleware.")
        ])
        pack = build_context_pack("fix auth issue", features, [], overrides=overrides)
        assert any("JWT middleware" in w for w in pack.warnings)

    def test_no_overrides_unchanged(self):
        features = [_feature("Auth", ["src/auth/login.py"])]
        pack_no_ov = build_context_pack("fix auth bug", features, [])
        pack_with_ov = build_context_pack("fix auth bug", features, [], overrides=None)
        assert pack_no_ov.relevant_files == pack_with_ov.relevant_files
        assert pack_no_ov.warnings == pack_with_ov.warnings


# ---------------------------------------------------------------------------
# generate_conventions_doc
# ---------------------------------------------------------------------------


class TestGenerateConventionsDoc:
    def test_contains_header(self, tmp_path):
        scan = _scan(tmp_path)
        doc = generate_conventions_doc(scan)
        assert "# Conventions" in doc

    def test_human_notes_included(self, tmp_path):
        scan = _scan(tmp_path)
        overrides = Overrides(notes=["JWT secret in env-var only."])
        doc = generate_conventions_doc(scan, overrides)
        assert "JWT secret in env-var only." in doc

    def test_python_conventions_included(self, tmp_path):
        scan = _scan(tmp_path, languages=["python"])
        doc = generate_conventions_doc(scan)
        assert "python" in doc.lower()
        assert "PEP 8" in doc

    def test_django_framework_conventions(self, tmp_path):
        scan = _scan(tmp_path, languages=["python"], framework_hints=["Django"])
        doc = generate_conventions_doc(scan)
        assert "Django" in doc
        assert "migrations" in doc.lower()

    def test_test_dir_convention_inferred(self, tmp_path):
        scan = _scan(tmp_path, test_dirs=["tests"])
        doc = generate_conventions_doc(scan)
        assert "tests" in doc

    def test_no_signals_graceful(self, tmp_path):
        scan = _scan(tmp_path)
        doc = generate_conventions_doc(scan)
        assert "# Conventions" in doc
        assert len(doc) > 10  # not empty

    def test_deterministic(self, tmp_path):
        scan = _scan(tmp_path, languages=["python"])
        overrides = Overrides(notes=["Keep it clean."])
        doc1 = generate_conventions_doc(scan, overrides)
        doc2 = generate_conventions_doc(scan, overrides)
        assert doc1 == doc2


# ---------------------------------------------------------------------------
# generate_safe_boundaries_doc
# ---------------------------------------------------------------------------


class TestGenerateSafeBoundariesDoc:
    def test_contains_header(self, tmp_path):
        scan = _scan(tmp_path)
        doc = generate_safe_boundaries_doc(scan)
        assert "# Safe boundaries" in doc

    def test_never_edit_in_doc(self, tmp_path):
        scan = _scan(tmp_path)
        overrides = Overrides(never_edit=["migrations/", "src/vendor/"])
        doc = generate_safe_boundaries_doc(scan, overrides)
        assert "migrations/" in doc
        assert "src/vendor/" in doc
        assert "never-edit" in doc.lower()

    def test_inferred_migrations_boundary(self, tmp_path):
        scan = _scan(tmp_path, top_level_dirs=["migrations", "src"])
        doc = generate_safe_boundaries_doc(scan)
        assert "migrations" in doc

    def test_inferred_vendor_boundary(self, tmp_path):
        scan = _scan(tmp_path, top_level_dirs=["vendor", "src"])
        doc = generate_safe_boundaries_doc(scan)
        assert "vendor" in doc

    def test_universal_boundaries_always_present(self, tmp_path):
        scan = _scan(tmp_path)
        doc = generate_safe_boundaries_doc(scan)
        assert "node_modules/" in doc
        assert ".git/" in doc

    def test_notes_in_fragile_section(self, tmp_path):
        scan = _scan(tmp_path)
        overrides = Overrides(notes=["Payment processor config is fragile."])
        doc = generate_safe_boundaries_doc(scan, overrides)
        assert "Payment processor config is fragile." in doc

    def test_no_overrides_graceful(self, tmp_path):
        scan = _scan(tmp_path)
        doc = generate_safe_boundaries_doc(scan)
        assert "# Safe boundaries" in doc

    def test_deterministic(self, tmp_path):
        scan = _scan(tmp_path, top_level_dirs=["migrations"])
        overrides = Overrides(never_edit=["migrations/"])
        doc1 = generate_safe_boundaries_doc(scan, overrides)
        doc2 = generate_safe_boundaries_doc(scan, overrides)
        assert doc1 == doc2

    def test_lock_file_inferred_when_pyproject(self, tmp_path):
        scan = _scan(tmp_path, config_files=["pyproject.toml"])
        doc = generate_safe_boundaries_doc(scan)
        assert "uv.lock" in doc


# ---------------------------------------------------------------------------
# Precedence: global + area-specific overlapping rules
# ---------------------------------------------------------------------------


class TestOverridePrecedence:
    def test_global_note_appears_before_area_note(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "notes:\n  - 'Global rule.'\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "billing.yaml",
            "notes:\n  - 'Billing rule.'\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.notes.index("Global rule.") < overrides.notes.index("Billing rule.")

    def test_global_always_include_prepends_area(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "always_include:\n  - src/core.py\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "auth.yaml",
            "always_include:\n  - src/auth.py\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.always_include == ["src/core.py", "src/auth.py"]

    def test_overlapping_never_edit_deduped(self, tmp_path):
        agent_memory = tmp_path / ".agent-memory"
        _write_yaml(
            agent_memory / "overrides" / "global.yaml",
            "never_edit:\n  - migrations/\n",
        )
        _write_yaml(
            agent_memory / "overrides" / "db.yaml",
            "never_edit:\n  - migrations/\n  - seeds/\n",
        )
        overrides = load_overrides(agent_memory)
        assert overrides.never_edit.count("migrations/") == 1
        assert "seeds/" in overrides.never_edit
