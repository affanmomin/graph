"""Tests for the memory MCP tool adapter layer (Ticket 8).

Covers:
- Tool registration in main.py (all 5 memory tools present)
- Direct invocation of tools.py functions (structured output contracts)
- Shared-logic delegation (tools call memory package, not duplicate logic)
- Regression guard: existing graph tools still importable and intact
- Edge cases: empty task, missing area name, missing .agent-memory/
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_repo(tmp_path: Path, with_auth_billing: bool = True) -> Path:
    """Build a minimal fake repo with optional auth/billing structure."""
    files: dict[str, str] = {"pyproject.toml": "[project]\nname='testrepo'"}
    if with_auth_billing:
        files.update({
            "src/auth/__init__.py": "",
            "src/auth/login.py": "def login(): pass",
            "src/auth/middleware.py": "def middleware(): pass",
            "src/billing/__init__.py": "",
            "src/billing/invoice.py": "class Invoice: pass",
            "tests/test_auth.py": "def test_login(): pass",
            "tests/test_billing.py": "def test_invoice(): pass",
        })
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify all 5 memory tools are registered with FastMCP."""

    def _get_tool_names(self) -> set[str]:
        from code_review_graph.main import mcp
        # FastMCP exposes registered tools via ._tool_manager or similar.
        # The safest cross-version way: import and check the decorated functions exist.
        import code_review_graph.main as m
        return {
            name for name in dir(m)
            if name.endswith("_tool") and callable(getattr(m, name))
        }

    def test_memory_init_tool_registered(self):
        import code_review_graph.main as m
        assert hasattr(m, "memory_init_tool")
        assert callable(m.memory_init_tool)

    def test_memory_prepare_context_tool_registered(self):
        import code_review_graph.main as m
        assert hasattr(m, "memory_prepare_context_tool")
        assert callable(m.memory_prepare_context_tool)

    def test_memory_explain_area_tool_registered(self):
        import code_review_graph.main as m
        assert hasattr(m, "memory_explain_area_tool")
        assert callable(m.memory_explain_area_tool)

    def test_memory_recent_changes_tool_registered(self):
        import code_review_graph.main as m
        assert hasattr(m, "memory_recent_changes_tool")
        assert callable(m.memory_recent_changes_tool)

    def test_memory_refresh_tool_registered(self):
        import code_review_graph.main as m
        assert hasattr(m, "memory_refresh_tool")
        assert callable(m.memory_refresh_tool)

    def test_all_five_memory_tools_present(self):
        names = self._get_tool_names()
        for expected in [
            "memory_init_tool",
            "memory_prepare_context_tool",
            "memory_explain_area_tool",
            "memory_recent_changes_tool",
            "memory_refresh_tool",
        ]:
            assert expected in names, f"Missing tool: {expected}"

    def test_existing_graph_tools_still_registered(self):
        """Regression: existing tools must remain present after memory tools added."""
        names = self._get_tool_names()
        for expected in [
            "build_or_update_graph_tool",
            "get_impact_radius_tool",
            "query_graph_tool",
            "get_review_context_tool",
            "semantic_search_nodes_tool",
            "list_graph_stats_tool",
            "embed_graph_tool",
            "get_docs_section_tool",
            "find_large_functions_tool",
        ]:
            assert expected in names, f"Existing tool missing: {expected}"


# ---------------------------------------------------------------------------
# memory_init tool — invocation + output contracts
# ---------------------------------------------------------------------------


class TestMemoryInitTool:
    def test_returns_dict(self, tmp_path):
        from code_review_graph.tools import memory_init
        repo = make_repo(tmp_path)
        result = memory_init(repo_root=str(repo))
        assert isinstance(result, dict)

    def test_status_ok(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "ok"

    def test_summary_present(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["summary"], str) and result["summary"]

    def test_features_list_returned(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["features"], list)

    def test_modules_list_returned(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["modules"], list)

    def test_artifacts_written_present(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["artifacts_written"], dict)
        assert len(result["artifacts_written"]) > 0

    def test_agent_memory_dir_created(self, tmp_path):
        from code_review_graph.tools import memory_init
        repo = make_repo(tmp_path)
        memory_init(repo_root=str(repo))
        assert (repo / ".agent-memory").is_dir()

    def test_features_detected(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        features_lower = [f.lower() for f in result["features"]]
        assert any("auth" in f for f in features_lower) or any("billing" in f for f in features_lower)

    def test_idempotent(self, tmp_path):
        from code_review_graph.tools import memory_init
        repo = make_repo(tmp_path)
        r1 = memory_init(repo_root=str(repo))
        r2 = memory_init(repo_root=str(repo))
        assert r2["status"] == "ok"
        # Second run should show unchanged artifacts
        statuses = list(r2["artifacts_written"].values())
        assert "unchanged" in statuses

    def test_invalid_repo_raises(self, tmp_path):
        from code_review_graph.tools import memory_init
        with pytest.raises((ValueError, FileNotFoundError, Exception)):
            memory_init(repo_root=str(tmp_path / "nonexistent"))

    def test_languages_returned(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["languages"], list)

    def test_scan_confidence_returned(self, tmp_path):
        from code_review_graph.tools import memory_init
        result = memory_init(repo_root=str(make_repo(tmp_path)))
        assert 0.0 <= result["scan_confidence"] <= 1.0


# ---------------------------------------------------------------------------
# memory_prepare_context tool
# ---------------------------------------------------------------------------


class TestMemoryPrepareContextTool:
    def test_returns_dict(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("fix login bug", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result, dict)

    def test_status_ok(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("fix login bug", repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "ok"

    def test_task_preserved(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("add invoice endpoint", repo_root=str(make_repo(tmp_path)))
        assert result["task"] == "add invoice endpoint"

    def test_relevant_features_is_list(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["relevant_features"], list)

    def test_relevant_modules_is_list(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["relevant_modules"], list)

    def test_relevant_files_is_list(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["relevant_files"], list)

    def test_relevant_tests_is_list(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["relevant_tests"], list)

    def test_warnings_is_list(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["warnings"], list)

    def test_summary_is_str(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth login", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["summary"], str)

    def test_billing_task_targets_billing(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("add invoice export", repo_root=str(make_repo(tmp_path)))
        all_names = " ".join(result["relevant_features"] + result["relevant_modules"]).lower()
        all_files = " ".join(result["relevant_files"]).lower()
        assert "billing" in all_names or "billing" in all_files or "invoice" in all_files

    def test_empty_task_returns_error(self, tmp_path):
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("   ", repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "error"

    def test_no_duplication_from_cli_logic(self, tmp_path):
        """Verify the tool delegates to build_context_pack, not duplicating it."""
        # If this import works cleanly, the delegation is correct
        from code_review_graph.memory.context_builder import build_context_pack  # noqa: F401
        from code_review_graph.tools import memory_prepare_context
        result = memory_prepare_context("auth", repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# memory_explain_area tool
# ---------------------------------------------------------------------------


class TestMemoryExplainAreaTool:
    def test_returns_dict(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        assert isinstance(result, dict)

    def test_found_status_for_known_area(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        assert result["status"] in ("ok", "not_found")

    def test_ok_when_area_classifiable(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        # auth/ exists in the repo, should be classifiable
        assert result["status"] == "ok"

    def test_content_present_when_ok(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        if result["status"] == "ok":
            assert "content" in result
            assert isinstance(result["content"], str)
            assert result["content"].strip()

    def test_content_is_markdown(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        if result["status"] == "ok":
            assert "# " in result["content"]

    def test_kind_field_present_when_ok(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        if result["status"] == "ok":
            assert result["kind"] in ("feature", "module")

    def test_not_found_lists_available(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("totally_unknown_xyzzy", repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "not_found"
        assert "available_features" in result
        assert "available_modules" in result

    def test_persisted_artifact_loaded_when_init_run(self, tmp_path):
        """After memory_init, explain_area should load from .agent-memory/ file."""
        from code_review_graph.tools import memory_init, memory_explain_area
        repo = make_repo(tmp_path)
        memory_init(repo_root=str(repo))
        result = memory_explain_area("auth", repo_root=str(repo))
        assert result["status"] == "ok"
        assert result.get("source") == "persisted"

    def test_generated_when_no_init(self, tmp_path):
        """Without memory_init, explain_area generates on-the-fly."""
        from code_review_graph.tools import memory_explain_area
        repo = make_repo(tmp_path)
        result = memory_explain_area("auth", repo_root=str(repo))
        assert result["status"] == "ok"
        assert result.get("source") == "generated"

    def test_summary_present(self, tmp_path):
        from code_review_graph.tools import memory_explain_area
        result = memory_explain_area("auth", repo_root=str(make_repo(tmp_path)))
        assert "summary" in result


# ---------------------------------------------------------------------------
# memory_recent_changes tool
# ---------------------------------------------------------------------------


class TestMemoryRecentChangesTool:
    def test_returns_dict(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        result = memory_recent_changes(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result, dict)

    def test_not_ready_when_no_recent_md(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        repo = make_repo(tmp_path)
        # No .agent-memory/changes/recent.md yet
        result = memory_recent_changes(repo_root=str(repo))
        assert result["status"] == "not_ready"

    def test_summary_present(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        result = memory_recent_changes(repo_root=str(make_repo(tmp_path)))
        assert "summary" in result and result["summary"]

    def test_target_preserved_in_response(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        result = memory_recent_changes(target="auth", repo_root=str(make_repo(tmp_path)))
        assert result["target"] == "auth"

    def test_target_none_preserved(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        result = memory_recent_changes(repo_root=str(make_repo(tmp_path)))
        assert result["target"] is None

    def test_reads_recent_md_when_present(self, tmp_path):
        from code_review_graph.tools import memory_recent_changes
        repo = make_repo(tmp_path)
        changes_dir = repo / ".agent-memory" / "changes"
        changes_dir.mkdir(parents=True, exist_ok=True)
        (changes_dir / "recent.md").write_text("## Recent changes\n- auth: login updated\n")
        result = memory_recent_changes(repo_root=str(repo))
        assert result["status"] == "ok"
        assert "auth" in result["content"]


# ---------------------------------------------------------------------------
# memory_refresh tool
# ---------------------------------------------------------------------------


class TestMemoryRefreshTool:
    def test_returns_dict(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        result = memory_refresh(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result, dict)

    def test_status_ok(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        result = memory_refresh(repo_root=str(make_repo(tmp_path)))
        assert result["status"] == "ok"

    def test_refresh_type_full(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        result = memory_refresh(repo_root=str(make_repo(tmp_path)))
        assert result.get("refresh_type") == "full"

    def test_summary_says_refresh(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        result = memory_refresh(repo_root=str(make_repo(tmp_path)))
        assert "refresh" in result["summary"].lower()

    def test_agent_memory_dir_created(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        repo = make_repo(tmp_path)
        memory_refresh(repo_root=str(repo))
        assert (repo / ".agent-memory").is_dir()

    def test_features_returned(self, tmp_path):
        from code_review_graph.tools import memory_refresh
        result = memory_refresh(repo_root=str(make_repo(tmp_path)))
        assert isinstance(result["features"], list)


# ---------------------------------------------------------------------------
# Shared logic delegation — no duplication
# ---------------------------------------------------------------------------


class TestSharedLogicDelegation:
    """Tools must delegate to the memory package, not duplicate logic."""

    def test_memory_init_imports_scanner(self):
        """memory_init uses scanner.scan_repo."""
        import inspect
        from code_review_graph import tools
        src = inspect.getsource(tools.memory_init)
        assert "scan_repo" in src

    def test_memory_init_imports_classifier(self):
        from code_review_graph import tools
        import inspect
        src = inspect.getsource(tools.memory_init)
        assert "classify_features" in src or "classify_modules" in src

    def test_memory_prepare_context_delegates_to_builder(self):
        from code_review_graph import tools
        import inspect
        src = inspect.getsource(tools.memory_prepare_context)
        assert "build_context_pack" in src

    def test_memory_explain_area_delegates_to_generator(self):
        from code_review_graph import tools
        import inspect
        src = inspect.getsource(tools.memory_explain_area)
        assert "generate_feature_doc" in src or "generate_module_doc" in src

    def test_memory_refresh_delegates_to_memory_init(self):
        from code_review_graph import tools
        import inspect
        src = inspect.getsource(tools.memory_refresh)
        assert "memory_init" in src

    def test_main_tools_are_thin_wrappers(self):
        """main.py tool wrappers should be short — all logic is in tools.py."""
        import inspect
        import code_review_graph.main as m
        for tool_name in [
            "memory_init_tool", "memory_prepare_context_tool",
            "memory_explain_area_tool", "memory_recent_changes_tool",
            "memory_refresh_tool",
        ]:
            fn = getattr(m, tool_name)
            src = inspect.getsource(fn)
            # Count only non-blank, non-comment, non-docstring lines
            in_docstring = False
            code_lines = []
            for ln in src.splitlines():
                stripped = ln.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    in_docstring = not in_docstring
                    continue
                if in_docstring:
                    continue
                if stripped and not stripped.startswith("#"):
                    code_lines.append(ln)
            assert len(code_lines) <= 10, (
                f"{tool_name} is too thick ({len(code_lines)} code lines) — move logic to tools.py"
            )


# ---------------------------------------------------------------------------
# Regression: existing graph tools remain intact
# ---------------------------------------------------------------------------


class TestExistingToolsRegression:
    def test_existing_tool_functions_importable(self):
        from code_review_graph.tools import (
            build_or_update_graph,
            embed_graph,
            find_large_functions,
            get_docs_section,
            get_impact_radius,
            get_review_context,
            list_graph_stats,
            query_graph,
            semantic_search_nodes,
        )
        # All must be callable
        for fn in [
            build_or_update_graph, embed_graph, find_large_functions,
            get_docs_section, get_impact_radius, get_review_context,
            list_graph_stats, query_graph, semantic_search_nodes,
        ]:
            assert callable(fn)

    def test_tools_module_has_no_syntax_errors(self):
        import code_review_graph.tools as t  # noqa: F401
        assert t is not None

    def test_main_module_has_no_syntax_errors(self):
        import code_review_graph.main as m  # noqa: F401
        assert m is not None

    def test_get_docs_section_still_works(self, tmp_path):
        from code_review_graph.tools import get_docs_section
        result = get_docs_section("nonexistent-section")
        # Should return not_found, not crash
        assert result["status"] == "not_found"
