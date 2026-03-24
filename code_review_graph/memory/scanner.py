"""Repo scanner for the memory subsystem.

Walks the repository filesystem and collects structural signals used by
the generator to produce ``.agent-memory/`` artifacts.

This module does NOT call the graph engine and does NOT write any files.
It reads only the filesystem.  All detection is heuristic — designed to work
on messy real-world repos, not only well-structured ones.

Public API
----------
scan_repo(repo_root) -> RepoScan
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language detection — reuse extension map from parser without importing it
# (avoids pulling in tree-sitter at scan time)
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".sol": "solidity",
    ".vue": "vue",
}

# Directories that are always noise — skip entirely during walk
_SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "dist", "build", "out", "target",
    ".next", ".nuxt", ".turbo",
    ".code-review-graph", ".agent-memory",
    "coverage", ".coverage",
})

# Names that suggest a directory contains tests
_TEST_DIR_NAMES: frozenset[str] = frozenset({
    "tests", "test", "spec", "specs", "__tests__", "e2e", "integration",
    "unit", "functional",
})

# Names that suggest documentation
_DOCS_DIR_NAMES: frozenset[str] = frozenset({
    "docs", "doc", "documentation", "wiki", "guides", "manual",
})

# Names that suggest source code roots
_SRC_DIR_NAMES: frozenset[str] = frozenset({
    "src", "lib", "app", "apps", "packages", "services", "modules",
    "core", "internal", "cmd", "pkg",
})

# Config files that give strong framework / stack hints
_CONFIG_FILES: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "package.json": "javascript/typescript",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "build.gradle": "java/kotlin",
    "build.gradle.kts": "kotlin",
    "Gemfile": "ruby",
    "composer.json": "php",
    "CMakeLists.txt": "c/cpp",
    "Makefile": "make",
    "Dockerfile": "docker",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
    ".github": "github-actions",
}

# Framework hints: if these files exist the project likely uses this framework
_FRAMEWORK_HINTS: dict[str, str] = {
    "manage.py": "Django",
    "wsgi.py": "Django/WSGI",
    "asgi.py": "Django/ASGI",
    "fastapi": "FastAPI",          # detected by directory name
    "flask": "Flask",
    "next.config.js": "Next.js",
    "next.config.ts": "Next.js",
    "nuxt.config.js": "Nuxt.js",
    "nuxt.config.ts": "Nuxt.js",
    "vite.config.js": "Vite",
    "vite.config.ts": "Vite",
    "angular.json": "Angular",
    "remix.config.js": "Remix",
    "svelte.config.js": "SvelteKit",
    "hardhat.config.js": "Hardhat (Solidity)",
    "foundry.toml": "Foundry (Solidity)",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class RepoScan:
    """Structural signals collected from a repository root.

    All path fields contain :class:`~pathlib.Path` objects relative to
    ``repo_root`` for portability.  Absolute paths are never stored here.

    Attributes:
        repo_root:        Absolute path to the repository root.
        top_level_dirs:   Sorted list of non-hidden top-level directories.
        source_dirs:      Directories that likely contain production source code.
        test_dirs:        Directories that likely contain tests.
        docs_dirs:        Directories that likely contain documentation.
        config_files:     Important config files found at or near the root.
        languages:        Detected programming languages (sorted, deduplicated).
        framework_hints:  Inferred frameworks (e.g. ``["Django", "React"]``).
        file_counts:      Map of language -> number of source files found.
        readme_path:      Repo-relative path to the README if one exists.
        confidence:       Float [0, 1] — how confident the scan is overall.
        notes:            Human-readable ambiguity notes for weak detections.
    """

    repo_root: Path
    top_level_dirs: list[str] = field(default_factory=list)
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)
    docs_dirs: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    framework_hints: list[str] = field(default_factory=list)
    file_counts: dict[str, int] = field(default_factory=dict)
    readme_path: str = ""
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_repo(repo_root: Path) -> RepoScan:
    """Walk *repo_root* and collect structural signals.

    Args:
        repo_root: Absolute path to the repository root directory.

    Returns:
        A populated :class:`RepoScan` instance.  Never raises — on partial
        failure a scan with low ``confidence`` and descriptive ``notes`` is
        returned instead.
    """
    scan = RepoScan(repo_root=repo_root)

    if not repo_root.is_dir():
        scan.confidence = 0.0
        scan.notes.append(f"repo_root does not exist or is not a directory: {repo_root}")
        return scan

    # --- Top-level dirs and README ---
    _collect_top_level(repo_root, scan)

    # --- Config files and framework hints ---
    _collect_config_files(repo_root, scan)

    # --- Language and file counts via filesystem walk ---
    _collect_languages(repo_root, scan)

    # --- Classify dirs into source / test / docs ---
    _classify_dirs(repo_root, scan)

    # --- Confidence ---
    scan.confidence = _compute_confidence(scan)

    return scan


# ---------------------------------------------------------------------------
# Internal collectors
# ---------------------------------------------------------------------------


def _collect_top_level(repo_root: Path, scan: RepoScan) -> None:
    """Populate top_level_dirs and readme_path."""
    dirs: list[str] = []
    for entry in sorted(repo_root.iterdir()):
        if entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
            dirs.append(entry.name)
        if entry.is_file() and entry.name.lower() in ("readme.md", "readme.rst", "readme.txt", "readme"):
            scan.readme_path = entry.name
    scan.top_level_dirs = dirs


def _collect_config_files(repo_root: Path, scan: RepoScan) -> None:
    """Detect config files and populate framework_hints."""
    found: list[str] = []
    hints: set[str] = set()

    for name, _hint in _CONFIG_FILES.items():
        if (repo_root / name).exists():
            found.append(name)

    # Framework hints from specific filenames
    for filename, framework in _FRAMEWORK_HINTS.items():
        if (repo_root / filename).exists():
            hints.add(framework)

    # Peek inside package.json for known frameworks
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        hints.update(_hints_from_package_json(pkg_json))

    # Peek inside pyproject.toml for known frameworks
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        hints.update(_hints_from_pyproject(pyproject))

    scan.config_files = sorted(found)
    scan.framework_hints = sorted(hints)


def _collect_languages(repo_root: Path, scan: RepoScan) -> None:
    """Walk the repo and count source files per language."""
    counts: dict[str, int] = {}

    for path in _walk_source_files(repo_root):
        lang = _EXT_TO_LANG.get(path.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    scan.file_counts = dict(sorted(counts.items()))
    scan.languages = sorted(counts.keys())


def _classify_dirs(repo_root: Path, scan: RepoScan) -> None:
    """Populate source_dirs, test_dirs, docs_dirs from top-level + one level deep."""
    src: list[str] = []
    tests: list[str] = []
    docs: list[str] = []

    # Check top-level dirs
    for name in scan.top_level_dirs:
        lower = name.lower()
        if lower in _TEST_DIR_NAMES:
            tests.append(name)
        elif lower in _DOCS_DIR_NAMES:
            docs.append(name)
        elif lower in _SRC_DIR_NAMES:
            src.append(name)

    # If no src dir was found at top level, treat the package/module dirs as source
    # but exclude dirs already classified as tests or docs
    already_classified = set(tests) | set(docs)
    if not src:
        for name in scan.top_level_dirs:
            if name in already_classified:
                continue
            p = repo_root / name
            if p.is_dir() and _dir_has_source(p):
                src.append(name)

    # Fallback: if still nothing, note the ambiguity
    if not src and scan.languages:
        scan.notes.append(
            "No conventional source directory (src/, lib/, app/) detected. "
            "Source may be at the repo root or in non-standard locations."
        )

    if not tests:
        scan.notes.append("No conventional test directory detected.")

    if not docs:
        scan.notes.append("No conventional docs directory detected.")

    scan.source_dirs = sorted(src)
    scan.test_dirs = sorted(tests)
    scan.docs_dirs = sorted(docs)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _walk_source_files(repo_root: Path):
    """Yield all source files under *repo_root*, skipping noise directories."""
    for entry in repo_root.rglob("*"):
        # Skip noise dirs — check every component of the relative path
        try:
            parts = entry.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        if entry.is_file() and entry.suffix.lower() in _EXT_TO_LANG:
            yield entry


def _dir_has_source(directory: Path) -> bool:
    """Return True if *directory* contains at least one recognised source file."""
    for f in directory.iterdir():
        if f.is_file() and f.suffix.lower() in _EXT_TO_LANG:
            return True
    return False


def _hints_from_package_json(path: Path) -> list[str]:
    """Extract framework hints from package.json dependencies."""
    import json
    hints: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        all_deps: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            all_deps.update(data.get(key, {}).keys())
        mapping = {
            "react": "React", "react-dom": "React",
            "next": "Next.js",
            "vue": "Vue",
            "nuxt": "Nuxt.js",
            "@angular/core": "Angular",
            "svelte": "Svelte",
            "@remix-run/react": "Remix",
            "express": "Express",
            "fastify": "Fastify",
            "nestjs": "NestJS", "@nestjs/core": "NestJS",
            "hardhat": "Hardhat",
        }
        for dep, framework in mapping.items():
            if dep in all_deps:
                hints.append(framework)
    except Exception:
        pass
    return hints


def _hints_from_pyproject(path: Path) -> list[str]:
    """Extract framework hints from pyproject.toml dependencies."""
    hints: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        mapping = {
            "django": "Django",
            "fastapi": "FastAPI",
            "flask": "Flask",
            "starlette": "Starlette",
            "litestar": "Litestar",
            "tornado": "Tornado",
        }
        for keyword, framework in mapping.items():
            if keyword in text:
                hints.append(framework)
    except Exception:
        pass
    return hints


def _compute_confidence(scan: RepoScan) -> float:
    """Return a confidence score [0, 1] based on what was detected."""
    score = 1.0
    if not scan.languages:
        score -= 0.4
    if not scan.source_dirs:
        score -= 0.2
    if not scan.config_files:
        score -= 0.1
    return max(0.0, round(score, 2))
