"""Disk writer for ``.agent-memory/`` artifacts.

This is the only module in the memory subsystem that writes to disk.
Generators produce content strings; this module decides whether and how to
persist them.

Public API
----------
ensure_memory_dirs(repo_root)           -- create .agent-memory/ + all subdirs
write_text_if_changed(path, content)    -- write markdown/text, skip if unchanged
write_json_if_changed(path, data)       -- write JSON, sorted keys, skip if unchanged
render_markdown_section(title, body)    -- format a titled markdown section

All write functions return a :class:`WriteStatus` literal:
    "created"   -- file did not exist; was written
    "updated"   -- file existed with different content; was rewritten
    "unchanged" -- file existed with identical content; no write performed

Design invariants
-----------------
- Writes are atomic: content is written to a sibling ``.tmp`` file then
  renamed, so a crash never leaves a partial artifact.
- Content comparison is done on the encoded bytes so encoding edge-cases
  (BOM, CRLF) never cause spurious rewrites.
- JSON output uses ``sort_keys=True`` and a stable indent so the same data
  always produces the same bytes — no Git diff noise.
- Every text file ends with exactly one newline (Unix convention).
- Override files in ``overrides/`` are never auto-overwritten; callers must
  use :func:`write_override_if_absent` for that directory.
- No temporary files are left behind on success or failure.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

WriteStatus = Literal["created", "updated", "unchanged"]

# Subdirectories that every .agent-memory/ folder must contain.
MEMORY_SUBDIRS: tuple[str, ...] = (
    "features",
    "modules",
    "tasks",
    "changes",
    "rules",
    "overrides",
    "metadata",
)


# ---------------------------------------------------------------------------
# Directory management
# ---------------------------------------------------------------------------


def ensure_memory_dirs(repo_root: Path) -> dict[str, Path]:
    """Create ``.agent-memory/`` and all required subdirectories.

    Idempotent — safe to call on a repo that already has the folder tree.
    Does not touch any existing files.

    Args:
        repo_root: Absolute path to the repository root.

    Returns:
        A dict mapping each directory key (``"root"``, ``"features"``, etc.)
        to its absolute :class:`~pathlib.Path`.

    Example::

        dirs = ensure_memory_dirs(Path("/my/repo"))
        # dirs["root"]     -> /my/repo/.agent-memory
        # dirs["features"] -> /my/repo/.agent-memory/features
    """
    root = repo_root / ".agent-memory"
    root.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {"root": root}
    for name in MEMORY_SUBDIRS:
        sub = root / name
        sub.mkdir(exist_ok=True)
        paths[name] = sub

    logger.debug("ensured .agent-memory/ dirs under %s", repo_root)
    return paths


# ---------------------------------------------------------------------------
# Text / markdown writer
# ---------------------------------------------------------------------------


def write_text_if_changed(path: Path, content: str) -> WriteStatus:
    """Write *content* to *path* only if the file is new or has changed.

    Ensures:
    - the file ends with exactly one trailing newline
    - the write is atomic (temp-file + rename)
    - the parent directory exists

    Args:
        path:    Destination path (absolute).
        content: Text content to write.

    Returns:
        ``"created"`` / ``"updated"`` / ``"unchanged"``.
    """
    normalised = _ensure_trailing_newline(content)
    encoded = normalised.encode("utf-8")

    if path.exists():
        existing = path.read_bytes()
        if existing == encoded:
            return "unchanged"
        status: WriteStatus = "updated"
    else:
        status = "created"

    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, encoded)
    logger.debug("%s %s", status, path)
    return status


# ---------------------------------------------------------------------------
# JSON writer
# ---------------------------------------------------------------------------


def write_json_if_changed(path: Path, data: dict) -> WriteStatus:
    """Serialise *data* to *path* as pretty-printed JSON only if changed.

    Ensures:
    - keys are sorted at every nesting level (stable Git diffs)
    - 2-space indent
    - trailing newline
    - atomic write

    Args:
        path: Destination path (absolute).
        data: JSON-serialisable dict.

    Returns:
        ``"created"`` / ``"updated"`` / ``"unchanged"``.
    """
    serialised = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False)
    return write_text_if_changed(path, serialised)


# ---------------------------------------------------------------------------
# Override-safe writer
# ---------------------------------------------------------------------------


def write_override_if_absent(path: Path, content: str) -> WriteStatus:
    """Write *content* to *path* only if the file does not already exist.

    Override files are human-authored. Once created they must never be
    auto-overwritten by the memory system. Use this function for any file
    under ``.agent-memory/overrides/``.

    Args:
        path:    Destination path (absolute).
        content: Default content to write on first creation.

    Returns:
        ``"created"`` if the file was written, ``"unchanged"`` if it already
        existed (regardless of content differences).
    """
    if path.exists():
        return "unchanged"
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, _ensure_trailing_newline(content).encode("utf-8"))
    logger.debug("created override scaffold %s", path)
    return "created"


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------


def render_markdown_section(title: str, body: str, level: int = 2) -> str:
    """Return a formatted markdown section string.

    Produces a heading at *level* (default ``##``) followed by *body*.
    Strips leading/trailing whitespace from *body* and ensures a blank line
    between the heading and the body text.

    Args:
        title: Section heading text (no leading ``#`` characters needed).
        body:  Section body text (may be multi-line).
        level: Heading level 1–6 (default 2).

    Returns:
        A markdown string ready for concatenation into an artifact.

    Example::

        render_markdown_section("Overview", "This module handles auth.")
        # "## Overview\\n\\nThis module handles auth."
    """
    if not 1 <= level <= 6:
        raise ValueError(f"Heading level must be 1–6, got {level}")
    hashes = "#" * level
    return f"{hashes} {title}\n\n{body.strip()}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_trailing_newline(text: str) -> str:
    """Return *text* with exactly one trailing newline."""
    return text.rstrip("\n") + "\n"


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically via a sibling temp file.

    The temp file is placed in the same directory as *path* so that
    ``os.replace`` can always perform an atomic rename (same filesystem).
    The temp file is removed on failure so no partial files are left behind.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
