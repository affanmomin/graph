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


_GLOBAL_YAML_TEMPLATE = """\
# .agent-memory/overrides/global.yaml
# Human override file for repo-memory. Edit freely — this file is never auto-overwritten.
#
# always_include  paths always surfaced in context packs regardless of task
# never_edit      paths Claude should never suggest modifying
# notes           free-text domain knowledge added to rules/conventions.md
# task_hints      pattern-matched hints surfaced when task description matches

# always_include:
#   - src/auth/middleware.py
#   - docs/architecture.md

# never_edit:
#   - migrations/
#   - src/vendor/

# notes:
#   - The auth module uses a custom JWT library (not PyJWT)
#   - All API handlers must validate with the shared RequestValidator

# task_hints:
#   - pattern: "add endpoint"
#     hint: "Register new routes in src/api/router.py and add a test in tests/api/"
#   - pattern: "database migration"
#     hint: "Use alembic revision --autogenerate; never edit existing migrations"
"""


def run_memory_init_pipeline(root: Path) -> dict:
    """Execute the full memory init pipeline and return structured results.

    This is the single shared implementation called by both the CLI command
    handler (:func:`memory_init_command`) and the MCP tool adapter
    (``tools.memory_init``).  Keeping logic here ensures both surfaces always
    produce identical output.

    Pipeline: scanner → classifier → generator → writer → metadata.
    Generates:
      - repo.md, architecture.md
      - features/<slug>.md (one per detected feature)
      - modules/<slug>.md  (one per detected module)
      - rules/conventions.md, rules/safe-boundaries.md
      - CLAUDE.md (compact session bootstrap for Claude Code)
      - metadata/manifest.json, sources.json, confidence.json

    Args:
        root: Absolute path to the repository root.

    Returns:
        Dict with keys: ``scan``, ``features``, ``modules``, ``dirs``,
        ``artifacts``, ``write_statuses``, ``feature_statuses``,
        ``module_statuses``.
    """
    from .classifier import classify_features, classify_modules
    from .generator import (  # noqa: I001
        generate_repo_summary, generate_architecture_doc,
        generate_feature_doc, generate_module_doc,
        generate_conventions_doc, generate_safe_boundaries_doc,
        generate_claude_memory_doc,
    )
    from .metadata import generate_manifest, save_manifest, save_sources_json, save_confidence_json
    from .overrides import load_overrides
    from .scanner import scan_repo
    from .writer import ensure_memory_dirs, write_text_if_changed

    # 1. Scan
    scan = scan_repo(root)

    # 2. Classify features and modules (deterministic, no LLMs)
    features = classify_features(root, scan)
    modules = classify_modules(root, scan)

    # 3. Ensure directory tree
    dirs = ensure_memory_dirs(root)

    artifacts: list[dict] = []
    write_statuses: dict[str, str] = {}

    # 4. Top-level docs
    write_statuses[".agent-memory/repo.md"] = write_text_if_changed(
        dirs["root"] / "repo.md", generate_repo_summary(scan)
    )
    artifacts.append({"artifact_id": "repo", "artifact_type": "repo",
                       "relative_path": ".agent-memory/repo.md"})

    write_statuses[".agent-memory/architecture.md"] = write_text_if_changed(
        dirs["root"] / "architecture.md", generate_architecture_doc(scan)
    )
    artifacts.append({"artifact_id": "architecture", "artifact_type": "architecture",
                       "relative_path": ".agent-memory/architecture.md"})

    # 5. Feature docs
    feature_statuses: list[tuple[str, str]] = []
    for feature in features:
        slug = feature.slug()
        rel = f".agent-memory/features/{slug}.md"
        st = write_text_if_changed(dirs["features"] / f"{slug}.md", generate_feature_doc(feature))
        write_statuses[rel] = st
        feature_statuses.append((rel, st))
        artifacts.append({"artifact_id": f"feature:{slug}", "artifact_type": "feature",
                           "relative_path": rel})

    # 6. Module docs
    module_statuses: list[tuple[str, str]] = []
    for module in modules:
        slug = module.slug()
        rel = f".agent-memory/modules/{slug}.md"
        st = write_text_if_changed(dirs["modules"] / f"{slug}.md", generate_module_doc(module))
        write_statuses[rel] = st
        module_statuses.append((rel, st))
        artifacts.append({"artifact_id": f"module:{slug}", "artifact_type": "module",
                           "relative_path": rel})

    # 7. Load overrides and write rule docs
    overrides = load_overrides(dirs["root"])

    rel_conv = ".agent-memory/rules/conventions.md"
    write_statuses[rel_conv] = write_text_if_changed(
        dirs["rules"] / "conventions.md", generate_conventions_doc(scan, overrides)
    )
    artifacts.append({"artifact_id": "rules:conventions", "artifact_type": "rules",
                       "relative_path": rel_conv})

    rel_sb = ".agent-memory/rules/safe-boundaries.md"
    write_statuses[rel_sb] = write_text_if_changed(
        dirs["rules"] / "safe-boundaries.md", generate_safe_boundaries_doc(scan, overrides)
    )
    artifacts.append({"artifact_id": "rules:safe-boundaries", "artifact_type": "rules",
                       "relative_path": rel_sb})

    # 8. CLAUDE.md — compact session bootstrap for Claude Code
    rel_claude = ".agent-memory/CLAUDE.md"
    write_statuses[rel_claude] = write_text_if_changed(
        dirs["root"] / "CLAUDE.md", generate_claude_memory_doc(scan, overrides)
    )
    artifacts.append({"artifact_id": "claude-md", "artifact_type": "claude-md",
                       "relative_path": rel_claude})

    # 9. Metadata
    manifest = generate_manifest(scan, artifacts)
    write_statuses[".agent-memory/metadata/manifest.json"] = save_manifest(manifest, dirs["metadata"])
    write_statuses[".agent-memory/metadata/sources.json"] = save_sources_json(
        features, modules, dirs["metadata"]
    )
    write_statuses[".agent-memory/metadata/confidence.json"] = save_confidence_json(
        features, modules, dirs["metadata"]
    )

    return {
        "scan": scan,
        "features": features,
        "modules": modules,
        "dirs": dirs,
        "artifacts": artifacts,
        "write_statuses": write_statuses,
        "feature_statuses": feature_statuses,
        "module_statuses": module_statuses,
    }


def memory_init_command(args: argparse.Namespace) -> None:
    """Scan repo and generate initial ``.agent-memory/`` artifacts.

    Delegates all logic to :func:`run_memory_init_pipeline` so the CLI and
    MCP tool always produce identical output.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    repo_root = _resolve_repo_root(args)
    print(f"repo-memory: init")
    print(f"  scanning {repo_root} ...")

    result = run_memory_init_pipeline(repo_root)

    scan = result["scan"]
    features = result["features"]
    modules = result["modules"]
    write_statuses = result["write_statuses"]
    feature_statuses = result["feature_statuses"]
    module_statuses = result["module_statuses"]

    print()
    print(f"  languages   : {', '.join(scan.languages) or 'none detected'}")
    print(f"  frameworks  : {', '.join(scan.framework_hints) or 'none detected'}")
    print(f"  source dirs : {', '.join(scan.source_dirs) or 'none detected'}")
    print(f"  test dirs   : {', '.join(scan.test_dirs) or 'none detected'}")
    print(f"  confidence  : {scan.confidence:.0%}")
    print(f"  features    : {len(features)}")
    print(f"  modules     : {len(modules)}")
    print()
    print(f"  .agent-memory/repo.md              [{write_statuses['.agent-memory/repo.md']}]")
    print(f"  .agent-memory/architecture.md      [{write_statuses['.agent-memory/architecture.md']}]")
    for rel, st in feature_statuses:
        print(f"  {rel} [{st}]")
    for rel, st in module_statuses:
        print(f"  {rel} [{st}]")
    print(f"  .agent-memory/rules/conventions.md     [{write_statuses['.agent-memory/rules/conventions.md']}]")
    print(f"  .agent-memory/rules/safe-boundaries.md [{write_statuses['.agent-memory/rules/safe-boundaries.md']}]")
    print(f"  .agent-memory/CLAUDE.md                [{write_statuses['.agent-memory/CLAUDE.md']}]")
    print(f"  .agent-memory/metadata/manifest.json   [{write_statuses['.agent-memory/metadata/manifest.json']}]")
    print(f"  .agent-memory/metadata/sources.json    [{write_statuses['.agent-memory/metadata/sources.json']}]")
    print(f"  .agent-memory/metadata/confidence.json [{write_statuses['.agent-memory/metadata/confidence.json']}]")
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
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from .classifier import classify_features, classify_modules
    from .lookup import explain_match, match_target
    from .scanner import scan_repo

    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    target: str = args.target

    print(f"repo-memory: explain")
    print(f"  repo root : {repo_root}")
    print(f"  target    : {target}")
    print()

    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    match = match_target(target, agent_memory, features, modules)
    print(explain_match(match, agent_memory, repo_root=repo_root))


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

    from .classifier import classify_features, classify_modules  # noqa: I001
    from .context_builder import build_context_pack
    from .overrides import load_overrides
    from .scanner import scan_repo

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

    # Load human overrides if .agent-memory/ exists
    overrides = load_overrides(_agent_memory_root(repo_root))

    pack = build_context_pack(task, features, modules, overrides=overrides, repo_root=repo_root)

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
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    from .classifier import classify_features, classify_modules
    from .lookup import changed_match, match_target
    from .scanner import scan_repo

    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    target: str = args.target

    print(f"repo-memory: changed")
    print(f"  repo root : {repo_root}")
    print(f"  target    : {target}")
    print()

    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    match = match_target(target, agent_memory, features, modules)
    print(changed_match(match, agent_memory, repo_root=repo_root))


# ---------------------------------------------------------------------------
# memory annotate
# ---------------------------------------------------------------------------


def memory_annotate_command(args: argparse.Namespace) -> None:
    """Create (if absent) and open the global human override file.

    Scaffolds ``.agent-memory/overrides/global.yaml`` with a commented template
    on first run.  On subsequent runs the file is left unchanged (human edits
    are never overwritten).

    If ``$EDITOR`` is set the file is opened in the configured editor.
    Otherwise the file path is printed with brief usage instructions.
    """
    import os
    import subprocess

    from .writer import write_override_if_absent

    repo_root = _resolve_repo_root(args)
    agent_memory = _agent_memory_root(repo_root)
    override_path = agent_memory / "overrides" / "global.yaml"

    print("repo-memory: annotate")
    print(f"  repo root     : {repo_root}")
    print(f"  override file : {override_path}")
    print()

    status = write_override_if_absent(override_path, _GLOBAL_YAML_TEMPLATE)
    if status == "created":
        print("  Created override scaffold.")
    else:
        print("  Override file already exists — human edits preserved.")
    print()

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        print(f"  Opening in {editor} ...")
        try:
            subprocess.run([editor, str(override_path)], check=False)
        except FileNotFoundError:
            print(f"  Warning: editor '{editor}' not found.")
            print(f"  Edit manually: {override_path}")
    else:
        print(f"  Edit the file to add domain knowledge and task hints:")
        print(f"    {override_path}")
        print()
        print("  Then re-run `memory init` to regenerate rules/conventions.md")
        print("  with your overrides applied.")
