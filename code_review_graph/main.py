"""MCP server entry point for Code Review Graph.

Run as: code-review-graph serve
Communicates via stdio (standard MCP transport).
"""

from __future__ import annotations

from typing import Optional

from fastmcp import FastMCP

from .tools import (
    build_or_update_graph,
    embed_graph,
    find_large_functions,
    get_docs_section,
    get_impact_radius,
    get_review_context,
    list_graph_stats,
    memory_explain_area,
    memory_init,
    memory_prepare_context,
    memory_recent_changes,
    memory_refresh,
    query_graph,
    semantic_search_nodes,
)

# NOTE: Thread-safe for stdio MCP (single-threaded). If adding HTTP/SSE
# transport with concurrent requests, replace with contextvars.ContextVar.
_default_repo_root: str | None = None

mcp = FastMCP(
    "code-review-graph",
    instructions=(
        "Persistent incremental knowledge graph for token-efficient, "
        "context-aware code reviews. Parses your codebase with Tree-sitter, "
        "builds a structural graph, and provides smart impact analysis."
    ),
)


@mcp.tool()
def build_or_update_graph_tool(
    full_rebuild: bool = False,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
) -> dict:
    """Build or incrementally update the code knowledge graph.

    Call this first to initialize the graph, or after making changes.
    By default performs an incremental update (only changed files).
    Set full_rebuild=True to re-parse every file.

    Args:
        full_rebuild: If True, re-parse all files. Default: False (incremental).
        repo_root: Repository root path. Auto-detected from current directory if omitted.
        base: Git ref to diff against for incremental updates. Default: HEAD~1.
    """
    return build_or_update_graph(
        full_rebuild=full_rebuild, repo_root=repo_root, base=base
    )


@mcp.tool()
def get_impact_radius_tool(
    changed_files: Optional[list[str]] = None,
    max_depth: int = 2,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
) -> dict:
    """Analyze the blast radius of changed files in the codebase.

    Shows which functions, classes, and files are impacted by changes.
    Auto-detects changed files from git if not specified.

    Args:
        changed_files: List of changed file paths (relative to repo root). Auto-detected if omitted.
        max_depth: Number of hops to traverse in the dependency graph. Default: 2.
        repo_root: Repository root path. Auto-detected if omitted.
        base: Git ref for auto-detecting changes. Default: HEAD~1.
    """
    return get_impact_radius(
        changed_files=changed_files, max_depth=max_depth,
        repo_root=repo_root, base=base,
    )


@mcp.tool()
def query_graph_tool(
    pattern: str,
    target: str,
    repo_root: Optional[str] = None,
) -> dict:
    """Run a predefined graph query to explore code relationships.

    Available patterns:
    - callers_of: Find functions that call the target
    - callees_of: Find functions called by the target
    - imports_of: Find what the target imports
    - importers_of: Find files that import the target
    - children_of: Find nodes contained in a file or class
    - tests_for: Find tests for the target
    - inheritors_of: Find classes inheriting from the target
    - file_summary: Get all nodes in a file

    Args:
        pattern: Query pattern name (see above).
        target: Node name, qualified name, or file path to query.
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return query_graph(pattern=pattern, target=target, repo_root=repo_root)


@mcp.tool()
def get_review_context_tool(
    changed_files: Optional[list[str]] = None,
    max_depth: int = 2,
    include_source: bool = True,
    max_lines_per_file: int = 200,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
) -> dict:
    """Generate a focused, token-efficient review context for code changes.

    Combines impact analysis with source snippets and review guidance.
    Use this for comprehensive code reviews.

    Args:
        changed_files: Files to review. Auto-detected from git diff if omitted.
        max_depth: Impact radius depth. Default: 2.
        include_source: Include source code snippets. Default: True.
        max_lines_per_file: Max source lines per file. Default: 200.
        repo_root: Repository root path. Auto-detected if omitted.
        base: Git ref for change detection. Default: HEAD~1.
    """
    return get_review_context(
        changed_files=changed_files, max_depth=max_depth,
        include_source=include_source, max_lines_per_file=max_lines_per_file,
        repo_root=repo_root, base=base,
    )


@mcp.tool()
def semantic_search_nodes_tool(
    query: str,
    kind: Optional[str] = None,
    limit: int = 20,
    repo_root: Optional[str] = None,
) -> dict:
    """Search for code entities by name, keyword, or semantic similarity.

    Uses vector embeddings for semantic search when available (run embed_graph_tool
    first, requires sentence-transformers). Falls back to keyword matching otherwise.

    Args:
        query: Search string to match against node names.
        kind: Optional filter: File, Class, Function, Type, or Test.
        limit: Maximum results. Default: 20.
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return semantic_search_nodes(
        query=query, kind=kind, limit=limit, repo_root=repo_root
    )


@mcp.tool()
def embed_graph_tool(
    repo_root: Optional[str] = None,
) -> dict:
    """Compute vector embeddings for all graph nodes to enable semantic search.

    Requires: pip install code-review-graph[embeddings]
    Uses the all-MiniLM-L6-v2 model (fast, 384-dim vectors).
    Only computes embeddings for nodes that don't already have them.

    After running this, semantic_search_nodes_tool will use vector similarity
    instead of keyword matching for much better results.

    Args:
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return embed_graph(repo_root=repo_root)


@mcp.tool()
def list_graph_stats_tool(
    repo_root: Optional[str] = None,
) -> dict:
    """Get aggregate statistics about the code knowledge graph.

    Shows total nodes, edges, languages, files, and last update time.
    Useful for checking if the graph is built and up to date.

    Args:
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return list_graph_stats(repo_root=repo_root)


@mcp.tool()
def get_docs_section_tool(
    section_name: str,
) -> dict:
    """Get a specific section from the LLM-optimized documentation reference.

    Returns only the requested section content for minimal token usage.
    Use this before answering any user question about the plugin.

    Available sections: usage, review-delta, review-pr, commands, legal,
    watch, embeddings, languages, troubleshooting.

    Args:
        section_name: The section to retrieve (e.g. "review-delta", "usage").
    """
    return get_docs_section(section_name=section_name, repo_root=_default_repo_root)


@mcp.tool()
def find_large_functions_tool(
    min_lines: int = 50,
    kind: Optional[str] = None,
    file_path_pattern: Optional[str] = None,
    limit: int = 50,
    repo_root: Optional[str] = None,
) -> dict:
    """Find functions, classes, or files exceeding a line-count threshold.

    Useful for decomposition audits, code quality checks, and enforcing
    size limits during code review. Results are ordered by line count.

    Args:
        min_lines: Minimum line count to flag. Default: 50.
        kind: Optional filter: Function, Class, File, or Test.
        file_path_pattern: Filter by file path substring (e.g. "components/").
        limit: Maximum results. Default: 50.
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return find_large_functions(
        min_lines=min_lines, kind=kind, file_path_pattern=file_path_pattern,
        limit=limit, repo_root=repo_root,
    )


@mcp.tool()
def memory_init_tool(
    repo_root: Optional[str] = None,
) -> dict:
    """Scan the repository and generate durable .agent-memory/ artifacts.

    Produces feature docs, module docs, and metadata (manifest, sources,
    confidence scores).  Safe to call repeatedly — unchanged files are skipped.
    Run this once per repo before using other memory tools.

    Args:
        repo_root: Repository root path. Auto-detected from current directory if omitted.
    """
    return memory_init(repo_root=repo_root)


@mcp.tool()
def memory_prepare_context_tool(
    task: str,
    repo_root: Optional[str] = None,
) -> dict:
    """Build a focused context pack for a developer task.

    Given a natural-language task description, returns the relevant features,
    modules, files, tests, warnings, and a concise summary — ready to inject
    into a fresh Claude Code session without re-explaining the codebase.

    Examples:
        "add oauth provider"
        "debug the login token refresh bug"
        "add invoice export endpoint"

    Args:
        task: Natural-language description of the task (required).
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return memory_prepare_context(task=task, repo_root=repo_root)


@mcp.tool()
def memory_explain_area_tool(
    name: str,
    repo_root: Optional[str] = None,
) -> dict:
    """Explain a named feature or module using stored repo memory.

    Looks up the matching .agent-memory/ artifact and returns its full
    content — purpose, files, tests, responsibilities, and warnings.
    Generates on-the-fly if memory init has not been run yet.

    Args:
        name: Feature or module name (e.g. "auth", "billing", "src/auth").
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return memory_explain_area(name=name, repo_root=repo_root)


@mcp.tool()
def memory_recent_changes_tool(
    target: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> dict:
    """Show recent meaningful changes for a feature, module, or file path.

    Reads from .agent-memory/changes/recent.md when available.
    Incremental change tracking requires running memory_refresh_tool after commits.

    Args:
        target: Optional feature name, module name, or file path to filter by.
                Returns all recent changes when omitted.
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return memory_recent_changes(target=target, repo_root=repo_root)


@mcp.tool()
def memory_refresh_tool(
    repo_root: Optional[str] = None,
) -> dict:
    """Regenerate .agent-memory/ artifacts to reflect the latest repo state.

    Rescans the repository and rewrites all memory artifacts. Unchanged
    content is skipped (idempotent). Run after significant commits or
    refactors to keep memory fresh.

    Args:
        repo_root: Repository root path. Auto-detected if omitted.
    """
    return memory_refresh(repo_root=repo_root)


def main(repo_root: str | None = None) -> None:
    """Run the MCP server via stdio."""
    global _default_repo_root
    _default_repo_root = repo_root
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
