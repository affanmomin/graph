"""Repo scanner for the memory subsystem.

Responsible for walking the repository filesystem and collecting structural
signals needed by the classifier and generator. This module does NOT modify
the core graph engine; it reads from it (and from the filesystem directly
where the graph does not have the signal needed).

Planned responsibilities:
- enumerate source files by language
- detect likely framework / project type (Django, FastAPI, React, etc.)
- locate documentation files (README, docs/, CHANGELOG, etc.)
- locate configuration files (pyproject.toml, package.json, etc.)
- locate test directories and test files
- provide a lightweight ``RepoScan`` result object for downstream use

TODO(scanner): implement ``scan_repo(repo_root)`` -> ``RepoScan``
TODO(scanner): detect framework via heuristics on filenames and imports
TODO(scanner): respect .gitignore and .agent-memory/overrides/ exclusions
"""

from __future__ import annotations

# TODO(scanner): imports will be added when implementation begins
