"""Incremental memory refresh orchestrator.

Responsible for detecting which memory artifacts are affected by recent repo
changes and triggering regeneration of only those artifacts.

Full regeneration is expensive and produces noisy Git diffs. The refresh
orchestrator keeps memory current by doing the minimum work needed.

It integrates with the existing ``incremental.py`` change-detection
infrastructure (Layer A) to learn which files changed, then uses the
classifier to determine which features/modules/artifacts are impacted.

Public API
----------
plan_refresh(changed_files, features, modules, full=False)  -> RefreshPlan
execute_refresh(plan, repo_root, features, modules, scan)   -> dict

Refresh modes:
- incremental (default): only artifacts whose source files changed
- full: all artifacts, used after major structural changes

Integration points:
- ``memory_refresh_command()`` in commands.py (explicit CLI refresh)
- ``incremental_update()`` in incremental.py (automatic post-update hook)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .models import FeatureMemory, ModuleMemory
    from .scanner import RepoScan

logger = logging.getLogger(__name__)

# Files at repo root or config changes trigger repo/architecture re-render.
_STRUCTURAL_SUFFIXES: frozenset[str] = frozenset(
    {".toml", ".json", ".yaml", ".yml", ".cfg", ".ini"}
)

# More than this many changed files → treat as a large change → refresh top-level docs.
_LARGE_CHANGE_THRESHOLD = 10

# Graph expansion caps — keep refresh bounded and non-noisy.
# Max related files to pull from BFS (pre-artifact-matching).
_GRAPH_EXPAND_MAX_FILES = 20
# Max additional artifact slugs (features + modules combined) from graph expansion.
_GRAPH_EXPAND_MAX_ARTIFACTS = 3


# ---------------------------------------------------------------------------
# RefreshPlan
# ---------------------------------------------------------------------------


@dataclass
class RefreshPlan:
    """Describes which memory artifacts need to be regenerated.

    Produced by :func:`plan_refresh` and consumed by :func:`execute_refresh`.
    All slug lists are sorted for deterministic output.

    Attributes:
        mode:                    ``"incremental"`` or ``"full"``.
        changed_files:           Sorted repo-relative paths of changed files.
        impacted_feature_slugs:  Feature slugs whose source files overlapped
                                 with *changed_files*.
        impacted_module_slugs:   Module slugs whose source files overlapped
                                 with *changed_files*.
        update_repo:             Whether ``.agent-memory/repo.md`` needs refresh.
        update_architecture:     Whether ``.agent-memory/architecture.md`` needs refresh.
        reason:                  Human-readable explanation of the plan.
    """

    mode: Literal["incremental", "full"]
    changed_files: list[str] = field(default_factory=list)
    impacted_feature_slugs: list[str] = field(default_factory=list)
    impacted_module_slugs: list[str] = field(default_factory=list)
    # Graph-expanded: artifacts structurally related to changed files via the
    # code graph but whose source files did not directly change.  Kept separate
    # from the direct slugs so callers can distinguish the two signals and so
    # the freshness metadata can record how much expansion occurred.
    graph_expanded_feature_slugs: list[str] = field(default_factory=list)
    graph_expanded_module_slugs: list[str] = field(default_factory=list)
    graph_expanded_files: list[str] = field(default_factory=list)
    update_repo: bool = False
    update_architecture: bool = False
    reason: str = ""


# ---------------------------------------------------------------------------
# plan_refresh
# ---------------------------------------------------------------------------


def plan_refresh(
    changed_files: list[str],
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    full: bool = False,
    repo_root: Path | None = None,
) -> RefreshPlan:
    """Determine which memory artifacts are affected by the given changed files.

    Compares *changed_files* against the source-file lists of every known
    feature and module to produce a minimal refresh plan.  When *repo_root* is
    supplied and a populated graph.db exists, the plan is additionally expanded
    with artifacts structurally reachable from the changed files via one BFS
    hop — allowing memory to stay current even when callers of a changed module
    are not directly listed in ``changed_files``.

    Graph expansion is capped at :data:`_GRAPH_EXPAND_MAX_ARTIFACTS` additional
    slugs so that a single noisy commit cannot trigger a full-repo refresh.
    No graph.db → silently falls back to direct path-matching only.

    Args:
        changed_files: Repo-relative paths of files that changed (from git
                       diff or file watcher).
        features:      Classified features from the current scan.
        modules:       Classified modules from the current scan.
        full:          If ``True``, mark every artifact as impacted regardless
                       of *changed_files* (full-refresh mode).
        repo_root:     Optional repo root used for graph-expansion lookup.
                       When ``None``, graph expansion is skipped.

    Returns:
        A :class:`RefreshPlan` describing the minimum set of artifacts to
        regenerate.
    """
    if full:
        return RefreshPlan(
            mode="full",
            changed_files=sorted(changed_files),
            impacted_feature_slugs=sorted(f.slug() for f in features),
            impacted_module_slugs=sorted(m.slug() for m in modules),
            update_repo=True,
            update_architecture=True,
            reason="full refresh requested",
        )

    if not changed_files:
        return RefreshPlan(
            mode="incremental",
            changed_files=[],
            reason="no changed files detected — nothing to refresh",
        )

    changed_set = set(changed_files)

    # Direct impact: features/modules whose source files directly changed.
    impacted_feature_slugs = sorted(
        f.slug()
        for f in features
        if any(fp in changed_set for fp in f.files)
    )
    impacted_module_slugs = sorted(
        m.slug()
        for m in modules
        if any(fp in changed_set for fp in m.files)
    )

    # Refresh repo.md / architecture.md when:
    #   - a file at the repo root changed (no parent directory parts)
    #   - a config/manifest file (*.toml, *.json, …) changed
    #   - many files changed at once (structural refactor)
    update_top_level = any(not Path(f).parent.parts for f in changed_files)
    update_config = any(Path(f).suffix.lower() in _STRUCTURAL_SUFFIXES for f in changed_files)
    large_change = len(changed_files) >= _LARGE_CHANGE_THRESHOLD
    update_repo = update_top_level or update_config or large_change

    # Graph expansion: find artifacts whose files are reachable via one BFS hop
    # from changed_files, but were not already caught by direct path-matching.
    graph_expanded_feature_slugs: list[str] = []
    graph_expanded_module_slugs: list[str] = []
    graph_expanded_files: list[str] = []

    if repo_root is not None:
        try:
            from .graph_bridge import get_graph_expanded_files
            expanded = get_graph_expanded_files(
                changed_files, repo_root, max_expansion=_GRAPH_EXPAND_MAX_FILES
            )
            if expanded:
                graph_expanded_files = expanded
                expanded_set = set(expanded)
                direct_feature_set = set(impacted_feature_slugs)
                direct_module_set = set(impacted_module_slugs)

                # Features whose files overlap the graph-expanded set, not already direct.
                extra_features = sorted(
                    f.slug()
                    for f in features
                    if f.slug() not in direct_feature_set
                    and any(fp in expanded_set for fp in f.files)
                )
                # Modules whose files overlap the graph-expanded set, not already direct.
                extra_modules = sorted(
                    m.slug()
                    for m in modules
                    if m.slug() not in direct_module_set
                    and any(fp in expanded_set for fp in m.files)
                )

                # Cap total additions to keep refresh bounded.
                budget = _GRAPH_EXPAND_MAX_ARTIFACTS
                graph_expanded_feature_slugs = extra_features[:budget]
                budget -= len(graph_expanded_feature_slugs)
                graph_expanded_module_slugs = extra_modules[:max(0, budget)]
        except Exception as exc:
            logger.debug("plan_refresh: graph expansion failed: %s", exc)

    n_direct = len(impacted_feature_slugs) + len(impacted_module_slugs)
    n_expanded = len(graph_expanded_feature_slugs) + len(graph_expanded_module_slugs)
    expansion_note = f", {n_expanded} graph-expanded" if n_expanded else ""
    reason = (
        f"incremental: {len(changed_files)} changed file(s), "
        f"{n_direct} artifact(s) directly impacted{expansion_note}"
    )

    return RefreshPlan(
        mode="incremental",
        changed_files=sorted(changed_files),
        impacted_feature_slugs=impacted_feature_slugs,
        impacted_module_slugs=impacted_module_slugs,
        graph_expanded_feature_slugs=graph_expanded_feature_slugs,
        graph_expanded_module_slugs=graph_expanded_module_slugs,
        graph_expanded_files=graph_expanded_files,
        update_repo=update_repo,
        update_architecture=update_repo,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# execute_refresh
# ---------------------------------------------------------------------------


def execute_refresh(
    plan: RefreshPlan,
    repo_root: Path,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    scan: RepoScan,
) -> dict[str, Any]:
    """Execute a refresh plan — regenerate only the affected memory artifacts.

    Writes to ``.agent-memory/`` using atomic writes (via ``writer.py``).
    Files whose content did not change are not rewritten, keeping Git diffs
    clean. Always updates ``changes/recent.md`` and ``metadata/freshness.json``.

    Args:
        plan:      The :class:`RefreshPlan` describing what to regenerate.
        repo_root: Absolute path to the repository root.
        features:  Classified features from the current scan.
        modules:   Classified modules from the current scan.
        scan:      The completed :class:`~scanner.RepoScan` for this repo.

    Returns:
        A dict with keys:
        - ``mode``: refresh mode used.
        - ``changed_files``: list of changed file paths.
        - ``artifacts_updated``: relative paths of artifacts that were written.
        - ``artifacts_skipped``: relative paths of artifacts that were unchanged.
        - ``graph_expanded_artifacts``: paths written due to graph expansion only.
        - ``reason``: the plan's reason string.
    """
    from .generator import (
        generate_architecture_doc,
        generate_feature_doc,
        generate_module_doc,
        generate_repo_summary,
    )
    from .writer import ensure_memory_dirs, write_json_if_changed, write_text_if_changed

    dirs = ensure_memory_dirs(repo_root)
    feature_by_slug = {f.slug(): f for f in features}
    module_by_slug = {m.slug(): m for m in modules}
    updated: list[str] = []
    skipped: list[str] = []
    graph_expanded_updated: list[str] = []

    # Fetch graph vocabulary and node summaries for the impacted artifacts.
    # This ensures refreshed docs remain graph-grounded (same as memory init).
    _vocabulary: dict[str, list[str]] = {}
    _node_summaries: dict = {}
    try:
        from .graph_bridge import get_file_node_summary, get_file_vocabulary, graph_available
        if graph_available(repo_root):
            _all_slugs = (
                set(plan.impacted_feature_slugs)
                | set(plan.graph_expanded_feature_slugs)
                | set(plan.impacted_module_slugs)
                | set(plan.graph_expanded_module_slugs)
            )
            _all_files: list[str] = []
            for slug in _all_slugs:
                f = feature_by_slug.get(slug)
                if f:
                    _all_files.extend(f.files)
                m = module_by_slug.get(slug)
                if m:
                    _all_files.extend(m.files)
            _all_files = list(dict.fromkeys(_all_files))  # deduplicate, preserve order
            if _all_files:
                _vocabulary = get_file_vocabulary(_all_files, repo_root)
                _node_summaries = get_file_node_summary(_all_files, repo_root)
    except Exception as exc:
        logger.debug("execute_refresh: graph vocabulary fetch failed: %s", exc)

    # --- repo.md ---
    if plan.update_repo:
        st = write_text_if_changed(dirs["root"] / "repo.md", generate_repo_summary(scan))
        _record(st, ".agent-memory/repo.md", updated, skipped)

    # --- architecture.md ---
    if plan.update_architecture:
        st = write_text_if_changed(
            dirs["root"] / "architecture.md", generate_architecture_doc(scan)
        )
        _record(st, ".agent-memory/architecture.md", updated, skipped)

    # --- directly impacted features ---
    for slug in plan.impacted_feature_slugs:
        feature = feature_by_slug.get(slug)
        if feature is None:
            logger.warning("refresh: feature %r not found in current classification", slug)
            continue
        rel = f".agent-memory/features/{slug}.md"
        st = write_text_if_changed(
            dirs["features"] / f"{slug}.md",
            generate_feature_doc(
                feature,
                vocabulary=_vocabulary or None,
                node_summaries=_node_summaries or None,
            ),
        )
        _record(st, rel, updated, skipped)

    # --- directly impacted modules ---
    for slug in plan.impacted_module_slugs:
        module = module_by_slug.get(slug)
        if module is None:
            logger.warning("refresh: module %r not found in current classification", slug)
            continue
        rel = f".agent-memory/modules/{slug}.md"
        st = write_text_if_changed(
            dirs["modules"] / f"{slug}.md",
            generate_module_doc(
                module,
                vocabulary=_vocabulary or None,
                node_summaries=_node_summaries or None,
            ),
        )
        _record(st, rel, updated, skipped)

    # --- graph-expanded features (structurally related, not directly changed) ---
    direct_feature_done = set(plan.impacted_feature_slugs)
    for slug in plan.graph_expanded_feature_slugs:
        if slug in direct_feature_done:
            continue  # already handled above (defensive dedup)
        feature = feature_by_slug.get(slug)
        if feature is None:
            logger.warning("refresh: graph-expanded feature %r not found", slug)
            continue
        rel = f".agent-memory/features/{slug}.md"
        st = write_text_if_changed(
            dirs["features"] / f"{slug}.md",
            generate_feature_doc(
                feature,
                vocabulary=_vocabulary or None,
                node_summaries=_node_summaries or None,
            ),
        )
        _record(st, rel, updated, skipped)
        if st != "unchanged":
            graph_expanded_updated.append(rel)

    # --- graph-expanded modules ---
    direct_module_done = set(plan.impacted_module_slugs)
    for slug in plan.graph_expanded_module_slugs:
        if slug in direct_module_done:
            continue
        module = module_by_slug.get(slug)
        if module is None:
            logger.warning("refresh: graph-expanded module %r not found", slug)
            continue
        rel = f".agent-memory/modules/{slug}.md"
        st = write_text_if_changed(
            dirs["modules"] / f"{slug}.md",
            generate_module_doc(
                module,
                vocabulary=_vocabulary or None,
                node_summaries=_node_summaries or None,
            ),
        )
        _record(st, rel, updated, skipped)
        if st != "unchanged":
            graph_expanded_updated.append(rel)

    # --- changes/recent.md (always written) ---
    recent_content = _generate_recent_changes_doc(plan, feature_by_slug, module_by_slug)
    write_text_if_changed(dirs["changes"] / "recent.md", recent_content)
    updated.append(".agent-memory/changes/recent.md")

    # --- metadata/freshness.json (always written) ---
    freshness = _generate_freshness_data(plan, updated)
    write_json_if_changed(dirs["metadata"] / "freshness.json", freshness)

    return {
        "mode": plan.mode,
        "changed_files": plan.changed_files,
        "artifacts_updated": updated,
        "artifacts_skipped": skipped,
        "graph_expanded_artifacts": graph_expanded_updated,
        "reason": plan.reason,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _record(status: str, rel_path: str, updated: list[str], skipped: list[str]) -> None:
    """Append *rel_path* to *updated* or *skipped* based on write status."""
    if status != "unchanged":
        updated.append(rel_path)
    else:
        skipped.append(rel_path)


def _generate_recent_changes_doc(
    plan: RefreshPlan,
    feature_by_slug: dict[str, FeatureMemory],
    module_by_slug: dict[str, ModuleMemory],
) -> str:
    """Generate markdown content for ``.agent-memory/changes/recent.md``.

    The document is intentionally compact — it is consumed by agents, not
    humans, so prose is minimal and lists are preferred.
    """
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        "# Recent changes",
        "",
        "> Auto-generated by repo-memory. Shows files and areas changed in the last refresh.",
        "",
        f"**Last refresh**: {now}  ",
        f"**Mode**: {plan.mode}  ",
        f"**Changed files**: {len(plan.changed_files)}",
        "",
    ]

    if plan.changed_files:
        lines += ["## Changed files", ""]
        for f in plan.changed_files:  # already sorted
            lines.append(f"- `{f}`")
        lines.append("")

    if plan.impacted_feature_slugs:
        lines += ["## Impacted features", ""]
        for slug in plan.impacted_feature_slugs:
            name = feature_by_slug[slug].name if slug in feature_by_slug else slug
            lines.append(f"- **{name}**")
        lines.append("")

    if plan.impacted_module_slugs:
        lines += ["## Impacted modules", ""]
        for slug in plan.impacted_module_slugs:
            name = module_by_slug[slug].name if slug in module_by_slug else slug
            lines.append(f"- **{name}**")
        lines.append("")

    if not plan.impacted_feature_slugs and not plan.impacted_module_slugs:
        lines += [
            "No classified features or modules were impacted by these changes.",
            "",
        ]

    # Graph-expanded areas — structurally related via the code graph.
    if plan.graph_expanded_feature_slugs or plan.graph_expanded_module_slugs:
        lines += ["## Graph-expanded areas", ""]
        lines.append(
            "> Structurally related to the changed files via the code graph "
            "(1-hop BFS). Refreshed proactively."
        )
        lines.append("")
        for slug in plan.graph_expanded_feature_slugs:
            name = feature_by_slug[slug].name if slug in feature_by_slug else slug
            lines.append(f"- **{name}** _(graph-related)_")
        for slug in plan.graph_expanded_module_slugs:
            name = module_by_slug[slug].name if slug in module_by_slug else slug
            lines.append(f"- **{name}** _(graph-related)_")
        lines.append("")

    return "\n".join(lines)


def _generate_freshness_data(plan: RefreshPlan, refreshed_artifacts: list[str]) -> dict[str, Any]:
    """Generate data for ``.agent-memory/metadata/freshness.json``."""
    return {
        "refreshed_at": datetime.now(tz=timezone.utc).isoformat(),
        "mode": plan.mode,
        "changed_files_count": len(plan.changed_files),
        "changed_files": plan.changed_files,  # already sorted
        "artifacts_refreshed": sorted(refreshed_artifacts),
        "impacted_features": plan.impacted_feature_slugs,  # already sorted
        "impacted_modules": plan.impacted_module_slugs,   # already sorted
        "graph_expanded_features": plan.graph_expanded_feature_slugs,
        "graph_expanded_modules": plan.graph_expanded_module_slugs,
        "graph_expanded_files": plan.graph_expanded_files,
        "reason": plan.reason,
    }
