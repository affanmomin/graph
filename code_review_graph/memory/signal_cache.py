"""Signal cache — avoids re-querying graph.db on every ``memory init`` run.

The cache stores the expensive derived signals computed from the graph:
vocabulary, node summaries, call-graph signals, structural-depth signals, and
hotspot nodes.  It is keyed by a hash that changes whenever graph.db is
modified or the set of source files changes.

Cache file  ``<repo_root>/.code-review-graph/signal_cache.json``
            Local-only: NOT committed.  Add to ``.gitignore`` along with the
            rest of ``.code-review-graph/``.

Public API
----------
compute_cache_key(db_path, all_files) -> str
load_signal_cache(repo_root) -> CachedSignals | None
save_signal_cache(repo_root, key, vocabulary, node_summaries, call_signals_map,
                  structural_signals_map, hotspot_nodes) -> None
deserialize_node_summaries(raw) -> dict[str, FileNodeSummary]
deserialize_call_signals_map(raw) -> dict[str, CallGraphSignals]
deserialize_structural_signals_map(raw) -> dict[str, StructuralDepthSignals]
deserialize_hotspot_nodes(raw) -> list[HotspotNode]
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .graph_bridge import (
        CallGraphSignals,
        FileNodeSummary,
        HotspotNode,
        StructuralDepthSignals,
    )

logger = logging.getLogger(__name__)

_CACHE_SUBDIR = ".code-review-graph"
_CACHE_FILENAME = "signal_cache.json"
_CACHE_VERSION = "1"


# ---------------------------------------------------------------------------
# CachedSignals dataclass
# ---------------------------------------------------------------------------


@dataclass
class CachedSignals:
    """All derived graph signals, loaded from cache.

    Attributes:
        cache_key:               SHA256 key the cache was saved under.
        vocabulary:              file_path → list[symbol_name].
        node_summaries:          file_path → FileNodeSummary.
        call_signals_map:        group_name → CallGraphSignals.
        structural_signals_map:  group_name → StructuralDepthSignals.
        hotspot_nodes:           Global hotspot list.
    """

    cache_key: str
    vocabulary: dict[str, list[str]] = field(default_factory=dict)
    node_summaries: dict = field(default_factory=dict)       # str -> FileNodeSummary
    call_signals_map: dict = field(default_factory=dict)     # str -> CallGraphSignals
    structural_signals_map: dict = field(default_factory=dict)  # str -> StructuralDepthSignals
    hotspot_nodes: list = field(default_factory=list)        # list[HotspotNode]


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------


def compute_cache_key(db_path: Path, all_files: list[str]) -> str:
    """Compute a deterministic cache key for the given graph.db and file list.

    The key changes whenever:
    - graph.db is modified (mtime changes), or
    - the set of source files being cached changes (different files or order).

    Args:
        db_path:   Absolute path to graph.db.
        all_files: Sorted list of repo-relative file paths being cached.

    Returns:
        A 64-character hex SHA256 digest.
    """
    try:
        mtime = os.path.getmtime(str(db_path))
    except OSError:
        mtime = 0.0

    sorted_files = sorted(all_files)
    payload = f"{mtime:.6f}|{','.join(sorted_files)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def _cache_path(repo_root: Path) -> Path:
    return repo_root / _CACHE_SUBDIR / _CACHE_FILENAME


def load_signal_cache(repo_root: Path) -> "CachedSignals | None":
    """Load the signal cache from disk.

    Returns ``None`` on any failure (missing file, corrupt JSON, version
    mismatch, or unexpected structure).  The caller should fall back to
    live graph queries in that case.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        :class:`CachedSignals` instance, or ``None`` on cache miss.
    """
    p = _cache_path(repo_root)
    if not p.exists():
        return None
    try:
        raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("signal_cache: could not read cache file: %s", exc)
        return None

    if not isinstance(raw, dict):
        return None
    if raw.get("version") != _CACHE_VERSION:
        logger.debug("signal_cache: version mismatch, discarding")
        return None
    key = raw.get("cache_key", "")
    if not isinstance(key, str) or not key:
        return None

    try:
        return CachedSignals(
            cache_key=key,
            vocabulary=raw.get("vocabulary") or {},
            node_summaries=deserialize_node_summaries(raw.get("node_summaries") or {}),
            call_signals_map=deserialize_call_signals_map(raw.get("call_signals_map") or {}),
            structural_signals_map=deserialize_structural_signals_map(
                raw.get("structural_signals_map") or {}
            ),
            hotspot_nodes=deserialize_hotspot_nodes(raw.get("hotspot_nodes") or []),
        )
    except Exception as exc:
        logger.debug("signal_cache: deserialization failed: %s", exc)
        return None


def save_signal_cache(
    repo_root: Path,
    cache_key: str,
    vocabulary: dict[str, list[str]],
    node_summaries: dict,   # dict[str, FileNodeSummary]
    call_signals_map: dict,  # dict[str, CallGraphSignals]
    structural_signals_map: dict,  # dict[str, StructuralDepthSignals]
    hotspot_nodes: list,    # list[HotspotNode]
) -> None:
    """Persist signals to the cache file.

    Silently swallows all errors — a failed cache write must never crash
    the init pipeline.

    Args:
        repo_root:               Repository root.
        cache_key:               Key produced by :func:`compute_cache_key`.
        vocabulary:              file_path → symbol names.
        node_summaries:          file_path → FileNodeSummary.
        call_signals_map:        group_name → CallGraphSignals.
        structural_signals_map:  group_name → StructuralDepthSignals.
        hotspot_nodes:           list[HotspotNode].
    """
    p = _cache_path(repo_root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "version": _CACHE_VERSION,
            "cache_key": cache_key,
            "vocabulary": vocabulary,
            "node_summaries": _serialize_node_summaries(node_summaries),
            "call_signals_map": _serialize_call_signals_map(call_signals_map),
            "structural_signals_map": _serialize_structural_signals_map(structural_signals_map),
            "hotspot_nodes": _serialize_hotspot_nodes(hotspot_nodes),
        }
        p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        logger.debug("signal_cache: saved to %s", p)
    except Exception as exc:
        logger.debug("signal_cache: failed to save: %s", exc)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_node_summaries(summaries: dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fp, ns in summaries.items():
        result[fp] = {
            "classes": list(ns.classes),
            "functions": list(ns.functions),
            "total_nodes": int(ns.total_nodes),
        }
    return result


def _serialize_call_signals_map(call_map: dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, sig in call_map.items():
        result[name] = {
            "entry_points": list(sig.entry_points),
            "key_helpers": list(sig.key_helpers),
            "entry_point_symbols": list(sig.entry_point_symbols),
        }
    return result


def _serialize_structural_signals_map(struct_map: dict) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, sig in struct_map.items():
        result[name] = {
            "inheritance_pairs": [list(pair) for pair in sig.inheritance_pairs],
            "coupling_files": list(sig.coupling_files),
            "coupling_score": float(sig.coupling_score),
        }
    return result


def _serialize_hotspot_nodes(nodes: list) -> list[dict[str, Any]]:
    return [
        {
            "name": h.name,
            "file_path": h.file_path,
            "kind": h.kind,
            "line_count": int(h.line_count),
        }
        for h in nodes
    ]


# ---------------------------------------------------------------------------
# Deserialization helpers
# ---------------------------------------------------------------------------


def deserialize_node_summaries(raw: dict[str, Any]) -> "dict[str, FileNodeSummary]":
    """Reconstruct FileNodeSummary objects from serialized dict.

    Args:
        raw: Dict produced by :func:`_serialize_node_summaries`.

    Returns:
        Dict mapping file_path → :class:`~graph_bridge.FileNodeSummary`.
    """
    if not raw:
        return {}
    try:
        from .graph_bridge import FileNodeSummary
        return {
            fp: FileNodeSummary(
                classes=list(v.get("classes") or []),
                functions=list(v.get("functions") or []),
                total_nodes=int(v.get("total_nodes") or 0),
            )
            for fp, v in raw.items()
            if isinstance(v, dict)
        }
    except Exception as exc:
        logger.debug("signal_cache: node_summaries deserialization error: %s", exc)
        return {}


def deserialize_call_signals_map(raw: dict[str, Any]) -> "dict[str, CallGraphSignals]":
    """Reconstruct CallGraphSignals objects from serialized dict.

    Args:
        raw: Dict produced by :func:`_serialize_call_signals_map`.

    Returns:
        Dict mapping group_name → :class:`~graph_bridge.CallGraphSignals`.
    """
    if not raw:
        return {}
    try:
        from .graph_bridge import CallGraphSignals
        return {
            name: CallGraphSignals(
                entry_points=list(v.get("entry_points") or []),
                key_helpers=list(v.get("key_helpers") or []),
                entry_point_symbols=list(v.get("entry_point_symbols") or []),
            )
            for name, v in raw.items()
            if isinstance(v, dict)
        }
    except Exception as exc:
        logger.debug("signal_cache: call_signals_map deserialization error: %s", exc)
        return {}


def deserialize_structural_signals_map(
    raw: dict[str, Any],
) -> "dict[str, StructuralDepthSignals]":
    """Reconstruct StructuralDepthSignals objects from serialized dict.

    Args:
        raw: Dict produced by :func:`_serialize_structural_signals_map`.

    Returns:
        Dict mapping group_name → :class:`~graph_bridge.StructuralDepthSignals`.
    """
    if not raw:
        return {}
    try:
        from .graph_bridge import StructuralDepthSignals
        return {
            name: StructuralDepthSignals(
                inheritance_pairs=[tuple(p) for p in (v.get("inheritance_pairs") or [])],
                coupling_files=list(v.get("coupling_files") or []),
                coupling_score=float(v.get("coupling_score") or 0.0),
            )
            for name, v in raw.items()
            if isinstance(v, dict)
        }
    except Exception as exc:
        logger.debug("signal_cache: structural_signals_map deserialization error: %s", exc)
        return {}


def deserialize_hotspot_nodes(raw: list[Any]) -> "list[HotspotNode]":
    """Reconstruct HotspotNode objects from serialized list.

    Args:
        raw: List produced by :func:`_serialize_hotspot_nodes`.

    Returns:
        List of :class:`~graph_bridge.HotspotNode`.
    """
    if not raw:
        return []
    try:
        from .graph_bridge import HotspotNode
        return [
            HotspotNode(
                name=str(item.get("name") or ""),
                file_path=str(item.get("file_path") or ""),
                kind=str(item.get("kind") or ""),
                line_count=int(item.get("line_count") or 0),
            )
            for item in raw
            if isinstance(item, dict)
        ]
    except Exception as exc:
        logger.debug("signal_cache: hotspot_nodes deserialization error: %s", exc)
        return []
