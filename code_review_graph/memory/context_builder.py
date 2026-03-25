"""Task-aware context pack builder.

This is the killer feature of the repo memory system.

Given a natural-language task description, the context builder assembles a
focused ``TaskContextPack`` containing exactly the repo context an AI agent
needs to start working — without requiring the developer to manually point it
at files or explain the codebase.

Public API
----------
build_context_pack(task, features, modules) -> TaskContextPack

Ranking strategy
----------------
Each feature and module receives a relevance score computed from:

1. **Name overlap** (weight 2.0):
   Tokens from the task that appear in the feature/module name.
   e.g. "auth" in task → high score for a feature named "auth".

2. **File-stem overlap** (weight 1.0):
   Tokens from the task that appear in the *stems* of files in the
   feature/module. e.g. "login" in task → score for a feature containing
   ``src/auth/login.py``.

3. **Path-directory overlap** (weight 1.5):
   Tokens from the task that appear in the *directory components* of files
   in the feature/module. e.g. "billing" in task → score for any area with
   files under ``src/billing/``.

All three components are normalised against the number of task tokens so that
shorter tasks are not penalised.

The raw score is multiplied by a confidence factor ``(0.4 + 0.6 * confidence)``
so that high-confidence classifications rank above speculative ones with the
same token overlap.

Threshold and caps:
- Items scoring below ``_MIN_SCORE`` (0.05) are excluded.
- At most ``_MAX_FEATURES`` (5) features and ``_MAX_MODULES`` (5) modules.
- At most ``_MAX_FILES`` (20) files in the pack.
- Files are ordered by feature/module rank (most relevant first).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .models import FeatureMemory, ModuleMemory, TaskContextPack

if TYPE_CHECKING:
    from .overrides import Overrides

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Task words that carry no feature/module signal
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "to", "in", "for", "of", "with", "by",
    "and", "or", "is", "it", "its", "this", "that", "i", "we",
    "my", "our", "add", "fix", "debug", "update", "implement",
    "create", "build", "change", "make", "write", "get", "set",
    "use", "run", "test", "check", "do", "be", "as", "at", "on",
    "from", "into", "should", "need", "new", "old", "help",
    "handle", "support", "allow", "enable", "disable", "also",
    "when", "where", "how", "what", "which", "want", "work",
})

_MAX_FEATURES = 5
_MAX_MODULES = 5
_MAX_FILES = 20
_MIN_SCORE = 0.05  # minimum relevance score to include anything

# Graph enrichment limits — how many extra entries the graph bridge may add.
# Final file count is still capped by _MAX_FILES at pack assembly time.
_GRAPH_SEED_FILES = 10   # top N heuristic files used as graph query seeds
_GRAPH_MAX_EXTRA_FILES = 5   # max new files added by graph enrichment
_GRAPH_MAX_EXTRA_TESTS = 5   # max new tests added by graph enrichment

# Scoring weights — must sum to _W_TOTAL
_W_NAME = 2.0      # strongest signal: task mentions the feature/module by name
_W_FILE_STEM = 1.0  # task mentions a file's stem (e.g. "login" → login.py)
_W_PATH_DIR = 1.5   # task mentions a directory component (e.g. "billing/")
_W_TOTAL = _W_NAME + _W_FILE_STEM + _W_PATH_DIR  # 4.5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context_pack(
    task: str,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    overrides: Overrides | None = None,
    repo_root: Path | None = None,
) -> TaskContextPack:
    """Build a focused context pack for *task*.

    Args:
        task:      Natural-language task string from the developer.
        features:  Classified :class:`~models.FeatureMemory` list for the repo.
        modules:   Classified :class:`~models.ModuleMemory` list for the repo.
        overrides: Optional loaded :class:`~overrides.Overrides`.  When
                   provided, ``always_include`` files are prepended, ``never_edit``
                   paths become warnings, and matching ``task_hints`` are injected.
        repo_root: Optional repo root path.  When provided and a graph.db exists,
                   the heuristic file list is enriched with structurally related
                   files and tests discovered via the graph engine.  Falls back
                   to heuristic-only behaviour when omitted or graph is absent.

    Returns:
        A populated :class:`~models.TaskContextPack`.  Never raises —
        returns a fallback pack if no matches are found.
    """
    task_tokens = _tokenize(task)

    # Score every feature and module
    scored_features = sorted(
        ((f, _score(task_tokens, f.name, f.files, f.confidence)) for f in features),
        key=lambda x: (-x[1], x[0].name),
    )
    scored_modules = sorted(
        ((m, _score(task_tokens, m.name, m.files, m.confidence)) for m in modules),
        key=lambda x: (-x[1], x[0].name),
    )

    # Keep only items above the relevance threshold
    relevant_features = [(f, s) for f, s in scored_features if s >= _MIN_SCORE][:_MAX_FEATURES]
    relevant_modules = [(m, s) for m, s in scored_modules if s >= _MIN_SCORE][:_MAX_MODULES]

    # Fallback: no match → take top candidates so the pack is never empty
    fallback = not relevant_features and not relevant_modules
    if fallback:
        relevant_features = scored_features[:min(2, len(scored_features))]
        relevant_modules = scored_modules[:min(2, len(scored_modules))]

    top_features = [f for f, _ in relevant_features]
    top_modules = [m for m, _ in relevant_modules]

    # Collect files and tests preserving relevance order (features first)
    files_ordered, tests_ordered = _collect_files_and_tests(top_features, top_modules)

    # Graph enrichment — add structurally related files/tests when graph is available.
    # Modifies files_ordered and tests_ordered in-place; never raises.
    graph_enriched = _enrich_with_graph(files_ordered, tests_ordered, repo_root)

    warnings = _build_warnings(
        relevant_features=top_features,
        relevant_modules=top_modules,
        fallback=fallback,
    )

    summary = _build_summary(
        task=task,
        relevant_features=top_features,
        relevant_modules=top_modules,
        files=files_ordered,
        warnings=warnings,
        fallback=fallback,
        graph_enriched=graph_enriched,
    )

    pack = TaskContextPack(
        task=task,
        relevant_features=[f.name for f in top_features],
        relevant_modules=[m.name for m in top_modules],
        relevant_files=files_ordered[:_MAX_FILES],
        relevant_tests=sorted(set(tests_ordered)),
        warnings=warnings,
        summary=summary,
    )

    # Apply human overrides last — they always win over inference
    if overrides and not overrides.is_empty():
        from .overrides import apply_overrides
        pack = apply_overrides(pack, overrides)

    return pack


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Return a set of meaningful lowercase tokens from *text*.

    Splits on whitespace and common punctuation; drops stop words and
    single-character tokens.
    """
    raw = re.split(r"[\s\-_./\\<>\"'`]+", text.lower())
    tokens: set[str] = set()
    for tok in raw:
        tok = tok.strip(".,;:!?()[]{}@#")
        if tok and len(tok) >= 2 and tok not in _STOP_WORDS:
            tokens.add(tok)
    return tokens


def _score(
    task_tokens: set[str],
    name: str,
    files: list[str],
    confidence: float,
) -> float:
    """Compute a relevance score for a feature/module against *task_tokens*.

    Returns a float approximately in [0.0, 1.0].
    """
    if not task_tokens:
        return 0.0

    n = len(task_tokens)  # normalisation denominator

    # Component 1: name overlap
    name_tokens = _tokenize(name)
    name_overlap = len(task_tokens & name_tokens) / n

    # Component 2: file-stem overlap
    stem_tokens: set[str] = set()
    for f in files:
        stem = Path(f).stem.lower()
        stem_tokens.update(re.split(r"[-_]", stem))
    stem_overlap = len(task_tokens & stem_tokens) / n

    # Component 3: directory-component overlap
    dir_tokens: set[str] = set()
    for f in files:
        for part in Path(f).parts[:-1]:
            dir_tokens.update(re.split(r"[-_]", part.lower()))
    dir_overlap = len(task_tokens & dir_tokens) / n

    raw = (_W_NAME * name_overlap + _W_FILE_STEM * stem_overlap + _W_PATH_DIR * dir_overlap) / _W_TOTAL
    # Confidence soft-weighting: high-confidence classifications rank higher
    return round(raw * (0.4 + 0.6 * confidence), 4)


# ---------------------------------------------------------------------------
# Graph enrichment
# ---------------------------------------------------------------------------


def _enrich_with_graph(
    files: list[str],
    tests: list[str],
    repo_root: Path | None,
) -> bool:
    """Enrich *files* and *tests* in-place with graph-backed relationships.

    Uses the top ``_GRAPH_SEED_FILES`` heuristic files as seeds and queries the
    graph bridge for structurally related source files and test files.  Only
    entries not already present are appended.

    Returns ``True`` if any new entries were added; ``False`` otherwise.
    Never raises — all graph errors are caught and logged at DEBUG level.
    """
    if repo_root is None:
        return False
    try:
        from .graph_bridge import get_related_files, get_related_tests, graph_available
        if not graph_available(repo_root):
            return False

        seed_files = files[:_GRAPH_SEED_FILES]
        existing_files: set[str] = set(files)
        existing_tests: set[str] = set(tests)

        graph_files = get_related_files(seed_files, repo_root, max_files=_GRAPH_MAX_EXTRA_FILES)
        graph_tests = get_related_tests(seed_files, repo_root, max_tests=_GRAPH_MAX_EXTRA_TESTS)

        enriched = False
        for gf in graph_files:
            if gf not in existing_files:
                files.append(gf)
                existing_files.add(gf)
                enriched = True
        for gt in graph_tests:
            if gt not in existing_tests:
                tests.append(gt)
                existing_tests.add(gt)
                enriched = True
        return enriched
    except Exception as exc:
        logger.debug("_enrich_with_graph: graph enrichment failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------


def _collect_files_and_tests(
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
) -> tuple[list[str], list[str]]:
    """Collect files and tests from features (first) then modules, deduplicating."""
    files: list[str] = []
    tests: list[str] = []
    seen_files: set[str] = set()
    seen_tests: set[str] = set()

    for source in [*features, *modules]:
        for fp in source.files:
            if fp not in seen_files:
                files.append(fp)
                seen_files.add(fp)
        for t in source.tests:
            if t not in seen_tests:
                tests.append(t)
                seen_tests.add(t)

    return files, tests


def _build_warnings(
    relevant_features: list[FeatureMemory],
    relevant_modules: list[ModuleMemory],
    fallback: bool,
) -> list[str]:
    """Assemble warning strings for the context pack."""
    warnings: list[str] = []

    if fallback:
        warnings.append(
            "No specific area matched this task. Showing broadest available context. "
            "Run `memory init` if .agent-memory/ artifacts are missing or stale."
        )

    # Features with no tests
    testless = [f.name for f in relevant_features if not f.tests]
    if testless:
        warnings.append(
            f"No tests linked to: {', '.join(testless)}. "
            "Verify coverage before submitting changes."
        )

    # Low-confidence classifications
    low_conf = [
        f.name for f in relevant_features if f.confidence < 0.6
    ] + [
        m.name for m in relevant_modules if m.confidence < 0.6
    ]
    if low_conf:
        warnings.append(
            f"Low-confidence classification for: {', '.join(low_conf)}. "
            "File membership may be incomplete — verify manually."
        )

    # Cross-cutting concern
    if len(relevant_features) >= 3:
        warnings.append(
            f"Task touches {len(relevant_features)} features — this may be a "
            "cross-cutting change. Consider scoping the task or coordinating across areas."
        )

    return warnings


def _build_summary(
    task: str,
    relevant_features: list[FeatureMemory],
    relevant_modules: list[ModuleMemory],
    files: list[str],
    warnings: list[str],
    fallback: bool,
    graph_enriched: bool = False,
) -> str:
    """Produce a concise, agent-readable summary paragraph for the context pack."""
    lines: list[str] = []

    lines.append(f"Task: {task}")

    # What it touches
    if relevant_features:
        names = ", ".join(f.name for f in relevant_features[:3])
        suffix = f" (+{len(relevant_features) - 3} more)" if len(relevant_features) > 3 else ""
        lines.append(f"Most likely touches: {names}{suffix}")
    elif relevant_modules:
        names = ", ".join(m.name for m in relevant_modules[:3])
        lines.append(f"Most likely touches modules: {names}")
    else:
        lines.append("Most likely touches: unknown — no features or modules classified yet")

    # What to inspect first
    if files:
        first = files[:3]
        lines.append(f"Inspect first: {', '.join(first)}")

    # Graph enrichment note
    if graph_enriched:
        lines.append("Context enriched with graph-backed structural relationships.")

    # First warning
    if warnings:
        lines.append(f"Note: {warnings[0]}")

    if fallback:
        lines.append("Tip: run `memory init` to generate better-targeted context.")

    return "\n".join(lines)
