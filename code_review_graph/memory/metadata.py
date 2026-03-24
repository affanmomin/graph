"""Metadata management for ``.agent-memory/`` artifacts.

Responsible for generating and writing the manifest and supporting metadata
files that track freshness, confidence, and source traceability.

Public API
----------
generate_manifest(scan, artifacts)              -> dict   (JSON-serialisable manifest)
save_manifest(manifest_dict, metadata_dir)      -> WriteStatus
save_sources_json(features, modules, dir)       -> WriteStatus
save_confidence_json(features, modules, dir)    -> WriteStatus
save_freshness_json(freshness_data, dir)        -> WriteStatus

load_freshness_json(metadata_dir)               -> dict | None
load_sources_json(metadata_dir)                 -> dict | None
load_confidence_json(metadata_dir)              -> dict | None
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import FeatureMemory, ModuleMemory
from .scanner import RepoScan
from .writer import WriteStatus, write_json_if_changed

logger = logging.getLogger(__name__)

MANIFEST_VERSION = "1"


def generate_manifest(
    scan: RepoScan,
    artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    """Build the manifest dict for ``.agent-memory/metadata/manifest.json``.

    Args:
        scan:      The completed :class:`~scanner.RepoScan` for this repo.
        artifacts: List of ``{"artifact_id": ..., "relative_path": ...,
                   "artifact_type": ...}`` dicts describing generated files.

    Returns:
        JSON-serialisable dict suitable for passing to :func:`save_manifest`.
    """
    return {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "repo_root": str(scan.repo_root),
        "source_roots": sorted(scan.source_dirs),
        "discovered_languages": sorted(scan.languages),
        "discovered_docs_dirs": sorted(scan.docs_dirs),
        "discovered_test_dirs": sorted(scan.test_dirs),
        "config_files": sorted(scan.config_files),
        "framework_hints": sorted(scan.framework_hints),
        "scan_confidence": scan.confidence,
        "generated_artifacts": sorted(artifacts, key=lambda a: a.get("relative_path", "")),
    }


def save_manifest(manifest: dict[str, Any], metadata_dir: Path) -> WriteStatus:
    """Write *manifest* to ``<metadata_dir>/manifest.json``.

    Args:
        manifest:     Dict produced by :func:`generate_manifest`.
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        :data:`~writer.WriteStatus` — ``"created"``, ``"updated"``, or
        ``"unchanged"``.
    """
    path = metadata_dir / "manifest.json"
    status = write_json_if_changed(path, manifest)
    logger.debug("%s manifest.json", status)
    return status


def save_sources_json(
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    metadata_dir: Path,
) -> WriteStatus:
    """Write ``sources.json`` — maps every tracked file to its feature/module.

    Args:
        features:     Classified feature list.
        modules:      Classified module list.
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        :data:`~writer.WriteStatus`.
    """
    # Build file → [feature/module names] index
    index: dict[str, list[str]] = {}

    for feature in sorted(features, key=lambda f: f.name):
        for file_path in feature.files:
            index.setdefault(file_path, []).append(f"feature:{feature.name}")

    for module in sorted(modules, key=lambda m: m.name):
        for file_path in module.files:
            index.setdefault(file_path, []).append(f"module:{module.name}")

    data = {
        "file_count": len(index),
        "sources": {k: sorted(v) for k, v in sorted(index.items())},
    }

    path = metadata_dir / "sources.json"
    status = write_json_if_changed(path, data)
    logger.debug("%s sources.json (%d files indexed)", status, len(index))
    return status


def save_freshness_json(
    freshness_data: dict[str, Any],
    metadata_dir: Path,
) -> WriteStatus:
    """Write ``freshness.json`` — tracks when and how memory was last refreshed.

    Args:
        freshness_data: Dict produced by
                        :func:`~refresh._generate_freshness_data`.
        metadata_dir:   Absolute path to ``.agent-memory/metadata/``.

    Returns:
        :data:`~writer.WriteStatus`.
    """
    path = metadata_dir / "freshness.json"
    status = write_json_if_changed(path, freshness_data)
    logger.debug("%s freshness.json", status)
    return status


def save_confidence_json(
    features: list[FeatureMemory],
    modules: list[ModuleMemory],
    metadata_dir: Path,
) -> WriteStatus:
    """Write ``confidence.json`` — confidence scores for every artifact.

    Args:
        features:     Classified feature list.
        modules:      Classified module list.
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        :data:`~writer.WriteStatus`.
    """
    feature_entries = [
        {
            "name": f.name,
            "slug": f.slug(),
            "type": "feature",
            "confidence": round(f.confidence, 4),
            "file_count": len(f.files),
            "test_count": len(f.tests),
        }
        for f in sorted(features, key=lambda f: f.name)
    ]

    module_entries = [
        {
            "name": m.name,
            "slug": m.slug(),
            "type": "module",
            "confidence": round(m.confidence, 4),
            "file_count": len(m.files),
            "test_count": len(m.tests),
        }
        for m in sorted(modules, key=lambda m: m.name)
    ]

    data = {
        "features": feature_entries,
        "modules": module_entries,
    }

    path = metadata_dir / "confidence.json"
    status = write_json_if_changed(path, data)
    logger.debug("%s confidence.json (%d features, %d modules)", status, len(features), len(modules))
    return status


# ---------------------------------------------------------------------------
# Loaders — read existing metadata from disk
# ---------------------------------------------------------------------------


def load_freshness_json(metadata_dir: Path) -> dict[str, Any] | None:
    """Load ``freshness.json`` from *metadata_dir*, or return ``None`` if absent.

    Args:
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        Parsed dict, or ``None`` if the file does not exist or cannot be parsed.
    """
    return _load_json(metadata_dir / "freshness.json")


def load_sources_json(metadata_dir: Path) -> dict[str, Any] | None:
    """Load ``sources.json`` (file → feature/module index) from *metadata_dir*.

    Args:
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        Parsed dict with a ``"sources"`` key, or ``None`` if absent.
    """
    return _load_json(metadata_dir / "sources.json")


def load_confidence_json(metadata_dir: Path) -> dict[str, Any] | None:
    """Load ``confidence.json`` (per-artifact confidence scores) from *metadata_dir*.

    Args:
        metadata_dir: Absolute path to ``.agent-memory/metadata/``.

    Returns:
        Parsed dict with ``"features"`` and ``"modules"`` keys, or ``None``.
    """
    return _load_json(metadata_dir / "confidence.json")


def _load_json(path: Path) -> dict[str, Any] | None:
    """Read and parse a JSON file; return ``None`` on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load %s: %s", path.name, exc)
        return None
