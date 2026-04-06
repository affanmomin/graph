# Phase 2 Tickets — repomind Production Readiness

> Hand this entire file to Claude in a new session.
> These 3 tickets are the critical path to making repomind ready for a 5-person internal pilot.

---

## Product Context

**repomind** is a CLI tool + MCP server that gives every repository a durable memory layer for Claude Code.
Core promise: **stop re-explaining your repo to AI every session.**

Architecture has 3 layers:
- **Layer A** (existing): code intelligence engine — Tree-sitter parser, SQLite graph, incremental updates
- **Layer B** (built): memory engine — `code_review_graph/memory/` — scanner, classifier, generator, context builder
- **Layer C** (built): agent interface — CLI commands + MCP tools

The memory layer generates `.agent-memory/` (committed to Git):
```
.agent-memory/
  CLAUDE.md            ← session bootstrap (~250 tokens, loaded automatically by Claude Code)
  architecture.md
  features/*.md
  modules/*.md
  changes/recent.md
  rules/
  metadata/
  overrides/
```

Key commands:
```bash
repomind build                                    # parse repo → graph.db (SQLite)
repomind memory init                              # scan + classify → generate .agent-memory/
repomind memory prepare-context "your task"       # return focused context pack for a task
repomind memory refresh                           # incremental update after code changes
repomind memory stats                             # show token savings + performance metrics
```

---

## The Problem Being Solved by These Tickets

`repomind memory prepare-context` currently takes **9 seconds** on average.
This matters because a `UserPromptSubmit` hook (in `hooks/hooks.json`) fires it automatically
every time a user types a task in Claude Code. A 9-second delay on every message
kills the product experience and will cause users to disable the hook on day one.

Additionally, **67% of prepare-context runs fall back** to a generic context dump
because the classifier can't match the task to a specific feature.
This makes the output noise rather than signal.

These 3 tickets fix both problems and complete the zero-friction install.

---

## Ticket 1 — Pre-build a pack cache at `memory init` time

### Problem

`memory_prepare_context_command` (in `code_review_graph/memory/commands.py:727`) runs this
pipeline on **every single call**:

```python
scan = scan_repo(repo_root)                   # walks filesystem
features = classify_features(repo_root, scan) # classifies all files
modules = classify_modules(repo_root, scan)   # same
# then: get_file_vocabulary() → graph DB query
# then: get_task_symbol_files() → graph DB query
# then: get_related_files() → graph DB query
# then: get_structural_neighbors() → graph DB query
```

Total: ~9 seconds. All of this except the final task-specific graph query is
**identical on every call** — same repo, same features, same modules, same vocabulary.

### Fix

At `memory init` time, pre-compute everything that doesn't depend on the task
and save it to `.agent-memory/metadata/pack_cache.json`.

At `prepare-context` time, load the cache (instant JSON read) and skip the
scan + classify + vocabulary fetch. Only run the task-specific graph query.

### What to build

**New file: `code_review_graph/memory/pack_cache.py`**

```python
"""Pre-computed pack cache for fast prepare-context lookups.

Written at memory init time. Read at prepare-context time.
Stored at .agent-memory/metadata/pack_cache.json (committed to Git).
"""
from __future__ import annotations
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

_CACHE_FILE = "metadata/pack_cache.json"
_CACHE_VERSION = 1


def build_pack_cache(
    features: list,           # list[FeatureMemory]
    modules: list,            # list[ModuleMemory]
    vocabulary: dict[str, list[str]],
    repo_root: Path,
) -> dict[str, Any]:
    """Build the cache dict. Called from run_memory_init_pipeline()."""
    return {
        "version": _CACHE_VERSION,
        "features": [
            {
                "name": f.name,
                "files": f.files,
                "tests": f.tests,
                "confidence": f.confidence,
                "summary": f.summary,
                # keyword index: all terms derived from name + file stems + dir parts + symbols
                "keywords": _extract_keywords(f.name, f.files, vocabulary),
            }
            for f in features
        ],
        "modules": [
            {
                "name": m.name,
                "files": m.files,
                "tests": m.tests,
                "confidence": m.confidence,
                "summary": m.summary,
                "keywords": _extract_keywords(m.name, m.files, vocabulary),
            }
            for m in modules
        ],
    }


def save_pack_cache(cache: dict, memory_root: Path) -> None:
    """Write cache to .agent-memory/metadata/pack_cache.json."""
    path = memory_root / _CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def load_pack_cache(memory_root: Path) -> dict | None:
    """Load cache. Returns None if missing or version mismatch."""
    path = memory_root / _CACHE_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_VERSION:
            return None
        return data
    except Exception:
        return None


def _extract_keywords(name: str, files: list[str], vocabulary: dict[str, list[str]]) -> list[str]:
    """Extract all matchable terms for a feature/module."""
    import re
    from pathlib import Path as P
    terms: set[str] = set()

    # name tokens
    for tok in re.split(r"[-_/\s]", name.lower()):
        if len(tok) > 2:
            terms.add(tok)

    # file stem tokens
    for f in files:
        stem = P(f).stem.lower()
        for tok in re.split(r"[-_]", stem):
            if len(tok) > 2:
                terms.add(tok)

    # directory tokens
    for f in files:
        for part in P(f).parts[:-1]:
            for tok in re.split(r"[-_]", part.lower()):
                if len(tok) > 2:
                    terms.add(tok)

    # symbol names from graph vocabulary (function/class names)
    for f in files:
        for sym in vocabulary.get(f, []):
            for tok in re.split(r"[-_A-Z]", sym):
                tok = tok.lower()
                if len(tok) > 3:
                    terms.add(tok)

    return sorted(terms)
```

**Wire into `run_memory_init_pipeline()` in `commands.py`**

After the vocabulary is fetched (around line 240, after the signal cache block),
add:

```python
# Pre-build pack cache for fast prepare-context lookups
from .pack_cache import build_pack_cache, save_pack_cache
_pack_cache = build_pack_cache(features, modules, vocabulary, root)
save_pack_cache(_pack_cache, mem_root)
```

**Wire into `memory_prepare_context_command()` in `commands.py`**

Replace the scan + classify block (lines 768-770) with cache-first logic:

```python
_t0 = time.perf_counter()

# Fast path: load pre-built cache if available
from .pack_cache import load_pack_cache
from .models import FeatureMemory, ModuleMemory
_cache = load_pack_cache(_agent_memory_root(repo_root))

if _cache is not None:
    # Reconstruct lightweight FeatureMemory / ModuleMemory from cache
    features = [
        FeatureMemory(
            name=f["name"], files=f["files"], tests=f["tests"],
            confidence=f["confidence"], summary=f["summary"],
        )
        for f in _cache.get("features", [])
    ]
    modules = [
        ModuleMemory(
            name=m["name"], files=m["files"], tests=m["tests"],
            confidence=m["confidence"], summary=m["summary"],
        )
        for m in _cache.get("modules", [])
    ]
    vocabulary = None  # already baked into cache keywords; graph boost still runs below
else:
    # Slow path: fall back to live scan (memory not initialized)
    from .scanner import scan_repo
    from .classifier import classify_features, classify_modules
    scan = scan_repo(repo_root)
    features = classify_features(repo_root, scan)
    modules = classify_modules(repo_root, scan)
    vocabulary = None
```

### Expected outcome

- `prepare-context` with cache: **< 0.5s** (was 9s)
- `prepare-context` without cache (first run / no init): unchanged slow path
- `pack_cache.json` committed to Git alongside other artifacts

### Tests to add (`tests/test_memory_pack_cache.py`)

```python
def test_build_and_load_roundtrip(tmp_path):
    """Cache survives a write/read roundtrip with correct structure."""

def test_load_returns_none_when_missing(tmp_path):
    """load_pack_cache returns None when file doesn't exist."""

def test_load_returns_none_on_version_mismatch(tmp_path):
    """load_pack_cache returns None when version field doesn't match."""

def test_keywords_include_file_stems(tmp_path):
    """Keywords for a feature include tokens from its file stems."""

def test_keywords_include_symbols(tmp_path):
    """Keywords include camelCase-split symbol names from vocabulary."""
```

### Verification

```bash
# 1. Rebuild cache
uv run repomind memory init

# 2. Check cache was written
cat .agent-memory/metadata/pack_cache.json | python3 -c "
import sys, json; d=json.load(sys.stdin)
print('features:', len(d['features']))
print('modules:', len(d['modules']))
print('keywords sample:', d['features'][0]['keywords'][:10])
"

# 3. Time prepare-context (should be <1s now)
time uv run repomind memory prepare-context "add a new language parser for Ruby"

# 4. Run tests
uv run pytest tests/test_memory_pack_cache.py -x --tb=short -q
```

---

## Ticket 2 — Better keyword matching to fix 67% fallback rate

### Problem

`repomind memory stats` shows 6 out of 9 prepare-context runs returned `[fallback]`.
Fallback means `_score()` in `context_builder.py:278` returned 0 for all features/modules,
so the pack just dumps the top candidates with no real matching.

The root cause: `_score()` only matches against feature **name** + **file stems** + **directory parts**.
If a user types "create a modal for the contact form", and the feature is named "UI Components"
with files like `src/components/ContactForm.tsx`, the name "UI Components" has zero overlap
with "modal", "contact", "form". The file stem `ContactForm` should match but `_tokenize()`
splits on non-alphanumeric only — it doesn't split `ContactForm` into `contact` + `form`.

### Fix

Two changes:

**1. Split camelCase in `_tokenize()` in `context_builder.py:251`**

Current:
```python
def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.split(r"\W+", text) if len(t) > 2}
```

Fix — also split on camelCase boundaries:
```python
def _tokenize(text: str) -> set[str]:
    # First split camelCase (e.g. ContactForm → Contact Form)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return {t.lower() for t in re.split(r"[\W_]+", text) if len(t) > 2}
```

This means `ContactForm.tsx` stem → `{"contact", "form"}` instead of `{"contactform"}`.

**2. Use pre-computed keywords from pack cache in `_score()`**

The pack cache (built in Ticket 1) already extracted keyword sets per feature/module
including symbols. Pass them into `_score()` as an additional matching source.

In `build_context_pack()` (line 110), after loading the cache, attach keywords to
each feature/module. Then in `_score()` add a Component 5: keyword overlap.

The simplest approach: store keywords on the model instances at runtime (not persisted
to the dataclass — just a temporary dict passed alongside).

In `build_context_pack()`:
```python
# Build keyword lookup from cache if available
_kw_map: dict[str, set[str]] = {}
if _cache is not None:
    for entry in [*_cache.get("features", []), *_cache.get("modules", [])]:
        _kw_map[entry["name"]] = set(entry["keywords"])
```

Then pass `_kw_map` into `_score()`:
```python
def _score(
    task_tokens: set[str],
    name: str,
    files: list[str],
    confidence: float,
    vocabulary: dict[str, list[str]] | None = None,
    keywords: set[str] | None = None,   # ← new param
) -> float:
```

Add Component 5 at the bottom:
```python
# Component 5: pre-computed keyword overlap (from pack cache)
keyword_overlap = 0.0
if keywords:
    keyword_overlap = min(len(task_tokens & keywords) / n, 1.0)
```

Include it in the final weighted sum with weight `_W_KEYWORD = 1.2` (slightly higher
than file-stem, because these keywords include symbol names and are more specific).

### Expected outcome

- Fallback rate drops from 67% to <25% on typical task descriptions
- Tasks like "create a modal", "fix the login form", "add rate limiting" match correctly
  even when feature names are generic ("UI", "Auth", "API")

### Tests to add (`tests/test_memory_context_builder.py` additions)

```python
def test_camelcase_tokenize():
    """ContactForm tokenizes to {'contact', 'form'}."""
    from code_review_graph.memory.context_builder import _tokenize
    assert "contact" in _tokenize("ContactForm")
    assert "form" in _tokenize("ContactForm")

def test_score_uses_keywords():
    """_score returns higher value when keywords overlap with task tokens."""

def test_no_fallback_on_camelcase_task(tmp_path):
    """build_context_pack does not fall back when task matches via camelCase file name."""
```

### Verification

```bash
# Run several tasks that previously fell back
uv run repomind memory prepare-context "create a modal for the contact form"
uv run repomind memory prepare-context "fix the login button"
uv run repomind memory prepare-context "add rate limiting to the API"

# Check stats — fallback count should drop
uv run repomind memory stats

# Run tests
uv run pytest tests/test_memory_context_builder.py -x --tb=short -q
```

---

## Ticket 3 — Auto-install the UserPromptSubmit hook on `memory init`

### Problem

A `UserPromptSubmit` hook exists at `hooks/hooks.json` that automatically runs
`prepare-context` on every message the user types in Claude Code. This gives
zero-friction context injection — user types a task, Claude sees the context pack
automatically with no manual command.

But the hook isn't wired up automatically. Each user has to manually edit
`~/.claude/settings.json` to register it. That's friction that kills adoption.

### Fix

At the end of `run_memory_init_pipeline()` (or `memory_init_command()`),
call a new function `install_prompt_hook()` that writes the hook entry into
`~/.claude/settings.json` if it isn't already there.

### What to build

**New function in `commands.py` (or a new `code_review_graph/memory/hooks.py`)**

```python
def install_prompt_hook(repo_root: Path) -> bool:
    """Write the UserPromptSubmit hook entry to ~/.claude/settings.json.

    Safe to call multiple times — checks if already installed before writing.
    Returns True if newly installed, False if already present or failed.
    """
    import json
    from pathlib import Path

    settings_path = Path.home() / ".claude" / "settings.json"

    # The hook command to inject
    hook_command = str(repo_root / "hooks" / "prompt-context.sh")
    hook_entry = {
        "type": "command",
        "command": hook_command,
    }

    try:
        # Load existing settings or start fresh
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        else:
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings = {}

        hooks = settings.setdefault("hooks", {})
        ups_hooks = hooks.setdefault("UserPromptSubmit", [])

        # Check if already installed (avoid duplicates)
        for block in ups_hooks:
            for h in block.get("hooks", []):
                if h.get("command") == hook_command:
                    return False  # already installed

        # Append a new hook block
        ups_hooks.append({"hooks": [hook_entry]})
        settings_path.write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )
        return True

    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("install_prompt_hook failed: %s", exc)
        return False
```

**Wire into `memory_init_command()` in `commands.py`**

At the end of `memory_init_command()`, after printing the summary, call:

```python
from .hooks import install_prompt_hook  # or inline if in commands.py
_hook_installed = install_prompt_hook(repo_root)
if _hook_installed:
    print("  Hook: UserPromptSubmit hook installed → Claude Code will auto-inject context.")
else:
    print("  Hook: already installed.")
```

**Important: only install if `hooks/prompt-context.sh` exists**

```python
_hook_script = repo_root / "hooks" / "prompt-context.sh"
if _hook_script.exists():
    _hook_installed = install_prompt_hook(repo_root)
    ...
```

### The hook script

The hook script already exists at `hooks/prompt-context.sh`. It:
1. Reads the user's prompt from stdin
2. Skips messages under 8 words (short follow-ups like "yes", "continue")
3. Runs `repomind memory prepare-context "$PROMPT"`
4. Outputs the context block so Claude sees it before the user's message

The `hooks/hooks.json` already has it registered under `UserPromptSubmit`.
The missing piece is auto-writing it into `~/.claude/settings.json` at init time.

### Expected outcome

- After `repomind memory init`, the hook is active in Claude Code with no manual steps
- User opens Claude Code, types "add dark mode to the settings page", and Claude
  immediately receives the relevant features/files/tests without any manual command
- `memory init` output includes a confirmation line: `Hook: UserPromptSubmit hook installed`

### Tests to add (`tests/test_memory_hooks.py`)

```python
def test_install_hook_writes_settings(tmp_path, monkeypatch):
    """install_prompt_hook creates ~/.claude/settings.json with correct structure."""

def test_install_hook_idempotent(tmp_path, monkeypatch):
    """Calling install_prompt_hook twice does not duplicate the entry."""

def test_install_hook_merges_with_existing_settings(tmp_path, monkeypatch):
    """install_prompt_hook preserves existing settings.json content."""

def test_install_hook_skips_when_script_missing(tmp_path):
    """install_prompt_hook returns False and does not crash when script not found."""
```

### Verification

```bash
# 1. Run init (should auto-install hook)
uv run repomind memory init

# 2. Verify hook appears in Claude settings
cat ~/.claude/settings.json | python3 -c "
import sys, json; s=json.load(sys.stdin)
hooks = s.get('hooks', {}).get('UserPromptSubmit', [])
print('UserPromptSubmit hooks:', len(hooks))
for b in hooks:
    for h in b.get('hooks', []):
        print(' -', h.get('command'))
"

# 3. Run tests
uv run pytest tests/test_memory_hooks.py -x --tb=short -q
```

---

## Running All Tests

```bash
# Full test suite baseline (should be 1412 passing before you start)
uv run pytest tests/ -q --tb=no

# After implementing all 3 tickets
uv run pytest tests/test_memory_pack_cache.py tests/test_memory_context_builder.py tests/test_memory_hooks.py -x --tb=short -q

# Full suite (should still be 1412+ passing)
uv run pytest tests/ -q --tb=no
```

---

## Key Files Reference

| File | What it does |
|------|-------------|
| `code_review_graph/memory/commands.py` | All CLI command handlers. `memory_prepare_context_command` at line 727. `run_memory_init_pipeline` at line 159. |
| `code_review_graph/memory/context_builder.py` | `build_context_pack()` — scores features/modules against a task. `_score()` at line 278. `_tokenize()` at line 251. |
| `code_review_graph/memory/scanner.py` | `scan_repo()` — walks filesystem, classifies dirs, extracts languages. |
| `code_review_graph/memory/classifier.py` | `classify_features()`, `classify_modules()` — groups files into product areas. |
| `code_review_graph/memory/models.py` | `FeatureMemory`, `ModuleMemory`, `TaskContextPack` dataclasses. |
| `code_review_graph/memory/graph_bridge.py` | Interface to the graph engine. `get_file_vocabulary()`, `get_task_symbol_files()`, `graph_available()`. |
| `code_review_graph/memory/telemetry.py` | `record()` + `print_stats()` — local metrics log. |
| `hooks/prompt-context.sh` | UserPromptSubmit hook script. Already exists. |
| `hooks/hooks.json` | Hook configuration. Already has UserPromptSubmit entry. |
| `.agent-memory/metadata/` | All metadata JSON files live here. Add `pack_cache.json` here. |

## Code Conventions

- Python 3.10+, PEP 8, 100 char line length (ruff)
- Type annotations on all public functions
- No `eval`, `exec`, `pickle`, `shell=True`
- Catch specific exceptions, log with `logger.debug/warning`
- All new files need corresponding tests
- Keep changes focused — do not refactor code outside ticket scope

## Execution Order

Do Ticket 1 first (pack cache). Ticket 2 builds on it (uses cache keywords in scoring).
Ticket 3 is independent and can be done in any order.
