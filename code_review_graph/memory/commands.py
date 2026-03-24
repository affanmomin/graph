"""CLI command handlers for the repo-memory subsystem.

Each function here corresponds to one ``code-review-graph memory <sub>`` command.
They accept an ``argparse.Namespace`` and print human-readable output.

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
    """Scan repo and generate initial ``.agent-memory/`` artifacts.

    Pipeline: scanner → classifier → generator → writer → metadata.
    Generates repo.md, architecture.md, features/*.md, modules/*.md,
    and metadata/manifest.json + sources.json + confidence.json.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from .scanner import scan_repo
    from .classifier import classify_features, classify_modules
    from .generator import (
        generate_repo_summary, generate_architecture_doc,
        generate_feature_doc, generate_module_doc,
    )
    from .metadata import generate_manifest, save_manifest, save_sources_json, save_confidence_json
    from .writer import ensure_memory_dirs, write_text_if_changed

    repo_root = _resolve_repo_root(args)
    print(f"repo-memory: init")
    print(f"  scanning {repo_root} ...")

    # 1. Scan
    scan = scan_repo(repo_root)

    # 2. Classify features and modules (deterministic, no LLMs)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    # 3. Ensure directory tree
    dirs = ensure_memory_dirs(repo_root)

    # 4. Generate and write top-level artifacts
    artifacts: list[dict] = []

    repo_md_path = dirs["root"] / "repo.md"
    s_repo = write_text_if_changed(repo_md_path, generate_repo_summary(scan))
    artifacts.append({
        "artifact_id": "repo",
        "artifact_type": "repo",
        "relative_path": ".agent-memory/repo.md",
    })

    arch_md_path = dirs["root"] / "architecture.md"
    s_arch = write_text_if_changed(arch_md_path, generate_architecture_doc(scan))
    artifacts.append({
        "artifact_id": "architecture",
        "artifact_type": "architecture",
        "relative_path": ".agent-memory/architecture.md",
    })

    # 5. Write feature docs
    feature_statuses: list[tuple[str, str]] = []
    for feature in features:
        slug = feature.slug()
        rel = f".agent-memory/features/{slug}.md"
        path = dirs["features"] / f"{slug}.md"
        st = write_text_if_changed(path, generate_feature_doc(feature))
        feature_statuses.append((rel, st))
        artifacts.append({
            "artifact_id": f"feature:{slug}",
            "artifact_type": "feature",
            "relative_path": rel,
        })

    # 6. Write module docs
    module_statuses: list[tuple[str, str]] = []
    for module in modules:
        slug = module.slug()
        rel = f".agent-memory/modules/{slug}.md"
        path = dirs["modules"] / f"{slug}.md"
        st = write_text_if_changed(path, generate_module_doc(module))
        module_statuses.append((rel, st))
        artifacts.append({
            "artifact_id": f"module:{slug}",
            "artifact_type": "module",
            "relative_path": rel,
        })

    # 7. Write metadata
    manifest = generate_manifest(scan, artifacts)
    s_manifest = save_manifest(manifest, dirs["metadata"])
    s_sources = save_sources_json(features, modules, dirs["metadata"])
    s_confidence = save_confidence_json(features, modules, dirs["metadata"])

    # 8. Summary output
    print()
    print(f"  languages   : {', '.join(scan.languages) or 'none detected'}")
    print(f"  frameworks  : {', '.join(scan.framework_hints) or 'none detected'}")
    print(f"  source dirs : {', '.join(scan.source_dirs) or 'none detected'}")
    print(f"  test dirs   : {', '.join(scan.test_dirs) or 'none detected'}")
    print(f"  confidence  : {scan.confidence:.0%}")
    print(f"  features    : {len(features)}")
    print(f"  modules     : {len(modules)}")
    print()
    print(f"  .agent-memory/repo.md              [{s_repo}]")
    print(f"  .agent-memory/architecture.md      [{s_arch}]")
    for rel, st in feature_statuses:
        print(f"  {rel} [{st}]")
    for rel, st in module_statuses:
        print(f"  {rel} [{st}]")
    print(f"  .agent-memory/metadata/manifest.json   [{s_manifest}]")
    print(f"  .agent-memory/metadata/sources.json    [{s_sources}]")
    print(f"  .agent-memory/metadata/confidence.json [{s_confidence}]")
    print()
    if scan.notes:
        print("  Notes:")
        for note in scan.notes:
            print(f"    - {note}")
        print()
    print("  Done. Commit .agent-memory/ to share memory with your team.")


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
