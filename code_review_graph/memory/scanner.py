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

# Names that indicate tooling / benchmark / evaluation directories — not production source.
# These should be labelled separately in architecture.md rather than listed as source dirs.
_TOOLING_DIR_NAMES: frozenset[str] = frozenset({
    "benchmarks", "benchmark", "perf", "evaluate", "evaluation",
    "scripts", "tools", "hack", "vendor", "third_party",
})

# Test file stem indicators — shared with classifier and flat_rescue
_TEST_PREFIXES: tuple[str, ...] = ("test_", "spec_")
_TEST_SUFFIXES: tuple[str, ...] = ("_test", "_spec", ".test", ".spec")

# Shape detection thresholds
_SHAPE_MIN_FILES: int = 3       # need at least this many source files to classify shape
_FLAT_MIN_FILES: int = 5        # flat-package requires at least this many files in ≤1 dir
_STRUCTURED_MIN_DIRS: int = 3   # structured requires at least this many unique parent dirs

# Test-framework config filenames that indicate co-located tests in a directory.
# Used to detect test dirs even when there's no conventional tests/ subdirectory.
_TEST_CONFIG_FILES: frozenset[str] = frozenset({
    "jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs",
    "jest.config.json",
    "vitest.config.js", "vitest.config.ts", "vitest.config.mts",
    "pytest.ini", "conftest.py",
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
    tooling_dirs: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    fixture_languages: list[str] = field(default_factory=list)
    framework_hints: list[str] = field(default_factory=list)
    file_counts: dict[str, int] = field(default_factory=dict)
    readme_path: str = ""
    readme_excerpt: str = ""
    cli_scripts: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    notes: list[str] = field(default_factory=list)
    repo_shape: str = "unknown"          # "structured" | "mixed" | "flat-package" | "unknown"
    shape_rationale: str = ""            # human-readable explanation of the shape verdict


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

    # --- Top-level dirs, README, and README excerpt ---
    _collect_top_level(repo_root, scan)

    # --- Config files, framework hints, and CLI scripts ---
    _collect_config_files(repo_root, scan)

    # --- Language and file counts via filesystem walk ---
    _collect_languages(repo_root, scan)

    # --- Classify dirs into source / test / docs ---
    _classify_dirs(repo_root, scan)

    # --- Repo shape ---
    _detect_shape(repo_root, scan)

    # --- Confidence ---
    scan.confidence = _compute_confidence(scan)

    return scan


# ---------------------------------------------------------------------------
# Internal collectors
# ---------------------------------------------------------------------------


def _collect_top_level(repo_root: Path, scan: RepoScan) -> None:
    """Populate top_level_dirs, readme_path, and readme_excerpt."""
    dirs: list[str] = []
    for entry in sorted(repo_root.iterdir()):
        if entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
            dirs.append(entry.name)
        if entry.is_file() and entry.name.lower() in ("readme.md", "readme.rst", "readme.txt", "readme"):
            scan.readme_path = entry.name
            scan.readme_excerpt = _extract_readme_excerpt(entry)
    scan.top_level_dirs = dirs


def _collect_config_files(repo_root: Path, scan: RepoScan) -> None:
    """Detect config files and populate framework_hints."""
    found: list[str] = []
    hints: set[str] = set()

    for name, _hint in _CONFIG_FILES.items():
        if (repo_root / name).exists():
            found.append(name)

    # Framework hints from specific filenames at repo root
    for filename, framework in _FRAMEWORK_HINTS.items():
        if (repo_root / filename).exists():
            hints.add(framework)

    # Peek inside package.json / pyproject.toml at repo root
    pkg_json = repo_root / "package.json"
    if pkg_json.exists():
        hints.update(_hints_from_package_json(pkg_json))

    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        hints.update(_hints_from_pyproject(pyproject))
        scan.cli_scripts = _parse_cli_scripts(pyproject)

    # Also scan one level deep (all top-level dirs) for framework signals.
    # This finds Express/Next.js when package.json lives in BE/ or FE/ subdirs
    # rather than at the repo root — common in monorepo and full-stack layouts.
    for top_dir in scan.top_level_dirs:
        sub = repo_root / top_dir
        if not sub.is_dir():
            continue
        sub_pkg = sub / "package.json"
        if sub_pkg.exists():
            hints.update(_hints_from_package_json(sub_pkg))
        sub_pyproject = sub / "pyproject.toml"
        if sub_pyproject.exists():
            hints.update(_hints_from_pyproject(sub_pyproject))
        for filename, framework in _FRAMEWORK_HINTS.items():
            if (sub / filename).exists():
                hints.add(framework)

    scan.config_files = sorted(found)
    scan.framework_hints = sorted(hints)


def _collect_languages(repo_root: Path, scan: RepoScan) -> None:
    """Walk the repo and count source files per language.

    Separates real project languages from fixture-only languages.
    A language is "fixture-only" when ALL its files live inside test directories
    (e.g. ``tests/fixtures/Sample.java``).  Fixture languages are stored in
    ``scan.fixture_languages`` and excluded from ``scan.languages`` to prevent
    test assets from inflating the detected tech stack.
    """
    counts: dict[str, int] = {}
    test_counts: dict[str, int] = {}

    for path in _walk_source_files(repo_root):
        lang = _EXT_TO_LANG.get(path.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
            # Track files that live inside a test directory
            try:
                parts = path.relative_to(repo_root).parts
                if any(p.lower() in _TEST_DIR_NAMES for p in parts[:-1]):
                    test_counts[lang] = test_counts.get(lang, 0) + 1
            except ValueError:
                pass

    # A language is fixture-only when every file of that language is in a test dir.
    real_langs: list[str] = []
    fixture_langs: list[str] = []
    for lang, total in counts.items():
        if test_counts.get(lang, 0) == total:
            fixture_langs.append(lang)
        else:
            real_langs.append(lang)

    scan.file_counts = dict(sorted(counts.items()))
    scan.languages = sorted(real_langs)
    scan.fixture_languages = sorted(fixture_langs)


def _classify_dirs(repo_root: Path, scan: RepoScan) -> None:
    """Populate source_dirs, test_dirs, docs_dirs, tooling_dirs from top-level + one level deep."""
    src: list[str] = []
    tests: list[str] = []
    docs: list[str] = []
    tooling: list[str] = []

    # Check top-level dirs — classify by name first
    for name in scan.top_level_dirs:
        lower = name.lower()
        if lower in _TEST_DIR_NAMES:
            tests.append(name)
        elif lower in _DOCS_DIR_NAMES:
            docs.append(name)
        elif lower in _SRC_DIR_NAMES:
            src.append(name)
        elif lower in _TOOLING_DIR_NAMES:
            tooling.append(name)

    # If no src dir was found at top level, treat the package/module dirs as source
    # but exclude dirs already classified as tests, docs, or tooling
    tooling_set = set(tooling)
    already_classified = set(tests) | set(docs) | tooling_set
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

    # Deep scan: look one level into source dirs for conventional test subdirs
    # and detect co-located tests via jest.config.*, vitest.config.*, etc.
    # This handles patterns like FE/jest.config.ts (tests co-located with source).
    tests_set = set(tests)
    for src_dir in src:
        src_path = repo_root / src_dir
        if not src_path.is_dir():
            continue
        # Check for __tests__, spec/, etc. one level deep
        for entry in src_path.iterdir():
            if entry.is_dir() and entry.name.lower() in _TEST_DIR_NAMES:
                rel = f"{src_dir}/{entry.name}"
                if rel not in tests_set:
                    tests.append(rel)
                    tests_set.add(rel)
        # Test config file in this source dir → tests are co-located here
        if _has_test_config(src_path) and src_dir not in tests_set:
            tests.append(src_dir)
            tests_set.add(src_dir)

    # Also check all top-level dirs for test subdirs and config files.
    # This catches layouts where BE/ or FE/ aren't in _SRC_DIR_NAMES but still
    # contain tests (either co-located via jest.config.* or in a __tests__/ subdir).
    for name in scan.top_level_dirs:
        dir_path = repo_root / name
        if not dir_path.is_dir():
            continue
        # Check for __tests__, spec/, etc. directly inside the top-level dir
        for entry in dir_path.iterdir():
            if entry.is_dir() and entry.name.lower() in _TEST_DIR_NAMES:
                rel = f"{name}/{entry.name}"
                if rel not in tests_set:
                    tests.append(rel)
                    tests_set.add(rel)
        # Test config file in this top-level dir → tests are co-located here
        if _has_test_config(dir_path) and name not in tests_set:
            tests.append(name)
            tests_set.add(name)

    if not tests:
        scan.notes.append("No conventional test directory detected.")

    if not docs:
        scan.notes.append("No conventional docs directory detected.")

    scan.source_dirs = sorted(src)
    scan.test_dirs = sorted(tests)
    scan.docs_dirs = sorted(docs)
    scan.tooling_dirs = sorted(tooling)


def _detect_shape(repo_root: Path, scan: RepoScan) -> None:
    """Classify the repository layout shape and store result on *scan*.

    Uses the number of unique parent directories of non-test source files as
    the primary signal:
    - ``flat-package``: ≤1 unique parent dir AND ≥ _FLAT_MIN_FILES source files
    - ``structured``:  ≥ _STRUCTURED_MIN_DIRS unique parent dirs
    - ``mixed``:       everything in between
    - ``unknown``:     fewer than _SHAPE_MIN_FILES source files found
    """
    parent_dirs: set[str] = set()
    total = 0

    for path in _walk_source_files(repo_root):
        # Skip test files so they don't inflate the directory count
        stem = path.stem.lower()
        if any(stem.startswith(p) for p in _TEST_PREFIXES):
            continue
        if any(stem.endswith(s) for s in _TEST_SUFFIXES):
            continue
        # Skip files that live inside a test directory
        try:
            parts = path.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(p.lower() in _TEST_DIR_NAMES for p in parts[:-1]):
            continue

        total += 1
        # Parent dir relative to repo_root (or "." for root-level files)
        rel_parent = path.parent.relative_to(repo_root)
        parent_dirs.add(str(rel_parent))

    n_dirs = len(parent_dirs)

    if total < _SHAPE_MIN_FILES:
        scan.repo_shape = "unknown"
        scan.shape_rationale = f"Too few source files to classify ({total} found, need ≥{_SHAPE_MIN_FILES})."
    elif n_dirs <= 1 and total >= _FLAT_MIN_FILES:
        scan.repo_shape = "flat-package"
        scan.shape_rationale = (
            f"All {total} source files share ≤1 parent directory — classic flat-package layout."
        )
    elif n_dirs >= _STRUCTURED_MIN_DIRS:
        scan.repo_shape = "structured"
        scan.shape_rationale = (
            f"{total} source files spread across {n_dirs} directories — conventional structured layout."
        )
    else:
        scan.repo_shape = "mixed"
        scan.shape_rationale = (
            f"{total} source files in {n_dirs} director{'y' if n_dirs == 1 else 'ies'} "
            f"— partial structure, may benefit from conventional layout."
        )


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


def _has_test_config(directory: Path) -> bool:
    """Return True if *directory* directly contains a test configuration file.

    Used to detect co-located test setups (e.g. ``FE/jest.config.ts``) when
    there is no conventional ``tests/`` or ``__tests__/`` subdirectory.
    """
    for fname in _TEST_CONFIG_FILES:
        if (directory / fname).exists():
            return True
    return False


def _extract_readme_excerpt(readme_path: Path) -> str:
    """Extract the first meaningful paragraph from a README file (up to 200 chars).

    Skips headings, badge lines, blockquotes, and HTML fragments so the result
    is a clean prose sentence describing the project.
    """
    try:
        text = readme_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            if "![" in stripped or "shields.io" in stripped or "badge" in stripped.lower():
                continue
            if stripped.startswith(">") or stripped.startswith("|") or stripped.startswith("<"):
                continue
            # Skip lines that are mostly punctuation / separators
            alnum = sum(1 for c in stripped if c.isalnum())
            if alnum < 10:
                continue
            return stripped[:200]
    except Exception:
        pass
    return ""


def _parse_cli_scripts(path: Path) -> dict[str, str]:
    """Parse ``[project.scripts]`` entries from a ``pyproject.toml`` file.

    Uses a simple line-by-line parser to avoid requiring ``tomllib`` / ``tomli``
    as a hard dependency (available in stdlib only on Python 3.11+).
    """
    scripts: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        in_scripts = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "[project.scripts]":
                in_scripts = True
                continue
            # New TOML section header — stop parsing scripts
            if stripped.startswith("[") and stripped.endswith("]") and in_scripts:
                break
            if in_scripts and "=" in stripped and not stripped.startswith("#"):
                parts = stripped.split("=", 1)
                key = parts[0].strip().strip('"').strip("'")
                val = parts[1].strip().strip('"').strip("'")
                if key:
                    scripts[key] = val
    except Exception:
        pass
    return scripts


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
