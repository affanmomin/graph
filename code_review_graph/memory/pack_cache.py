"""Pre-computed pack cache for fast prepare-context lookups.

Written once at ``memory init`` time; read on every ``prepare-context`` call.
Stored at ``.agent-memory/metadata/pack_cache.json`` and committed to Git.

Without the cache, ``prepare-context`` runs scan_repo + classify_features +
classify_modules + graph vocabulary fetch on every call (~9 s).
With the cache, those steps are replaced by a single JSON load (~5 ms).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import FeatureMemory, ModuleMemory

logger = logging.getLogger(__name__)

_CACHE_FILE = "metadata/pack_cache.json"
_CACHE_VERSION = 1


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_pack_cache(
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    vocabulary: dict[str, list[str]],
) -> dict[str, Any]:
    """Build the serialisable cache dict from classified features/modules.

    Args:
        features:   Classified feature list from ``classify_features()``.
        modules:    Classified module list from ``classify_modules()``.
        vocabulary: Graph vocabulary mapping file_path → list[symbol_name].
                    Pass ``{}`` when graph is unavailable.

    Returns:
        Dict ready to pass to :func:`save_pack_cache`.
    """
    return {
        "version": _CACHE_VERSION,
        "features": [
            {
                "name": f.name,
                "files": f.files,
                "tests": f.tests,
                "confidence": f.confidence,
                "summary": f.summary,
                "keywords": _extract_keywords(f.name, f.files, vocabulary),
            }
            for f in features
        ],
        "modules": [
            {
                "name": m.name,
                "files": m.files,
                "tests": m.tests,
                "confidence": m.confidence,
                "summary": m.summary,
                "keywords": _extract_keywords(m.name, m.files, vocabulary),
            }
            for m in modules
        ],
    }


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------


def save_pack_cache(cache: dict[str, Any], memory_root: Path) -> None:
    """Write cache to ``.agent-memory/metadata/pack_cache.json``.

    Args:
        cache:       Dict returned by :func:`build_pack_cache`.
        memory_root: Path to the ``.agent-memory/`` directory.
    """
    path = memory_root / _CACHE_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
        logger.debug("pack_cache: saved to %s", path)
    except Exception as exc:
        logger.warning("pack_cache: failed to save: %s", exc)


def load_pack_cache(memory_root: Path) -> dict[str, Any] | None:
    """Load the pack cache. Returns ``None`` if missing or version mismatch.

    Args:
        memory_root: Path to the ``.agent-memory/`` directory.

    Returns:
        Cache dict or ``None`` when the cache cannot be used.
    """
    path = memory_root / _CACHE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            logger.debug("pack_cache: version mismatch, ignoring")
            return None
        return data
    except Exception as exc:
        logger.debug("pack_cache: load failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Reconstruct models from cache
# ---------------------------------------------------------------------------


def features_from_cache(cache: dict[str, Any]) -> list[FeatureMemory]:
    """Reconstruct ``FeatureMemory`` instances from a loaded cache dict."""
    from .models import FeatureMemory
    return [
        FeatureMemory(
            name=e["name"],
            files=e["files"],
            tests=e["tests"],
            confidence=e["confidence"],
            summary=e.get("summary", ""),
        )
        for e in cache.get("features", [])
    ]


def modules_from_cache(cache: dict[str, Any]) -> list[ModuleMemory]:
    """Reconstruct ``ModuleMemory`` instances from a loaded cache dict."""
    from .models import ModuleMemory
    return [
        ModuleMemory(
            name=e["name"],
            files=e["files"],
            tests=e["tests"],
            confidence=e["confidence"],
            summary=e.get("summary", ""),
        )
        for e in cache.get("modules", [])
    ]


def keywords_from_cache(cache: dict[str, Any]) -> dict[str, set[str]]:
    """Return a name → keyword-set mapping for fast scoring lookups."""
    return {
        e["name"]: set(e.get("keywords", []))
        for e in [*cache.get("features", []), *cache.get("modules", [])]
    }


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------


def _extract_keywords(
    name: str,
    files: list[str],
    vocabulary: dict[str, list[str]],
) -> list[str]:
    """Extract all matchable terms for a feature/module.

    Combines:
    - name tokens (split on delimiters + camelCase)
    - file stem tokens (camelCase-split)
    - directory component tokens
    - symbol names from graph vocabulary (camelCase-split)

    Returns a sorted, deduplicated list of lowercase strings (length >= 3).
    """
    terms: set[str] = set()

    # name tokens
    for tok in _split_camel(name):
        if len(tok) >= 3:
            terms.add(tok)

    # file stem + directory tokens
    for f in files:
        p = Path(f)
        for tok in _split_camel(p.stem):
            if len(tok) >= 3:
                terms.add(tok)
        for part in p.parts[:-1]:
            for tok in _split_camel(part):
                if len(tok) >= 3:
                    terms.add(tok)

    # symbol names from graph vocabulary
    for f in files:
        for sym in vocabulary.get(f, []):
            for tok in _split_camel(sym):
                if len(tok) >= 3:
                    terms.add(tok)

    return sorted(terms)


def _split_camel(text: str) -> list[str]:
    """Split a string on delimiters and camelCase boundaries.

    Examples:
        "ContactForm"  → ["contact", "form"]
        "getUserById"  → ["get", "user", "by", "id"]
        "auth-service" → ["auth", "service"]
    """
    # Insert space before uppercase letters that follow lowercase letters
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Insert space before sequences like "HTMLParser" → "HTML Parser"
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    # Split on all non-alphanumeric characters
    return [t.lower() for t in re.split(r"[\W_]+", spaced) if t]
