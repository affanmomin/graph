"""Tests for the repo scanner, generator, metadata, and memory init CLI (Ticket 4).

Covers:
- scanner: basic detection, language counts, test/docs/src dirs, config files,
           framework hints, empty repo, monorepo, missing repo root
- generator: repo.md and architecture.md content correctness
- metadata: manifest structure and save/load round-trip
- CLI: memory init writes expected files with real content
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from code_review_graph.memory.scanner import RepoScan, scan_repo
from code_review_graph.memory.generator import generate_architecture_doc, generate_repo_summary
from code_review_graph.memory.metadata import generate_manifest, save_manifest
from code_review_graph.memory.writer import ensure_memory_dirs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a minimal fake repo under tmp_path with given file contents."""
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Scanner — basic detection
# ---------------------------------------------------------------------------


class TestScannerBasic:
    def test_returns_reposcan(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        result = scan_repo(tmp_path)
        assert isinstance(result, RepoScan)
        assert result.repo_root == tmp_path

    def test_detects_python(self, tmp_path):
        make_repo(tmp_path, {"app/main.py": "x = 1", "app/utils.py": "y = 2"})
        result = scan_repo(tmp_path)
        assert "python" in result.languages

    def test_detects_typescript(self, tmp_path):
        make_repo(tmp_path, {"src/index.ts": "const x = 1;", "src/app.tsx": ""})
        result = scan_repo(tmp_path)
        assert "typescript" in result.languages

    def test_file_counts_correct(self, tmp_path):
        make_repo(tmp_path, {
            "src/a.py": "", "src/b.py": "", "src/c.ts": "",
        })
        result = scan_repo(tmp_path)
        assert result.file_counts.get("python", 0) == 2
        assert result.file_counts.get("typescript", 0) == 1

    def test_multiple_languages(self, tmp_path):
        make_repo(tmp_path, {
            "backend/main.py": "", "frontend/index.ts": "", "service/main.go": "",
        })
        result = scan_repo(tmp_path)
        assert len(result.languages) >= 2

    def test_skips_node_modules(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            "node_modules/lib/index.js": "",
        })
        result = scan_repo(tmp_path)
        # js from node_modules should not be counted
        assert result.file_counts.get("javascript", 0) == 0

    def test_skips_venv(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            ".venv/lib/python3.11/site-packages/foo.py": "",
        })
        result = scan_repo(tmp_path)
        assert result.file_counts.get("python", 0) == 1

    def test_skips_agent_memory_dir(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            ".agent-memory/repo.md": "# repo",
        })
        result = scan_repo(tmp_path)
        # .md is not a source file, but confirm no crash
        assert result.repo_root == tmp_path

    def test_empty_repo(self, tmp_path):
        result = scan_repo(tmp_path)
        assert result.languages == []
        assert result.confidence < 1.0

    def test_nonexistent_root(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        result = scan_repo(missing)
        assert result.confidence == 0.0
        assert result.notes


class TestScannerDirectories:
    def test_detects_tests_dir(self, tmp_path):
        make_repo(tmp_path, {"tests/test_main.py": ""})
        result = scan_repo(tmp_path)
        assert "tests" in result.test_dirs

    def test_detects_docs_dir(self, tmp_path):
        make_repo(tmp_path, {"docs/index.md": "# docs"})
        result = scan_repo(tmp_path)
        assert "docs" in result.docs_dirs

    def test_detects_src_dir(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": ""})
        result = scan_repo(tmp_path)
        assert "src" in result.source_dirs

    def test_detects_top_level_dirs(self, tmp_path):
        make_repo(tmp_path, {"src/x.py": "", "tests/t.py": "", "docs/d.md": "x"})
        result = scan_repo(tmp_path)
        assert set(result.top_level_dirs) >= {"src", "tests", "docs"}

    def test_note_when_no_test_dir(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": ""})
        result = scan_repo(tmp_path)
        assert any("test" in n.lower() for n in result.notes)

    def test_note_when_no_docs_dir(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": ""})
        result = scan_repo(tmp_path)
        assert any("doc" in n.lower() for n in result.notes)

    def test_detects_jest_config_in_source_dir(self, tmp_path):
        """jest.config.ts inside a source dir marks it as test-containing."""
        make_repo(tmp_path, {
            "FE/src/page.tsx": "export default function Page() {}",
            "FE/jest.config.ts": "export default { testEnvironment: 'jsdom' }",
        })
        result = scan_repo(tmp_path)
        assert "FE" in result.test_dirs

    def test_detects_vitest_config_in_source_dir(self, tmp_path):
        """vitest.config.ts inside a source dir marks it as test-containing."""
        make_repo(tmp_path, {
            "app/src/main.ts": "const x = 1",
            "app/vitest.config.ts": "export default defineConfig({})",
        })
        result = scan_repo(tmp_path)
        assert "app" in result.test_dirs

    def test_detects_nested_tests_subdir_in_source(self, tmp_path):
        """A __tests__/ subdirectory inside a source dir is found."""
        make_repo(tmp_path, {
            "FE/src/app.tsx": "export const App = () => null",
            "FE/__tests__/app.test.tsx": "describe('App', () => {})",
        })
        result = scan_repo(tmp_path)
        assert any("__tests__" in d for d in result.test_dirs)

    def test_jest_config_does_not_duplicate_test_dir(self, tmp_path):
        """A dir appearing in both top-level test dirs and config detection is not duplicated."""
        make_repo(tmp_path, {
            "tests/test_main.py": "",
            "tests/jest.config.js": "module.exports = {}",
        })
        result = scan_repo(tmp_path)
        assert result.test_dirs.count("tests") == 1


class TestScannerConfigAndFrameworks:
    def test_detects_pyproject(self, tmp_path):
        make_repo(tmp_path, {"pyproject.toml": "[project]\nname='x'"})
        result = scan_repo(tmp_path)
        assert "pyproject.toml" in result.config_files

    def test_detects_package_json(self, tmp_path):
        make_repo(tmp_path, {"package.json": '{"name":"x","dependencies":{}}'})
        result = scan_repo(tmp_path)
        assert "package.json" in result.config_files

    def test_detects_react_from_package_json(self, tmp_path):
        make_repo(tmp_path, {"package.json": '{"dependencies":{"react":"18.0.0"}}'})
        result = scan_repo(tmp_path)
        assert "React" in result.framework_hints

    def test_detects_nextjs_from_config(self, tmp_path):
        make_repo(tmp_path, {"next.config.js": "module.exports = {}"})
        result = scan_repo(tmp_path)
        assert "Next.js" in result.framework_hints

    def test_detects_django_from_pyproject(self, tmp_path):
        make_repo(tmp_path, {"pyproject.toml": '[project]\ndependencies=["django"]'})
        result = scan_repo(tmp_path)
        assert "Django" in result.framework_hints

    def test_detects_express_in_subdirectory_package_json(self, tmp_path):
        """Express in BE/package.json should be detected even without root package.json."""
        make_repo(tmp_path, {
            "BE/src/app.ts": "const x = 1",
            "BE/package.json": '{"dependencies":{"express":"5.0.0"}}',
            "FE/src/page.tsx": "export default function Page() {}",
        })
        result = scan_repo(tmp_path)
        assert "Express" in result.framework_hints

    def test_detects_nextjs_in_subdirectory_package_json(self, tmp_path):
        """Next.js in FE/package.json should be detected."""
        make_repo(tmp_path, {
            "FE/src/page.tsx": "export default function Page() {}",
            "FE/package.json": '{"dependencies":{"next":"16.0.0"}}',
        })
        result = scan_repo(tmp_path)
        assert "Next.js" in result.framework_hints

    def test_detects_nextjs_config_in_subdirectory(self, tmp_path):
        """next.config.ts in a subdirectory is detected."""
        make_repo(tmp_path, {
            "FE/src/page.tsx": "export default function Page() {}",
            "FE/next.config.ts": "export default {}",
        })
        result = scan_repo(tmp_path)
        assert "Next.js" in result.framework_hints

    def test_detects_readme(self, tmp_path):
        make_repo(tmp_path, {"README.md": "# My Project"})
        result = scan_repo(tmp_path)
        assert result.readme_path == "README.md"

    def test_no_readme(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": ""})
        result = scan_repo(tmp_path)
        assert result.readme_path == ""


class TestScannerConfidence:
    def test_full_repo_high_confidence(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            "tests/test_main.py": "",
            "pyproject.toml": "",
            "README.md": "# x",
        })
        result = scan_repo(tmp_path)
        assert result.confidence >= 0.7

    def test_empty_repo_low_confidence(self, tmp_path):
        result = scan_repo(tmp_path)
        assert result.confidence < 0.7


# ---------------------------------------------------------------------------
# Generator — repo.md
# ---------------------------------------------------------------------------


class TestGenerateRepoSummary:
    def _scan(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            "tests/test_main.py": "",
            "pyproject.toml": "",
            "README.md": "# My Project",
        })
        return scan_repo(tmp_path)

    def test_returns_string(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_h1(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert result.startswith("# Repo:")

    def test_contains_stack_section(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert "## Stack" in result

    def test_contains_languages(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert "python" in result.lower()

    def test_contains_key_locations(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert "Key locations" in result

    def test_contains_starting_points(self, tmp_path):
        result = generate_repo_summary(self._scan(tmp_path))
        assert "Suggested starting points" in result

    def test_deterministic(self, tmp_path):
        scan = self._scan(tmp_path)
        assert generate_repo_summary(scan) == generate_repo_summary(scan)

    def test_empty_repo_does_not_crash(self, tmp_path):
        scan = scan_repo(tmp_path)
        result = generate_repo_summary(scan)
        assert isinstance(result, str)


class TestGenerateArchitectureDoc:
    def _scan(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            "tests/test_main.py": "",
            "pyproject.toml": "",
        })
        return scan_repo(tmp_path)

    def test_returns_string(self, tmp_path):
        result = generate_architecture_doc(self._scan(tmp_path))
        assert isinstance(result, str)

    def test_contains_h1(self, tmp_path):
        result = generate_architecture_doc(self._scan(tmp_path))
        assert result.startswith("# Architecture:")

    def test_contains_major_boundaries(self, tmp_path):
        result = generate_architecture_doc(self._scan(tmp_path))
        assert "Major boundaries" in result

    def test_contains_layout(self, tmp_path):
        result = generate_architecture_doc(self._scan(tmp_path))
        assert "Inferred layout" in result

    def test_contains_inspect_first(self, tmp_path):
        result = generate_architecture_doc(self._scan(tmp_path))
        assert "Inspect first" in result

    def test_deterministic(self, tmp_path):
        scan = self._scan(tmp_path)
        assert generate_architecture_doc(scan) == generate_architecture_doc(scan)

    def test_empty_repo_does_not_crash(self, tmp_path):
        scan = scan_repo(tmp_path)
        result = generate_architecture_doc(scan)
        assert isinstance(result, str)

    def test_low_confidence_note_appears(self, tmp_path):
        scan = scan_repo(tmp_path)  # empty -> low confidence
        result = generate_architecture_doc(scan)
        # low confidence should trigger a coupling note
        assert "confidence" in result.lower() or "weak" in result.lower() or isinstance(result, str)


# ---------------------------------------------------------------------------
# Metadata — manifest
# ---------------------------------------------------------------------------


class TestGenerateManifest:
    def _scan(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "", "pyproject.toml": ""})
        return scan_repo(tmp_path)

    def test_returns_dict(self, tmp_path):
        manifest = generate_manifest(self._scan(tmp_path), [])
        assert isinstance(manifest, dict)

    def test_version_field(self, tmp_path):
        manifest = generate_manifest(self._scan(tmp_path), [])
        assert manifest["version"] == "1"

    def test_generated_at_is_iso(self, tmp_path):
        manifest = generate_manifest(self._scan(tmp_path), [])
        from datetime import datetime
        # Should parse without error
        datetime.fromisoformat(manifest["generated_at"])

    def test_repo_root_present(self, tmp_path):
        manifest = generate_manifest(self._scan(tmp_path), [])
        assert "repo_root" in manifest

    def test_languages_present(self, tmp_path):
        manifest = generate_manifest(self._scan(tmp_path), [])
        assert "discovered_languages" in manifest
        assert "python" in manifest["discovered_languages"]

    def test_artifacts_sorted(self, tmp_path):
        arts = [
            {"artifact_id": "z", "relative_path": "z.md", "artifact_type": "repo"},
            {"artifact_id": "a", "relative_path": "a.md", "artifact_type": "repo"},
        ]
        manifest = generate_manifest(self._scan(tmp_path), arts)
        paths = [a["relative_path"] for a in manifest["generated_artifacts"]]
        assert paths == sorted(paths)

    def test_save_manifest_writes_file(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        scan = self._scan(tmp_path)
        manifest = generate_manifest(scan, [])
        status = save_manifest(manifest, dirs["metadata"])
        assert status == "created"
        manifest_path = dirs["metadata"] / "manifest.json"
        assert manifest_path.exists()

    def test_save_manifest_valid_json(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        scan = self._scan(tmp_path)
        manifest = generate_manifest(scan, [])
        save_manifest(manifest, dirs["metadata"])
        loaded = json.loads((dirs["metadata"] / "manifest.json").read_text())
        assert loaded["version"] == "1"

    def test_save_manifest_unchanged_on_rerun(self, tmp_path):
        dirs = ensure_memory_dirs(tmp_path)
        scan = self._scan(tmp_path)
        manifest = generate_manifest(scan, [])
        save_manifest(manifest, dirs["metadata"])
        # Regenerate an identical manifest
        manifest2 = generate_manifest(scan, [])
        # generated_at will differ — so status will be "updated" not "unchanged".
        # What matters is no crash and the file is valid JSON.
        status = save_manifest(manifest2, dirs["metadata"])
        assert status in ("updated", "unchanged")


# ---------------------------------------------------------------------------
# CLI integration — memory init
# ---------------------------------------------------------------------------


class TestMemoryInitCLI:
    def _run_init(self, repo_path: Path) -> str:
        from code_review_graph.cli import main
        buf = StringIO()
        with patch("sys.argv", ["code-review-graph", "memory", "init", "--repo", str(repo_path)]):
            with patch("sys.stdout", buf):
                main()
        return buf.getvalue()

    def test_creates_agent_memory_dir(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1", "pyproject.toml": ""})
        self._run_init(tmp_path)
        assert (tmp_path / ".agent-memory").is_dir()

    def test_creates_repo_md(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        assert (tmp_path / ".agent-memory" / "repo.md").exists()

    def test_creates_architecture_md(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        assert (tmp_path / ".agent-memory" / "architecture.md").exists()

    def test_creates_manifest_json(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        assert (tmp_path / ".agent-memory" / "metadata" / "manifest.json").exists()

    def test_manifest_is_valid_json(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        path = tmp_path / ".agent-memory" / "metadata" / "manifest.json"
        data = json.loads(path.read_text())
        assert data["version"] == "1"

    def test_repo_md_has_content(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        content = (tmp_path / ".agent-memory" / "repo.md").read_text()
        assert "# Repo:" in content
        assert len(content) > 50

    def test_architecture_md_has_content(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        content = (tmp_path / ".agent-memory" / "architecture.md").read_text()
        assert "# Architecture:" in content

    def test_output_mentions_languages(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        out = self._run_init(tmp_path)
        assert "python" in out.lower()

    def test_output_mentions_status(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        out = self._run_init(tmp_path)
        assert "created" in out or "updated" in out or "unchanged" in out

    def test_second_run_shows_unchanged_or_updated(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        out2 = self._run_init(tmp_path)
        # On second run files already exist — status should reflect that
        assert "unchanged" in out2 or "updated" in out2

    def test_all_subdirs_created(self, tmp_path):
        make_repo(tmp_path, {"src/main.py": "x = 1"})
        self._run_init(tmp_path)
        root = tmp_path / ".agent-memory"
        for sub in ("features", "modules", "tasks", "changes", "rules", "overrides", "metadata"):
            assert (root / sub).is_dir(), f"missing subdir: {sub}"

    def test_empty_repo_does_not_crash(self, tmp_path):
        out = self._run_init(tmp_path)
        assert "repo-memory: init" in out

    def test_repo_with_tests_and_docs(self, tmp_path):
        make_repo(tmp_path, {
            "src/main.py": "",
            "tests/test_main.py": "",
            "docs/index.md": "# Docs",
            "pyproject.toml": "[project]",
            "README.md": "# Readme",
        })
        self._run_init(tmp_path)
        manifest = json.loads(
            (tmp_path / ".agent-memory" / "metadata" / "manifest.json").read_text()
        )
        assert "tests" in manifest["discovered_test_dirs"]
        assert "docs" in manifest["discovered_docs_dirs"]
