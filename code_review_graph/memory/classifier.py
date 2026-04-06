"""Feature and module classifier for the memory subsystem.

Classifies source files into two complementary groupings:

- **Modules** — structural/package boundaries (how the code is organised).
  Based on directory structure, package layouts, and import concentration.

- **Features** — domain/product groupings (what the code does).
  Based on domain-keyword folder names, route/controller/service naming
  patterns, and cross-cutting file clusters.

Neither classification is perfect.  Every result carries a ``confidence``
score so consumers can decide how much to trust it.  The system is designed
to work on messy real-world repos — low confidence with explanatory rationale
is better than silence.

Public API
----------
classify_modules(repo_root, scan)   -> list[ModuleMemory]
classify_features(repo_root, scan)  -> list[FeatureMemory]
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from .models import FeatureMemory, ModuleMemory
from .scanner import RepoScan, _EXT_TO_LANG, _SKIP_DIRS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain keyword vocabulary for feature detection
# ---------------------------------------------------------------------------

# Tier-1: strong domain signals — high confidence when matched
_DOMAIN_KEYWORDS_HIGH: frozenset[str] = frozenset({
    "auth", "authentication", "authorisation", "authorization",
    "login", "logout", "signup", "register", "password", "oauth", "jwt", "session",
    "billing", "payment", "payments", "checkout", "stripe", "invoice", "subscription",
    "user", "users", "account", "accounts", "profile", "profiles",
    "notification", "notifications", "email", "emails", "sms", "webhook", "webhooks",
    "search", "indexing", "query",
    "admin", "dashboard",
    "onboarding",
    "api", "graphql", "rest",
    "upload", "uploads", "storage", "media", "assets",
    "report", "reports", "analytics", "metrics",
    "cart", "order", "orders", "catalog", "product", "products",
    "chat", "messaging", "inbox",
    "audit", "logging", "log",
    "config", "settings", "configuration",
    "migration", "migrations",
    "cache", "caching",
    "task", "tasks", "job", "jobs", "queue", "worker", "workers",
    "health", "monitoring",
    "test", "tests",
})

# Tier-2: weaker signals — medium confidence
_DOMAIN_KEYWORDS_MEDIUM: frozenset[str] = frozenset({
    "core", "common", "shared", "utils", "util", "helpers", "helper",
    "base", "abstract", "mixin", "mixins",
    "middleware", "handler", "handlers",
    "model", "models", "schema", "schemas",
    "view", "views", "template", "templates",
    "route", "routes", "router", "routers",
    "controller", "controllers",
    "service", "services",
    "repository", "repositories", "repo",
    "client", "clients",
    "server",
    "cli",
    "db", "database",
    "types", "interfaces",
})

# Structural directory names that indicate module-level boundaries
_MODULE_BOUNDARY_NAMES: frozenset[str] = frozenset({
    "src", "lib", "app", "apps", "packages", "services", "modules",
    "core", "internal", "cmd", "pkg", "backend", "frontend",
})

# Test file indicators
_TEST_PREFIXES: tuple[str, ...] = ("test_", "spec_")
_TEST_SUFFIXES: tuple[str, ...] = ("_test", "_spec", ".test", ".spec")
_TEST_DIRS: frozenset[str] = frozenset({
    "tests", "test", "spec", "specs", "__tests__", "e2e",
    "integration", "unit", "functional",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_modules(repo_root: Path, scan: RepoScan) -> list[ModuleMemory]:
    """Classify the repository into structural modules.

    A module corresponds to a cohesive package or sub-tree boundary —
    roughly "how the code is organised."

    Strategy (in order of signal strength):
    1. Top-level source sub-packages that contain an ``__init__`` or
       index file (strong boundary signal).
    2. Direct children of monorepo ``apps/``, ``packages/``, ``services/``
       directories.
    3. Top-level source directories themselves when no sub-packages exist.

    Args:
        repo_root: Absolute path to the repository root.
        scan:      Completed :class:`~scanner.RepoScan`.

    Returns:
        Sorted list of :class:`~models.ModuleMemory`, deduplicated, with
        confidence scores attached.
    """
    candidates: dict[str, _ModuleCandidate] = {}

    # Strategy 0 — root-level package files (files directly in the source package root,
    # not in any sub-package).  This catches repos where the source package has both
    # root-level engine files (e.g. parser.py, graph.py) AND a sub-package (e.g. memory/).
    # Without this, root-level files get no module entry at all.
    for src_dir in scan.source_dirs:
        src_path = repo_root / src_dir
        if not src_path.is_dir():
            continue
        if not (src_path / "__init__.py").exists():
            continue
        root_files = [
            f for f in _source_files_under(repo_root, src_path)
            if Path(f).parent == Path(src_dir)
            and Path(f).name != "__init__.py"   # package marker, not a real module file
        ]
        if root_files:
            key = src_dir
            if key not in candidates:
                n = len(root_files)
                confidence = 0.90 if n >= 5 else 0.85 if n >= 3 else 0.80
                rationale = f"root-level package files in '{src_dir}' ({n} files)"
                candidates[key] = _ModuleCandidate(
                    name=src_dir,
                    directory=src_dir,
                    files=root_files,
                    confidence=confidence,
                    rationale=rationale,
                )

    # Strategy 1 — sub-packages inside known source dirs
    for src_dir in scan.source_dirs:
        src_path = repo_root / src_dir
        if src_path.is_dir():
            _collect_subpackages(repo_root, src_path, src_dir, candidates)

    # Strategy 2 — monorepo app/package/service entries
    for top_dir in scan.top_level_dirs:
        if top_dir.lower() in ("apps", "packages", "services", "modules"):
            parent = repo_root / top_dir
            if parent.is_dir():
                _collect_monorepo_entries(repo_root, parent, top_dir, candidates)

    # Strategy 3 — fallback: each source dir is itself a module
    if not candidates:
        for src_dir in scan.source_dirs:
            src_path = repo_root / src_dir
            files = _source_files_under(repo_root, src_path)
            if files:
                key = src_dir
                candidates[key] = _ModuleCandidate(
                    name=src_dir,
                    directory=src_dir,
                    files=files,
                    confidence=0.6,
                    rationale="top-level source directory (no sub-packages detected)",
                )

    # Graph enrichment: compute signals for all candidates in one DB pass.
    groups = {name: cand.files for name, cand in candidates.items()}
    graph_signals = _get_graph_signals(repo_root, groups)

    # Build file → module name map for dependency resolution.
    file_to_module: dict[str, str] = {}
    for name, cand in candidates.items():
        for fp in cand.files:
            file_to_module[fp] = cand.name

    # Build ModuleMemory objects
    result: list[ModuleMemory] = []
    for cand in candidates.values():
        tests = _find_tests_for(repo_root, scan, cand.files)
        confidence = cand.confidence
        sig = graph_signals.get(cand.name)
        if sig is not None:
            # Adjust confidence based on internal graph connectivity.
            delta = sig.confidence_delta(len(cand.files))
            confidence = max(0.2, min(0.98, confidence + delta))
            # Merge graph-found tests (TESTED_BY edges) with heuristic tests.
            tests = sorted(set(tests) | set(sig.test_files))
        result.append(ModuleMemory(
            name=cand.name,
            files=sorted(cand.files),
            tests=tests,
            confidence=round(confidence, 2),
            summary=cand.rationale,
        ))

    # Populate dependencies / dependents from graph signals (module-level edges).
    if graph_signals:
        _resolve_module_dependencies(result, graph_signals, file_to_module)

    return sorted(result, key=lambda m: m.name)


def classify_features(repo_root: Path, scan: RepoScan) -> list[FeatureMemory]:
    """Classify the repository into domain/product features.

    A feature corresponds to a user-visible capability or domain area —
    roughly "what the code does."

    Strategy (in order of signal strength):
    1. Directories whose name matches a Tier-1 domain keyword (high confidence).
    2. Directories whose name matches a Tier-2 domain keyword (medium confidence).
    3. Directories that appear across multiple source sub-trees with the same
       name token (cross-cutting feature signal, lower confidence).

    Only directories that contain at least one source file are emitted.
    Directories already claimed by a higher-tier match are not re-emitted.

    Args:
        repo_root: Absolute path to the repository root.
        scan:      Completed :class:`~scanner.RepoScan`.

    Returns:
        Sorted list of :class:`~models.FeatureMemory`, deduplicated, with
        confidence scores attached.
    """
    candidates: dict[str, _FeatureCandidate] = {}

    # Strategy 0 (highest priority): graph-based import clustering.
    # Uses IMPORTS_FROM edges to find connected file clusters, then names
    # them by dominant domain keyword. This finds cross-cutting features
    # like "auth" across routes/ + services/ + middleware/ that directory
    # scanning misses entirely.
    _cluster_by_imports(repo_root, scan, candidates)

    # Walk every source directory (fills gaps where graph clustering found nothing)
    search_roots = _feature_search_roots(repo_root, scan)

    for search_root in search_roots:
        _scan_for_features(repo_root, search_root, candidates)

    # Cross-cutting: tokens that appear in multiple sub-trees (lower confidence)
    _detect_cross_cutting(repo_root, scan, candidates)

    # Graph enrichment: compute signals for all candidates in one DB pass.
    groups = {cand.name: cand.files for cand in candidates.values() if cand.files}
    graph_signals = _get_graph_signals(repo_root, groups)

    # Build FeatureMemory objects
    result: list[FeatureMemory] = []
    for cand in candidates.values():
        if not cand.files:
            continue
        tests = _find_tests_for(repo_root, scan, cand.files)
        confidence = cand.confidence
        sig = graph_signals.get(cand.name)
        if sig is not None:
            # Adjust confidence based on internal graph connectivity.
            delta = sig.confidence_delta(len(cand.files))
            confidence = max(0.2, min(0.98, confidence + delta))
            # Merge graph-found tests (TESTED_BY edges) with heuristic tests.
            tests = sorted(set(tests) | set(sig.test_files))
        result.append(FeatureMemory(
            name=cand.name,
            files=sorted(cand.files),
            tests=tests,
            confidence=round(confidence, 2),
            summary=cand.rationale,
        ))

    result = sorted(result, key=lambda f: f.name)

    # Flat-package / feature-poor rescue: when no features were found via normal
    # heuristics, or the scan explicitly reports a flat-package layout, attempt
    # embedding-assisted or keyword-name grouping.
    if not result or scan.repo_shape == "flat-package":
        try:
            from .flat_rescue import rescue_flat_features
            rescued = rescue_flat_features(repo_root, scan, result)
            if rescued:
                # Merge rescued features — avoid duplicating any already-found names
                existing_names = {f.name.lower() for f in result}
                for rf in rescued:
                    if rf.name.lower() not in existing_names:
                        result.append(rf)
                        existing_names.add(rf.name.lower())
                result = sorted(result, key=lambda f: f.name)
        except Exception as exc:
            logger.debug("classifier: flat rescue failed: %s", exc)

    return result


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


class _ModuleCandidate:
    __slots__ = ("name", "directory", "files", "confidence", "rationale")

    def __init__(
        self,
        name: str,
        directory: str,
        files: list[str],
        confidence: float,
        rationale: str,
    ) -> None:
        self.name = name
        self.directory = directory
        self.files = files
        self.confidence = confidence
        self.rationale = rationale


class _FeatureCandidate:
    __slots__ = ("name", "files", "confidence", "rationale")

    def __init__(
        self,
        name: str,
        files: list[str],
        confidence: float,
        rationale: str,
    ) -> None:
        self.name = name
        self.files = files
        self.confidence = confidence
        self.rationale = rationale


# ---------------------------------------------------------------------------
# Module classification helpers
# ---------------------------------------------------------------------------


def _collect_subpackages(
    repo_root: Path,
    src_path: Path,
    src_dir: str,
    candidates: dict[str, _ModuleCandidate],
) -> None:
    """Collect immediate sub-directories of *src_path* that look like packages."""
    for entry in sorted(src_path.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue

        files = _source_files_under(repo_root, entry)
        if not files:
            continue

        # Strong signal: has __init__.py / index file
        has_init = (
            (entry / "__init__.py").exists()
            or (entry / "index.ts").exists()
            or (entry / "index.js").exists()
            or (entry / "mod.rs").exists()
            or (entry / "main.go").exists()
        )

        name = f"{src_dir}/{entry.name}"
        confidence = 0.9 if has_init else 0.7
        rationale = (
            f"sub-package of '{src_dir}' with package init file"
            if has_init
            else f"sub-directory of '{src_dir}' containing source files"
        )

        # Don't create a sub-package entry if the source dir itself has no further
        # structure — that would just duplicate the parent.
        key = name
        if key not in candidates:
            candidates[key] = _ModuleCandidate(
                name=name,
                directory=str(entry.relative_to(repo_root)),
                files=files,
                confidence=confidence,
                rationale=rationale,
            )


def _collect_monorepo_entries(
    repo_root: Path,
    parent: Path,
    parent_name: str,
    candidates: dict[str, _ModuleCandidate],
) -> None:
    """Collect direct children of monorepo container dirs (apps/, packages/, etc.)."""
    for entry in sorted(parent.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in _SKIP_DIRS or entry.name.startswith("."):
            continue

        files = _source_files_under(repo_root, entry)
        if not files:
            continue

        name = f"{parent_name}/{entry.name}"
        key = name
        if key not in candidates:
            candidates[key] = _ModuleCandidate(
                name=name,
                directory=str(entry.relative_to(repo_root)),
                files=files,
                confidence=0.85,
                rationale=f"monorepo entry under '{parent_name}/'",
            )


# ---------------------------------------------------------------------------
# Feature classification helpers
# ---------------------------------------------------------------------------


def _feature_search_roots(repo_root: Path, scan: RepoScan) -> list[Path]:
    """Return directories to scan for feature keywords."""
    roots: list[Path] = []

    for src_dir in scan.source_dirs:
        p = repo_root / src_dir
        if p.is_dir():
            roots.append(p)

    # Also scan monorepo containers one level deeper
    for top in scan.top_level_dirs:
        if top.lower() in ("apps", "packages", "services", "modules"):
            p = repo_root / top
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_dir() and child.name not in _SKIP_DIRS:
                        roots.append(child)

    # Fallback: scan from repo root
    if not roots:
        roots.append(repo_root)

    return roots


def _scan_for_features(
    repo_root: Path,
    search_root: Path,
    candidates: dict[str, _FeatureCandidate],
) -> None:
    """Walk *search_root* and emit feature candidates for keyword-matching dirs."""
    for entry in sorted(search_root.rglob("*")):
        if not entry.is_dir():
            continue
        # Skip noise
        try:
            parts = entry.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
            continue

        token = entry.name.lower()

        if token in _DOMAIN_KEYWORDS_HIGH:
            confidence = 0.9
            rationale = f"high-confidence domain keyword '{entry.name}'"
        elif token in _DOMAIN_KEYWORDS_MEDIUM:
            confidence = 0.6
            rationale = f"medium-confidence structural keyword '{entry.name}'"
        else:
            continue

        files = _source_files_under(repo_root, entry)
        if not files:
            continue

        # Use the directory name as the feature name (title-cased)
        name = _title_case_name(entry.name)
        key = name.lower()

        if key not in candidates:
            candidates[key] = _FeatureCandidate(
                name=name,
                files=files,
                confidence=confidence,
                rationale=rationale,
            )
        else:
            # Merge files if the same feature name appears in multiple locations
            existing = candidates[key]
            merged = sorted(set(existing.files) | set(files))
            existing.files = merged
            # Keep the higher confidence of the two
            existing.confidence = max(existing.confidence, confidence)


def _detect_cross_cutting(
    repo_root: Path,
    scan: RepoScan,
    candidates: dict[str, _FeatureCandidate],
) -> None:
    """Detect directory name tokens that repeat across multiple source sub-trees.

    When the same non-trivial name appears as a sub-directory under two or more
    different source roots, it is likely a cross-cutting domain feature.
    """
    # Map token -> list of matching directories
    token_dirs: dict[str, list[Path]] = defaultdict(list)

    # Use source_dirs when available; fall back to all non-noise top-level dirs
    search_tops = scan.source_dirs if scan.source_dirs else scan.top_level_dirs

    for src_dir in search_tops:
        src_path = repo_root / src_dir
        if not src_path.is_dir():
            continue
        for entry in src_path.iterdir():
            if entry.is_dir() and entry.name not in _SKIP_DIRS and not entry.name.startswith("."):
                token_dirs[entry.name.lower()].append(entry)

    for token, dirs in token_dirs.items():
        if len(dirs) < 2:
            continue
        # Skip generic names and already-classified features
        if token in _DOMAIN_KEYWORDS_HIGH or token in _DOMAIN_KEYWORDS_MEDIUM:
            continue
        if token in candidates:
            continue
        if len(token) < 3:
            continue

        all_files: list[str] = []
        for d in dirs:
            all_files.extend(_source_files_under(repo_root, d))

        if not all_files:
            continue

        name = _title_case_name(token)
        candidates[token] = _FeatureCandidate(
            name=name,
            files=sorted(all_files),
            confidence=0.45,
            rationale=f"token '{token}' appears in {len(dirs)} source sub-trees",
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _source_files_under(repo_root: Path, directory: Path) -> list[str]:
    """Return repo-relative paths of all *non-test* source files under *directory*."""
    files: list[str] = []
    if not directory.is_dir():
        return files
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        try:
            parts = f.relative_to(repo_root).parts
        except ValueError:
            continue
        if any(p in _SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        if f.suffix.lower() not in _EXT_TO_LANG:
            continue
        # Exclude test files so they don't appear in the "Main files" list
        stem = f.stem.lower()
        if stem.startswith(_TEST_PREFIXES) or stem.endswith(_TEST_SUFFIXES):
            continue
        if any(p in _TEST_DIRS for p in parts):
            continue
        files.append(str(f.relative_to(repo_root)).replace("\\", "/"))
    return sorted(files)


def _find_tests_for(
    repo_root: Path,
    scan: RepoScan,
    source_files: list[str],
) -> list[str]:
    """Return repo-relative test files likely related to *source_files*.

    Matches by:
    1. Test file name mirrors a source file name (``test_auth.py`` ↔ ``auth.py``).
    2. Test file lives in a test directory and its stem contains a source file stem.
    """
    if not scan.test_dirs:
        return []

    # Build a set of source file stems for quick lookup
    source_stems = {Path(f).stem.lower() for f in source_files}
    # Also collect the parent directory tokens
    source_tokens: set[str] = set()
    for f in source_files:
        parts = Path(f).parts
        source_tokens.update(p.lower() for p in parts)

    tests: list[str] = []
    for test_dir in scan.test_dirs:
        test_path = repo_root / test_dir
        if not test_path.is_dir():
            continue
        for f in test_path.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in _EXT_TO_LANG:
                continue
            stem = f.stem.lower()
            # Strip test_ / _test prefixes/suffixes for matching
            clean = stem
            for prefix in _TEST_PREFIXES:
                if clean.startswith(prefix):
                    clean = clean[len(prefix):]
            for suffix in _TEST_SUFFIXES:
                if clean.endswith(suffix):
                    clean = clean[: -len(suffix)]

            if clean in source_stems or any(tok in clean for tok in source_tokens if len(tok) > 3):
                try:
                    rel = str(f.relative_to(repo_root)).replace("\\", "/")
                    tests.append(rel)
                except ValueError:
                    pass

    return sorted(set(tests))


def _title_case_name(raw: str) -> str:
    """Convert a directory token like ``auth`` to a display name like ``Auth``.

    Handles snake_case, kebab-case, and camelCase reasonably.
    """
    # Split on underscores and hyphens
    parts = raw.replace("-", "_").split("_")
    return " ".join(p.capitalize() for p in parts if p)


# ---------------------------------------------------------------------------
# Graph enrichment helpers
# ---------------------------------------------------------------------------


def _get_graph_signals(
    repo_root: Path,
    groups: dict[str, list[str]],
) -> dict:
    """Load graph signals for all groups in a single DB pass.

    Returns an empty dict when the graph is unavailable or any error occurs,
    allowing the caller to proceed with filesystem-only classification.
    """
    if not groups:
        return {}
    try:
        from .graph_bridge import get_all_classifier_signals
        return get_all_classifier_signals(groups, repo_root)
    except Exception as exc:
        logger.debug("classifier: graph signals unavailable: %s", exc)
        return {}


def _resolve_module_dependencies(
    modules: list[ModuleMemory],
    signals: dict,
    file_to_module: dict[str, str],
) -> None:
    """Populate ``dependencies`` and ``dependents`` on each module using graph signals.

    Maps the raw file paths in :attr:`ClassifierGraphSignals.external_dep_files`
    and :attr:`ClassifierGraphSignals.external_dependent_files` back to named
    modules via *file_to_module*.  Updates the lists in-place.

    Args:
        modules:        The full list of classified modules (mutated in-place).
        signals:        Mapping of module name → ClassifierGraphSignals.
        file_to_module: Mapping of repo-relative file path → owning module name.
    """
    for module in modules:
        sig = signals.get(module.name)
        if sig is None:
            continue

        # Modules that this module imports from.
        dep_names: set[str] = set()
        for fp in sig.external_dep_files:
            owner = file_to_module.get(fp)
            if owner and owner != module.name:
                dep_names.add(owner)
        module.dependencies = sorted(dep_names)

        # Modules that import from this module.
        dependent_names: set[str] = set()
        for fp in sig.external_dependent_files:
            owner = file_to_module.get(fp)
            if owner and owner != module.name:
                dependent_names.add(owner)
        module.dependents = sorted(dependent_names)


# ---------------------------------------------------------------------------
# Graph-based import clustering (Strategy 0)
# ---------------------------------------------------------------------------


def _cluster_by_imports(
    repo_root: Path,
    scan: RepoScan,
    candidates: dict[str, "_FeatureCandidate"],
) -> None:
    """Populate *candidates* using keyword-anchored subgraph expansion.

    For each Tier-1 domain keyword found in any source file path:
    1. Collect anchor files — files whose path contains the keyword
    2. Expand 1 hop forward (files the anchors import)
    3. Expand 1 hop backward (files that import the anchors)
    4. Exclude shared infrastructure hubs (imported by many unrelated files)

    This finds cross-cutting features that span directories — e.g. "auth"
    across routes/auth.ts + services/auth.service.ts + middleware/auth.ts.
    Only emits a feature when the result spans >= 2 different directories,
    meaning it genuinely crosses the directory boundary (directory scan
    handles the single-directory case).

    This is strictly additive: only inserts new candidates, never removes
    or replaces anything already in *candidates*.
    """
    import re as _re

    try:
        from .graph_bridge import get_import_graph

        # Collect all source files across source dirs
        all_source: list[str] = []
        for src_dir in scan.source_dirs:
            src_path = repo_root / src_dir
            if not src_path.is_dir():
                continue
            for p in src_path.rglob("*"):
                if p.is_file() and p.suffix in _EXT_TO_LANG:
                    skip = any(part in _SKIP_DIRS for part in p.parts)
                    if not skip:
                        try:
                            all_source.append(str(p.relative_to(repo_root)))
                        except ValueError:
                            pass

        if not all_source:
            return

        import_graph = get_import_graph(repo_root, all_source)
        if not import_graph:
            return

        # Build reverse index: target → set of files that import it
        reverse: dict[str, set[str]] = defaultdict(set)
        for src, targets in import_graph.items():
            for tgt in targets:
                reverse[tgt].add(src)

        # Mark shared infrastructure hubs — files imported by many unrelated files
        # (config/env.ts, db/prisma.ts, utils/logger.ts) should not anchor features
        _HUB_THRESHOLD = 4
        hub_files: set[str] = {fp for fp, importers in reverse.items()
                               if len(importers) >= _HUB_THRESHOLD}

        # Find keyword anchors: Tier-1 domain keywords present in any file path
        keyword_anchors: dict[str, set[str]] = defaultdict(set)
        for f in all_source:
            if _is_test_path(f):
                continue
            p = Path(f)
            for part in list(p.parts) + [p.stem]:
                spaced = _re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
                for tok in _re.split(r"[\W_]+", spaced.lower()):
                    if len(tok) >= 3 and tok in _DOMAIN_KEYWORDS_HIGH:
                        keyword_anchors[tok].add(f)

        if not keyword_anchors:
            return

        # Expand each keyword 1 hop in both directions
        for keyword, anchors in keyword_anchors.items():
            feature_files: set[str] = set(anchors)

            for anchor in anchors:
                # Forward: what anchor imports (excluding shared hubs)
                for imported in import_graph.get(anchor, []):
                    if imported not in hub_files and not _is_test_path(imported):
                        feature_files.add(imported)
                # Backward: what imports anchor (excluding shared hubs)
                for importer in reverse.get(anchor, set()):
                    if importer not in hub_files and not _is_test_path(importer):
                        feature_files.add(importer)

            if len(feature_files) < 2:
                continue

            # Only emit when result spans multiple directories (cross-cutting signal)
            unique_dirs = {
                str(Path(f).parent) for f in feature_files
            }
            if len(unique_dirs) < 2:
                continue

            feature_name = keyword.title()
            existing = candidates.get(feature_name)
            if existing is not None and existing.confidence >= 0.80:
                # Already found with high confidence — just merge in any new files
                existing.files = sorted(set(existing.files) | feature_files)
                continue

            candidates[feature_name] = _FeatureCandidate(
                name=feature_name,
                files=sorted(feature_files),
                confidence=0.80,
                rationale=(
                    f"graph import cluster — '{keyword}' anchor across "
                    f"{len(unique_dirs)} directories, {len(feature_files)} file(s)"
                ),
            )

    except Exception as exc:
        logger.debug("_cluster_by_imports: failed: %s", exc)


def _is_test_path(file_path: str) -> bool:
    """Return True if the file looks like a test file."""
    lp = file_path.lower()
    return any(frag in lp for frag in (
        "/tests/", "/test/", "/spec/",
        "test_", "_test.", ".spec.", ".test.",
    ))


def _name_cluster(files: list[str]) -> str | None:
    """Return the best domain keyword name for a cluster, or None.

    Scores every path token in the cluster against high- and medium-tier
    domain keyword vocabulary. Returns the token with the highest weighted
    hit count, biased toward Tier-1 keywords.
    """
    scores: dict[str, float] = defaultdict(float)

    for f in files:
        p = Path(f)
        # Tokens from directory components + file stem
        parts = list(p.parts[:-1]) + [p.stem]
        for part in parts:
            # Split camelCase + delimiters
            import re
            tokens_raw = re.sub(r"([a-z])([A-Z])", r"\1 \2", part)
            tokens = [t.lower() for t in re.split(r"[\W_]+", tokens_raw) if len(t) >= 3]
            for tok in tokens:
                if tok in _DOMAIN_KEYWORDS_HIGH:
                    scores[tok] += 2.0
                elif tok in _DOMAIN_KEYWORDS_MEDIUM:
                    scores[tok] += 1.0

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    # Require a minimum signal strength
    return best if scores[best] >= 2.0 else None
