"""Embedding-assisted classification rescue for flat-package repositories.

When the classifier finds no features (or the repo is detected as flat-package),
this module attempts to group source files into logical features using:

1. **Local embeddings** (``sentence-transformers all-MiniLM-L6-v2``) — semantic
   grouping based on file stem text enriched with graph vocabulary symbols.
   The model is lazy-imported so it is only loaded when actually needed.

2. **Keyword-name fallback** — groups files by matching domain tokens in their
   filename stems when embeddings are unavailable or the package is not installed.

Both paths apply graph ``ClassifierGraphSignals`` for confidence adjustment and
test discovery after clustering is complete.

Public API
----------
rescue_flat_features(repo_root, scan, existing_features) -> list[FeatureMemory]
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from pathlib import Path

from .models import FeatureMemory
from .scanner import (
    RepoScan,
    _EXT_TO_LANG,
    _SKIP_DIRS,
    _TEST_DIR_NAMES,
    _TEST_PREFIXES,
    _TEST_SUFFIXES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuneable constants
# ---------------------------------------------------------------------------

_MAX_FILES_TO_EMBED: int = 40       # hard cap for embedding pass
_MAX_CLUSTERS: int = 8              # maximum number of output feature clusters
_SIMILARITY_THRESHOLD: float = 0.55 # cosine similarity threshold to join a cluster
_MAX_VOCAB_SYMBOLS: int = 6         # vocab symbols to include in embedding text

# Domain token sets — imported lazily to avoid circular dependency with classifier
# We inline a minimal copy here so flat_rescue is self-contained.
_HIGH_DOMAIN_TOKENS: frozenset[str] = frozenset({
    "auth", "authentication", "authorization", "login", "logout", "signup",
    "billing", "payment", "payments", "checkout", "invoice", "subscription",
    "user", "users", "account", "accounts", "profile",
    "notification", "notifications", "email", "sms", "webhook",
    "search", "admin", "dashboard", "onboarding", "api", "graphql",
    "upload", "storage", "media", "report", "analytics", "metrics",
    "cart", "order", "orders", "catalog", "product", "products",
    "chat", "messaging", "audit", "cache", "task", "tasks", "job",
    "queue", "worker", "health",
})

_MED_DOMAIN_TOKENS: frozenset[str] = frozenset({
    "core", "common", "shared", "utils", "util", "helpers",
    "base", "middleware", "handler", "model", "models", "schema",
    "view", "views", "route", "routes", "router", "controller",
    "service", "services", "repository", "client", "server", "cli",
    "db", "database", "types",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def rescue_flat_features(
    repo_root: Path,
    scan: RepoScan,
    existing_features: list[FeatureMemory],
) -> list[FeatureMemory]:
    """Attempt to recover feature groupings for flat-package or feature-poor repos.

    Collects source files not already covered by *existing_features*, then
    clusters them via embeddings (if available) or keyword heuristics.

    Args:
        repo_root:         Absolute path to the repository root.
        scan:              Completed :class:`~scanner.RepoScan`.
        existing_features: Features already found by the normal classifier.

    Returns:
        New :class:`~models.FeatureMemory` items (may be empty if nothing useful
        was found).  Never raises — failures return an empty list with a log warning.
    """
    try:
        return _rescue(repo_root, scan, existing_features)
    except Exception as exc:  # pragma: no cover
        logger.warning("flat_rescue: unexpected error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Internal entry point
# ---------------------------------------------------------------------------


def _rescue(
    repo_root: Path,
    scan: RepoScan,
    existing_features: list[FeatureMemory],
) -> list[FeatureMemory]:
    files = _collect_flat_source_files(repo_root, scan, existing_features)
    if len(files) < 2:
        return []

    # Fetch graph vocabulary once for all files (enriches embedding text + naming)
    vocabulary: dict[str, list[str]] = {}
    try:
        from .graph_bridge import get_file_vocabulary, graph_available
        if graph_available(repo_root):
            vocabulary = get_file_vocabulary(files[:_MAX_FILES_TO_EMBED], repo_root)
    except Exception as exc:
        logger.debug("flat_rescue: vocabulary fetch failed: %s", exc)

    # Try embedding-based rescue first
    features = _try_embedding_rescue(files, repo_root, scan, vocabulary)

    # Fall back to keyword-name rescue
    if features is None:
        features = _keyword_name_rescue(files, repo_root, scan)

    if not features:
        return []

    # Apply graph signals: confidence adjustment + test discovery
    _apply_graph_signals(features, repo_root)

    return sorted(features, key=lambda f: f.name)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------


def _collect_flat_source_files(
    repo_root: Path,
    scan: RepoScan,
    existing_features: list[FeatureMemory],
) -> list[str]:
    """Collect non-test source files not already claimed by *existing_features*.

    Returns repo-relative POSIX paths, sorted for determinism.
    """
    claimed: set[str] = set()
    for feat in existing_features:
        claimed.update(feat.files)

    result: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _EXT_TO_LANG:
            continue
        try:
            parts = path.relative_to(repo_root).parts
        except ValueError:
            continue
        # Skip noise dirs
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        # Skip test dirs
        if any(p.lower() in _TEST_DIR_NAMES for p in parts[:-1]):
            continue
        # Skip test files
        stem = path.stem.lower()
        if any(stem.startswith(p) for p in _TEST_PREFIXES):
            continue
        if any(stem.endswith(s) for s in _TEST_SUFFIXES):
            continue

        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        if rel not in claimed:
            result.append(rel)

    return sorted(result)


# ---------------------------------------------------------------------------
# Embedding rescue
# ---------------------------------------------------------------------------


def _try_embedding_rescue(
    files: list[str],
    repo_root: Path,
    scan: RepoScan,
    vocabulary: dict[str, list[str]],
) -> list[FeatureMemory] | None:
    """Attempt embedding-based clustering.

    Returns a list of :class:`~models.FeatureMemory` on success, or ``None``
    when sentence-transformers is not installed or encoding fails.
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore[import]
    except ImportError:
        logger.debug("flat_rescue: sentence-transformers not installed; using keyword rescue")
        return None

    embed_files = files[:_MAX_FILES_TO_EMBED]

    try:
        model = SentenceTransformer("all-MiniLM-L6-v2")
        texts = [_build_file_text(f, vocabulary) for f in embed_files]
        raw = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        embeddings: list[list[float]] = [list(map(float, v)) for v in raw]
    except Exception as exc:
        logger.debug("flat_rescue: embedding encode failed: %s", exc)
        return None

    clusters = _cluster_by_similarity(
        embed_files, embeddings, _SIMILARITY_THRESHOLD, _MAX_CLUSTERS
    )

    # Any files beyond _MAX_FILES_TO_EMBED fall into keyword rescue for the remainder
    leftover = files[_MAX_FILES_TO_EMBED:]

    features: list[FeatureMemory] = []
    for cluster_files in clusters:
        if not cluster_files:
            continue
        name = _name_cluster(cluster_files, vocabulary)
        tests = _find_tests_for_files(repo_root, scan, cluster_files)
        features.append(FeatureMemory(
            name=name,
            files=sorted(cluster_files),
            tests=tests,
            confidence=0.45,  # embedding clusters start at lower confidence
            summary="embedding-clustered flat-package grouping",
        ))

    # Merge leftover files into the closest cluster by keyword, or create new ones
    if leftover:
        kw_extras = _keyword_name_rescue(leftover, repo_root, scan)
        _merge_features(features, kw_extras)

    return features if features else None


def _build_file_text(file_path: str, vocabulary: dict[str, list[str]]) -> str:
    """Build the text to embed for a single file.

    Combines the filename stem (with underscores/hyphens replaced by spaces)
    and the first few vocabulary symbols from the graph.
    """
    stem = Path(file_path).stem.replace("_", " ").replace("-", " ")
    symbols = vocabulary.get(file_path, [])[:_MAX_VOCAB_SYMBOLS]
    if symbols:
        return f"{stem} {' '.join(symbols)}"
    return stem


# ---------------------------------------------------------------------------
# Greedy cosine clustering
# ---------------------------------------------------------------------------


def _cluster_by_similarity(
    files: list[str],
    embeddings: list[list[float]],
    threshold: float,
    max_clusters: int,
) -> list[list[str]]:
    """Group files into clusters using greedy cosine similarity.

    Algorithm (deterministic given sorted input):
    1. The first file seeds cluster 0.
    2. For each subsequent file, compute cosine similarity to every cluster
       centroid (mean of member embeddings).
    3. If the maximum similarity exceeds *threshold*, join that cluster.
    4. Otherwise, if fewer than *max_clusters* exist, create a new cluster.
    5. When at the cap, join the cluster with the highest similarity.

    Centroids are recomputed incrementally after each assignment.

    Args:
        files:      Repo-relative file paths (same order as *embeddings*).
        embeddings: L2-normalised embedding vectors (sentence-transformers output).
        threshold:  Minimum cosine similarity to merge into an existing cluster.
        max_clusters: Hard cap on the number of output clusters.

    Returns:
        List of file groups.  Each group is a non-empty list of file paths.
    """
    if not files:
        return []

    cluster_files: list[list[str]] = [[files[0]]]
    # Centroid = mean of L2-normalised vectors (not re-normalised; we compare via dot)
    cluster_centroids: list[list[float]] = [embeddings[0][:]]

    for i in range(1, len(files)):
        emb = embeddings[i]
        sims = [_dot(emb, c) for c in cluster_centroids]
        best_idx = max(range(len(sims)), key=lambda j: sims[j])
        best_sim = sims[best_idx]

        if best_sim >= threshold or len(cluster_files) >= max_clusters:
            # Join best cluster
            cluster_files[best_idx].append(files[i])
            # Update centroid (incremental mean)
            n = len(cluster_files[best_idx])
            old_c = cluster_centroids[best_idx]
            cluster_centroids[best_idx] = [
                old_c[k] * (n - 1) / n + emb[k] / n for k in range(len(emb))
            ]
        else:
            # Create new cluster
            cluster_files.append([files[i]])
            cluster_centroids.append(emb[:])

    return cluster_files


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two equal-length vectors (cosine similarity for L2-normalised)."""
    return sum(x * y for x, y in zip(a, b))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (handles un-normalised input)."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Keyword-name rescue
# ---------------------------------------------------------------------------


def _keyword_name_rescue(
    files: list[str],
    repo_root: Path,
    scan: RepoScan,
) -> list[FeatureMemory]:
    """Group files by dominant domain token in their filename stems.

    Each file's stem is split on ``_`` and ``-``.  Tokens that appear in the
    high- or medium-domain keyword sets are used as group keys.  Files with no
    matching keyword are collected into a single ``"Core"`` group.

    Returns:
        One :class:`~models.FeatureMemory` per keyword group (plus ``"Core"``
        if non-empty).  Groups are sorted by name.
    """
    groups: dict[str, list[str]] = {}  # key (lower) → files

    for f in sorted(files):
        tokens = _stem_tokens(Path(f).stem)
        key = None
        # Prefer high-confidence domain token
        for tok in tokens:
            if tok in _HIGH_DOMAIN_TOKENS:
                key = tok
                break
        if key is None:
            for tok in tokens:
                if tok in _MED_DOMAIN_TOKENS:
                    key = tok
                    break
        if key is None:
            key = "_other"

        groups.setdefault(key, []).append(f)

    features: list[FeatureMemory] = []
    for key, grp_files in sorted(groups.items()):
        if key == "_other":
            name = "Core"
            confidence = 0.35
            summary = "ungrouped flat-package files (keyword rescue fallback)"
        else:
            name = key.capitalize()
            confidence = 0.50 if key in _HIGH_DOMAIN_TOKENS else 0.40
            summary = f"keyword-rescue grouping for token '{key}'"
        tests = _find_tests_for_files(repo_root, scan, grp_files)
        features.append(FeatureMemory(
            name=name,
            files=sorted(grp_files),
            tests=tests,
            confidence=confidence,
            summary=summary,
        ))

    return sorted(features, key=lambda f: f.name)


# ---------------------------------------------------------------------------
# Cluster naming
# ---------------------------------------------------------------------------


def _name_cluster(cluster_files: list[str], vocabulary: dict[str, list[str]]) -> str:
    """Derive a human-readable feature name for a cluster of files.

    Priority:
    1. Any high-domain-token stem token found in the cluster files.
    2. Any medium-domain-token stem token found in the cluster files.
    3. Any vocabulary symbol that maps cleanly to a domain token.
    4. The most common stem token across all files (ignoring short tokens).
    5. The stem of the first file (last resort).
    """
    all_tokens: list[str] = []
    for f in sorted(cluster_files):
        all_tokens.extend(_stem_tokens(Path(f).stem))

    # 1. High-confidence domain token
    for tok in all_tokens:
        if tok in _HIGH_DOMAIN_TOKENS:
            return tok.capitalize()

    # 2. Medium-confidence domain token
    for tok in all_tokens:
        if tok in _MED_DOMAIN_TOKENS:
            return tok.capitalize()

    # 3. Vocabulary symbol domain hit
    for f in sorted(cluster_files):
        for sym in vocabulary.get(f, []):
            sym_lower = sym.lower()
            toks = _stem_tokens(sym_lower)
            for tok in toks:
                if tok in _HIGH_DOMAIN_TOKENS:
                    return tok.capitalize()

    # 4. Most common token (length > 3)
    long_tokens = [t for t in all_tokens if len(t) > 3]
    if long_tokens:
        most_common, _ = Counter(long_tokens).most_common(1)[0]
        return most_common.capitalize()

    # 5. First file stem
    return Path(cluster_files[0]).stem.capitalize()


def _stem_tokens(stem: str) -> list[str]:
    """Split a file stem into lowercase tokens on underscores and hyphens."""
    return [t.lower() for t in stem.replace("-", "_").split("_") if t]


# ---------------------------------------------------------------------------
# Test discovery (inline — avoids circular import with classifier)
# ---------------------------------------------------------------------------


def _find_tests_for_files(
    repo_root: Path,
    scan: RepoScan,
    source_files: list[str],
) -> list[str]:
    """Return test files likely related to *source_files*.

    Mirrors the logic in ``classifier._find_tests_for`` without importing it.
    """
    if not scan.test_dirs:
        return []

    source_stems = {Path(f).stem.lower() for f in source_files}
    source_tokens: set[str] = set()
    for f in source_files:
        source_tokens.update(p.lower() for p in Path(f).parts)

    tests: list[str] = []
    for test_dir in scan.test_dirs:
        test_path = repo_root / test_dir
        if not test_path.is_dir():
            continue
        for f in test_path.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _EXT_TO_LANG:
                continue
            stem = f.stem.lower()
            clean = stem
            for prefix in _TEST_PREFIXES:
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
                    break
            for suffix in _TEST_SUFFIXES:
                if clean.endswith(suffix):
                    clean = clean[: -len(suffix)]
                    break
            if clean in source_stems or any(tok in clean for tok in source_tokens if len(tok) > 3):
                try:
                    tests.append(str(f.relative_to(repo_root)).replace("\\", "/"))
                except ValueError:
                    pass

    return sorted(set(tests))


# ---------------------------------------------------------------------------
# Graph signal application
# ---------------------------------------------------------------------------


def _apply_graph_signals(features: list[FeatureMemory], repo_root: Path) -> None:
    """Adjust confidence and augment tests using graph ``ClassifierGraphSignals``."""
    if not features:
        return
    groups = {f.name: f.files for f in features}
    try:
        from .graph_bridge import get_all_classifier_signals
        signals = get_all_classifier_signals(groups, repo_root)
        for feat in features:
            sig = signals.get(feat.name)
            if sig is None:
                continue
            delta = sig.confidence_delta(len(feat.files))
            feat.confidence = round(max(0.2, min(0.98, feat.confidence + delta)), 2)
            feat.tests = sorted(set(feat.tests) | set(sig.test_files))
    except Exception as exc:
        logger.debug("flat_rescue: graph signals unavailable: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _merge_features(
    base: list[FeatureMemory],
    extras: list[FeatureMemory],
) -> None:
    """Merge *extras* into *base* in-place.

    If a name already exists in *base*, the files are merged and confidence
    is averaged.  New names are appended.
    """
    name_map = {f.name.lower(): f for f in base}
    for extra in extras:
        key = extra.name.lower()
        if key in name_map:
            existing = name_map[key]
            existing.files = sorted(set(existing.files) | set(extra.files))
            existing.tests = sorted(set(existing.tests) | set(extra.tests))
            existing.confidence = round((existing.confidence + extra.confidence) / 2, 2)
        else:
            base.append(extra)
            name_map[key] = extra
