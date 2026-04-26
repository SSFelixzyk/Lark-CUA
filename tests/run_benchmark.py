"""
Benchmark runner for Lark-CUA.

Usage:
    # Run all cases
    python tests/run_benchmark.py

    # Run specific product
    python tests/run_benchmark.py --product im
    python tests/run_benchmark.py --product docs
    python tests/run_benchmark.py --product calendar

    # Run specific level
    python tests/run_benchmark.py --level L1
    python tests/run_benchmark.py --level L2

    # Run by tag
    python tests/run_benchmark.py --tag smoke
    python tests/run_benchmark.py --tag core

    # Dry run (no actual mouse/keyboard actions)
    python tests/run_benchmark.py --dry-run --product im --level L1

    # Customize test contact
    python tests/run_benchmark.py --contact "张三" --group "CUA-Lark课题-6"

Results are saved to: tests/results/benchmark_<timestamp>.json
"""

import sys
import json
import time
import argparse
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from agent.loop import LarkAgent, RunResult

try:
    import yaml
except ImportError:
    print("PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

BENCHMARK_DIR = Path(__file__).parent / "benchmark"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Default placeholders — override with CLI args
DEFAULT_CONTACT = "<<TEST_CONTACT>>"
DEFAULT_GROUP = "CUA-Lark课题-6"


# ── Loading ───────────────────────────────────────────────────────────────────

def load_cases(product_filter=None, level_filter=None, tag_filter=None):
    files = {
        "im": BENCHMARK_DIR / "im.yaml",
        "docs": BENCHMARK_DIR / "docs.yaml",
        "calendar": BENCHMARK_DIR / "calendar.yaml",
    }

    cases = []
    for product, path in files.items():
        if product_filter and product != product_filter:
            continue
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for case in data.get("cases", []):
            case["product"] = product
            if level_filter and case.get("level") != level_filter:
                continue
            if tag_filter and tag_filter not in case.get("tags", []):
                continue
            cases.append(case)

    return cases


def fill_placeholders(text: str, contact: str, group: str) -> str:
    text = text.replace("<<TEST_CONTACT>>", contact)
    text = text.replace("<<TEST_GROUP>>", group)
    text = text.replace("<<DATE>>", datetime.now().strftime("%Y%m%d"))
    return text


# ── Running ───────────────────────────────────────────────────────────────────

def run_case(case: dict, contact: str, group: str, dry_run: bool) -> dict:
    task = fill_placeholders(case["task"].strip(), contact, group)

    if dry_run:
        print(f"  [DRY-RUN] Task: {task[:80]}...")
        return {
            "id": case["id"],
            "product": case["product"],
            "level": case["level"],
            "title": case["title"],
            "status": "skipped",
            "total_steps": 0,
            "elapsed_ms": 0,
            "steps": [],
        }

    agent = LarkAgent(max_steps=case.get("timeout_steps", 20))
    result: RunResult = agent.run(task)

    return {
        "id": case["id"],
        "product": case["product"],
        "level": case["level"],
        "title": case["title"],
        "task": task,
        "status": result.status,
        "total_steps": result.total_steps,
        "elapsed_ms": result.elapsed_ms,
        "expected_steps": case.get("expected_steps"),
        "steps": [
            {
                "step": s.step,
                "action_type": s.action_type,
                "thought": s.thought,
                "status": s.status,
                "elapsed_ms": s.elapsed_ms,
                "error": s.error,
            }
            for s in result.steps
        ],
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    total = len(results)
    done = sum(1 for r in results if r["status"] == "done")
    failed = sum(1 for r in results if r["status"] == "failed")
    error = sum(1 for r in results if r["status"] in ("error", "timeout"))
    skipped = sum(1 for r in results if r["status"] == "skipped")

    tsr = done / (total - skipped) * 100 if (total - skipped) > 0 else 0
    avg_steps = (
        sum(r["total_steps"] for r in results if r["status"] == "done") / done
        if done > 0 else 0
    )
    avg_time = (
        sum(r["elapsed_ms"] for r in results if r["status"] == "done") / done / 1000
        if done > 0 else 0
    )

    print()
    print("=" * 65)
    print("BENCHMARK RESULTS")
    print("=" * 65)
    print(f"  Total cases   : {total}")
    print(f"  DONE          : {done}")
    print(f"  FAILED        : {failed}")
    print(f"  ERROR/TIMEOUT : {error}")
    print(f"  SKIPPED       : {skipped}")
    print(f"  Task Success Rate (TSR): {tsr:.1f}%")
    print(f"  Avg steps (done cases): {avg_steps:.1f}")
    print(f"  Avg time  (done cases): {avg_time:.1f}s")
    print()

    # Per-case table
    print(f"  {'ID':<18} {'Level':<6} {'Status':<10} {'Steps':>6} {'Time':>8}")
    print(f"  {'-'*18} {'-'*6} {'-'*10} {'-'*6} {'-'*8}")
    for r in results:
        t = f"{r['elapsed_ms']/1000:.1f}s" if r["elapsed_ms"] else "-"
        print(f"  {r['id']:<18} {r['level']:<6} {r['status']:<10} {r['total_steps']:>6} {t:>8}")

    # Per-product summary
    products = sorted(set(r["product"] for r in results))
    print()
    print(f"  {'Product':<12} {'TSR':>8} {'Cases':>7}")
    print(f"  {'-'*12} {'-'*8} {'-'*7}")
    for p in products:
        pr = [r for r in results if r["product"] == p and r["status"] != "skipped"]
        if not pr:
            continue
        p_done = sum(1 for r in pr if r["status"] == "done")
        p_tsr = p_done / len(pr) * 100
        print(f"  {p:<12} {p_tsr:>7.1f}% {len(pr):>7}")

    print("=" * 65)


def save_results(results: list[dict]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"benchmark_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": ts,
                "total": len(results),
                "done": sum(1 for r in results if r["status"] == "done"),
                "tsr": sum(1 for r in results if r["status"] == "done")
                / max(len([r for r in results if r["status"] != "skipped"]), 1)
                * 100,
                "cases": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Lark-CUA Benchmark Runner")
    parser.add_argument("--product", choices=["im", "docs", "calendar"])
    parser.add_argument("--level", choices=["L1", "L2", "L3"])
    parser.add_argument("--tag")
    parser.add_argument("--contact", default=DEFAULT_CONTACT,
                        help="Replace <<TEST_CONTACT>> placeholder")
    parser.add_argument("--group", default=DEFAULT_GROUP,
                        help="Replace <<TEST_GROUP>> placeholder")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=int, default=5,
                        help="Seconds to wait before starting (default 5)")
    args = parser.parse_args()

    cases = load_cases(args.product, args.level, args.tag)
    if not cases:
        print("No cases matched the filters.")
        sys.exit(0)

    print(f"Loaded {len(cases)} case(s)")
    for c in cases:
        print(f"  {c['id']:<18} [{c['level']}] {c['title']}")
    print()

    if not args.dry_run:
        if args.contact == DEFAULT_CONTACT:
            print("WARNING: --contact not set, <<TEST_CONTACT>> will be literal in tasks.")
        print(f"Starting in {args.delay}s — switch to Feishu window now...")
        time.sleep(args.delay)

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case['id']} — {case['title']}")
        r = run_case(case, args.contact, args.group, args.dry_run)
        results.append(r)
        status_str = r["status"].upper()
        print(f"  => {status_str}  steps={r['total_steps']}  time={r['elapsed_ms']/1000:.1f}s")

        # Pause between cases so the screen can settle
        if i < len(cases) and not args.dry_run:
            time.sleep(3)

    print_summary(results)
    out = save_results(results)
    print(f"\nResults saved to: {out}")


if __name__ == "__main__":
    main()
