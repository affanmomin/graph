"""Benchmark runner for the repo-memory system.

Runs automated metric collection against a target repo.  Produces a JSON
results file and prints a summary table.

Usage
-----
    uv run python benchmarks/run_benchmark.py --repo .
    uv run python benchmarks/run_benchmark.py --repo /path/to/other-repo
    uv run python benchmarks/run_benchmark.py --repo . --tasks benchmarks/tasks/sample_tasks.json
    uv run python benchmarks/run_benchmark.py --repo . --skip-init

Design
------
- Calls the memory system's Python API directly (no subprocess).
- Writes results to benchmarks/results/<repo-slug>-<timestamp>.json.
- All metrics are computed deterministically from API outputs.
- Manual metrics (relevance, usefulness) are NOT computed here — see scorecard_template.md.

Automated metrics collected
---------------------------
Classification:
  feature_count           Number of features classified
  module_count            Number of modules classified
  avg_feature_confidence  Mean confidence across features
  avg_module_confidence   Mean confidence across modules
  features_with_tests     Features that have at least one test file mapped
  modules_with_deps       Modules that have at least one dependency

Context pack (per task):
  files_returned          Files in the context pack
  features_matched        Feature names returned
  modules_matched         Module names returned
  tokens_estimated        Sum of file sizes / 4 (rough token estimate)
  expected_files_hit      Expected file stems that appear in the pack
  expected_files_total    Number of expected file stems in the task spec
  coverage_pct            expected_files_hit / expected_files_total * 100

Refresh planning:
  directly_impacted       Feature/module slugs directly matched to changes
  graph_expanded          Graph-expanded slugs added beyond direct matches
  total_plan_size         Total artifact slugs in the refresh plan

Timing:
  init_seconds            Time to run full init pipeline
  context_pack_seconds    Time to build all context packs

Pass/fail thresholds (see THRESHOLDS dict):
  feature_count >= 2
  avg_confidence >= 0.5
  files_returned <= 20
  tokens_estimated <= 40000
  coverage_pct >= 50.0
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Locate repo root and make imports work ────────────────────────────────────
_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── Pass/fail thresholds ──────────────────────────────────────────────────────
THRESHOLDS: dict[str, tuple[str, float]] = {
    "feature_count":         (">=", 2),
    "avg_feature_confidence": (">=", 0.5),
    "avg_module_confidence":  (">=", 0.5),
    "files_returned":         ("<=", 20),
    "tokens_estimated":       ("<=", 40000),
    "coverage_pct":           (">=", 50.0),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    """Simple filesystem slug matching the memory writer convention."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _estimate_tokens(files: list[str], repo_root: Path) -> int:
    total = 0
    for fp in files:
        try:
            total += (repo_root / fp).stat().st_size // 4
        except OSError:
            pass
    return total


def _passes(metric: str, value: float) -> bool:
    if metric not in THRESHOLDS:
        return True
    op, threshold = THRESHOLDS[metric]
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    return True


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# ── Core evaluation steps ─────────────────────────────────────────────────────

def run_init(repo_root: Path) -> tuple[dict, float]:
    """Run memory init pipeline and return (result_dict, elapsed_seconds)."""
    from code_review_graph.memory.commands import run_memory_init_pipeline

    t0 = time.perf_counter()
    result = run_memory_init_pipeline(repo_root)
    elapsed = time.perf_counter() - t0
    return result, elapsed


def collect_classification_metrics(
    features: list[Any],
    modules: list[Any],
) -> dict[str, Any]:
    """Compute classification quality metrics from FeatureMemory/ModuleMemory lists."""
    f_conf = [f.confidence for f in features]
    m_conf = [m.confidence for m in modules]
    features_with_tests = sum(1 for f in features if getattr(f, "tests", []))
    modules_with_deps = sum(1 for m in modules if getattr(m, "dependencies", []))

    return {
        "feature_count": len(features),
        "module_count": len(modules),
        "avg_feature_confidence": round(_mean(f_conf), 3),
        "avg_module_confidence": round(_mean(m_conf), 3),
        "features_with_tests": features_with_tests,
        "modules_with_deps": modules_with_deps,
    }


def collect_context_pack_metrics(
    tasks: list[dict],
    features: list[Any],
    modules: list[Any],
    repo_root: Path,
    overrides: Any,
) -> tuple[list[dict], float]:
    """Build a context pack for each task and return (per_task_metrics, total_seconds)."""
    from code_review_graph.memory.context_builder import build_context_pack

    results: list[dict] = []
    t0 = time.perf_counter()

    for task in tasks:
        task_id = task["id"]
        description = task["description"]
        expected_stems = [
            Path(f).stem
            for f in task.get("expected_files_contain", [])
        ]

        pack = build_context_pack(description, features, modules, overrides=overrides)

        files = list(pack.relevant_files) if pack else []
        file_stems = {Path(f).stem for f in files}
        features_matched = list(pack.relevant_features) if pack else []
        modules_matched = list(pack.relevant_modules) if pack else []
        tokens = _estimate_tokens(files, repo_root)

        hits = sum(1 for s in expected_stems if s in file_stems)
        total_expected = len(expected_stems)
        coverage = round(hits / total_expected * 100, 1) if total_expected else None

        task_result: dict[str, Any] = {
            "task_id": task_id,
            "description": description,
            "files_returned": len(files),
            "features_matched": features_matched,
            "modules_matched": modules_matched,
            "tokens_estimated": tokens,
            "expected_files_hit": hits,
            "expected_files_total": total_expected,
            "coverage_pct": coverage,
            "files": files,
            "pass": {},
        }

        # Per-task pass/fail
        for metric in ("files_returned", "tokens_estimated", "coverage_pct"):
            val = task_result.get(metric)
            if val is not None:
                task_result["pass"][metric] = _passes(metric, float(val))

        results.append(task_result)

    elapsed = time.perf_counter() - t0
    return results, elapsed


def collect_refresh_metrics(
    features: list[Any],
    modules: list[Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Run a refresh plan on recently changed files and return metrics."""
    from code_review_graph.memory.refresh import plan_refresh
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        changed_files = [f.strip() for f in out.strip().splitlines() if f.strip()]
    except Exception:
        changed_files = []

    if not changed_files:
        return {"changed_files": 0, "skipped": "no changed files detected"}

    plan = plan_refresh(changed_files, features, modules, repo_root=repo_root)
    return {
        "changed_files": len(changed_files),
        "directly_impacted": len(plan.impacted_feature_slugs) + len(plan.impacted_module_slugs),
        "graph_expanded": len(plan.graph_expanded_feature_slugs) + len(plan.graph_expanded_module_slugs),
        "total_plan_size": (
            len(plan.impacted_feature_slugs) + len(plan.impacted_module_slugs)
            + len(plan.graph_expanded_feature_slugs)
            + len(plan.graph_expanded_module_slugs)
        ),
        "reason": plan.reason,
    }


# ── Report formatting ─────────────────────────────────────────────────────────

def _pass_marker(passed: bool | None) -> str:
    if passed is True:
        return "PASS"
    if passed is False:
        return "FAIL"
    return "    "


def print_summary(report: dict) -> None:
    """Print a compact human-readable summary to stdout."""
    repo = report["repo"]
    ts = report["timestamp"]
    cm = report["classification"]
    tasks = report["context_packs"]
    refresh = report.get("refresh", {})
    timing = report["timing"]

    print(f"\n{'='*60}")
    print(f"Benchmark: {repo}")
    print(f"Run at:    {ts}")
    print(f"{'='*60}")

    print("\n[Classification]")
    for k, v in cm.items():
        threshold_pass = _passes(k, float(v)) if isinstance(v, (int, float)) else None
        marker = _pass_marker(threshold_pass) if k in THRESHOLDS else "    "
        print(f"  {marker}  {k}: {v}")

    print("\n[Context Packs]")
    all_pass = True
    for t in tasks:
        cov = t.get("coverage_pct")
        cov_str = f"{cov}%" if cov is not None else "N/A"
        files = t["files_returned"]
        tokens = t["tokens_estimated"]
        cov_pass = t["pass"].get("coverage_pct", True)
        files_pass = t["pass"].get("files_returned", True)
        tokens_pass = t["pass"].get("tokens_estimated", True)
        task_ok = all([cov_pass, files_pass, tokens_pass])
        if not task_ok:
            all_pass = False
        marker = "PASS" if task_ok else "FAIL"
        print(f"  {marker}  [{t['task_id']}] files={files} tokens={tokens} coverage={cov_str}")
        print(f"        features={t['features_matched']}")

    print(f"\n  Overall context-packs: {'PASS' if all_pass else 'FAIL'}")

    if refresh and "skipped" not in refresh:
        print("\n[Refresh Planning]")
        print(f"  Changed files:     {refresh['changed_files']}")
        print(f"  Directly impacted: {refresh['directly_impacted']}")
        print(f"  Graph-expanded:    {refresh['graph_expanded']}")
        print(f"  Total plan size:   {refresh['total_plan_size']}")

    print("\n[Timing]")
    print(f"  Init:          {timing['init_seconds']:.2f}s")
    print(f"  Context packs: {timing['context_pack_seconds']:.2f}s")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the repo-memory benchmark harness.",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to the target repo root (default: current directory)",
    )
    parser.add_argument(
        "--tasks",
        default=str(_HERE / "tasks" / "sample_tasks.json"),
        help="Path to a tasks JSON file (default: benchmarks/tasks/sample_tasks.json)",
    )
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="Skip re-running init; use existing .agent-memory/ artifacts",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write JSON results (default: benchmarks/results/<slug>-<ts>.json)",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo).resolve()
    if not repo_root.is_dir():
        print(f"ERROR: repo path does not exist: {repo_root}", file=sys.stderr)
        sys.exit(1)

    # Load tasks
    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        print(f"ERROR: tasks file not found: {tasks_path}", file=sys.stderr)
        sys.exit(1)
    with open(tasks_path) as f:
        tasks_data = json.load(f)
    tasks = tasks_data.get("tasks", [])

    repo_slug = _slug(repo_root.name)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print(f"Running benchmark on: {repo_root}")
    print(f"Tasks file: {tasks_path} ({len(tasks)} tasks)")

    # ── Step 1: Init (or load existing) ──────────────────────────────────────
    if args.skip_init:
        print("Skipping init — loading existing .agent-memory/ ...")
        from code_review_graph.memory.scanner import scan_repo
        from code_review_graph.memory.classifier import classify_features, classify_modules

        scan = scan_repo(repo_root)
        features = classify_features(repo_root, scan)
        modules = classify_modules(repo_root, scan)
        init_elapsed = 0.0
        overrides = None
    else:
        print("Running memory init pipeline ...")
        init_result, init_elapsed = run_init(repo_root)
        features = init_result.get("features", [])
        modules = init_result.get("modules", [])
        try:
            from code_review_graph.memory.overrides import load_overrides
            overrides = load_overrides(repo_root / ".agent-memory")
        except Exception:
            overrides = None
        print(f"  Init complete in {init_elapsed:.2f}s")
        print(f"  {len(features)} features, {len(modules)} modules")

    # ── Step 2: Classification metrics ───────────────────────────────────────
    cm = collect_classification_metrics(features, modules)

    # ── Step 3: Context pack metrics ─────────────────────────────────────────
    print("Building context packs ...")
    try:
        from code_review_graph.memory.overrides import load_overrides
        overrides = load_overrides(repo_root / ".agent-memory")
    except Exception:
        overrides = None

    cp_results, cp_elapsed = collect_context_pack_metrics(
        tasks, features, modules, repo_root, overrides
    )
    print(f"  Done in {cp_elapsed:.2f}s")

    # ── Step 4: Refresh planning metrics ─────────────────────────────────────
    print("Collecting refresh plan metrics ...")
    refresh_metrics = collect_refresh_metrics(features, modules, repo_root)

    # ── Assemble report ───────────────────────────────────────────────────────
    report: dict[str, Any] = {
        "version": 1,
        "repo": str(repo_root),
        "timestamp": ts,
        "classification": cm,
        "context_packs": cp_results,
        "refresh": refresh_metrics,
        "timing": {
            "init_seconds": round(init_elapsed, 3),
            "context_pack_seconds": round(cp_elapsed, 3),
        },
        "thresholds": {k: {"op": v[0], "value": v[1]} for k, v in THRESHOLDS.items()},
    }

    # ── Write results ─────────────────────────────────────────────────────────
    out_path = Path(args.output) if args.output else (
        _HERE / "results" / f"{repo_slug}-{ts}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nResults written to: {out_path}")

    # ── Print summary ─────────────────────────────────────────────────────────
    print_summary(report)


if __name__ == "__main__":
    main()
