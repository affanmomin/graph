"""CLI command handlers for the repo-memory subsystem.

Each function here corresponds to one ``code-review-graph memory <sub>`` command.
They accept an ``argparse.Namespace`` and print human-readable output.

Business logic will be wired in from later tickets (scanner, classifier,
generator, context_builder, refresh, overrides). For now every handler
validates its arguments and returns a clear "not yet implemented" message
so the CLI surface is stable and testable from this ticket onwards.

Handler naming convention mirrors the existing CLI: ``memory_<verb>_command``.
All handlers share the same signature: ``(args: argparse.Namespace) -> None``.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_repo_root(args: argparse.Namespace) -> Path:
    """Return the repo root path, auto-detecting when not supplied."""
    if getattr(args, "repo", None):
        return Path(args.repo)
    # Lazy import keeps graph engine decoupled at module level
    from ..incremental import find_project_root
    return find_project_root()


def _agent_memory_root(repo_root: Path) -> Path:
    """Return the ``.agent-memory/`` path for the given repo root."""
    return repo_root / ".agent-memory"


# ---------------------------------------------------------------------------
# memory init
# ---------------------------------------------------------------------------


def memory_init_command(args: argparse.Namespace) -> None:
    """Scaffold ``.agent-memory/`` and generate initial memory artifacts.

    Full implementation: scanner → classifier → generator → writer → metadata.
    This placeholder validates the repo root and prints what will be done.

    TODO(T3+): wire real scanner, classifier, generator, writer calls here.
    """
    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)

    print(f"repo-memory: init")
    print(f"  repo root     : {repo_root}")
    print(f"  target folder : {agent_memory}")
    print()
    print("  [not yet implemented]")
    print("  Will generate:")
    print("    .agent-memory/repo.md")
    print("    .agent-memory/architecture.md")
    print("    .agent-memory/features/*.md")
    print("    .agent-memory/modules/*.md")
    print("    .agent-memory/changes/recent.md")
    print("    .agent-memory/rules/conventions.md")
    print("    .agent-memory/metadata/manifest.json")
    print()
    print("  Run again after Ticket 3+ lands to get real output.")


# ---------------------------------------------------------------------------
# memory refresh
# ---------------------------------------------------------------------------


def memory_refresh_command(args: argparse.Namespace) -> None:
    """Refresh ``.agent-memory/`` artifacts, incrementally by default.

    Detects which source files changed since the last generation and
    regenerates only the affected artifacts. Use ``--full`` to regenerate
    everything regardless of what changed.

    TODO(T6): wire real refresh orchestrator (refresh.py) here.
    """
    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    full = getattr(args, "full", False)

    mode = "full" if full else "incremental"
    print(f"repo-memory: refresh ({mode})")
    print(f"  repo root     : {repo_root}")
    print(f"  target folder : {agent_memory}")
    print()
    print("  [not yet implemented]")
    print(f"  Will run a {mode} refresh of all .agent-memory/ artifacts.")
    if not full:
        print("  Pass --full to regenerate everything regardless of changes.")


# ---------------------------------------------------------------------------
# memory explain
# ---------------------------------------------------------------------------


def memory_explain_command(args: argparse.Namespace) -> None:
    """Explain a feature, module, or file path using stored memory.

    Looks up the closest matching memory artifact for the given target
    and prints a grounded, concise explanation suitable for pasting into
    a Claude Code session.

    TODO(T4+): load and display the relevant .agent-memory/ artifact here.
    """
    repo_root = _resolve_repo_root(args)
    target: str = args.target

    print(f"repo-memory: explain")
    print(f"  repo root : {repo_root}")
    print(f"  target    : {target}")
    print()
    print("  [not yet implemented]")
    print(f"  Will explain '{target}' using generated memory artifacts.")
    print("  Run `memory init` first to generate artifacts.")


# ---------------------------------------------------------------------------
# memory prepare-context
# ---------------------------------------------------------------------------


def memory_prepare_context_command(args: argparse.Namespace) -> None:
    """Assemble a focused context pack for the given natural-language task.

    This is the core product feature. Returns a ``TaskContextPack`` containing
    the relevant features, modules, files, tests, warnings, and a task summary
    — ready to be injected into a fresh Claude Code session.

    TODO(T7): wire context_builder.build_context() + overrides here.
    """
    repo_root = _resolve_repo_root(args)
    task: str = args.task

    if not task.strip():
        print("Error: task description cannot be empty.", flush=True)
        raise SystemExit(1)

    print(f"repo-memory: prepare-context")
    print(f"  repo root : {repo_root}")
    print(f"  task      : {task}")
    print()
    print("  [not yet implemented]")
    print("  Will return:")
    print("    relevant features")
    print("    relevant modules")
    print("    relevant files")
    print("    relevant tests")
    print("    warnings / safe-boundary notes")
    print("    task summary for Claude Code")
    print()
    print("  Run `memory init` first to generate artifacts.")


# ---------------------------------------------------------------------------
# memory changed
# ---------------------------------------------------------------------------


def memory_changed_command(args: argparse.Namespace) -> None:
    """Show recent meaningful changes affecting a feature, module, or path.

    Surfaces what changed recently in the specified area so a developer
    starting a task can understand the current state without reading git log.

    TODO(T4+): load changes/recent.md and filter by target area.
    """
    repo_root = _resolve_repo_root(args)
    target: str = args.target

    print(f"repo-memory: changed")
    print(f"  repo root : {repo_root}")
    print(f"  target    : {target}")
    print()
    print("  [not yet implemented]")
    print(f"  Will show recent changes affecting '{target}'.")
    print("  Run `memory init` first to generate change artifacts.")


# ---------------------------------------------------------------------------
# memory annotate
# ---------------------------------------------------------------------------


def memory_annotate_command(args: argparse.Namespace) -> None:
    """Open or create the human override file for ``.agent-memory/overrides/``.

    Override files let developers correct and constrain generated memory —
    marking files that must always be included, paths that must never be
    edited, and task-specific hints.

    TODO(T7): scaffold the override YAML file and open it in $EDITOR.
    """
    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    overrides_dir = agent_memory / "overrides"

    print(f"repo-memory: annotate")
    print(f"  repo root     : {repo_root}")
    print(f"  overrides dir : {overrides_dir}")
    print()
    print("  [not yet implemented]")
    print("  Will scaffold .agent-memory/overrides/rules.yaml and open it")
    print("  in your $EDITOR for human correction and guidance.")
    print()
    print("  Override format (coming soon):")
    print("    always_include: [src/auth/middleware.py]")
    print("    never_edit:     [migrations/]")
    print("    notes:          ['The auth module uses a custom JWT library.']")
    print("    task_hints:     [{pattern: 'add endpoint', hint: '...'}]")
