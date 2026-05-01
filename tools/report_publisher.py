"""
Benchmark report publisher.

Usage:
    # MD only
    python tools/report_publisher.py tests/results/benchmark_XXXXXX.json

    # MD + Feishu cloud doc (with per-step screenshots + AI insights)
    python tools/report_publisher.py tests/results/benchmark_XXXXXX.json --publish

    # Skip AI insight generation (faster, no Doubao call)
    python tools/report_publisher.py ... --publish --no-insights

    # Override target folder
    python tools/report_publisher.py ... --publish --folder <folder_token>

MD saved to: reports/benchmark_{ts}.md
Screenshots auto-detected from: screenshots/run_{ts}/
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from report import md, feishu_doc, insight_agent

REPORTS_DIR = Path(__file__).parent.parent / "reports"


def main():
    parser = argparse.ArgumentParser(description="Lark-CUA Benchmark Report Publisher")
    parser.add_argument("result_json", help="Path to benchmark result JSON")
    parser.add_argument("--publish",     action="store_true",
                        help="Publish to Feishu cloud document")
    parser.add_argument("--no-insights", action="store_true",
                        help="Skip AI insight generation")
    parser.add_argument("--folder",      default="",
                        help="Feishu folder token (overrides FEISHU_REPORT_FOLDER in .env)")
    args = parser.parse_args()

    result_path = Path(args.result_json)
    if not result_path.exists():
        print(f"ERROR: file not found: {result_path}")
        sys.exit(1)

    with open(result_path, encoding="utf-8") as f:
        result_json = json.load(f)

    ts = result_json.get("timestamp", result_path.stem.replace("benchmark_", ""))
    REPORTS_DIR.mkdir(exist_ok=True)

    shot_base = config.SCREENSHOT_DIR / f"run_{ts}"

    # ── MD report (always) ────────────────────────────────────────────────────
    md_path = REPORTS_DIR / f"benchmark_{ts}.md"
    md.render(result_json, output_path=md_path)
    cases = result_json.get("cases", [])
    tsr   = result_json.get("tsr", 0)
    print(f"[report] MD  → {md_path}")
    print(f"[report] TSR {tsr:.1f}%  |  {len(cases)} cases")

    # ── Feishu cloud doc (optional) ───────────────────────────────────────────
    if args.publish:
        folder = args.folder or config.FEISHU_REPORT_FOLDER
        if not folder:
            print("ERROR: --folder not set and FEISHU_REPORT_FOLDER not in .env")
            sys.exit(1)

        # Generate AI insights unless skipped
        insights = None
        if not args.no_insights:
            try:
                insights = insight_agent.generate(
                    result_json,
                    screenshot_base=shot_base if shot_base.exists() else None,
                )
            except Exception as e:
                print(f"[report] WARNING: AI 分析生成失败 ({e})，继续发布...")

        url = feishu_doc.publish(
            result_json,
            md_path=md_path,
            folder_token=folder,
            screenshot_base=shot_base if shot_base.exists() else None,
            insights=insights,
        )
        print(f"[report] Feishu doc → {url}")


if __name__ == "__main__":
    main()
