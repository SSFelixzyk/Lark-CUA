"""
Benchmark runner for Lark-CUA.

Usage:
    # Run all cases
    python tests/run_benchmark.py

    # Run specific product
    python tests/run_benchmark.py --product im
    python tests/run_benchmark.py --product docs
    python tests/run_benchmark.py --product calendar
    python tests/run_benchmark.py --product base
    python tests/run_benchmark.py --product vc
    python tests/run_benchmark.py --product mail
    python tests/run_benchmark.py --product gui

    # Run specific level
    python tests/run_benchmark.py --level L1
    python tests/run_benchmark.py --level L2

    # Run by tag
    python tests/run_benchmark.py --tag smoke
    python tests/run_benchmark.py --tag core
    
    # Run specific case id(s)
    python tests/run_benchmark.py --case-id IM_L2_001
    python tests/run_benchmark.py --case-id IM_L2_001,DOC_L2_003
    python tests/run_benchmark.py --case-id IM_L2_001 --case-id DOC_L2_003

    # Dry run (no actual mouse/keyboard actions)
    python tests/run_benchmark.py --dry-run --product im --level L1

    # Customize test contact / group / meeting id
    python tests/run_benchmark.py --contact "张三" --group "CUA-Lark课题-6"
    python tests/run_benchmark.py --product vc --meeting-id "123456789"

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
DEFAULT_MEETING_ID = "<<MEETING_ID>>"


# ── Loading ───────────────────────────────────────────────────────────────────

def load_cases(product_filter=None, level_filter=None, tag_filter=None, case_ids_filter=None):
    files = {
        "im": BENCHMARK_DIR / "im.yaml",
        "docs": BENCHMARK_DIR / "docs.yaml",
        "calendar": BENCHMARK_DIR / "calendar.yaml",
        "base": BENCHMARK_DIR / "base.yaml",
        "vc": BENCHMARK_DIR / "vc.yaml",
        "mail": BENCHMARK_DIR / "mail.yaml",
        "gui": BENCHMARK_DIR / "gui_primitives.yaml",
    }

    # Also scan generated/ subdirectories
    generated_dir = BENCHMARK_DIR / "generated"
    if generated_dir.exists():
        for yaml_path in sorted(generated_dir.rglob("*.yaml")):
            product = yaml_path.parent.name  # e.g. generated/im/IM_GEN_001.yaml → "im"
            if product not in files:
                files[product] = None  # placeholder, loaded individually below
            # Load each generated file as a single-case "product"
            with open(yaml_path, encoding="utf-8") as f:
                case = yaml.safe_load(f)
            if not isinstance(case, dict) or "id" not in case:
                continue
            case.setdefault("product", product)
            if product_filter and case["product"] != product_filter:
                continue
            if level_filter and case.get("level") != level_filter:
                continue
            if tag_filter and tag_filter not in case.get("tags", []):
                continue
            if case_ids_filter and case["id"] not in case_ids_filter:
                continue
            # Avoid duplicates if also in main yaml
            files[f"_gen_{case['id']}"] = case  # store case directly

    cases = []
    for product, path_or_case in files.items():
        if product.startswith("_gen_"):
            cases.append(path_or_case)
            continue
        if path_or_case is None:
            continue
        if product_filter and product != product_filter:
            continue
        with open(path_or_case, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        for case in data.get("cases", []):
            case["product"] = product
            if level_filter and case.get("level") != level_filter:
                continue
            if tag_filter and tag_filter not in case.get("tags", []):
                continue
            if case_ids_filter and case.get("id") not in case_ids_filter:
                continue
            cases.append(case)

    return cases


def parse_case_ids(case_id_args: list[str] | None) -> set[str]:
    if not case_id_args:
        return set()

    case_ids = set()
    for raw in case_id_args:
        for item in raw.split(","):
            cid = item.strip()
            if cid:
                case_ids.add(cid)
    return case_ids


def fill_placeholders(
    text: str, contact: str, group: str, meeting_id: str = DEFAULT_MEETING_ID
) -> str:
    text = text.replace("<<TEST_CONTACT>>", contact)
    text = text.replace("<<TEST_GROUP>>", group)
    text = text.replace("<<MEETING_ID>>", meeting_id)
    text = text.replace("<<DATE>>", datetime.now().strftime("%Y%m%d"))
    return text


# ── Running ───────────────────────────────────────────────────────────────────

def run_case(
    case: dict, contact: str, group: str, meeting_id: str, dry_run: bool,
    run_screenshot_dir=None,
) -> dict:
    task = fill_placeholders(case["task"].strip(), contact, group, meeting_id)
    if case.get("ui_hints"):
        hints = fill_placeholders(case["ui_hints"].strip(), contact, group, meeting_id)
        task = task + f"\n\n【界面提示】{hints}"

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

    screenshot_dir = None
    if run_screenshot_dir is not None:
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", case["title"])[:30]
        screenshot_dir = run_screenshot_dir / f"{case['id']}_{safe_title}"

    # Fill placeholders in checkpoints before passing to agent
    raw_checkpoints = case.get("checkpoints", [])
    checkpoints = [
        fill_placeholders(cp, contact, group, meeting_id)
        for cp in raw_checkpoints
    ] if raw_checkpoints else None

    run_start = datetime.now()
    agent = LarkAgent(max_steps=case.get("timeout_steps", 20), screenshot_dir=screenshot_dir)
    result: RunResult = agent.run(task, checkpoints=checkpoints)
    run_end = datetime.now()

    # Post-execution verification (CLI + VLM)
    verification = None
    if result.status in ("done", "failed"):
        try:
            from agent.verifier import verify
            final_shot = (
                Path(result.steps[-1].screenshot) if result.steps else None
            )
            # Fill placeholders in success_criteria before VLM assertion
            # (checkpoints are already filled — passed to agent above)
            case_filled = dict(case)
            if case.get("checkpoints"):
                case_filled["checkpoints"] = checkpoints or []
            if case.get("success_criteria"):
                case_filled["success_criteria"] = fill_placeholders(
                    case["success_criteria"], contact, group, meeting_id
                )
            vr = verify(
                case_filled, final_shot,
                screenshot_dir=screenshot_dir,
                run_start=run_start,
                checkpoint_shots=result.checkpoint_shots,
            )
            verification = {
                "overall": vr.overall,
                "checks": [
                    {
                        "checkpoint": c.checkpoint,
                        "method": c.method,
                        "passed": c.passed,
                        "reason": c.reason,
                    }
                    for c in vr.checks
                ],
            }
            print(f"  [verify] {vr.overall.upper()}  "
                  + "  ".join(f"{'OK' if c.passed else ('NG' if c.passed is False else '??')}/{c.method}"
                               for c in vr.checks))
        except Exception as e:
            print(f"  [verify] skipped ({e})")

    return {
        "id": case["id"],
        "product": case["product"],
        "level": case["level"],
        "title": case["title"],
        "task": task,
        "start_time": run_start.isoformat(timespec="seconds"),
        "end_time": run_end.isoformat(timespec="seconds"),
        "status": result.status,
        "total_steps": result.total_steps,
        "elapsed_ms": result.elapsed_ms,
        "expected_steps": case.get("expected_steps"),
        "verification": verification,
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


def save_results(results: list[dict], ts: str | None = None) -> Path:
    if ts is None:
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
    parser.add_argument(
        "--product",
        choices=["im", "docs", "calendar", "base", "vc", "mail", "gui"],
    )
    parser.add_argument("--level", choices=["L1", "L2", "L3"])
    parser.add_argument("--tag")
    parser.add_argument(
        "--case-id",
        action="append",
        help="Run only specific case id(s). Supports comma-separated values and repeated flags.",
    )
    parser.add_argument("--contact", default=DEFAULT_CONTACT,
                        help="Replace <<TEST_CONTACT>> placeholder")
    parser.add_argument("--group", default=DEFAULT_GROUP,
                        help="Replace <<TEST_GROUP>> placeholder")
    parser.add_argument(
        "--meeting-id",
        default=DEFAULT_MEETING_ID,
        help="Replace <<MEETING_ID>> placeholder (video conference join case)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delay", type=int, default=5,
                        help="Seconds to wait before starting (default 5)")
    args = parser.parse_args()

    case_ids_filter = parse_case_ids(args.case_id)
    cases = load_cases(args.product, args.level, args.tag, case_ids_filter)
    if not cases:
        print("No cases matched the filters.")
        sys.exit(0)

    print(f"Loaded {len(cases)} case(s)")
    for c in cases:
        print(f"  {c['id']:<18} [{c['level']}] {c['title']}")
    print()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not args.dry_run:
        if args.contact == DEFAULT_CONTACT:
            print("WARNING: --contact not set, <<TEST_CONTACT>> will be literal in tasks.")
        if args.meeting_id == DEFAULT_MEETING_ID:
            print("WARNING: --meeting-id not set, <<MEETING_ID>> will be literal in tasks.")
        print(f"Starting in {args.delay}s — switch to Feishu window now...")
        time.sleep(args.delay)

    import config as _cfg
    run_screenshot_dir = _cfg.SCREENSHOT_DIR / f"run_{ts}" if not args.dry_run else None
    print(f"Screenshots: {run_screenshot_dir or '(dry-run, skipped)'}\n")

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case['id']} — {case['title']}")
        r = run_case(case, args.contact, args.group, args.meeting_id, args.dry_run, run_screenshot_dir)
        results.append(r)
        status_str = r["status"].upper()
        print(f"  => {status_str}  steps={r['total_steps']}  time={r['elapsed_ms']/1000:.1f}s")
        if r["status"] == "error" and r["steps"]:
            last_err = r["steps"][-1].get("error")
            if last_err:
                print(f"     reason: {last_err}")

        # Pause between cases so the screen can settle
        if i < len(cases) and not args.dry_run:
            time.sleep(3)

    print_summary(results)
    out = save_results(results, ts)
    print(f"\nResults saved to: {out}")
    if run_screenshot_dir:
        print(f"Screenshots in : {run_screenshot_dir}")


if __name__ == "__main__":
    main()
