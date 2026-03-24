"""Target lookup and result formatting for ``memory explain`` and ``memory changed``.

Given a free-form target string (feature name, module name, or file path),
this module finds the best matching memory artifact and surfaces the relevant
stored information without re-running analysis.

Design rules
------------
- Read existing ``.agent-memory/`` artifacts from disk wherever possible.
- Fall back to live classification only when artifacts are absent.
- No duplicate analysis logic — reuse context_builder scoring internally.
- All output is produced as plain strings so commands stay trivially testable.

Public API
----------
match_target(target, agent_memory_root, features, modules)  -> TargetMatch
explain_match(match, agent_memory_root)                     -> str
changed_match(match, agent_memory_root)                     -> str
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .models import FeatureMemory, ModuleMemory

logger = logging.getLogger(__name__)

# Minimum score for a fuzzy match to be accepted
_FUZZY_THRESHOLD = 0.08

# Max files to list in explain output
_MAX_EXPLAIN_FILES = 8
_MAX_EXPLAIN_TESTS = 4


# ---------------------------------------------------------------------------
# TargetMatch dataclass
# ---------------------------------------------------------------------------


@dataclass
class TargetMatch:
    """Result of looking up a target string in classified memory.

    Attributes:
        kind:         ``"feature"``, ``"module"``, ``"path"``, or ``"not_found"``.
        name:         Human-readable name of the matched area.
        slug:         Filesystem slug (used to locate the artifact file).
        obj:          The underlying :class:`~models.FeatureMemory` or
                      :class:`~models.ModuleMemory` object; ``None`` for path
                      matches and not-found cases.
        artifact_path: Absolute path to the generated ``.md`` artifact, or
                      ``None`` if it has not been written yet.
        score:        Relevance score from context_builder (0–1).
        alternatives: Names of other close matches (for ambiguous targets).
        stale:        ``True`` when freshness data marks this area as stale.
    """

    kind: Literal["feature", "module", "path", "not_found"]
    name: str = ""
    slug: str = ""
    obj: object = None  # FeatureMemory | ModuleMemory
    artifact_path: Path | None = None
    score: float = 0.0
    alternatives: list[str] = field(default_factory=list)
    stale: bool = False

    def found(self) -> bool:
        """Return True when a match was found."""
        return self.kind != "not_found"


# ---------------------------------------------------------------------------
# match_target
# ---------------------------------------------------------------------------


def match_target(
    target: str,
    agent_memory_root: Path,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
) -> TargetMatch:
    """Find the best match for *target* among features, modules, and paths.

    Resolution order:
    1. Exact name match (case-insensitive) against features, then modules.
    2. Slug match.
    3. Path match via ``sources.json`` → feature/module that owns the file.
    4. Substring match on name.
    5. Score-based fuzzy match using the context_builder relevance function.
    6. Not found.

    Args:
        target:           Free-form target string from the user.
        agent_memory_root: Absolute path to ``.agent-memory/``.
        features:         Classified features from the current scan.
        modules:          Classified modules from the current scan.

    Returns:
        A :class:`TargetMatch`.
    """
    t = target.strip()
    t_lower = t.lower()

    # 1. Exact feature name (case-insensitive)
    for f in features:
        if f.name.lower() == t_lower:
            return _make_match("feature", f, agent_memory_root, score=1.0)

    # 2. Exact module name (case-insensitive)
    for m in modules:
        if m.name.lower() == t_lower:
            return _make_match("module", m, agent_memory_root, score=1.0)

    # 3. Slug match (handles "authentication", "src-auth" etc.)
    for f in features:
        if f.slug() == t_lower.replace(" ", "-"):
            return _make_match("feature", f, agent_memory_root, score=1.0)
    for m in modules:
        if m.slug() == t_lower.replace(" ", "-").replace(".", "-"):
            return _make_match("module", m, agent_memory_root, score=1.0)

    # 4. Path match — look up in sources.json
    path_match = _match_by_path(t, agent_memory_root, features, modules)
    if path_match is not None:
        return path_match

    # 5. Substring match
    feat_sub = [f for f in features if t_lower in f.name.lower() or f.name.lower() in t_lower]
    mod_sub = [m for m in modules if t_lower in m.name.lower() or m.name.lower() in t_lower]
    if len(feat_sub) == 1 and not mod_sub:
        return _make_match("feature", feat_sub[0], agent_memory_root, score=0.7)
    if len(mod_sub) == 1 and not feat_sub:
        return _make_match("module", mod_sub[0], agent_memory_root, score=0.7)

    # 6. Score-based fuzzy match
    return _fuzzy_match(t, agent_memory_root, features, modules)


# ---------------------------------------------------------------------------
# explain_match — produce explanation string
# ---------------------------------------------------------------------------


def explain_match(match: TargetMatch, agent_memory_root: Path) -> str:
    """Return a concise explanation string for *match*.

    Reads the stored artifact file when available; generates an on-the-fly
    summary from the FeatureMemory/ModuleMemory object as a fallback.

    Args:
        match:             A :class:`TargetMatch` returned by :func:`match_target`.
        agent_memory_root: Absolute path to ``.agent-memory/``.

    Returns:
        Multi-line string ready to print.
    """
    if not match.found():
        return _not_found_explain(match)

    lines: list[str] = []

    # Header
    kind_label = "Feature" if match.kind == "feature" else "Module"
    lines.append(f"{kind_label}: {match.name}")
    lines.append("")

    obj = match.obj
    if obj is None:
        lines.append("  (no classification data available)")
        return "\n".join(lines)

    # Confidence
    conf_pct = f"{obj.confidence:.0%}"
    conf_note = _confidence_label(obj.confidence)
    lines.append(f"  Confidence : {conf_pct} ({conf_note})")

    # Purpose (inline — keep it one line)
    file_count = len(obj.files)
    lines.append(
        f"  Purpose    : {kind_label} covering {file_count} file(s). "
        f"Classified by {_classification_source(obj.confidence)}."
    )
    lines.append("")

    # Main files
    if obj.files:
        lines.append("  Main files:")
        for fp in sorted(obj.files)[:_MAX_EXPLAIN_FILES]:
            lines.append(f"    - {fp}")
        if len(obj.files) > _MAX_EXPLAIN_FILES:
            lines.append(f"    … and {len(obj.files) - _MAX_EXPLAIN_FILES} more")
        lines.append("")

    # Dependencies / neighbors
    deps = getattr(obj, "dependencies", [])
    if deps:
        lines.append("  Dependencies / neighbors:")
        for d in sorted(deps)[:5]:
            lines.append(f"    - {d}")
        lines.append("")

    # Related tests
    if obj.tests:
        lines.append("  Related tests:")
        for t in sorted(obj.tests)[:_MAX_EXPLAIN_TESTS]:
            lines.append(f"    - {t}")
        if len(obj.tests) > _MAX_EXPLAIN_TESTS:
            lines.append(f"    … and {len(obj.tests) - _MAX_EXPLAIN_TESTS} more")
        lines.append("")
    else:
        lines.append("  Related tests : none detected")
        lines.append("")

    # Safe-boundary warnings from safe-boundaries.md
    sb_warnings = _safe_boundary_warnings(obj.files, agent_memory_root)
    if sb_warnings:
        lines.append("  Safe-boundary notes:")
        for w in sb_warnings:
            lines.append(f"    ! {w}")
        lines.append("")

    # Freshness hint
    freshness_line = _freshness_hint(match.name, match.slug, match.kind, agent_memory_root)
    if freshness_line:
        lines.append(f"  Freshness  : {freshness_line}")

    if match.stale:
        lines.append("  ⚠  Artifact may be stale — run `memory refresh`.")

    if match.alternatives:
        lines.append("")
        lines.append(f"  Similar areas: {', '.join(match.alternatives)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# changed_match — produce change summary string
# ---------------------------------------------------------------------------


def changed_match(match: TargetMatch, agent_memory_root: Path) -> str:
    """Return a change summary string for *match*.

    Reads ``freshness.json`` and ``changes/recent.md``, filtering for files
    belonging to the matched feature or module.

    Args:
        match:             A :class:`TargetMatch` returned by :func:`match_target`.
        agent_memory_root: Absolute path to ``.agent-memory/``.

    Returns:
        Multi-line string ready to print.
    """
    if not match.found():
        return _not_found_changed(match)

    from .metadata import load_freshness_json

    metadata_dir = agent_memory_root / "metadata"
    freshness = load_freshness_json(metadata_dir)

    lines: list[str] = []
    kind_label = "Feature" if match.kind == "feature" else "Module"
    lines.append(f"{kind_label}: {match.name}")
    lines.append("")

    if freshness is None:
        lines.append(
            "  No refresh data found. Run `memory refresh` to track changes."
        )
        if match.alternatives:
            lines.append(f"  Similar areas: {', '.join(match.alternatives)}")
        return "\n".join(lines)

    # Last refresh metadata
    refreshed_at = freshness.get("refreshed_at", "unknown")
    mode = freshness.get("mode", "unknown")
    lines.append(f"  Last refresh : {_format_timestamp(refreshed_at)} ({mode})")
    lines.append(f"  Changed files: {freshness.get('changed_files_count', 0)} in last refresh")
    lines.append("")

    # Filter changed files to those belonging to this area
    obj = match.obj
    area_files: list[str] = []
    if obj is not None:
        known = set(obj.files)
        for cf in freshness.get("changed_files", []):
            if cf in known or any(cf.startswith(f.rsplit("/", 1)[0]) for f in known):
                area_files.append(cf)

    if area_files:
        lines.append("  Recently changed in this area:")
        for f in area_files[:10]:
            lines.append(f"    - {f}")
        lines.append("")
    else:
        lines.append("  No recent changes detected in this area.")
        lines.append("")

    # Impacted in last refresh?
    slug = match.slug
    impacted_features = freshness.get("impacted_features", [])
    impacted_modules = freshness.get("impacted_modules", [])
    impacted = (
        (match.kind == "feature" and slug in impacted_features)
        or (match.kind == "module" and slug in impacted_modules)
    )
    if impacted:
        lines.append("  Status: ✓ artifact was refreshed in last update")
    else:
        lines.append("  Status: artifact was NOT refreshed in last update")
        if freshness.get("changed_files_count", 0) > 0:
            lines.append("          (no files in this area changed)")

    refreshed_artifacts = freshness.get("artifacts_refreshed", [])
    area_artifacts = [a for a in refreshed_artifacts if slug in a]
    if area_artifacts:
        lines.append("")
        lines.append("  Refreshed artifacts:")
        for a in area_artifacts:
            lines.append(f"    - {a}")

    if match.stale:
        lines.append("")
        lines.append("  ⚠  Memory may be stale — run `memory refresh`.")

    if match.alternatives:
        lines.append("")
        lines.append(f"  Similar areas: {', '.join(match.alternatives)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_match(
    kind: Literal["feature", "module"],
    obj: object,
    agent_memory_root: Path,
    score: float,
    alternatives: list[str] | None = None,
) -> TargetMatch:
    """Construct a TargetMatch for a found feature or module."""
    slug = obj.slug()
    subdir = "features" if kind == "feature" else "modules"
    artifact_path = agent_memory_root / subdir / f"{slug}.md"
    return TargetMatch(
        kind=kind,
        name=obj.name,
        slug=slug,
        obj=obj,
        artifact_path=artifact_path if artifact_path.exists() else None,
        score=score,
        alternatives=alternatives or [],
    )


def _match_by_path(
    target: str,
    agent_memory_root: Path,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
) -> TargetMatch | None:
    """Try to match *target* as a repo-relative file path via sources.json."""
    # Normalise path separators for comparison
    t_norm = target.replace("\\", "/")

    # Check all feature files
    for f in features:
        for fp in f.files:
            if fp == t_norm or fp.endswith("/" + t_norm) or t_norm.endswith("/" + fp):
                return TargetMatch(
                    kind="path",
                    name=f"{f.name} (via path: {target})",
                    slug=f.slug(),
                    obj=f,
                    artifact_path=_artifact_path(agent_memory_root, "feature", f.slug()),
                    score=0.9,
                )

    # Check all module files
    for m in modules:
        for fp in m.files:
            if fp == t_norm or fp.endswith("/" + t_norm) or t_norm.endswith("/" + fp):
                return TargetMatch(
                    kind="path",
                    name=f"{m.name} (via path: {target})",
                    slug=m.slug(),
                    obj=m,
                    artifact_path=_artifact_path(agent_memory_root, "module", m.slug()),
                    score=0.9,
                )

    return None


def _fuzzy_match(
    target: str,
    agent_memory_root: Path,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
) -> TargetMatch:
    """Score all features and modules; return best if above threshold."""
    # Lazy import to avoid circular dependency at module level
    from .context_builder import _score, _tokenize  # type: ignore[attr-defined]

    tokens = _tokenize(target)
    if not tokens:
        return TargetMatch(kind="not_found", name=target)

    scored: list[tuple[float, Literal["feature", "module"], object]] = []
    for f in features:
        s = _score(tokens, f.name, f.files, f.confidence)
        scored.append((s, "feature", f))
    for m in modules:
        s = _score(tokens, m.name, m.files, m.confidence)
        scored.append((s, "module", m))

    if not scored:
        return TargetMatch(kind="not_found", name=target)

    scored.sort(key=lambda x: -x[0])
    best_score, best_kind, best_obj = scored[0]

    if best_score < _FUZZY_THRESHOLD:
        # Collect any candidates that were close to suggest alternatives
        close = [str(o.name) for s, _, o in scored[1:5] if s >= _FUZZY_THRESHOLD * 0.5]
        return TargetMatch(kind="not_found", name=target, alternatives=close)

    # Find alternatives (other matches within 70% of best score)
    alternatives = [
        str(o.name)
        for s, _, o in scored[1:4]
        if s >= best_score * 0.7 and s >= _FUZZY_THRESHOLD
    ]

    return _make_match(best_kind, best_obj, agent_memory_root, score=best_score,
                       alternatives=alternatives)


def _artifact_path(
    agent_memory_root: Path,
    kind: str,
    slug: str,
) -> Path | None:
    subdir = "features" if kind == "feature" else "modules"
    p = agent_memory_root / subdir / f"{slug}.md"
    return p if p.exists() else None


def _safe_boundary_warnings(files: list[str], agent_memory_root: Path) -> list[str]:
    """Return safe-boundary notes relevant to *files* from safe-boundaries.md."""
    sb_path = agent_memory_root / "rules" / "safe-boundaries.md"
    if not sb_path.exists():
        return []
    try:
        content = sb_path.read_text(encoding="utf-8")
    except OSError:
        return []

    warnings: list[str] = []
    for line in content.splitlines():
        # Lines like "- `migrations/` — append-only"
        if not line.strip().startswith("- `"):
            continue
        # Extract the path from backticks
        start = line.index("`") + 1
        end = line.index("`", start)
        boundary_path = line[start:end]
        # Check if any of the area's files fall under this boundary
        for fp in files:
            if fp.startswith(boundary_path.rstrip("/")) or boundary_path.rstrip("/") in fp:
                note = line.strip().lstrip("- ")
                if note not in warnings:
                    warnings.append(note)
                break

    return warnings[:3]  # cap to keep output concise


def _freshness_hint(
    name: str,
    slug: str,
    kind: str,
    agent_memory_root: Path,
) -> str:
    """Return a one-line freshness summary for the matched area."""
    from .metadata import load_freshness_json

    freshness = load_freshness_json(agent_memory_root / "metadata")
    if freshness is None:
        return "unknown — run `memory refresh` to track freshness"

    refreshed_at = freshness.get("refreshed_at", "")
    mode = freshness.get("mode", "")
    impacted_features = freshness.get("impacted_features", [])
    impacted_modules = freshness.get("impacted_modules", [])

    was_refreshed = (
        (kind == "feature" and slug in impacted_features)
        or (kind == "module" and slug in impacted_modules)
    )

    ts = _format_timestamp(refreshed_at)
    if was_refreshed:
        return f"refreshed {ts} ({mode})"
    return f"last global refresh {ts} — this area was not impacted"


def _format_timestamp(iso_str: str) -> str:
    """Format an ISO timestamp to a compact human-readable string."""
    if not iso_str or iso_str == "unknown":
        return "unknown"
    try:
        # Accept both offset-aware and naive timestamps
        dt_str = iso_str.replace("Z", "+00:00")
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(dt_str)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, AttributeError):
        return iso_str[:19]  # return first 19 chars as fallback


def _confidence_label(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.65:
        return "medium"
    return "low"


def _classification_source(confidence: float) -> str:
    if confidence >= 0.85:
        return "explicit package boundary"
    if confidence >= 0.65:
        return "directory name heuristic"
    return "weak heuristic"


def _not_found_explain(match: TargetMatch) -> str:
    lines = [f"  Target '{match.name}' not found in classified memory."]
    if match.alternatives:
        lines.append(f"  Did you mean: {', '.join(match.alternatives)}?")
    lines.append("  Run `memory init` to generate memory artifacts.")
    return "\n".join(lines)


def _not_found_changed(match: TargetMatch) -> str:
    lines = [f"  Target '{match.name}' not found in classified memory."]
    if match.alternatives:
        lines.append(f"  Did you mean: {', '.join(match.alternatives)}?")
    lines.append("  Run `memory init` + `memory refresh` to track changes.")
    return "\n".join(lines)
