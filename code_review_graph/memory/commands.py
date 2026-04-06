"""CLI command handlers for the repo-memory subsystem.

Each function here corresponds to one ``code-review-graph memory <sub>`` command.
They accept an ``argparse.Namespace`` and print human-readable output.

Handler naming convention mirrors the existing CLI: ``memory_<verb>_command``.
All handlers share the same signature: ``(args: argparse.Namespace) -> None``.
"""

from __future__ import annotations

import argparse
import logging
import time
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


def compute_quality_verdict(
    features: list,
    modules: list,
    graph_used: bool,
    vocabulary_used: bool,
) -> dict:
    """Compute a quality verdict for the memory init output.

    Verdict levels (richest to weakest):

    - **rich**   — graph present, vocabulary enriched, 3+ features or 4+ modules,
                   avg confidence ≥ 75 %.
    - **good**   — 2+ features or 3+ modules detected (with or without graph).
    - **sparse** — some areas detected but missing graph or low confidence.
    - **weak**   — zero features detected, regardless of graph state.

    Args:
        features:         Detected :class:`~models.FeatureMemory` list.
        modules:          Detected :class:`~models.ModuleMemory` list.
        graph_used:       True when graph.db was available and queried.
        vocabulary_used:  True when graph vocabulary enriched the artifacts.

    Returns:
        Dict with keys: ``verdict``, ``message``, ``guidance``,
        ``avg_confidence``, ``graph_used``, ``vocabulary_used``.
    """
    n_features = len(features)
    n_modules = len(modules)
    all_items = [*features, *modules]
    confidences = [item.confidence for item in all_items]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    low_conf_count = sum(1 for c in confidences if c < 0.6)

    if n_features == 0 and n_modules <= 1:
        verdict = "weak"
        graph_note = "present" if graph_used else "absent"
        message = f"Weak — 0 features detected. Graph {graph_note}."
        guidance: list[str] = ["No domain features were detected."]
        if not graph_used:
            guidance.append(
                "Run `repomind build` then `memory init` again for graph-grounded output."
            )
        guidance += [
            "Or add domain knowledge in `.agent-memory/overrides/global.yaml`.",
            "Ensure source directories follow standard naming (src/, lib/, app/).",
        ]
    elif n_features >= 2 or n_modules >= 3:
        if graph_used and vocabulary_used and avg_conf >= 0.75 and (n_features >= 3 or n_modules >= 4):
            verdict = "rich"
            message = (
                f"Rich — graph-grounded ({n_features} feature(s), {n_modules} module(s), "
                f"avg confidence {avg_conf:.0%})."
            )
            guidance = []
        else:
            verdict = "good"
            graph_note = "" if graph_used else " (heuristic-only)"
            message = (
                f"Good{graph_note} — {n_features} feature(s) + {n_modules} module(s) detected."
            )
            guidance = (
                [] if graph_used
                else ["Run `repomind build` to add graph-grounded depth."]
            )
    else:
        verdict = "sparse"
        message = f"Sparse — {n_features} feature(s) + {n_modules} module(s) detected."
        guidance = []
        if not graph_used:
            guidance.append("Graph is absent — run `repomind build` for richer context.")
        if low_conf_count > 0:
            guidance.append(
                f"{low_conf_count} area(s) have low confidence — "
                "verify groupings or add overrides."
            )

    return {
        "verdict": verdict,
        "message": message,
        "guidance": guidance,
        "avg_confidence": round(avg_conf, 3),
        "graph_used": graph_used,
        "vocabulary_used": vocabulary_used,
    }


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
        generate_hotspots_doc,
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

    # 2b. Fetch graph vocabulary and per-file node summaries for content-aware generation
    vocabulary: dict[str, list[str]] = {}
    node_summaries: dict = {}

    # 2b-pre. Check signal cache before hitting graph.db
    _cache_hit = False
    _cached: object = None
    all_files_for_cache: list[str] = []
    try:
        from .graph_bridge import graph_available as _graph_available_check
        from .signal_cache import CachedSignals, compute_cache_key, load_signal_cache, save_signal_cache
        _db_path = root / ".code-review-graph" / "graph.db"
        if _graph_available_check(root):
            all_files_for_cache = sorted({f for item in [*features, *modules] for f in item.files})
            _new_key = compute_cache_key(_db_path, all_files_for_cache)
            _loaded = load_signal_cache(root)
            if _loaded is not None and _loaded.cache_key == _new_key:
                _cached = _loaded
                _cache_hit = True
                logger.debug("signal_cache: cache HIT (key=%s)", _new_key[:12])
            else:
                logger.debug("signal_cache: cache MISS (key=%s)", _new_key[:12])
    except Exception as _ce:
        logger.debug("signal_cache: cache lookup failed: %s", _ce)

    if _cache_hit and _cached is not None:
        vocabulary = _cached.vocabulary  # type: ignore[union-attr]
        node_summaries = _cached.node_summaries  # type: ignore[union-attr]
    else:
        try:
            from .graph_bridge import get_file_node_summary, get_file_vocabulary, graph_available
            if graph_available(root):
                all_files = list({f for item in [*features, *modules] for f in item.files})
                vocabulary = get_file_vocabulary(all_files, root)
                node_summaries = get_file_node_summary(all_files, root)
        except Exception:
            vocabulary = {}
            node_summaries = {}

    vocabulary_used = bool(vocabulary)

    # 2c. Fetch call-graph, structural-depth, and hotspot signals (Phase 4)
    call_signals_map: dict[str, object] = {}
    structural_signals_map: dict[str, object] = {}
    hotspot_nodes: list = []

    if _cache_hit and _cached is not None:
        call_signals_map = _cached.call_signals_map  # type: ignore[union-attr]
        structural_signals_map = _cached.structural_signals_map  # type: ignore[union-attr]
        hotspot_nodes = _cached.hotspot_nodes  # type: ignore[union-attr]
    else:
        try:
            from .graph_bridge import (
                get_all_call_graph_signals,
                get_all_hotspot_nodes,
                get_all_structural_depth_signals,
            )
            if vocabulary_used:  # graph is available (vocabulary was already fetched)
                all_files = list({f for item in [*features, *modules] for f in item.files})
                feature_groups = {f.name: f.files for f in features}
                module_groups = {m.name: m.files for m in modules}
                all_groups = {**feature_groups, **module_groups}
                call_signals_map = get_all_call_graph_signals(all_groups, root)
                structural_signals_map = get_all_structural_depth_signals(module_groups, root)
                hotspot_nodes = get_all_hotspot_nodes(root)
        except Exception:
            pass

        # Save computed signals to cache for next run (only when graph was used)
        if vocabulary_used and all_files_for_cache:
            try:
                from .signal_cache import compute_cache_key, save_signal_cache
                _db_path = root / ".code-review-graph" / "graph.db"
                _key = compute_cache_key(_db_path, all_files_for_cache)
                save_signal_cache(
                    root, _key,
                    vocabulary, node_summaries,
                    call_signals_map, structural_signals_map,
                    hotspot_nodes,
                )
            except Exception as _se:
                logger.debug("signal_cache: failed to save after compute: %s", _se)

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

    # Fetch architecture graph signals when graph is available (Ticket D)
    _arch_signals = None
    try:
        from .graph_bridge import get_architecture_graph_signals, graph_available
        if graph_available(root):
            _arch_signals = get_architecture_graph_signals(root)
    except Exception:
        pass

    write_statuses[".agent-memory/architecture.md"] = write_text_if_changed(
        dirs["root"] / "architecture.md", generate_architecture_doc(scan, graph_signals=_arch_signals)
    )
    artifacts.append({"artifact_id": "architecture", "artifact_type": "architecture",
                       "relative_path": ".agent-memory/architecture.md"})

    # 5. Feature docs
    feature_statuses: list[tuple[str, str]] = []
    for feature in features:
        slug = feature.slug()
        rel = f".agent-memory/features/{slug}.md"
        st = write_text_if_changed(
            dirs["features"] / f"{slug}.md",
            generate_feature_doc(
                feature,
                vocabulary=vocabulary or None,
                node_summaries=node_summaries or None,
                call_signals=call_signals_map.get(feature.name) or None,
            ),
        )
        write_statuses[rel] = st
        feature_statuses.append((rel, st))
        artifacts.append({"artifact_id": f"feature:{slug}", "artifact_type": "feature",
                           "relative_path": rel})

    # 6. Module docs
    module_statuses: list[tuple[str, str]] = []
    for module in modules:
        slug = module.slug()
        rel = f".agent-memory/modules/{slug}.md"
        st = write_text_if_changed(
            dirs["modules"] / f"{slug}.md",
            generate_module_doc(
                module,
                vocabulary=vocabulary or None,
                node_summaries=node_summaries or None,
                call_signals=call_signals_map.get(module.name) or None,
                structural_signals=structural_signals_map.get(module.name) or None,
            ),
        )
        write_statuses[rel] = st
        module_statuses.append((rel, st))
        artifacts.append({"artifact_id": f"module:{slug}", "artifact_type": "module",
                           "relative_path": rel})

    # 6b. Hotspots doc (Ticket 4.2) — written only when graph data is available
    rel_hotspots = ".agent-memory/changes/hotspots.md"
    hotspots_content = generate_hotspots_doc(hotspot_nodes, scan)
    write_statuses[rel_hotspots] = write_text_if_changed(
        dirs["changes"] / "hotspots.md", hotspots_content
    )
    artifacts.append({"artifact_id": "changes:hotspots", "artifact_type": "changes",
                       "relative_path": rel_hotspots})

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

    # 9. Pack cache — pre-computed index for fast prepare-context lookups
    try:
        from .pack_cache import build_pack_cache, save_pack_cache
        _pack_cache = build_pack_cache(features, modules, vocabulary)
        save_pack_cache(_pack_cache, dirs["root"])
    except Exception as _pce:
        logger.debug("pack_cache: failed to build/save: %s", _pce)

    # 10. Metadata
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
        "vocabulary_used": vocabulary_used,
        "repo_shape": scan.repo_shape,
        "shape_rationale": scan.shape_rationale,
    }


def memory_init_command(args: argparse.Namespace) -> None:
    """Scan repo and generate initial ``.agent-memory/`` artifacts.

    Delegates all logic to :func:`run_memory_init_pipeline` so the CLI and
    MCP tool always produce identical output.
    """
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    repo_root = _resolve_repo_root(args)

    if not repo_root.exists():
        print(f"error: path does not exist: {repo_root}", flush=True)
        import sys
        sys.exit(1)
    if not repo_root.is_dir():
        print(f"error: path is not a directory: {repo_root}", flush=True)
        import sys
        sys.exit(1)

    print(f"repo-memory: init")
    print(f"  scanning {repo_root} ...")

    # Early graph-missing notice — before the (slower) pipeline runs, so the
    # user understands degraded mode immediately.
    _db_check = repo_root / ".code-review-graph" / "graph.db"
    if not _db_check.exists():
        print()
        print("  NOTE: graph.db not found — running in heuristic-only mode.")
        print("  For richer output (call graphs, import chains, entry points), run first:")
        print("    repomind build")
        print("  then re-run:")
        print("    repomind memory init")
        print()

    _t0 = time.perf_counter()
    result = run_memory_init_pipeline(repo_root)
    _duration = time.perf_counter() - _t0

    scan = result["scan"]
    features = result["features"]
    modules = result["modules"]
    write_statuses = result["write_statuses"]
    feature_statuses = result["feature_statuses"]
    module_statuses = result["module_statuses"]
    vocabulary_used: bool = result.get("vocabulary_used", False)

    print()
    print(f"  languages   : {', '.join(scan.languages) or 'none detected'}")
    print(f"  frameworks  : {', '.join(scan.framework_hints) or 'none detected'}")
    print(f"  source dirs : {', '.join(scan.source_dirs) or 'none detected'}")
    print(f"  test dirs   : {', '.join(scan.test_dirs) or 'none detected'}")
    print(f"  confidence  : {scan.confidence:.0%}")
    print(f"  repo shape  : {scan.repo_shape}")
    print(f"  features    : {len(features)}")
    print(f"  modules     : {len(modules)}")
    print()
    print(f"  .agent-memory/repo.md              [{write_statuses['.agent-memory/repo.md']}]")
    print(f"  .agent-memory/architecture.md      [{write_statuses['.agent-memory/architecture.md']}]")
    for rel, st in feature_statuses:
        print(f"  {rel} [{st}]")
    for rel, st in module_statuses:
        print(f"  {rel} [{st}]")
    print(f"  .agent-memory/changes/hotspots.md      [{write_statuses.get('.agent-memory/changes/hotspots.md', 'skip')}]")
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

    # Flat-package notice
    if scan.repo_shape == "flat-package":
        print("  Note: flat-package layout detected.")
        print(f"    {scan.shape_rationale}")
        print("    Feature groupings may be approximate — consider adding overrides/")
        print("    to improve classification accuracy.")
        print()

    # Fix 5: graph-missing degraded mode notice
    from .graph_bridge import graph_available as _graph_available
    _graph_used = _graph_available(repo_root)
    if not _graph_used:
        print("  Note: graph.db not found — running in heuristic-only mode.")
        print("  For richer context (import chains, call graphs, blast radius), run:")
        print("    code-review-graph build")
        print()

    # Fix 7: .gitignore check — warn if .agent-memory/ would be excluded
    gitignore_path = repo_root / ".gitignore"
    if gitignore_path.exists():
        try:
            gi_text = gitignore_path.read_text(encoding="utf-8", errors="replace")
            gi_lines = [ln.strip() for ln in gi_text.splitlines()]
            agent_ignored = any(
                ln in (".agent-memory", ".agent-memory/")
                for ln in gi_lines if not ln.startswith("#")
            )
            graph_ignored = any(
                ln in (".code-review-graph", ".code-review-graph/")
                for ln in gi_lines if not ln.startswith("#")
            )
            if agent_ignored:
                print("  WARNING: .agent-memory/ appears in .gitignore — memory will NOT be committed.")
                print("  Remove it from .gitignore so teammates share this memory.")
                print()
            if not graph_ignored:
                print("  Tip: add '.code-review-graph/' to .gitignore — it's local-only state.")
                print()
        except OSError:
            pass

    # Fix 1: CLAUDE.md auto-load guidance
    claude_md_path = repo_root / "CLAUDE.md"
    agent_claude_ref = "@.agent-memory/CLAUDE.md"
    has_ref = False
    if claude_md_path.exists():
        try:
            has_ref = agent_claude_ref in claude_md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not has_ref:
        print("  *** ACTION REQUIRED — load memory into Claude Code ***")
        print(f"  Add this line to your CLAUDE.md so every session gets context:")
        print(f"    {agent_claude_ref}")
        print()
        print("  Without it, Claude Code will NOT auto-load .agent-memory/CLAUDE.md.")
        print()

    # Quality verdict (reuses _graph_used computed above)
    verdict_result = compute_quality_verdict(
        features, modules,
        graph_used=_graph_used,
        vocabulary_used=vocabulary_used,
    )
    quality = verdict_result["verdict"]
    print(f"  Quality: {verdict_result['message']}")
    for guidance_line in verdict_result["guidance"]:
        print(f"    -> {guidance_line}")
    if verdict_result["guidance"]:
        print()
    print(f"  Completed in {_duration:.2f}s")
    print()

    print("  Done. Commit .agent-memory/ to share memory with your team.")

    # Record metrics
    from .telemetry import record
    record("init", {
        "duration_s": round(_duration, 3),
        "feature_count": len(features),
        "module_count": len(modules),
        "avg_confidence": verdict_result["avg_confidence"],
        "artifact_count": len(write_statuses),
        "graph_used": _graph_used,
        "vocabulary_used": vocabulary_used,
        "quality": quality,
    }, repo_root)


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

    _t0 = time.perf_counter()
    plan = plan_refresh(changed_files, features, modules, full=full, repo_root=repo_root)
    result = execute_refresh(plan, repo_root, features, modules, scan)
    _duration = time.perf_counter() - _t0

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

    print(f"  Completed in {_duration:.2f}s")
    print("  Done.")

    from .telemetry import record
    record("refresh", {
        "duration_s": round(_duration, 3),
        "changed_files": len(result["changed_files"]),
        "artifacts_refreshed": len(result["artifacts_updated"]),
        "artifacts_skipped": len(result["artifacts_skipped"]),
        "mode": result["mode"],
    }, repo_root)


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

    # Degraded-mode notice before expensive classification
    _expl_agent_mem = _agent_memory_root(repo_root)
    _expl_memory_initialized = (_expl_agent_mem / "metadata" / "manifest.json").exists()
    _expl_graph_exists = (repo_root / ".code-review-graph" / "graph.db").exists()
    if not _expl_memory_initialized:
        print("  Hint: memory not initialized — explanation is heuristic-only.")
        print("  Run `repomind build` then `repomind memory init` for graph-grounded output.")
        print()
    elif not _expl_graph_exists:
        print("  Hint: graph.db absent — graph signals unavailable.")
        print("  Run `repomind build` for call-graph and entry-point data.")
        print()

    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    _t0 = time.perf_counter()
    match = match_target(target, agent_memory, features, modules)
    output = explain_match(match, agent_memory, repo_root=repo_root)
    _duration = time.perf_counter() - _t0
    print(output)

    from .telemetry import record
    obj = match.obj
    record("explain", {
        "duration_s": round(_duration, 3),
        "target": target[:60],
        "match_kind": match.kind,
        "found": match.found(),
        "confidence": round(getattr(obj, "confidence", 0.0), 3) if obj else None,
        "files_count": len(getattr(obj, "files", [])) if obj else 0,
    }, repo_root)


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

    # First-run degraded-mode hints (shown before the pipeline runs)
    _agent_mem = _agent_memory_root(repo_root)
    _db_exists = (repo_root / ".code-review-graph" / "graph.db").exists()
    _memory_initialized = (_agent_mem / "metadata" / "manifest.json").exists()
    if not as_json:
        if not _memory_initialized:
            print("  Hint: .agent-memory/ not yet initialized.")
            print("  For best results run:")
            print("    repomind build          # parse repo into graph")
            print("    repomind memory init    # generate memory artifacts")
            print()
        elif not _db_exists:
            print("  Hint: graph.db not found — running in heuristic-only mode.")
            print("  Run `repomind build` for graph-grounded context.")
            print()

    _t0 = time.perf_counter()

    # Fast path: load pre-built cache if .agent-memory/ is initialised
    from .pack_cache import features_from_cache, keywords_from_cache, load_pack_cache, modules_from_cache
    _cache = load_pack_cache(_agent_memory_root(repo_root))

    if _cache is not None:
        features = features_from_cache(_cache)
        modules = modules_from_cache(_cache)
        _kw_map = keywords_from_cache(_cache)
    else:
        # Slow path: live scan (memory not yet initialised)
        scan = scan_repo(repo_root)
        features = classify_features(repo_root, scan)
        modules = classify_modules(repo_root, scan)
        _kw_map = {}

    # Load human overrides if .agent-memory/ exists
    overrides = load_overrides(_agent_memory_root(repo_root))

    pack = build_context_pack(
        task, features, modules,
        overrides=overrides, repo_root=repo_root, kw_map=_kw_map,
    )
    _duration = time.perf_counter() - _t0

    if as_json:
        _print_pack_json(pack)
    else:
        _print_pack_text(pack)

    # Estimate tokens (4 chars ≈ 1 token heuristic)
    _tokens = sum(
        len((repo_root / f).read_text(encoding="utf-8", errors="replace"))
        for f in pack.relevant_files
        if (repo_root / f).is_file()
    ) // 4

    from .telemetry import record
    record("prepare-context", {
        "duration_s": round(_duration, 3),
        "task_len": len(task),
        "features_matched": len(pack.relevant_features),
        "modules_matched": len(pack.relevant_modules),
        "files_returned": len(pack.relevant_files),
        "tests_returned": len(pack.relevant_tests),
        "tokens_estimated": _tokens,
        "fallback": any("No specific area matched" in w for w in pack.warnings),
        "graph_enriched": "graph-backed" in pack.summary.lower(),
    }, repo_root)


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
        print("  No relevant context found.")
        print("  To generate memory artifacts, run:")
        print("    repomind build          # parse repo into graph (if not done)")
        print("    repomind memory init    # generate .agent-memory/ artifacts")


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

    # Degraded-mode notice before expensive classification
    _chg_agent_mem = _agent_memory_root(repo_root)
    _chg_memory_initialized = (_chg_agent_mem / "metadata" / "manifest.json").exists()
    _chg_graph_exists = (repo_root / ".code-review-graph" / "graph.db").exists()
    if not _chg_memory_initialized:
        print("  Hint: memory not initialized — change summary will be limited.")
        print("  Run `repomind build` then `repomind memory init` for full change tracking.")
        print()
    elif not _chg_graph_exists:
        print("  Hint: graph.db absent — graph impact analysis unavailable.")
        print("  Run `repomind build` to enable structural change impact.")
        print()

    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)

    _t0 = time.perf_counter()
    match = match_target(target, agent_memory, features, modules)
    output = changed_match(match, agent_memory, repo_root=repo_root)
    _duration = time.perf_counter() - _t0
    print(output)

    from .metadata import load_freshness_json
    from .telemetry import record
    freshness = load_freshness_json(agent_memory / "metadata")
    from .graph_bridge import graph_available
    record("changed", {
        "duration_s": round(_duration, 3),
        "target": target[:60],
        "match_kind": match.kind,
        "found": match.found(),
        "has_freshness": freshness is not None,
        "graph_used": graph_available(repo_root),
    }, repo_root)


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


# ---------------------------------------------------------------------------
# memory stats
# ---------------------------------------------------------------------------


def memory_stats_command(args: argparse.Namespace) -> None:
    """Show a performance summary of recent memory command runs.

    Reads the local ``.code-review-graph/memory-metrics.jsonl`` log and prints
    timing, quality scores, and aggregate stats.  The log is local-only and
    never committed.
    """
    from .telemetry import print_stats

    repo_root = _resolve_repo_root(args)
    last = getattr(args, "last", 20)

    print("repo-memory: stats")
    print(f"  repo root : {repo_root}")
    print()
    print_stats(repo_root, last=last)

