"""Tests for the repo-memory package foundation (Ticket 1).

Verifies that:
- the ``code_review_graph.memory`` package imports cleanly
- all expected modules are present and importable
- all core models are constructible with defaults
- model field types and safe-default behaviour are correct
- no existing graph behaviour is broken by the new package
"""

from __future__ import annotations

from datetime import datetime


# ---------------------------------------------------------------------------
# Package-level imports
# ---------------------------------------------------------------------------


class TestMemoryPackageImports:
    """The memory package and all its modules must import without errors."""

    def test_package_importable(self):
        import code_review_graph.memory  # noqa: F401

    def test_models_importable(self):
        from code_review_graph.memory import models  # noqa: F401

    def test_scanner_importable(self):
        from code_review_graph.memory import scanner  # noqa: F401

    def test_classifier_importable(self):
        from code_review_graph.memory import classifier  # noqa: F401

    def test_generator_importable(self):
        from code_review_graph.memory import generator  # noqa: F401

    def test_writer_importable(self):
        from code_review_graph.memory import writer  # noqa: F401

    def test_metadata_importable(self):
        from code_review_graph.memory import metadata  # noqa: F401

    def test_refresh_importable(self):
        from code_review_graph.memory import refresh  # noqa: F401

    def test_context_builder_importable(self):
        from code_review_graph.memory import context_builder  # noqa: F401

    def test_overrides_importable(self):
        from code_review_graph.memory import overrides  # noqa: F401

    def test_public_exports(self):
        """All models re-exported from __init__ must be importable at package level."""
        from code_review_graph.memory import (
            ArtifactMetadata,
            FeatureMemory,
            MemoryArtifact,
            MemoryManifest,
            ModuleMemory,
            TaskContextPack,
        )
        assert ArtifactMetadata is not None
        assert FeatureMemory is not None
        assert MemoryArtifact is not None
        assert MemoryManifest is not None
        assert ModuleMemory is not None
        assert TaskContextPack is not None


# ---------------------------------------------------------------------------
# ArtifactMetadata
# ---------------------------------------------------------------------------


class TestArtifactMetadata:
    def test_construct_minimal(self):
        from code_review_graph.memory.models import ArtifactMetadata
        m = ArtifactMetadata(artifact_path=".agent-memory/features/auth.md")
        assert m.artifact_path == ".agent-memory/features/auth.md"
        assert isinstance(m.generated_at, datetime)
        assert m.source_files == []
        assert m.confidence == 1.0
        assert m.stale is False

    def test_construct_full(self):
        from code_review_graph.memory.models import ArtifactMetadata
        m = ArtifactMetadata(
            artifact_path=".agent-memory/repo.md",
            source_files=["src/auth.py", "src/models.py"],
            confidence=0.75,
            stale=True,
        )
        assert m.confidence == 0.75
        assert m.stale is True
        assert len(m.source_files) == 2

    def test_mutable_default_safety(self):
        """Two instances must not share the same source_files list."""
        from code_review_graph.memory.models import ArtifactMetadata
        a = ArtifactMetadata(artifact_path="a.md")
        b = ArtifactMetadata(artifact_path="b.md")
        a.source_files.append("x.py")
        assert b.source_files == []

    def test_as_dict_is_serialisable(self):
        from code_review_graph.memory.models import ArtifactMetadata
        m = ArtifactMetadata(
            artifact_path=".agent-memory/repo.md",
            source_files=["b.py", "a.py"],
            confidence=0.8,
        )
        d = m.as_dict()
        assert d["artifact_path"] == ".agent-memory/repo.md"
        assert d["source_files"] == ["a.py", "b.py"]  # sorted
        assert isinstance(d["generated_at"], str)
        assert d["confidence"] == 0.8
        assert d["stale"] is False


# ---------------------------------------------------------------------------
# MemoryArtifact
# ---------------------------------------------------------------------------


class TestMemoryArtifact:
    def test_construct_minimal(self):
        from code_review_graph.memory.models import MemoryArtifact
        a = MemoryArtifact(
            artifact_id="feature-auth",
            artifact_type="feature",
            relative_path=".agent-memory/features/auth.md",
            title="Authentication",
        )
        assert a.artifact_id == "feature-auth"
        assert a.artifact_type == "feature"
        assert a.source_files == []
        assert isinstance(a.generated_at, datetime)

    def test_mutable_default_safety(self):
        from code_review_graph.memory.models import MemoryArtifact
        a = MemoryArtifact(artifact_id="a", artifact_type="repo",
                           relative_path="a.md", title="A")
        b = MemoryArtifact(artifact_id="b", artifact_type="repo",
                           relative_path="b.md", title="B")
        a.source_files.append("x.py")
        assert b.source_files == []

    def test_as_dict_sorted_sources(self):
        from code_review_graph.memory.models import MemoryArtifact
        a = MemoryArtifact(
            artifact_id="repo",
            artifact_type="repo",
            relative_path=".agent-memory/repo.md",
            title="Repo",
            source_files=["z.py", "a.py"],
        )
        d = a.as_dict()
        assert d["source_files"] == ["a.py", "z.py"]


# ---------------------------------------------------------------------------
# FeatureMemory
# ---------------------------------------------------------------------------


class TestFeatureMemory:
    def test_construct_minimal(self):
        from code_review_graph.memory.models import FeatureMemory
        f = FeatureMemory(name="Authentication")
        assert f.name == "Authentication"
        assert f.files == []
        assert f.tests == []
        assert f.dependencies == []
        assert f.confidence == 1.0
        assert f.summary == ""

    def test_slug(self):
        from code_review_graph.memory.models import FeatureMemory
        assert FeatureMemory(name="User Auth").slug() == "user-auth"
        assert FeatureMemory(name="API/Routes").slug() == "api-routes"

    def test_mutable_default_safety(self):
        from code_review_graph.memory.models import FeatureMemory
        a = FeatureMemory(name="A")
        b = FeatureMemory(name="B")
        a.files.append("x.py")
        assert b.files == []

    def test_construct_full(self):
        from code_review_graph.memory.models import FeatureMemory
        f = FeatureMemory(
            name="Payments",
            files=["src/payments.py"],
            tests=["tests/test_payments.py"],
            dependencies=["Authentication"],
            confidence=0.9,
            summary="Handles Stripe payment flows.",
        )
        assert f.confidence == 0.9
        assert f.summary == "Handles Stripe payment flows."


# ---------------------------------------------------------------------------
# ModuleMemory
# ---------------------------------------------------------------------------


class TestModuleMemory:
    def test_construct_minimal(self):
        from code_review_graph.memory.models import ModuleMemory
        m = ModuleMemory(name="code_review_graph.memory")
        assert m.name == "code_review_graph.memory"
        assert m.files == []
        assert m.tests == []
        assert m.dependencies == []
        assert m.dependents == []
        assert m.confidence == 1.0
        assert m.summary == ""

    def test_slug(self):
        from code_review_graph.memory.models import ModuleMemory
        assert ModuleMemory(name="code_review_graph.memory").slug() == "code_review_graph-memory"
        assert ModuleMemory(name="src/auth").slug() == "src-auth"

    def test_mutable_default_safety(self):
        from code_review_graph.memory.models import ModuleMemory
        a = ModuleMemory(name="A")
        b = ModuleMemory(name="B")
        a.dependents.append("X")
        assert b.dependents == []


# ---------------------------------------------------------------------------
# TaskContextPack
# ---------------------------------------------------------------------------


class TestTaskContextPack:
    def test_construct_minimal(self):
        from code_review_graph.memory.models import TaskContextPack
        p = TaskContextPack(task="Add rate limiting to the API")
        assert p.task == "Add rate limiting to the API"
        assert p.relevant_features == []
        assert p.relevant_modules == []
        assert p.relevant_files == []
        assert p.relevant_tests == []
        assert p.warnings == []
        assert p.summary == ""

    def test_is_empty_when_no_context(self):
        from code_review_graph.memory.models import TaskContextPack
        p = TaskContextPack(task="some task")
        assert p.is_empty() is True

    def test_is_not_empty_with_features(self):
        from code_review_graph.memory.models import TaskContextPack
        p = TaskContextPack(task="some task", relevant_features=["Auth"])
        assert p.is_empty() is False

    def test_is_not_empty_with_files(self):
        from code_review_graph.memory.models import TaskContextPack
        p = TaskContextPack(task="some task", relevant_files=["src/auth.py"])
        assert p.is_empty() is False

    def test_mutable_default_safety(self):
        from code_review_graph.memory.models import TaskContextPack
        a = TaskContextPack(task="A")
        b = TaskContextPack(task="B")
        a.warnings.append("danger")
        assert b.warnings == []


# ---------------------------------------------------------------------------
# MemoryManifest
# ---------------------------------------------------------------------------


class TestMemoryManifest:
    def test_construct_defaults(self):
        from code_review_graph.memory.models import MemoryManifest
        m = MemoryManifest()
        assert m.version == "1"
        assert isinstance(m.generated_at, datetime)
        assert m.source_roots == []
        assert m.languages == []
        assert m.artifacts == []
        assert m.artifact_count() == 0

    def test_artifact_count(self):
        from code_review_graph.memory.models import MemoryArtifact, MemoryManifest
        a = MemoryArtifact(artifact_id="x", artifact_type="repo",
                           relative_path="x.md", title="X")
        m = MemoryManifest(artifacts=[a])
        assert m.artifact_count() == 1

    def test_as_dict_artifacts_sorted(self):
        from code_review_graph.memory.models import MemoryArtifact, MemoryManifest
        a1 = MemoryArtifact(artifact_id="z", artifact_type="feature",
                            relative_path=".agent-memory/features/z.md", title="Z")
        a2 = MemoryArtifact(artifact_id="a", artifact_type="feature",
                            relative_path=".agent-memory/features/a.md", title="A")
        m = MemoryManifest(artifacts=[a1, a2])
        d = m.as_dict()
        paths = [art["relative_path"] for art in d["artifacts"]]
        assert paths == sorted(paths)

    def test_mutable_default_safety(self):
        from code_review_graph.memory.models import MemoryManifest
        a = MemoryManifest()
        b = MemoryManifest()
        a.languages.append("python")
        assert b.languages == []


# ---------------------------------------------------------------------------
# Existing graph behaviour guard
# ---------------------------------------------------------------------------


class TestExistingGraphUnaffected:
    """Smoke test: importing the memory package must not break graph imports."""

    def test_graph_still_importable(self):
        import code_review_graph.memory  # noqa: F401 (trigger memory import first)
        from code_review_graph.graph import GraphStore  # noqa: F401
        assert GraphStore is not None

    def test_parser_still_importable(self):
        import code_review_graph.memory  # noqa: F401
        from code_review_graph.parser import NodeInfo  # noqa: F401
        assert NodeInfo is not None
