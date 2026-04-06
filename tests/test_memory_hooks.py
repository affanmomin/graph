"""Tests for code_review_graph/memory/hooks.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_review_graph.memory.hooks import install_prompt_hook


def _make_script(repo_root: Path) -> Path:
    """Create a dummy prompt-context.sh so install_prompt_hook finds it."""
    script = repo_root / "hooks" / "prompt-context.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/bash\necho hello\n")
    return script


# ---------------------------------------------------------------------------
# Basic install
# ---------------------------------------------------------------------------


def test_install_creates_settings(tmp_path, monkeypatch):
    """install_prompt_hook creates ~/.claude/settings.json with correct structure."""
    settings_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr("code_review_graph.memory.hooks._SETTINGS_PATH", settings_path)

    repo_root = tmp_path / "repo"
    _make_script(repo_root)

    result = install_prompt_hook(repo_root)

    assert result is True
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text())
    ups = settings["hooks"]["UserPromptSubmit"]
    assert len(ups) == 1
    assert ups[0]["hooks"][0]["type"] == "command"
    assert "prompt-context.sh" in ups[0]["hooks"][0]["command"]


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_install_idempotent(tmp_path, monkeypatch):
    """Calling install_prompt_hook twice does not add a duplicate entry."""
    settings_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr("code_review_graph.memory.hooks._SETTINGS_PATH", settings_path)

    repo_root = tmp_path / "repo"
    _make_script(repo_root)

    first = install_prompt_hook(repo_root)
    second = install_prompt_hook(repo_root)

    assert first is True
    assert second is False  # already installed

    settings = json.loads(settings_path.read_text())
    ups = settings["hooks"]["UserPromptSubmit"]
    assert len(ups) == 1  # not duplicated


# ---------------------------------------------------------------------------
# Merges with existing settings
# ---------------------------------------------------------------------------


def test_install_merges_existing_settings(tmp_path, monkeypatch):
    """install_prompt_hook preserves other keys in existing settings.json."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"theme": "dark", "model": "claude-opus-4-6"}))
    monkeypatch.setattr("code_review_graph.memory.hooks._SETTINGS_PATH", settings_path)

    repo_root = tmp_path / "repo"
    _make_script(repo_root)

    install_prompt_hook(repo_root)

    settings = json.loads(settings_path.read_text())
    assert settings["theme"] == "dark"
    assert settings["model"] == "claude-opus-4-6"
    assert "UserPromptSubmit" in settings["hooks"]


def test_install_merges_existing_hooks(tmp_path, monkeypatch):
    """install_prompt_hook appends to an existing UserPromptSubmit list."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "/other/hook.sh"}]}
            ]
        }
    }
    settings_path.write_text(json.dumps(existing))
    monkeypatch.setattr("code_review_graph.memory.hooks._SETTINGS_PATH", settings_path)

    repo_root = tmp_path / "repo"
    _make_script(repo_root)

    install_prompt_hook(repo_root)

    settings = json.loads(settings_path.read_text())
    ups = settings["hooks"]["UserPromptSubmit"]
    assert len(ups) == 2  # original + new


# ---------------------------------------------------------------------------
# Script missing
# ---------------------------------------------------------------------------


def test_install_skips_when_script_missing(tmp_path, monkeypatch):
    """install_prompt_hook returns False and does not crash when script not found."""
    settings_path = tmp_path / ".claude" / "settings.json"
    monkeypatch.setattr("code_review_graph.memory.hooks._SETTINGS_PATH", settings_path)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    # intentionally do NOT create hooks/prompt-context.sh

    result = install_prompt_hook(repo_root)

    assert result is False
    assert not settings_path.exists()
