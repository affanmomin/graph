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

# Catch-all suppression thresholds.
# A feature is considered a "catch-all" group if it has both very low
# confidence AND a large file count — this pattern indicates a directory-level
# fallback classification rather than a coherent domain feature.  Catch-alls
# are excluded from fallback context packs when better alternatives exist.
_CATCHALL_MAX_CONFIDENCE = 0.45
_CATCHALL_MIN_FILES = 30

# Graph enrichment limits — how many extra entries the graph bridge may add.
# Final file count is still capped by _MAX_FILES at pack assembly time.
_GRAPH_SEED_FILES = 10        # top N heuristic files used as graph query seeds
_GRAPH_MAX_EXTRA_FILES = 5    # max new files added via impact-radius BFS
_GRAPH_MAX_EXTRA_TESTS = 5    # max new tests added by graph enrichment
_GRAPH_MAX_NEIGHBORS = 3      # max new files added via structural (import) neighbors
_GRAPH_MAX_SYMBOL_FILES = 3   # max new files added via symbol-level task routing

# Scoring weights — must sum to _W_TOTAL
_W_NAME = 2.0      # strongest signal: task mentions the feature/module by name
_W_FILE_STEM = 1.0  # task mentions a file's stem (e.g. "login" → login.py)
_W_PATH_DIR = 1.5   # task mentions a directory component (e.g. "billing/")
_W_SYMBOL = 1.0    # task tokens match function/class names from graph vocabulary
_W_KEYWORD = 1.2   # task tokens match pre-computed keyword index (pack cache)
_W_TOTAL = _W_NAME + _W_FILE_STEM + _W_PATH_DIR  # 4.5 (without symbol/keyword)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_context_pack(
    task: str,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    overrides: Overrides | None = None,
    repo_root: Path | None = None,
    vocabulary: dict[str, list[str]] | None = None,
    kw_map: dict[str, set[str]] | None = None,
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
        vocabulary: Optional dict mapping file_path -> list[symbol_name] from the
                   graph.  When provided, symbol names are added to the scoring
                   vocabulary so tasks like "fix token expiry" match files
                   containing ``validate_token()`` even without a directory
                   keyword match.

    Returns:
        A populated :class:`~models.TaskContextPack`.  Never raises —
        returns a fallback pack if no matches are found.
    """
    task_tokens = _tokenize(task)

    # If vocabulary not passed in, try to fetch from graph
    if vocabulary is None and repo_root is not None:
        try:
            from .graph_bridge import get_file_vocabulary, graph_available
            if graph_available(repo_root):
                all_files = list({f for item in [*features, *modules] for f in item.files})
                vocabulary = get_file_vocabulary(all_files, repo_root)
        except Exception:
            vocabulary = None

    # Score every feature and module
    scored_features = sorted(
        (
            (f, _score(task_tokens, f.name, f.files, f.confidence, vocabulary,
                       keywords=kw_map.get(f.name) if kw_map else None))
            for f in features
        ),
        key=lambda x: (-x[1], x[0].name),
    )
    scored_modules = sorted(
        (
            (m, _score(task_tokens, m.name, m.files, m.confidence, vocabulary,
                       keywords=kw_map.get(m.name) if kw_map else None))
            for m in modules
        ),
        key=lambda x: (-x[1], x[0].name),
    )

    # Graph symbol boost: rerank items whose files contain task-relevant symbols.
    # This lifts items with no lexical name/path match but relevant symbol-owning files,
    # e.g. "fix token expiry" should surface the auth feature even if the task never
    # mentions "auth" or "src/auth/".
    graph_symbol_routed = False
    precomputed_symbol_files: list[str] = []
    if repo_root is not None and task.strip():
        _boosts, precomputed_symbol_files = _graph_symbol_boost(task, features, modules, repo_root)
        if _boosts:
            graph_symbol_routed = True
            scored_features = sorted(
                [(f, s + _boosts.get(id(f), 0.0)) for f, s in scored_features],
                key=lambda x: (-x[1], x[0].name),
            )
            scored_modules = sorted(
                [(m, s + _boosts.get(id(m), 0.0)) for m, s in scored_modules],
                key=lambda x: (-x[1], x[0].name),
            )

    # Keep only items above the relevance threshold
    relevant_features = [(f, s) for f, s in scored_features if s >= _MIN_SCORE][:_MAX_FEATURES]
    relevant_modules = [(m, s) for m, s in scored_modules if s >= _MIN_SCORE][:_MAX_MODULES]

    # Fallback: no match → take top candidates so the pack is never empty.
    # Prefer meaningful features over catch-all groups (low-confidence, large
    # file-count areas that are directory-level fallbacks, not domain features).
    fallback = not relevant_features and not relevant_modules
    if fallback:
        meaningful = [(f, s) for f, s in scored_features if not _is_catchall(f)]
        fallback_feats = meaningful if meaningful else scored_features
        relevant_features = fallback_feats[:min(2, len(fallback_feats))]
        relevant_modules = scored_modules[:min(2, len(scored_modules))]

    top_features = [f for f, _ in relevant_features]
    top_modules = [m for m, _ in relevant_modules]

    # Collect files and tests preserving relevance order (features first)
    files_ordered, tests_ordered = _collect_files_and_tests(top_features, top_modules)

    # Graph enrichment — add structurally related files/tests when graph is available.
    # Modifies files_ordered and tests_ordered in-place; never raises.
    # Pass precomputed symbol_files to avoid a second get_task_symbol_files() call and to
    # use them as extended seeds for test discovery.
    graph_enriched = _enrich_with_graph(
        files_ordered, tests_ordered, repo_root, task=task,
        symbol_files=precomputed_symbol_files or None,
    )

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
        graph_symbol_routed=graph_symbol_routed,
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

    Splits on whitespace, common punctuation, and camelCase boundaries so
    that identifiers like ``ContactForm`` produce tokens ``{"contact", "form"}``
    and task descriptions like "create a modal for the ContactForm" match a
    feature whose files contain ``ContactForm.tsx``.
    """
    # Split camelCase before lower-casing: "ContactForm" → "Contact Form"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    raw = re.split(r"[\s\-_./\\<>\"'`]+", text.lower())
    tokens: set[str] = set()
    for tok in raw:
        tok = tok.strip(".,;:!?()[]{}@#")
        if tok and len(tok) >= 2 and tok not in _STOP_WORDS:
            tokens.add(tok)
    return tokens


def _is_catchall(feature: FeatureMemory) -> bool:
    """Return True when *feature* looks like a catch-all classification.

    A catch-all is a large, low-confidence group that results from
    directory-level fallback heuristics rather than genuine domain detection.
    Including these in context packs adds noise without meaningful routing.
    They are still written as memory artifacts — this only suppresses them
    from the fallback context-pack selection path.
    """
    return feature.confidence < _CATCHALL_MAX_CONFIDENCE and len(feature.files) >= _CATCHALL_MIN_FILES


def _score(
    task_tokens: set[str],
    name: str,
    files: list[str],
    confidence: float,
    vocabulary: dict[str, list[str]] | None = None,
    keywords: set[str] | None = None,
) -> float:
    """Compute a relevance score for a feature/module against *task_tokens*.

    Five scoring components (weights defined in module constants):
    1. Name overlap    — task tokens appear in the feature/module name
    2. File-stem overlap — task tokens appear in file stems (camelCase-split)
    3. Path-dir overlap  — task tokens appear in directory components
    4. Symbol overlap    — task tokens appear in function/class names from graph
                           (only when *vocabulary* is provided)
    5. Keyword overlap   — task tokens appear in pre-computed keyword index from
                           pack cache (camelCase-split file stems + symbol names)

    Returns a float approximately in [0.0, 1.0].
    """
    if not task_tokens:
        return 0.0

    n = len(task_tokens)  # normalisation denominator

    # Component 1: name overlap
    name_tokens = _tokenize(name)
    name_overlap = len(task_tokens & name_tokens) / n

    # Component 2: file-stem overlap (tokenize splits camelCase)
    stem_tokens: set[str] = set()
    for f in files:
        stem_tokens.update(_tokenize(Path(f).stem))
    stem_overlap = len(task_tokens & stem_tokens) / n

    # Component 3: directory-component overlap
    dir_tokens: set[str] = set()
    for f in files:
        for part in Path(f).parts[:-1]:
            dir_tokens.update(_tokenize(part))
    dir_overlap = len(task_tokens & dir_tokens) / n

    # Component 4: symbol overlap — function/class names from graph vocabulary
    symbol_overlap = 0.0
    if vocabulary:
        sym_tokens: set[str] = set()
        for fp in files:
            for sym in vocabulary.get(fp, []):
                sym_tokens.update(_tokenize(sym))
        symbol_overlap = min(len(task_tokens & sym_tokens) / n, 1.0)

    # Component 5: pre-computed keyword index (pack cache — broadest signal)
    keyword_overlap = 0.0
    if keywords:
        keyword_overlap = min(len(task_tokens & keywords) / n, 1.0)

    # Accumulate weights for active components
    w_total = _W_TOTAL
    raw = _W_NAME * name_overlap + _W_FILE_STEM * stem_overlap + _W_PATH_DIR * dir_overlap
    if vocabulary:
        raw += _W_SYMBOL * symbol_overlap
        w_total += _W_SYMBOL
    if keywords:
        raw += _W_KEYWORD * keyword_overlap
        w_total += _W_KEYWORD

    # Confidence soft-weighting: high-confidence classifications rank higher
    return round((raw / w_total) * (0.4 + 0.6 * confidence), 4)


# ---------------------------------------------------------------------------
# Graph symbol boost
# ---------------------------------------------------------------------------


def _graph_symbol_boost(
    task: str,
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    repo_root: Path,
) -> tuple[dict[int, float], list[str]]:
    """Compute graph-based score boosts for features/modules owning task-relevant symbol files.

    Calls ``get_task_symbol_files()`` once, maps the returned source files back
    to the features/modules that own them, and returns a proportional boost for
    each matched item.  Also returns the raw symbol file list so the caller can
    pass it to ``_enrich_with_graph()`` without a second graph DB open.

    The boost is intentionally small (max ~0.27) — enough to lift a zero-heuristic
    item above ``_MIN_SCORE`` when it genuinely owns a symbol file, while leaving
    lexical matches dominant when both signals fire.

    Returns:
        Tuple of (boosts: dict[id(item) → float], symbol_files: list[str]).
        Both are empty when the graph is unavailable or no files are found.
    """
    try:
        from .graph_bridge import get_task_symbol_files
        symbol_files = get_task_symbol_files(task, repo_root, max_files=10)
        if not symbol_files:
            return {}, []

        symbol_file_set = set(symbol_files)
        boosts: dict[int, float] = {}

        for item in [*features, *modules]:
            matched = sum(1 for f in item.files if f in symbol_file_set)
            if matched > 0:
                frac = matched / max(len(item.files), 1)
                # Mirror the confidence soft-weighting used in _score()
                boost = min(frac * 0.3, 0.3) * (0.4 + 0.6 * item.confidence)
                boosts[id(item)] = boost

        return boosts, symbol_files
    except Exception as exc:
        logger.debug("_graph_symbol_boost: failed: %s", exc)
        return {}, []


# ---------------------------------------------------------------------------
# Graph enrichment
# ---------------------------------------------------------------------------


def _enrich_with_graph(
    files: list[str],
    tests: list[str],
    repo_root: Path | None,
    task: str = "",
    symbol_files: list[str] | None = None,
) -> bool:
    """Enrich *files* and *tests* in-place with graph-backed relationships.

    Four complementary graph strategies are combined:

    1. **Impact-radius files** — source files reachable within 1 BFS hop of the
       heuristic seed files (``get_related_files``).
    2. **Related tests** — test files linked via TESTED_BY edges or 1-hop BFS
       (``get_related_tests``), seeded from heuristic files *plus* symbol files
       so tests for symbol-matched files are always discovered.
    3. **Structural neighbors** — files connected via IMPORTS_FROM in either
       direction (``get_structural_neighbors``).
    4. **Symbol files** — when *task* is provided, files containing symbols
       whose names match the task description (``get_task_symbol_files``).
       When *symbol_files* is passed in (pre-computed by ``_graph_symbol_boost``),
       the graph is not queried again.

    Only entries not already present are appended.  All caps are applied before
    the final ``_MAX_FILES`` ceiling at pack assembly time.

    Returns ``True`` if any new entries were added; ``False`` otherwise.
    Never raises — all graph errors are caught and logged at DEBUG level.
    """
    if repo_root is None:
        return False
    try:
        from .graph_bridge import (
            get_related_files,
            get_related_tests,
            get_structural_neighbors,
            get_task_symbol_files,
            graph_available,
        )
        if not graph_available(repo_root):
            return False

        # 4.1: Prepend call-graph entry points so they seed test/neighbor discovery
        # before the plain heuristic ordering takes over.
        try:
            from .graph_bridge import get_all_call_graph_signals
            _cg = get_all_call_graph_signals({"_ctx": files[:_GRAPH_SEED_FILES]}, repo_root)
            _ep = _cg.get("_ctx", None)
            if _ep and _ep.entry_points:
                files_ep_first = list(dict.fromkeys([*_ep.entry_points, *files]))
                files[:] = files_ep_first
        except Exception:
            pass

        seed_files = files[:_GRAPH_SEED_FILES]
        existing_files: set[str] = set(files)
        existing_tests: set[str] = set(tests)

        # Resolve symbol files early — before test discovery — so symbol-matched
        # files contribute as test-discovery seeds alongside heuristic seeds.
        if symbol_files is None:
            symbol_files = []
            if task.strip():
                symbol_files = get_task_symbol_files(
                    task, repo_root, max_files=_GRAPH_MAX_SYMBOL_FILES
                )

        # Extended seeds: heuristic seeds + symbol-matched files (deduped, ordered).
        extended_seeds = list(dict.fromkeys([*seed_files, *symbol_files]))

        graph_files = get_related_files(seed_files, repo_root, max_files=_GRAPH_MAX_EXTRA_FILES)
        # Test discovery uses extended_seeds so symbol-matched file tests are found.
        graph_tests = get_related_tests(extended_seeds, repo_root, max_tests=_GRAPH_MAX_EXTRA_TESTS)
        graph_neighbors = get_structural_neighbors(
            seed_files, repo_root, max_neighbors=_GRAPH_MAX_NEIGHBORS
        )

        enriched = False
        for gf in [*graph_files, *graph_neighbors, *symbol_files]:
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
        if relevant_features or relevant_modules:
            warnings.append(
                "No specific area matched this task — showing broadest available context."
            )
        else:
            warnings.append(
                "No areas classified yet. Run `memory init` to generate context artifacts."
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
    graph_symbol_routed: bool = False,
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

    # Graph enrichment note — symbol routing takes precedence over generic enrichment note
    if graph_symbol_routed:
        lines.append("Routing based on graph symbol match (task mentions symbols, not directory names).")
    elif graph_enriched:
        lines.append("Context enriched with graph-backed structural relationships.")

    # First warning
    if warnings:
        lines.append(f"Note: {warnings[0]}")

    if fallback:
        lines.append("Tip: run `memory init` to generate better-targeted context.")

    return "\n".join(lines)
