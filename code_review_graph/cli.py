"""CLI entry point for code-review-graph.

Usage:
    code-review-graph install
    code-review-graph init
    code-review-graph build [--base BASE]
    code-review-graph update [--base BASE]
    code-review-graph watch
    code-review-graph status
    code-review-graph serve
    code-review-graph visualize

    code-review-graph memory init
    code-review-graph memory refresh [--full]
    code-review-graph memory explain <target>
    code-review-graph memory prepare-context "<task>"
    code-review-graph memory changed <target>
    code-review-graph memory annotate
"""

from __future__ import annotations

import sys

# Python version check — must come before any other imports
if sys.version_info < (3, 10):
    print("code-review-graph requires Python 3.10 or higher.")
    print(f"  You are running Python {sys.version}")
    print()
    print("Install Python 3.10+: https://www.python.org/downloads/")
    sys.exit(1)

import argparse
import json
import logging
import os
from importlib.metadata import version as pkg_version
from pathlib import Path


def _get_version() -> str:
    """Get the installed package version."""
    try:
        return pkg_version("code-review-graph")
    except Exception:
        return "dev"


def _supports_color() -> bool:
    """Check if the terminal likely supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty"):
        return False
    return sys.stdout.isatty()


def _print_banner() -> None:
    """Print the startup banner with graph art and available commands."""
    color = _supports_color()
    version = _get_version()

    # ANSI escape codes
    c = "\033[36m" if color else ""   # cyan — graph art
    y = "\033[33m" if color else ""   # yellow — center node
    b = "\033[1m" if color else ""    # bold
    d = "\033[2m" if color else ""    # dim
    g = "\033[32m" if color else ""   # green — commands
    m = "\033[35m" if color else ""   # magenta — memory commands
    r = "\033[0m" if color else ""    # reset

    print(f"""
{c}  ●──●──●{r}
{c}  │╲ │ ╱│{r}       {b}code-review-graph{r}  {d}v{version}{r}
{c}  ●──{y}◆{c}──●{r}
{c}  │╱ │ ╲│{r}       {d}Graph-powered repo memory for Claude Code.{r}
{c}  ●──●──●{r}       {d}Stop re-explaining your codebase every session.{r}

  {b}Graph commands:{r}
    {g}install{r}     Set up Claude Code integration
    {g}init{r}        Alias for install
    {g}build{r}       Full graph build {d}(parse all files){r}
    {g}update{r}      Incremental update {d}(changed files only){r}
    {g}watch{r}       Auto-update on file changes
    {g}status{r}      Show graph statistics
    {g}visualize{r}   Generate interactive HTML graph
    {g}serve{r}       Start MCP server

  {b}Repo-memory commands:{r}
    {m}memory init{r}             Generate .agent-memory/ artifacts
    {m}memory refresh{r}          Refresh memory {d}(incremental by default){r}
    {m}memory explain{r} {d}<target>{r}  Explain a feature, module, or path
    {m}memory prepare-context{r}  Build task context pack for Claude Code
    {m}memory changed{r} {d}<target>{r}  Show recent changes in an area
    {m}memory annotate{r}         Edit human override guidance
    {m}memory stats{r}            Performance and quality metrics

  {d}Run{r} {b}code-review-graph <command> --help{r} {d}for details{r}
""")


def _handle_init(args: argparse.Namespace) -> None:
    """Set up .mcp.json in the project root for Claude Code integration."""
    from .incremental import find_repo_root

    repo_root = Path(args.repo) if args.repo else find_repo_root()
    if not repo_root:
        repo_root = Path.cwd()

    mcp_path = repo_root / ".mcp.json"
    dry_run = getattr(args, "dry_run", False)

    mcp_config = {
        "mcpServers": {
            "code-review-graph": {
                "command": "uvx",
                "args": ["code-review-graph", "serve"],
            }
        }
    }

    # Merge into existing .mcp.json if present
    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text())
            if "code-review-graph" in existing.get("mcpServers", {}):
                print(f"Already configured in {mcp_path}")
                return
            existing.setdefault("mcpServers", {}).update(mcp_config["mcpServers"])
            mcp_config = existing
        except json.JSONDecodeError:
            print(f"Warning: existing {mcp_path} has invalid JSON, overwriting.")
        except (KeyError, TypeError):
            print(f"Warning: existing {mcp_path} has unexpected structure, overwriting.")

    if dry_run:
        print(f"[dry-run] Would write to {mcp_path}:")
        print(json.dumps(mcp_config, indent=2))
        print()
        print("[dry-run] No files were modified.")
        return

    mcp_path.write_text(json.dumps(mcp_config, indent=2) + "\n")
    print(f"Created {mcp_path}")
    print()
    print("Next steps:")
    print("  1. code-review-graph build    # build the knowledge graph")
    print("  2. Restart Claude Code        # to pick up the new MCP server")


def _add_memory_subparsers(memory_cmd: argparse.ArgumentParser) -> None:
    """Register all ``memory <sub>`` subcommands onto the memory parser."""
    mem_sub = memory_cmd.add_subparsers(dest="memory_command")

    # memory init
    mem_init = mem_sub.add_parser(
        "init",
        help="Scan repo and generate .agent-memory/ artifacts",
        description=(
            "Initialise repo memory for this repository.\n\n"
            "Scans the codebase, classifies features and modules, and generates\n"
            "durable .agent-memory/ artifacts that can be committed to Git.\n\n"
            "Example:\n"
            "  code-review-graph memory init\n"
            "  code-review-graph memory init --repo /path/to/repo"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_init.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # memory refresh
    mem_refresh = mem_sub.add_parser(
        "refresh",
        help="Refresh .agent-memory/ artifacts (incremental by default)",
        description=(
            "Refresh repo memory after code changes.\n\n"
            "By default only artifacts affected by recent file changes are\n"
            "regenerated (incremental mode). Use --full to regenerate everything.\n\n"
            "Examples:\n"
            "  code-review-graph memory refresh\n"
            "  code-review-graph memory refresh --full"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_refresh.add_argument("--repo", default=None, help="Repository root (auto-detected)")
    mem_refresh.add_argument(
        "--full", action="store_true",
        help="Regenerate all artifacts regardless of what changed",
    )

    # memory explain
    mem_explain = mem_sub.add_parser(
        "explain",
        help="Explain a feature, module, or file path using stored memory",
        description=(
            "Print a grounded explanation of a repo area from generated memory.\n\n"
            "TARGET can be a feature name, module name, or relative file path.\n\n"
            "Examples:\n"
            "  code-review-graph memory explain authentication\n"
            "  code-review-graph memory explain code_review_graph/memory\n"
            "  code-review-graph memory explain src/api/routes.py"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_explain.add_argument("target", help="Feature name, module name, or file path to explain")
    mem_explain.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # memory prepare-context
    mem_ctx = mem_sub.add_parser(
        "prepare-context",
        help="Build a focused context pack for a task (for Claude Code)",
        description=(
            "Assemble a task-aware context pack from stored memory.\n\n"
            "Given a natural-language task description, returns the relevant\n"
            "features, modules, files, tests, warnings, and a task summary\n"
            "ready to inject into a fresh Claude Code session.\n\n"
            "Examples:\n"
            '  code-review-graph memory prepare-context "add rate limiting to the API"\n'
            '  code-review-graph memory prepare-context "fix the auth token refresh bug"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_ctx.add_argument("task", help="Natural-language task description (quote it)")
    mem_ctx.add_argument("--repo", default=None, help="Repository root (auto-detected)")
    mem_ctx.add_argument(
        "--json", action="store_true", default=False,
        help="Output context pack as JSON instead of human-readable text",
    )

    # memory changed
    mem_changed = mem_sub.add_parser(
        "changed",
        help="Show recent meaningful changes in a feature, module, or path",
        description=(
            "Show what changed recently in the specified area.\n\n"
            "Surfaces recent git changes filtered to TARGET so developers\n"
            "starting a task understand current state without reading raw git log.\n\n"
            "Examples:\n"
            "  code-review-graph memory changed authentication\n"
            "  code-review-graph memory changed src/api"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_changed.add_argument("target", help="Feature name, module name, or file path")
    mem_changed.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # memory stats
    mem_stats = mem_sub.add_parser(
        "stats",
        help="Show performance and quality metrics from recent memory command runs",
        description=(
            "Display a summary of recent memory command runs from the local\n"
            "metrics log (.code-review-graph/memory-metrics.jsonl).\n\n"
            "Shows: timing, classification quality, context pack sizes,\n"
            "token estimates, fallback rate, and graph enrichment usage.\n\n"
            "Example:\n"
            "  repomind memory stats\n"
            "  repomind memory stats --last 50"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_stats.add_argument("--repo", default=None, help="Repository root (auto-detected)")
    mem_stats.add_argument("--last", type=int, default=20, help="Number of recent runs to show (default: 20)")

    # memory annotate
    mem_annotate = mem_sub.add_parser(
        "annotate",
        help="Edit human override guidance in .agent-memory/overrides/",
        description=(
            "Open or scaffold the human override file for this repo.\n\n"
            "Override files let you correct and constrain generated memory:\n"
            "  always_include — files always surfaced in context packs\n"
            "  never_edit     — paths Claude should never suggest changing\n"
            "  notes          — free-text domain knowledge\n"
            "  task_hints     — task-pattern-matched hints for Claude\n\n"
            "Example:\n"
            "  code-review-graph memory annotate"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mem_annotate.add_argument("--repo", default=None, help="Repository root (auto-detected)")


def _handle_memory(args: argparse.Namespace) -> None:
    """Dispatch ``memory <sub>`` commands to the appropriate handler."""
    from .memory.commands import (
        memory_annotate_command,
        memory_changed_command,
        memory_explain_command,
        memory_init_command,
        memory_prepare_context_command,
        memory_refresh_command,
        memory_stats_command,
    )

    sub = getattr(args, "memory_command", None)

    dispatch = {
        "init": memory_init_command,
        "refresh": memory_refresh_command,
        "explain": memory_explain_command,
        "prepare-context": memory_prepare_context_command,
        "changed": memory_changed_command,
        "annotate": memory_annotate_command,
        "stats": memory_stats_command,
    }

    if sub in dispatch:
        dispatch[sub](args)
    else:
        # No sub-command — print memory-specific help
        print("usage: code-review-graph memory <command> [options]")
        print()
        print("Repo-memory commands:")
        print("  init              Generate .agent-memory/ artifacts for this repo")
        print("  refresh           Refresh memory artifacts (incremental by default)")
        print("  explain <target>  Explain a feature, module, or file path")
        print("  prepare-context   Build task context pack for Claude Code")
        print("  changed <target>  Show recent changes in a feature, module, or path")
        print("  annotate          Edit human override guidance")
        print("  stats             Show performance and quality metrics")
        print()
        print("Run 'code-review-graph memory <command> --help' for details.")


def main() -> None:
    """Main CLI entry point."""
    ap = argparse.ArgumentParser(
        prog="code-review-graph",
        description="Persistent incremental knowledge graph for code reviews",
    )
    ap.add_argument(
        "-v", "--version", action="store_true", help="Show version and exit"
    )
    sub = ap.add_subparsers(dest="command")

    # install (primary) + init (alias)
    install_cmd = sub.add_parser(
        "install", help="Register MCP server with Claude Code (creates .mcp.json)"
    )
    install_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")
    install_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing files",
    )

    init_cmd = sub.add_parser(
        "init", help="Alias for install"
    )
    init_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")
    init_cmd.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without writing files",
    )

    # build
    build_cmd = sub.add_parser("build", help="Full graph build (re-parse all files)")
    build_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # update
    update_cmd = sub.add_parser("update", help="Incremental update (only changed files)")
    update_cmd.add_argument("--base", default="HEAD~1", help="Git diff base (default: HEAD~1)")
    update_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # watch
    watch_cmd = sub.add_parser("watch", help="Watch for changes and auto-update")
    watch_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # status
    status_cmd = sub.add_parser("status", help="Show graph statistics")
    status_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # visualize
    vis_cmd = sub.add_parser("visualize", help="Generate interactive HTML graph visualization")
    vis_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # serve
    serve_cmd = sub.add_parser("serve", help="Start MCP server (stdio transport)")
    serve_cmd.add_argument("--repo", default=None, help="Repository root (auto-detected)")

    # memory — repo-memory command group
    memory_cmd = sub.add_parser(
        "memory",
        help="Repo-memory commands (init, refresh, explain, prepare-context, changed, annotate)",
    )
    _add_memory_subparsers(memory_cmd)

    args = ap.parse_args()

    if args.version:
        print(f"code-review-graph {_get_version()}")
        return

    if not args.command:
        _print_banner()
        return

    if args.command == "serve":
        from .main import main as serve_main
        serve_main(repo_root=args.repo)
        return

    if args.command == "memory":
        _handle_memory(args)
        return

    if args.command in ("init", "install"):
        _handle_init(args)
        return

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    from .graph import GraphStore
    from .incremental import (
        find_project_root,
        find_repo_root,
        full_build,
        get_db_path,
        incremental_update,
        watch,
    )

    if args.command == "update":
        # update requires git for diffing
        repo_root = Path(args.repo) if args.repo else find_repo_root()
        if not repo_root:
            logging.error("Not in a git repository. 'update' requires git for diffing.")
            logging.error("Use 'build' for a full parse, or run 'git init' first.")
            sys.exit(1)
    else:
        repo_root = Path(args.repo) if args.repo else find_project_root()

    db_path = get_db_path(repo_root)
    store = GraphStore(db_path)

    try:
        if args.command == "build":
            result = full_build(repo_root, store)
            print(
                f"Full build: {result['files_parsed']} files, "
                f"{result['total_nodes']} nodes, {result['total_edges']} edges"
            )
            if result["errors"]:
                print(f"Errors: {len(result['errors'])}")

        elif args.command == "update":
            result = incremental_update(repo_root, store, base=args.base, refresh_memory=True)
            print(
                f"Incremental: {result['files_updated']} files updated, "
                f"{result['total_nodes']} nodes, {result['total_edges']} edges"
            )

        elif args.command == "status":
            stats = store.get_stats()
            print(f"Nodes: {stats.total_nodes}")
            print(f"Edges: {stats.total_edges}")
            print(f"Files: {stats.files_count}")
            print(f"Languages: {', '.join(stats.languages)}")
            print(f"Last updated: {stats.last_updated or 'never'}")

        elif args.command == "watch":
            watch(repo_root, store)

        elif args.command == "visualize":
            from .visualization import generate_html
            html_path = repo_root / ".code-review-graph" / "graph.html"
            generate_html(store, html_path)
            print(f"Visualization: {html_path}")
            print("Open in browser to explore your codebase graph.")

    finally:
        store.close()
