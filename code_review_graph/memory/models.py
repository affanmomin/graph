"""Core data models for the repo-memory subsystem.

All persistent memory artifacts and in-memory transfer objects are defined here.
These models are used throughout the memory package and are designed to be:

- serialisable to/from JSON for metadata files
- suitable for rendering into markdown memory artifacts
- lightweight enough to pass through MCP tool responses
- extensible without breaking existing callers

Design notes:
- Use ``field(default_factory=...)`` for all mutable defaults (lists, dicts).
- ``generated_at`` and ``stale`` fields carry freshness / trust signals.
- ``confidence`` is a float in [0.0, 1.0]; 1.0 = fully grounded, 0.0 = speculative.
- ``source_files`` always contains repo-relative paths, never absolute paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _now_utc() -> datetime:
    """Return the current UTC datetime (used as a safe default factory)."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# ArtifactMetadata
# ---------------------------------------------------------------------------


@dataclass
class ArtifactMetadata:
    """Freshness and traceability metadata attached to every memory artifact.

    Stored in ``.agent-memory/metadata/`` as part of ``freshness.json``,
    ``confidence.json``, and ``sources.json``.

    Attributes:
        artifact_path: Repo-relative path to the artifact file
                       (e.g. ``".agent-memory/features/auth.md"``).
        generated_at:  UTC datetime when this artifact was last (re)generated.
        source_files:  Repo-relative paths of source files that contributed
                       to this artifact's content.
        confidence:    Float in [0.0, 1.0]. Reflects how well-grounded the
                       artifact is (source coverage, doc availability, etc.).
        stale:         True when source files have changed since last generation.
    """

    artifact_path: str
    generated_at: datetime = field(default_factory=_now_utc)
    source_files: list[str] = field(default_factory=list)
    confidence: float = 1.0
    stale: bool = False

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return {
            "artifact_path": self.artifact_path,
            "generated_at": self.generated_at.isoformat(),
            "source_files": sorted(self.source_files),
            "confidence": self.confidence,
            "stale": self.stale,
        }


# ---------------------------------------------------------------------------
# MemoryArtifact
# ---------------------------------------------------------------------------


@dataclass
class MemoryArtifact:
    """A single generated memory artifact written to ``.agent-memory/``.

    Represents any artifact type (repo summary, feature doc, module doc, etc.).
    The ``artifact_type`` field identifies which generator produced it and
    determines the subdirectory it lives in.

    Attributes:
        artifact_id:   Stable identifier (slug), e.g. ``"feature-auth"``.
        artifact_type: One of ``"repo"``, ``"architecture"``, ``"feature"``,
                       ``"module"``, ``"task"``, ``"changes"``, ``"rules"``.
        relative_path: Repo-relative output path, e.g.
                       ``".agent-memory/features/auth.md"``.
        title:         Human-readable title used as the markdown H1.
        source_files:  Repo-relative source paths that informed this artifact.
        generated_at:  UTC datetime of last generation.
    """

    artifact_id: str
    artifact_type: str
    relative_path: str
    title: str
    source_files: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=_now_utc)

    # TODO(memory): add ``content: str`` once generator.py is implemented.
    # TODO(memory): add ``metadata: ArtifactMetadata`` linkage.

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "relative_path": self.relative_path,
            "title": self.title,
            "source_files": sorted(self.source_files),
            "generated_at": self.generated_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# FeatureMemory
# ---------------------------------------------------------------------------


@dataclass
class FeatureMemory:
    """Memory artifact describing a single product feature.

    Produced by ``classifier.py`` (grouping) and ``generator.py`` (content).
    Written to ``.agent-memory/features/<slug>.md``.

    Attributes:
        name:         Feature name, e.g. ``"Authentication"``.
        files:        Repo-relative source files that implement this feature.
        tests:        Repo-relative test files that cover this feature.
        dependencies: Other feature or module names this feature depends on.
        confidence:   Float in [0.0, 1.0]; lower when grouping was inferred.
        summary:      Concise, agent-readable description of the feature.
    """

    name: str
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    confidence: float = 1.0
    summary: str = ""

    # TODO(memory): add ``entry_points: list[str]`` for key function/class refs.
    # TODO(memory): add ``conventions: list[str]`` for feature-level style notes.

    def slug(self) -> str:
        """Return a filesystem-safe slug derived from the feature name."""
        return self.name.lower().replace(" ", "-").replace("/", "-")


# ---------------------------------------------------------------------------
# ModuleMemory
# ---------------------------------------------------------------------------


@dataclass
class ModuleMemory:
    """Memory artifact describing a single code module (package or directory).

    Produced by ``classifier.py`` (grouping) and ``generator.py`` (content).
    Written to ``.agent-memory/modules/<slug>.md``.

    Attributes:
        name:        Module name, e.g. ``"code_review_graph.memory"``.
        files:       Repo-relative source files that belong to this module.
        tests:       Repo-relative test files for this module.
        dependencies: Module names this module imports / depends on.
        dependents:  Module names that import from this module.
        confidence:  Float in [0.0, 1.0]; lower when grouping was inferred.
        summary:     Concise, agent-readable description of the module's role.
    """

    name: str
    files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    confidence: float = 1.0
    summary: str = ""

    # TODO(memory): add ``risks: list[str]`` for high-churn or coupling notes.
    # TODO(memory): add ``exported_symbols: list[str]`` for public API surface.

    def slug(self) -> str:
        """Return a filesystem-safe slug derived from the module name."""
        return self.name.lower().replace(".", "-").replace("/", "-")


# ---------------------------------------------------------------------------
# TaskContextPack
# ---------------------------------------------------------------------------


@dataclass
class TaskContextPack:
    """A focused context bundle assembled for a specific developer task.

    This is the primary output of ``context_builder.py`` and the core product
    deliverable. It is returned by the ``prepare-context`` CLI command and the
    corresponding MCP tool.

    The pack is designed to be injected into a Claude Code session so the agent
    starts with exactly the context needed for the task — no more, no less.

    Attributes:
        task:              Natural-language task description provided by the developer.
        relevant_features: Feature names relevant to this task.
        relevant_modules:  Module names relevant to this task.
        relevant_files:    Repo-relative file paths most likely needed.
        relevant_tests:    Repo-relative test file paths related to the task.
        warnings:          Rules, safe-boundary notes, or risk flags to surface.
        summary:           Concise task framing paragraph for Claude Code.
    """

    task: str
    relevant_features: list[str] = field(default_factory=list)
    relevant_modules: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    relevant_tests: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    # TODO(memory): add ``recent_changes: list[str]`` once refresh.py is wired in.
    # TODO(memory): add ``confidence: float`` reflecting how complete this pack is.

    def is_empty(self) -> bool:
        """Return True if no relevant context was found."""
        return not any([
            self.relevant_features,
            self.relevant_modules,
            self.relevant_files,
        ])


# ---------------------------------------------------------------------------
# MemoryManifest
# ---------------------------------------------------------------------------


@dataclass
class MemoryManifest:
    """Top-level manifest for the ``.agent-memory/`` folder.

    Written to ``.agent-memory/metadata/manifest.json`` on every generation
    and refresh. Acts as the index of all generated artifacts and the overall
    state of repo memory.

    Attributes:
        version:      Manifest schema version string (e.g. ``"1"``).
        generated_at: UTC datetime of the most recent full generation.
        source_roots: Repo-relative root directories that were scanned.
        languages:    Programming languages detected in the repo.
        artifacts:    All generated memory artifacts in this repo.
    """

    version: str = "1"
    generated_at: datetime = field(default_factory=_now_utc)
    source_roots: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    artifacts: list[MemoryArtifact] = field(default_factory=list)

    # TODO(memory): add ``last_incremental_refresh: Optional[datetime]``.
    # TODO(memory): add ``total_source_files: int`` for quick health checks.

    def artifact_count(self) -> int:
        """Return the total number of artifacts tracked by this manifest."""
        return len(self.artifacts)

    def as_dict(self) -> dict:
        """Return a JSON-serialisable dict representation."""
        return {
            "version": self.version,
            "generated_at": self.generated_at.isoformat(),
            "source_roots": sorted(self.source_roots),
            "languages": sorted(self.languages),
            "artifact_count": self.artifact_count(),
            "artifacts": [a.as_dict() for a in sorted(self.artifacts, key=lambda a: a.relative_path)],
        }
