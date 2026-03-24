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
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from .classifier import classify_features, classify_modules
    from .refresh import execute_refresh, plan_refresh
    from .scanner import scan_repo  # noqa: I001

    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    full = getattr(args, "full", False)
    mode = "full" if full else "incremental"

    print(f"repo-memory: refresh ({mode})")
    print(f"  repo root     : {repo_root}")
    print(f"  target folder : {agent_memory}")
    print()

    if not agent_memory.exists():
        print("  .agent-memory/ not found — run 'memory init' first.")
        return

    # Determine changed files for incremental mode
    changed_files: list[str] = []
    if not full:
        from ..incremental import get_changed_files, get_staged_and_unstaged
        changed_files = get_changed_files(repo_root)
        if not changed_files:
            # Fallback: staged and unstaged edits (pre-commit or workdir changes)
            changed_files = get_staged_and_unstaged(repo_root)

    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    plan = plan_refresh(changed_files, features, modules, full=full)
    result = execute_refresh(plan, repo_root, features, modules, scan)

    print(f"  mode          : {result['mode']}")
    print(f"  changed files : {len(result['changed_files'])}")
    print(f"  plan          : {result['reason']}")
    print()

    if result["artifacts_updated"]:
        print("  Updated:")
        for a in result["artifacts_updated"]:
            print(f"    {a}")
        print()

    if result["artifacts_skipped"]:
        print("  Unchanged:")
        for a in result["artifacts_skipped"]:
            print(f"    {a}")
        print()

    print("  Done.")


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

    Pipeline: scanner → classifier → context_builder → output (text or JSON).
    Runs the classifier fresh each time so the pack always reflects the current
    repo state, even if ``memory init`` has not been run.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from .scanner import scan_repo
    from .classifier import classify_features, classify_modules
    from .context_builder import build_context_pack

    repo_root = _resolve_repo_root(args)
    task: str = args.task.strip()
    as_json: bool = getattr(args, "json", False)

    if not task:
        print("Error: task description cannot be empty.", flush=True)
        raise SystemExit(1)

    # Classify — fast, deterministic, no LLMs
    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    pack = build_context_pack(task, features, modules)

    if as_json:
        _print_pack_json(pack)
    else:
        _print_pack_text(pack)


def _print_pack_text(pack) -> None:
    """Print a TaskContextPack in human-readable format."""
    print(f"repo-memory: prepare-context")
    print(f"  task: {pack.task}")
    print()

    if pack.relevant_features:
        print("  Relevant features:")
        for name in pack.relevant_features:
            print(f"    - {name}")
        print()

    if pack.relevant_modules:
        print("  Relevant modules:")
        for name in pack.relevant_modules:
            print(f"    - {name}")
        print()

    if pack.relevant_files:
        print("  Files to inspect:")
        for f in pack.relevant_files:
            print(f"    - {f}")
        print()

    if pack.relevant_tests:
        print("  Related tests:")
        for t in pack.relevant_tests:
            print(f"    - {t}")
        print()

    if pack.warnings:
        print("  Warnings:")
        for w in pack.warnings:
            print(f"    ! {w}")
        print()

    print("  Summary:")
    for line in pack.summary.splitlines():
        print(f"    {line}")
    print()

    if pack.is_empty():
        print("  No relevant context found. Run `memory init` to generate memory artifacts.")


def _print_pack_json(pack) -> None:
    """Print a TaskContextPack as JSON."""
    import json
    data = {
        "task": pack.task,
        "relevant_features": pack.relevant_features,
        "relevant_modules": pack.relevant_modules,
        "relevant_files": pack.relevant_files,
        "relevant_tests": pack.relevant_tests,
        "warnings": pack.warnings,
        "summary": pack.summary,
    }
    print(json.dumps(data, indent=2))


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
