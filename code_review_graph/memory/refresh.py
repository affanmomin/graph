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
) -> RefreshPlan:
    """Determine which memory artifacts are affected by the given changed files.

    Compares *changed_files* against the source-file lists of every known
    feature and module to produce a minimal refresh plan. No disk I/O is
    performed — this is a pure in-memory computation.

    Args:
        changed_files: Repo-relative paths of files that changed (from git
                       diff or file watcher).
        features:      Classified features from the current scan.
        modules:       Classified modules from the current scan.
        full:          If ``True``, mark every artifact as impacted regardless
                       of *changed_files* (full-refresh mode).

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

    # Features impacted: any source file in feature.files touched
    impacted_feature_slugs = sorted(
        f.slug()
        for f in features
        if any(fp in changed_set for fp in f.files)
    )

    # Modules impacted: any source file in module.files touched
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

    n_impacted = len(impacted_feature_slugs) + len(impacted_module_slugs)
    reason = (
        f"incremental: {len(changed_files)} changed file(s), "
        f"{n_impacted} artifact(s) impacted"
    )

    return RefreshPlan(
        mode="incremental",
        changed_files=sorted(changed_files),
        impacted_feature_slugs=impacted_feature_slugs,
        impacted_module_slugs=impacted_module_slugs,
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

    # --- impacted features ---
    for slug in plan.impacted_feature_slugs:
        feature = feature_by_slug.get(slug)
        if feature is None:
            logger.warning("refresh: feature %r not found in current classification", slug)
            continue
        rel = f".agent-memory/features/{slug}.md"
        st = write_text_if_changed(dirs["features"] / f"{slug}.md", generate_feature_doc(feature))
        _record(st, rel, updated, skipped)

    # --- impacted modules ---
    for slug in plan.impacted_module_slugs:
        module = module_by_slug.get(slug)
        if module is None:
            logger.warning("refresh: module %r not found in current classification", slug)
            continue
        rel = f".agent-memory/modules/{slug}.md"
        st = write_text_if_changed(dirs["modules"] / f"{slug}.md", generate_module_doc(module))
        _record(st, rel, updated, skipped)

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
        "impacted_modules": plan.impacted_module_slugs,  # already sorted
        "reason": plan.reason,
    }
