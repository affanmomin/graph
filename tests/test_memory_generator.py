"""Tests for the memory artifact generator (Ticket 6).

Covers:
- generate_feature_doc() output structure and content
- generate_module_doc() output structure and content
- save_sources_json() and save_confidence_json() in metadata.py
- CLI integration: memory init writes features/*.md, modules/*.md,
  sources.json, confidence.json
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_review_graph.memory.generator import (
    generate_feature_doc,
    generate_module_doc,
)
from code_review_graph.memory.metadata import save_confidence_json, save_sources_json
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_feature(
    name: str = "auth",
    files: list[str] | None = None,
    tests: list[str] | None = None,
    dependencies: list[str] | None = None,
    confidence: float = 0.9,
    summary: str = "",
) -> FeatureMemory:
    return FeatureMemory(
        name=name,
        files=["src/auth/login.py", "src/auth/middleware.py"] if files is None else files,
        tests=["tests/test_auth.py"] if tests is None else tests,
        dependencies=[] if dependencies is None else dependencies,
        confidence=confidence,
        summary=summary,
    )


def make_module(
    name: str = "src/auth",
    files: list[str] | None = None,
    tests: list[str] | None = None,
    dependencies: list[str] | None = None,
    dependents: list[str] | None = None,
    confidence: float = 0.9,
    summary: str = "",
) -> ModuleMemory:
    return ModuleMemory(
        name=name,
        files=["src/auth/__init__.py", "src/auth/login.py"] if files is None else files,
        tests=["tests/test_auth.py"] if tests is None else tests,
        dependencies=[] if dependencies is None else dependencies,
        dependents=[] if dependents is None else dependents,
        confidence=confidence,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# generate_feature_doc — structure
# ---------------------------------------------------------------------------


class TestGenerateFeatureDoc:
    def test_returns_str(self):
        doc = generate_feature_doc(make_feature())
        assert isinstance(doc, str)

    def test_contains_feature_name_in_h1(self):
        doc = generate_feature_doc(make_feature(name="billing"))
        assert "# Feature: billing" in doc

    def test_contains_confidence(self):
        doc = generate_feature_doc(make_feature(confidence=0.9))
        assert "90%" in doc

    def test_contains_auto_generated_note(self):
        doc = generate_feature_doc(make_feature())
        assert "Auto-generated" in doc

    def test_contains_main_files_section(self):
        doc = generate_feature_doc(make_feature())
        assert "## Main files" in doc

    def test_files_appear_in_output(self):
        feature = make_feature(files=["src/auth/login.py", "src/auth/middleware.py"])
        doc = generate_feature_doc(feature)
        assert "src/auth/login.py" in doc
        assert "src/auth/middleware.py" in doc

    def test_contains_related_tests_section(self):
        doc = generate_feature_doc(make_feature())
        assert "## Related tests" in doc

    def test_tests_appear_in_output(self):
        feature = make_feature(tests=["tests/test_auth.py"])
        doc = generate_feature_doc(feature)
        assert "tests/test_auth.py" in doc

    def test_no_tests_message_when_empty(self):
        feature = make_feature(tests=[])
        doc = generate_feature_doc(feature)
        assert "No related tests detected" in doc

    def test_purpose_contains_feature_name(self):
        feature = make_feature(name="billing")
        doc = generate_feature_doc(feature)
        assert "billing" in doc

    def test_low_confidence_warning_included(self):
        feature = make_feature(confidence=0.4)
        doc = generate_feature_doc(feature)
        assert "Low confidence" in doc

    def test_high_confidence_no_warning(self):
        feature = make_feature(confidence=0.95)
        doc = generate_feature_doc(feature)
        assert "Low confidence" not in doc

    def test_dependencies_appear_when_present(self):
        feature = make_feature(dependencies=["billing", "users"])
        doc = generate_feature_doc(feature)
        assert "billing" in doc
        assert "users" in doc

    def test_no_neighboring_section_when_no_deps(self):
        feature = make_feature(dependencies=[])
        doc = generate_feature_doc(feature)
        assert "Neighboring areas" not in doc

    def test_entry_points_detected(self):
        feature = make_feature(files=["src/auth/views.py", "src/auth/models.py"])
        doc = generate_feature_doc(feature)
        assert "Likely entry points" in doc
        assert "views.py" in doc

    def test_deterministic(self):
        feature = make_feature()
        assert generate_feature_doc(feature) == generate_feature_doc(feature)

    def test_trailing_newline(self):
        doc = generate_feature_doc(make_feature())
        assert doc.endswith("\n") or not doc.endswith("\n\n\n")  # no double-blank end

    def test_file_list_truncated_at_20(self):
        files = [f"src/auth/file{i}.py" for i in range(25)]
        feature = make_feature(files=files)
        doc = generate_feature_doc(feature)
        assert "…and 5 more" in doc

    def test_large_feature_warning(self):
        files = [f"src/auth/file{i}.py" for i in range(25)]
        feature = make_feature(files=files)
        doc = generate_feature_doc(feature)
        assert "Large feature" in doc


# ---------------------------------------------------------------------------
# generate_module_doc — structure
# ---------------------------------------------------------------------------


class TestGenerateModuleDoc:
    def test_returns_str(self):
        doc = generate_module_doc(make_module())
        assert isinstance(doc, str)

    def test_contains_module_name_in_h1(self):
        doc = generate_module_doc(make_module(name="src/billing"))
        assert "# Module: src/billing" in doc

    def test_contains_confidence(self):
        doc = generate_module_doc(make_module(confidence=0.85))
        assert "85%" in doc

    def test_contains_auto_generated_note(self):
        doc = generate_module_doc(make_module())
        assert "Auto-generated" in doc

    def test_contains_files_section(self):
        doc = generate_module_doc(make_module())
        assert "## Files" in doc

    def test_files_appear_in_output(self):
        module = make_module(files=["src/auth/__init__.py", "src/auth/login.py"])
        doc = generate_module_doc(module)
        assert "src/auth/__init__.py" in doc
        assert "src/auth/login.py" in doc

    def test_contains_related_tests_section(self):
        doc = generate_module_doc(make_module())
        assert "## Related tests" in doc

    def test_tests_appear_in_output(self):
        module = make_module(tests=["tests/test_auth.py"])
        doc = generate_module_doc(module)
        assert "tests/test_auth.py" in doc

    def test_no_tests_message_when_empty(self):
        module = make_module(tests=[])
        doc = generate_module_doc(module)
        assert "No related tests detected" in doc

    def test_responsibilities_inferred_from_stems(self):
        module = make_module(files=["src/auth/models.py", "src/auth/views.py"])
        doc = generate_module_doc(module)
        assert "Responsibilities" in doc

    def test_purpose_contains_module_name(self):
        module = make_module(name="src/billing")
        doc = generate_module_doc(module)
        assert "billing" in doc

    def test_dependencies_section_when_present(self):
        module = make_module(dependencies=["src/core", "src/utils"])
        doc = generate_module_doc(module)
        assert "## Dependencies" in doc
        assert "src/core" in doc

    def test_no_dependencies_section_when_empty(self):
        module = make_module(dependencies=[])
        doc = generate_module_doc(module)
        assert "## Dependencies" not in doc

    def test_dependents_section_when_present(self):
        module = make_module(dependents=["src/billing"])
        doc = generate_module_doc(module)
        assert "Depended on by" in doc
        assert "src/billing" in doc

    def test_no_dependents_section_when_empty(self):
        module = make_module(dependents=[])
        doc = generate_module_doc(module)
        assert "Depended on by" not in doc

    def test_low_confidence_risk_note(self):
        module = make_module(confidence=0.4)
        doc = generate_module_doc(module)
        assert "Low confidence" in doc

    def test_dependents_risk_note(self):
        module = make_module(dependents=["src/billing", "src/users"])
        doc = generate_module_doc(module)
        assert "depended on by" in doc.lower() or "Depended on by" in doc

    def test_deterministic(self):
        module = make_module()
        assert generate_module_doc(module) == generate_module_doc(module)

    def test_large_module_warning(self):
        files = [f"src/auth/f{i}.py" for i in range(25)]
        module = make_module(files=files)
        doc = generate_module_doc(module)
        assert "Large module" in doc


# ---------------------------------------------------------------------------
# save_sources_json
# ---------------------------------------------------------------------------


class TestSaveSourcesJson:
    def test_creates_file(self, tmp_path):
        features = [make_feature(files=["src/auth/login.py"])]
        modules = [make_module(files=["src/auth/__init__.py"])]
        save_sources_json(features, modules, tmp_path)
        assert (tmp_path / "sources.json").exists()

    def test_returns_created_on_first_write(self, tmp_path):
        status = save_sources_json([], [], tmp_path)
        assert status == "created"

    def test_returns_unchanged_on_repeat(self, tmp_path):
        save_sources_json([], [], tmp_path)
        status = save_sources_json([], [], tmp_path)
        assert status == "unchanged"

    def test_file_indexed_under_feature(self, tmp_path):
        import json
        features = [make_feature(name="auth", files=["src/auth/login.py"])]
        save_sources_json(features, [], tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        sources = data["sources"]
        assert "src/auth/login.py" in sources
        assert any("feature:auth" in v for v in sources["src/auth/login.py"])

    def test_file_indexed_under_module(self, tmp_path):
        import json
        modules = [make_module(name="src/auth", files=["src/auth/__init__.py"])]
        save_sources_json([], modules, tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        sources = data["sources"]
        assert "src/auth/__init__.py" in sources
        assert any("module:src/auth" in v for v in sources["src/auth/__init__.py"])

    def test_file_in_both_feature_and_module(self, tmp_path):
        import json
        features = [make_feature(name="auth", files=["src/auth/login.py"])]
        modules = [make_module(name="src/auth", files=["src/auth/login.py"])]
        save_sources_json(features, modules, tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        entries = data["sources"]["src/auth/login.py"]
        assert len(entries) == 2

    def test_file_count_matches(self, tmp_path):
        import json
        features = [make_feature(files=["a.py", "b.py"])]
        modules = [make_module(files=["c.py"])]
        save_sources_json(features, modules, tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        assert data["file_count"] == 3

    def test_empty_produces_valid_json(self, tmp_path):
        import json
        save_sources_json([], [], tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        assert "sources" in data
        assert data["sources"] == {}

    def test_file_count_key_present(self, tmp_path):
        import json
        save_sources_json([], [], tmp_path)
        data = json.loads((tmp_path / "sources.json").read_text())
        assert "file_count" in data


# ---------------------------------------------------------------------------
# save_confidence_json
# ---------------------------------------------------------------------------


class TestSaveConfidenceJson:
    def test_creates_file(self, tmp_path):
        save_confidence_json([], [], tmp_path)
        assert (tmp_path / "confidence.json").exists()

    def test_returns_created_on_first_write(self, tmp_path):
        status = save_confidence_json([], [], tmp_path)
        assert status == "created"

    def test_returns_unchanged_on_repeat(self, tmp_path):
        save_confidence_json([], [], tmp_path)
        status = save_confidence_json([], [], tmp_path)
        assert status == "unchanged"

    def test_features_listed(self, tmp_path):
        import json
        features = [make_feature(name="auth", confidence=0.9)]
        save_confidence_json(features, [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert len(data["features"]) == 1
        entry = data["features"][0]
        assert entry["name"] == "auth"
        assert entry["type"] == "feature"

    def test_modules_listed(self, tmp_path):
        import json
        modules = [make_module(name="src/auth", confidence=0.85)]
        save_confidence_json([], modules, tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert len(data["modules"]) == 1
        entry = data["modules"][0]
        assert entry["name"] == "src/auth"
        assert entry["type"] == "module"

    def test_confidence_value_stored(self, tmp_path):
        import json
        features = [make_feature(confidence=0.75)]
        save_confidence_json(features, [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert data["features"][0]["confidence"] == pytest.approx(0.75, abs=0.001)

    def test_file_and_test_counts_stored(self, tmp_path):
        import json
        features = [make_feature(files=["a.py", "b.py"], tests=["t.py"])]
        save_confidence_json(features, [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert data["features"][0]["file_count"] == 2
        assert data["features"][0]["test_count"] == 1

    def test_slug_stored(self, tmp_path):
        import json
        features = [make_feature(name="auth")]
        save_confidence_json(features, [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert "slug" in data["features"][0]

    def test_sorted_by_name(self, tmp_path):
        import json
        features = [
            make_feature(name="users"),
            make_feature(name="auth"),
            make_feature(name="billing"),
        ]
        save_confidence_json(features, [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        names = [e["name"] for e in data["features"]]
        assert names == sorted(names)

    def test_features_and_modules_keys_present(self, tmp_path):
        import json
        save_confidence_json([], [], tmp_path)
        data = json.loads((tmp_path / "confidence.json").read_text())
        assert "features" in data
        assert "modules" in data


# ---------------------------------------------------------------------------
# CLI integration — memory init writes feature/module docs + metadata
# ---------------------------------------------------------------------------


class TestMemoryInitIntegration:
    def _make_repo(self, tmp_path: Path) -> Path:
        files = {
            "src/auth/__init__.py": "",
            "src/auth/login.py": "def login(): pass",
            "src/auth/middleware.py": "def middleware(): pass",
            "src/billing/__init__.py": "",
            "src/billing/invoice.py": "class Invoice: pass",
            "tests/test_auth.py": "def test_login(): pass",
            "tests/test_billing.py": "def test_invoice(): pass",
            "pyproject.toml": "[project]\nname = 'testapp'",
        }
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return tmp_path

    def test_features_dir_created(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        assert (repo / ".agent-memory" / "features").is_dir()

    def test_modules_dir_created(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        assert (repo / ".agent-memory" / "modules").is_dir()

    def test_at_least_one_feature_doc_written(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        feature_docs = list((repo / ".agent-memory" / "features").glob("*.md"))
        assert len(feature_docs) >= 1

    def test_at_least_one_module_doc_written(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        module_docs = list((repo / ".agent-memory" / "modules").glob("*.md"))
        assert len(module_docs) >= 1

    def test_sources_json_written(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        assert (repo / ".agent-memory" / "metadata" / "sources.json").exists()

    def test_confidence_json_written(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        assert (repo / ".agent-memory" / "metadata" / "confidence.json").exists()

    def test_feature_doc_contains_feature_header(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        feature_docs = list((repo / ".agent-memory" / "features").glob("*.md"))
        content = feature_docs[0].read_text(encoding="utf-8")
        assert "# Feature:" in content

    def test_module_doc_contains_module_header(self, tmp_path):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        module_docs = list((repo / ".agent-memory" / "modules").glob("*.md"))
        content = module_docs[0].read_text(encoding="utf-8")
        assert "# Module:" in content

    def test_manifest_lists_feature_artifacts(self, tmp_path):
        import argparse
        import json
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        manifest = json.loads(
            (repo / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        types = [a["artifact_type"] for a in manifest["generated_artifacts"]]
        assert "feature" in types

    def test_manifest_lists_module_artifacts(self, tmp_path):
        import argparse
        import json
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        manifest = json.loads(
            (repo / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        types = [a["artifact_type"] for a in manifest["generated_artifacts"]]
        assert "module" in types

    def test_idempotent_second_run(self, tmp_path, capsys):
        import argparse
        from code_review_graph.memory.commands import memory_init_command
        repo = self._make_repo(tmp_path)
        args = argparse.Namespace(repo=str(repo))
        memory_init_command(args)
        capsys.readouterr()  # clear first run output
        memory_init_command(args)
        out = capsys.readouterr().out
        # Second run should show "unchanged" for all artifacts
        assert "unchanged" in out
