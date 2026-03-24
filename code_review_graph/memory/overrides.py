"""Human override loader and applicator.

Allows developers to correct and guide generated memory — making the system
more trustworthy over time without requiring a full re-scan.

Override files live under ``.agent-memory/overrides/`` as YAML files and are
committed to Git so they are shared across teammates and machines.

Override file schema
--------------------

    # .agent-memory/overrides/global.yaml
    always_include:
      - src/auth/middleware.py     # always surface in context packs
      - docs/architecture.md

    never_edit:
      - src/vendor/                # never suggest changes here
      - migrations/                # treat as append-only

    notes:
      - "The auth module uses a custom JWT library, not python-jose."
      - "Database migrations are managed by the infra team, not app devs."

    task_hints:
      - pattern: "add endpoint"
        hint: "New endpoints go in src/api/routes/. Register in src/api/__init__.py."
      - pattern: "fix auth"
        hint: "Check src/auth/middleware.py first. JWT secret is env-var only."

File loading order
------------------
1. ``global.yaml`` — repo-wide rules (loaded first)
2. Area-specific files (``auth.yaml``, ``billing.yaml``, …) sorted alphabetically

Merge behaviour
---------------
- ``always_include`` / ``never_edit`` / ``notes``: concatenated in load order,
  duplicates removed (first occurrence wins).
- ``task_hints``: concatenated in load order, deduped on ``(pattern, hint)``.
- Human entries are never overwritten or dropped.

Public API
----------
load_overrides(agent_memory_root)           -> Overrides
apply_overrides(pack, overrides)            -> TaskContextPack
task_hint_match(task, overrides)            -> list[str]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TaskHint:
    """A pattern-triggered hint injected into task context packs.

    Attributes:
        pattern: Keyword or phrase to match against the task description.
                 Matching is case-insensitive token overlap.
        hint:    The guidance string to surface when the pattern matches.
    """

    pattern: str
    hint: str


@dataclass
class Overrides:
    """Merged override rules loaded from ``.agent-memory/overrides/``.

    Attributes:
        always_include: Repo-relative paths always surfaced in context packs,
                        regardless of relevance scoring.
        never_edit:     Repo-relative paths or patterns that must never be
                        suggested for editing. Surfaced as warnings.
        notes:          Free-text domain knowledge injected into context packs
                        and rule documents.
        task_hints:     Pattern-matched hints for specific task types.
        source_files:   Override YAML files that contributed to this instance.
    """

    always_include: list[str] = field(default_factory=list)
    never_edit: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    task_hints: list[TaskHint] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> Overrides:
        """Return an empty Overrides instance (no override files present)."""
        return cls()

    def is_empty(self) -> bool:
        """Return True if no override rules are defined."""
        return not any([self.always_include, self.never_edit, self.notes, self.task_hints])


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_overrides(agent_memory_root: Path) -> Overrides:
    """Load and merge all override YAML files from ``<agent_memory_root>/overrides/``.

    ``global.yaml`` is loaded first; remaining files follow in alphabetical order.
    Invalid or unreadable YAML files are skipped with a warning — they never
    prevent the system from running.

    Args:
        agent_memory_root: Absolute path to the ``.agent-memory/`` directory.

    Returns:
        A merged :class:`Overrides` instance.  Returns :meth:`Overrides.empty`
        if the ``overrides/`` directory does not exist or contains no YAML.
    """
    overrides_dir = agent_memory_root / "overrides"
    if not overrides_dir.is_dir():
        return Overrides.empty()

    yaml_files = sorted(overrides_dir.glob("*.yaml"))
    if not yaml_files:
        return Overrides.empty()

    # global.yaml always comes first
    yaml_files = sorted(
        yaml_files,
        key=lambda p: ("" if p.name == "global.yaml" else p.name),
    )

    merged = Overrides()
    seen_always: set[str] = set()
    seen_never: set[str] = set()
    seen_notes: set[str] = set()
    seen_hints: set[tuple[str, str]] = set()

    for yaml_path in yaml_files:
        raw = _load_yaml_file(yaml_path)
        if raw is None:
            continue

        rel_name = yaml_path.name
        merged.source_files.append(rel_name)

        # always_include
        for entry in _coerce_string_list(raw.get("always_include"), yaml_path):
            if entry not in seen_always:
                merged.always_include.append(entry)
                seen_always.add(entry)

        # never_edit
        for entry in _coerce_string_list(raw.get("never_edit"), yaml_path):
            if entry not in seen_never:
                merged.never_edit.append(entry)
                seen_never.add(entry)

        # notes
        for entry in _coerce_string_list(raw.get("notes"), yaml_path):
            if entry not in seen_notes:
                merged.notes.append(entry)
                seen_notes.add(entry)

        # task_hints
        for raw_hint in _coerce_hint_list(raw.get("task_hints"), yaml_path):
            key = (raw_hint.pattern.lower().strip(), raw_hint.hint.strip())
            if key not in seen_hints:
                merged.task_hints.append(raw_hint)
                seen_hints.add(key)

    return merged


# ---------------------------------------------------------------------------
# Applying overrides to a context pack
# ---------------------------------------------------------------------------


def apply_overrides(pack: Any, overrides: Overrides) -> Any:
    """Apply *overrides* to a :class:`~models.TaskContextPack`.

    Modifies the pack in place (returns new lists — does not mutate input).

    Rules applied:
    1. ``always_include`` files are prepended to ``relevant_files``.
    2. ``never_edit`` paths each add a "Never edit" warning.
    3. Matched ``task_hints`` are appended to ``warnings``.
    4. Human ``notes`` are appended to ``warnings`` (at most 3 to stay concise).

    Args:
        pack:      A :class:`~models.TaskContextPack` instance.
        overrides: Merged :class:`Overrides` to apply.

    Returns:
        The same *pack* object with updated fields.
    """
    if overrides.is_empty():
        return pack

    # 1. Prepend always_include files (deduplicated)
    existing = set(pack.relevant_files)
    prepend = [f for f in overrides.always_include if f not in existing]
    pack.relevant_files = prepend + pack.relevant_files

    # 2. never_edit warnings
    never_warnings = [
        f"Never edit: `{path}` (marked in overrides)"
        for path in overrides.never_edit
    ]

    # 3. Task hints that match the current task
    hints = task_hint_match(pack.task, overrides)
    hint_warnings = [f"Hint: {h}" for h in hints]

    # 4. Human notes (cap to 3 to keep pack concise)
    note_warnings = [f"Note: {n}" for n in overrides.notes[:3]]

    # Append override-sourced warnings, avoiding duplicates
    existing_warnings = set(pack.warnings)
    for w in never_warnings + hint_warnings + note_warnings:
        if w not in existing_warnings:
            pack.warnings.append(w)
            existing_warnings.add(w)

    return pack


# ---------------------------------------------------------------------------
# Task hint matching
# ---------------------------------------------------------------------------


def task_hint_match(task: str, overrides: Overrides) -> list[str]:
    """Return hints whose patterns match the given task description.

    Matching is case-insensitive token overlap: a hint matches when every word
    in its ``pattern`` appears somewhere in the task text.

    Args:
        task:      Natural-language task description.
        overrides: Loaded :class:`Overrides` instance.

    Returns:
        List of matching hint strings, in load order.
    """
    if not overrides.task_hints:
        return []

    task_lower = task.lower()
    task_tokens = set(re.split(r"[\s\-_./]+", task_lower))

    matched: list[str] = []
    seen: set[str] = set()

    for th in overrides.task_hints:
        pattern_tokens = set(re.split(r"[\s\-_./]+", th.pattern.lower()))
        # All pattern tokens must appear in task (substring or token match)
        if all(
            any(pt in tt for tt in task_tokens) or pt in task_lower
            for pt in pattern_tokens
            if pt
        ):
            if th.hint not in seen:
                matched.append(th.hint)
                seen.add(th.hint)

    return matched


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_yaml_file(path: Path) -> dict[str, Any] | None:
    """Load and parse a YAML file; return None on any error."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "PyYAML not available — override file %s will be skipped. "
            "Install pyyaml to enable overrides.",
            path.name,
        )
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, PermissionError) as exc:
        logger.warning("Could not read override file %s: %s", path.name, exc)
        return None

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("Invalid YAML in override file %s: %s", path.name, exc)
        return None

    if data is None:
        return {}  # empty file is valid

    if not isinstance(data, dict):
        logger.warning(
            "Override file %s must be a YAML mapping (dict), got %s — skipping.",
            path.name,
            type(data).__name__,
        )
        return None

    return data


def _coerce_string_list(value: Any, source: Path) -> list[str]:
    """Return a list of strings from *value*, logging and skipping bad items."""
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning(
            "Expected a list in %s, got %s — skipping field.",
            source.name,
            type(value).__name__,
        )
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        else:
            logger.debug("Skipping non-string item %r in %s", item, source.name)
    return result


def _coerce_hint_list(value: Any, source: Path) -> list[TaskHint]:
    """Parse the ``task_hints`` list from raw YAML data."""
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning(
            "Expected a list for task_hints in %s, got %s — skipping.",
            source.name,
            type(value).__name__,
        )
        return []
    hints: list[TaskHint] = []
    for item in value:
        if not isinstance(item, dict):
            logger.debug("Skipping non-dict task_hint %r in %s", item, source.name)
            continue
        pattern = item.get("pattern", "")
        hint = item.get("hint", "")
        if not isinstance(pattern, str) or not pattern.strip():
            logger.debug("task_hint missing 'pattern' in %s: %r", source.name, item)
            continue
        if not isinstance(hint, str) or not hint.strip():
            logger.debug("task_hint missing 'hint' in %s: %r", source.name, item)
            continue
        hints.append(TaskHint(pattern=pattern.strip(), hint=hint.strip()))
    return hints
