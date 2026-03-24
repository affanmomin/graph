"""Metadata management for ``.agent-memory/`` artifacts.

Responsible for reading and writing the metadata files that track freshness,
confidence, and source traceability for every generated memory artifact.

Metadata files live under ``.agent-memory/metadata/`` and are committed to
Git alongside the artifacts they describe. They allow agents and humans to:
- detect stale artifacts without re-reading all source files
- understand how confident to be in a given summary
- trace which source files contributed to each artifact

Metadata file layout:
    .agent-memory/metadata/
        manifest.json    -- overall index of all artifacts (MemoryManifest)
        freshness.json   -- per-artifact last-generated and stale flags
        confidence.json  -- per-artifact confidence scores
        sources.json     -- per-artifact source file lists

Design constraints:
- JSON output must be sorted and pretty-printed for stable Git diffs
- all datetime values serialised as ISO 8601 UTC strings
- reading must tolerate missing files (first run, partial generation)

TODO(metadata): implement ``load_manifest(agent_memory_root)`` -> MemoryManifest
TODO(metadata): implement ``save_manifest(manifest, agent_memory_root)``
TODO(metadata): implement ``load_artifact_metadata(artifact_path)`` -> ArtifactMetadata
TODO(metadata): implement ``mark_stale(artifact_paths, agent_memory_root)``
TODO(metadata): implement ``update_freshness(artifact, agent_memory_root)``
"""

from __future__ import annotations

# TODO(metadata): imports will be added when implementation begins
