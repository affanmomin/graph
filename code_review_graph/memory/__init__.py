"""Repo Memory subsystem for code-review-graph.

This package implements the memory layer (Layer B) that sits on top of the existing
code intelligence engine (Layer A). It is responsible for:

- scanning and classifying repo areas into features and modules
- generating durable `.agent-memory/` markdown and metadata artifacts
- building task-aware context packs for Claude Code
- managing incremental memory refresh tied to repo changes
- loading and applying human override guidance
- tracking freshness, confidence, and source traceability per artifact

Nothing in this package modifies the core graph engine (graph.py, parser.py,
incremental.py, tools.py). It reads from the graph; it does not rewrite it.

Public surface (to be completed in later tickets):
    from code_review_graph.memory.models import (
        MemoryArtifact, FeatureMemory, ModuleMemory,
        TaskContextPack, MemoryManifest, ArtifactMetadata,
    )
"""

from .models import (
    ArtifactMetadata,
    FeatureMemory,
    MemoryArtifact,
    MemoryManifest,
    ModuleMemory,
    TaskContextPack,
)
from .classifier import classify_features, classify_modules
from .context_builder import build_context_pack
from .generator import (
    generate_architecture_doc,
    generate_feature_doc,
    generate_module_doc,
    generate_repo_summary,
)
from .writer import (
    MEMORY_SUBDIRS,
    WriteStatus,
    ensure_memory_dirs,
    render_markdown_section,
    write_json_if_changed,
    write_override_if_absent,
    write_text_if_changed,
)

__all__ = [
    # classifier
    "classify_features",
    "classify_modules",
    # context builder
    "build_context_pack",
    # generator
    "generate_architecture_doc",
    "generate_feature_doc",
    "generate_module_doc",
    "generate_repo_summary",
    # models
    "ArtifactMetadata",
    "FeatureMemory",
    "MemoryArtifact",
    "MemoryManifest",
    "ModuleMemory",
    "TaskContextPack",
    # writer
    "MEMORY_SUBDIRS",
    "WriteStatus",
    "ensure_memory_dirs",
    "render_markdown_section",
    "write_json_if_changed",
    "write_override_if_absent",
    "write_text_if_changed",
]
