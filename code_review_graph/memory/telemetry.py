"""Local performance telemetry for the repo-memory subsystem.

Writes a JSONL log to ``.code-review-graph/memory-metrics.jsonl`` — local only,
gitignored, never committed.  Provides a ``print_stats()`` function used by the
``memory stats`` CLI command to display a human-readable performance summary.

Design principles
-----------------
- Zero external dependencies.
- Never raises — all I/O errors are swallowed silently so telemetry cannot
  break the main command.
- Records only timing, counts, and quality scores.  No file contents, no task
  text beyond length, no personal data.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_METRICS_FILE = "memory-metrics.jsonl"
_GRAPH_DIR = ".code-review-graph"
_MAX_LINES_KEPT = 500  # rotate after this many entries


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def record(command: str, metrics: dict[str, Any], repo_root: Path) -> None:
    """Append one metrics entry to the local JSONL log.

    Args:
        command:   Short command name, e.g. ``"init"``, ``"prepare-context"``.
        metrics:   Dict of scalar metrics (numbers, bools, short strings).
        repo_root: Repo root — determines where the log file lives.
    """
    try:
        log_path = _log_path(repo_root)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "command": command,
            **metrics,
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

        _maybe_rotate(log_path)
    except Exception as exc:
        logger.debug("telemetry.record failed: %s", exc)


# ---------------------------------------------------------------------------
# Read + display
# ---------------------------------------------------------------------------


def print_stats(repo_root: Path, last: int = 20) -> None:
    """Print a human-readable summary of recent memory command runs.

    Args:
        repo_root: Repo root path.
        last:      How many recent entries to show (default 20).
    """
    log_path = _log_path(repo_root)
    if not log_path.exists():
        print("  No metrics recorded yet. Run any memory command to start tracking.")
        return

    entries = _read_entries(log_path)
    if not entries:
        print("  Metrics log is empty.")
        return

    recent = entries[-last:]
    print(f"  Last {len(recent)} memory command runs  (log: {log_path})")
    print()

    # Header
    print(f"  {'Time':<20}  {'Command':<18}  {'Duration':>8}  Details")
    print(f"  {'-'*20}  {'-'*18}  {'-'*8}  {'-'*40}")

    for e in recent:
        ts = e.get("ts", "")[:19].replace("T", " ")
        cmd = e.get("command", "?")
        dur = e.get("duration_s")
        dur_str = f"{dur:.2f}s" if isinstance(dur, (int, float)) else "  —"
        detail = _format_detail(e)
        print(f"  {ts:<20}  {cmd:<18}  {dur_str:>8}  {detail}")

    print()
    _print_aggregate(entries)
    _print_savings(repo_root, entries)


def _format_detail(e: dict[str, Any]) -> str:
    """Format the most useful metrics for a single entry as a short string."""
    cmd = e.get("command", "")
    parts: list[str] = []

    if cmd == "init":
        parts.append(f"features={e.get('feature_count', '?')}")
        parts.append(f"modules={e.get('module_count', '?')}")
        conf = e.get("avg_confidence")
        if conf is not None:
            parts.append(f"conf={conf:.0%}")
        if e.get("graph_used"):
            parts.append("graph=yes")
        quality = e.get("quality")
        if quality:
            parts.append(f"[{quality}]")

    elif cmd == "prepare-context":
        parts.append(f"files={e.get('files_returned', '?')}")
        parts.append(f"tokens≈{e.get('tokens_estimated', '?')}")
        score = e.get("top_score")
        if score is not None:
            parts.append(f"score={score:.2f}")
        if e.get("fallback"):
            parts.append("[fallback]")
        if e.get("graph_enriched"):
            parts.append("graph=yes")

    elif cmd == "refresh":
        parts.append(f"changed={e.get('changed_files', '?')}")
        parts.append(f"refreshed={e.get('artifacts_refreshed', '?')}")
        parts.append(f"mode={e.get('mode', '?')}")

    elif cmd == "explain":
        parts.append(f"target={e.get('target', '?')[:20]}")
        parts.append(f"kind={e.get('match_kind', '?')}")
        conf = e.get("confidence")
        if conf is not None:
            parts.append(f"conf={conf:.0%}")

    elif cmd == "changed":
        parts.append(f"target={e.get('target', '?')[:20]}")
        parts.append("freshness=" + ("yes" if e.get("has_freshness") else "no"))
        if e.get("graph_used"):
            parts.append("graph=yes")

    return "  ".join(parts) if parts else ""


def _print_aggregate(entries: list[dict[str, Any]]) -> None:
    """Print aggregate stats across all recorded entries."""
    from collections import defaultdict
    by_cmd: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_cmd[e.get("command", "?")].append(e)

    print(f"  Aggregate ({len(entries)} total runs):")
    for cmd, runs in sorted(by_cmd.items()):
        durs = [r["duration_s"] for r in runs if isinstance(r.get("duration_s"), (int, float))]
        avg_dur = sum(durs) / len(durs) if durs else None
        dur_str = f"{avg_dur:.2f}s avg" if avg_dur is not None else "  —"

        extras: list[str] = []
        if cmd == "init":
            confs = [r["avg_confidence"] for r in runs if isinstance(r.get("avg_confidence"), float)]
            if confs:
                extras.append(f"avg conf {sum(confs)/len(confs):.0%}")
        elif cmd == "prepare-context":
            files = [r["files_returned"] for r in runs if isinstance(r.get("files_returned"), int)]
            fallbacks = sum(1 for r in runs if r.get("fallback"))
            if files:
                extras.append(f"avg {sum(files)/len(files):.1f} files/pack")
            if fallbacks:
                extras.append(f"{fallbacks}/{len(runs)} fallbacks")

        extra_str = "  " + ", ".join(extras) if extras else ""
        print(f"    {cmd:<18}  {len(runs):>3} runs  {dur_str}{extra_str}")
    print()


def _print_savings(repo_root: Path, entries: list[dict[str, Any]]) -> None:
    """Print a before/after token savings comparison to prove product value."""
    _SOURCE_EXTS = {
        ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java",
        ".rb", ".cs", ".cpp", ".c", ".h", ".swift", ".kt", ".vue",
        ".sol", ".md",
    }
    _SKIP_DIRS = {
        ".git", "node_modules", "__pycache__", ".repomind",
        ".code-review-graph", ".venv", "venv", "dist", "build",
        ".agent-memory",
    }

    # --- measure repo source files ---
    total_chars = 0
    total_files = 0
    try:
        for path in repo_root.rglob("*"):
            if path.is_file() and path.suffix in _SOURCE_EXTS:
                # skip ignored dirs
                parts = set(path.relative_to(repo_root).parts[:-1])
                if parts & _SKIP_DIRS:
                    continue
                try:
                    total_chars += path.stat().st_size
                    total_files += 1
                except OSError:
                    pass
    except Exception:
        return

    if total_files == 0:
        return

    # --- measure .agent-memory/ artifacts ---
    mem_root = repo_root / ".agent-memory"
    mem_chars = 0
    mem_files = 0
    if mem_root.exists():
        for p in mem_root.rglob("*"):
            if p.is_file():
                try:
                    mem_chars += p.stat().st_size
                    mem_files += 1
                except OSError:
                    pass

    # --- bootstrap file (CLAUDE.md) ---
    claude_bytes = 0
    claude_path = mem_root / "CLAUDE.md"
    if claude_path.exists():
        try:
            claude_bytes = claude_path.stat().st_size
        except OSError:
            pass

    # --- last prepare-context entry ---
    last_pc = next(
        (e for e in reversed(entries) if e.get("command") == "prepare-context"),
        None,
    )

    # --- compute token estimates (4 chars ≈ 1 token) ---
    repo_tokens = total_chars // 4
    mem_tokens = mem_chars // 4
    claude_tokens = claude_bytes // 4

    print(f"  Token savings  (proof of value)")
    print(f"  {'─'*50}")

    repo_kb = total_chars / 1024
    mem_kb = mem_chars / 1024
    ratio = total_chars / mem_chars if mem_chars else 0
    print(f"  Repo source files   : {total_files:>4} files  {repo_kb:>7.1f} KB  ≈ {repo_tokens:,} tokens")
    if mem_files:
        print(f"  .agent-memory/      : {mem_files:>4} files  {mem_kb:>7.1f} KB  ≈ {mem_tokens:,} tokens"
              + (f"  ({ratio:.0f}× smaller)" if ratio >= 2 else ""))
    if claude_bytes:
        bootstrap_ratio = total_chars / claude_bytes if claude_bytes else 0
        print(f"  CLAUDE.md bootstrap :            {claude_bytes:>7} B   ≈ {claude_tokens:,} tokens"
              + (f"  ({bootstrap_ratio:.0f}× smaller)" if bootstrap_ratio >= 2 else ""))

    if last_pc:
        files_ret = last_pc.get("files_returned", 0)
        tokens_ret = last_pc.get("tokens_estimated", 0)
        scope_pct = (1 - files_ret / total_files) * 100 if total_files else 0
        print()
        print(f"  Last prepare-context:")
        print(f"    {files_ret} of {total_files} files returned  ({scope_pct:.0f}% scope reduction)")
        if tokens_ret:
            savings = repo_tokens - tokens_ret
            savings_pct = savings / repo_tokens * 100 if repo_tokens else 0
            print(f"    ≈ {tokens_ret:,} tokens to read those files"
                  f"  vs ≈ {repo_tokens:,} tokens for full repo"
                  f"  ({savings_pct:.0f}% saved)")

    print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log_path(repo_root: Path) -> Path:
    return repo_root / _GRAPH_DIR / _METRICS_FILE


def _read_entries(log_path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as exc:
        logger.debug("telemetry._read_entries failed: %s", exc)
    return entries


def _maybe_rotate(log_path: Path) -> None:
    """Keep the log under _MAX_LINES_KEPT by dropping the oldest entries."""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_LINES_KEPT:
            keep = lines[-_MAX_LINES_KEPT:]
            log_path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except Exception:
        pass
