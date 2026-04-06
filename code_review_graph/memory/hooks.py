"""Claude Code hook utilities for the repo-memory subsystem.

The preferred integration path is the MCP server (``repomind serve``) — Claude
calls ``memory_prepare_context_tool`` itself when it needs context, spending
tokens only when they save more downstream.

The ``UserPromptSubmit`` hook (injecting context on every message) is NOT
installed automatically because it fires on follow-up messages where Claude
already has context, wasting tokens rather than saving them.

This module is kept for opt-in manual installation if users prefer the hook
approach for specific workflows.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def install_prompt_hook(repo_root: Path) -> bool:
    """Write the UserPromptSubmit hook entry to ``~/.claude/settings.json``.

    Args:
        repo_root: Repo root — used to build the absolute path to the hook script.

    Returns:
        ``True`` if the hook was newly installed, ``False`` if it was already
        present or could not be installed (script missing, write error, etc.).
    """
    hook_script = repo_root / "hooks" / "prompt-context.sh"
    if not hook_script.exists():
        logger.debug("install_prompt_hook: script not found at %s, skipping", hook_script)
        return False

    hook_command = str(hook_script)

    try:
        # Load or create settings
        if _SETTINGS_PATH.exists():
            try:
                settings: dict = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                settings = {}
        else:
            settings = {}

        hooks = settings.setdefault("hooks", {})
        ups_list: list = hooks.setdefault("UserPromptSubmit", [])

        # Check if already installed (avoid duplicates)
        for block in ups_list:
            for h in block.get("hooks", []):
                if h.get("command") == hook_command:
                    logger.debug("install_prompt_hook: already installed, skipping")
                    return False

        # Append new hook block
        ups_list.append({"hooks": [{"type": "command", "command": hook_command}]})

        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        logger.debug("install_prompt_hook: installed at %s", _SETTINGS_PATH)
        return True

    except Exception as exc:
        logger.warning("install_prompt_hook: failed: %s", exc)
        return False
