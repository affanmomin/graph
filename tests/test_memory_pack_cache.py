"""Tests for code_review_graph/memory/pack_cache.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_review_graph.memory.pack_cache import (
    _CACHE_VERSION,
    _extract_keywords,
    _split_camel,
    build_pack_cache,
    features_from_cache,
    keywords_from_cache,
    load_pack_cache,
    modules_from_cache,
    save_pack_cache,
)
from code_review_graph.memory.models import FeatureMemory, ModuleMemory


# ---------------------------------------------------------------------------
# _split_camel
# ---------------------------------------------------------------------------


def test_split_camel_simple():
    assert "contact" in _split_camel("ContactForm")
    assert "form" in _split_camel("ContactForm")


def test_split_camel_acronym():
    result = _split_camel("HTMLParser")
    assert "html" in result
    assert "parser" in result


def test_split_camel_snake():
    assert "auth" in _split_camel("auth_service")
    assert "service" in _split_camel("auth_service")


def test_split_camel_lowercase():
    result = _split_camel("getusers")
    assert result == ["getusers"]


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------


def test_keywords_include_file_stems():
    kws = _extract_keywords("UI", ["src/components/ContactForm.tsx"], {})
    assert "contact" in kws
    assert "form" in kws


def test_keywords_include_dir_parts():
    kws = _extract_keywords("Auth", ["src/auth/login.py"], {})
    assert "auth" in kws
    assert "login" in kws


def test_keywords_include_symbols():
    vocab = {"src/auth/login.py": ["validateToken", "hashPassword"]}
    kws = _extract_keywords("Auth", ["src/auth/login.py"], vocab)
    assert "validate" in kws
    assert "token" in kws
    assert "hash" in kws
    assert "password" in kws


def test_keywords_min_length():
    """Short tokens (< 3 chars) are excluded."""
    kws = _extract_keywords("UI", ["src/ui/ab.py"], {})
    assert "ab" not in kws


# ---------------------------------------------------------------------------
# build_pack_cache
# ---------------------------------------------------------------------------


def _make_feature(name: str, files: list[str]) -> FeatureMemory:
    return FeatureMemory(name=name, files=files, confidence=0.8)


def _make_module(name: str, files: list[str]) -> ModuleMemory:
    return ModuleMemory(name=name, files=files, confidence=0.9)


def test_build_cache_structure():
    features = [_make_feature("Auth", ["src/auth/login.py"])]
    modules = [_make_module("core", ["src/core/__init__.py"])]
    cache = build_pack_cache(features, modules, {})

    assert cache["version"] == _CACHE_VERSION
    assert len(cache["features"]) == 1
    assert len(cache["modules"]) == 1
    assert cache["features"][0]["name"] == "Auth"
    assert cache["modules"][0]["name"] == "core"


def test_build_cache_keywords_populated():
    features = [_make_feature("UI", ["src/components/ContactForm.tsx"])]
    cache = build_pack_cache(features, [], {})
    kws = cache["features"][0]["keywords"]
    assert "contact" in kws
    assert "form" in kws


# ---------------------------------------------------------------------------
# save / load roundtrip
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path):
    mem_root = tmp_path / ".agent-memory"
    features = [_make_feature("Auth", ["src/auth.py"])]
    cache = build_pack_cache(features, [], {})
    save_pack_cache(cache, mem_root)

    loaded = load_pack_cache(mem_root)
    assert loaded is not None
    assert loaded["version"] == _CACHE_VERSION
    assert loaded["features"][0]["name"] == "Auth"


def test_load_returns_none_when_missing(tmp_path):
    assert load_pack_cache(tmp_path / ".agent-memory") is None


def test_load_returns_none_on_version_mismatch(tmp_path):
    mem_root = tmp_path / ".agent-memory"
    cache_path = mem_root / "metadata" / "pack_cache.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps({"version": 999, "features": [], "modules": []}))
    assert load_pack_cache(mem_root) is None


# ---------------------------------------------------------------------------
# features_from_cache / modules_from_cache / keywords_from_cache
# ---------------------------------------------------------------------------


def test_features_from_cache_roundtrip(tmp_path):
    features = [_make_feature("Payments", ["src/payments.py"])]
    cache = build_pack_cache(features, [], {})
    recovered = features_from_cache(cache)
    assert len(recovered) == 1
    assert recovered[0].name == "Payments"
    assert recovered[0].files == ["src/payments.py"]


def test_modules_from_cache_roundtrip(tmp_path):
    modules = [_make_module("api", ["src/api/__init__.py"])]
    cache = build_pack_cache([], modules, {})
    recovered = modules_from_cache(cache)
    assert len(recovered) == 1
    assert recovered[0].name == "api"


def test_keywords_from_cache_returns_sets(tmp_path):
    features = [_make_feature("Auth", ["src/auth/login.py"])]
    cache = build_pack_cache(features, [], {})
    kw_map = keywords_from_cache(cache)
    assert "Auth" in kw_map
    assert isinstance(kw_map["Auth"], set)
    assert "login" in kw_map["Auth"]
