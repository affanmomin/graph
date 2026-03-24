"""Metadata management for ``.agent-memory/`` artifacts.

Responsible for generating and writing the manifest and supporting metadata
files that track freshness, confidence, and source traceability.

Public API (this ticket)
------------------------
generate_manifest(scan, artifacts)  -> dict   (JSON-serialisable manifest)
save_manifest(manifest_dict, dirs)  -> WriteStatus
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
