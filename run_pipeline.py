"""
Lark-CUA 全流程一键启动脚本。

流程：
  1. DSL 生成  — 自然语言 → YAML 测试用例
  2. DSL 评分  — 5 维度质量检查（可跳过）
  3. GUI 执行  — Benchmark 运行（ReAct 循环 + 双层验证）
  4. 报告发布  — MD 归档 + 飞书云文档（含步骤截图 + AI 分析）

用法示例：
    python run_pipeline.py \\
        --task "打开与张三的单聊，发送「测试消息 Hello」" \\
        --product im \\
        --contact "张三" \\
        --publish

    # 跳过评分、跳过 AI 分析，快速运行
    python run_pipeline.py --task "..." --product im --contact "张三" \\
        --publish --skip-eval --no-insights
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config


# ── helpers ───────────────────────────────────────────────────────────────────

def _section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _abort(msg: str) -> None:
    print(f"\n[ABORT] {msg}")
    sys.exit(1)


# ── Step 1: DSL generation ────────────────────────────────────────────────────

def step_dsl_generate(task: str, product: str) -> Path:
    _section("Step 1 / 4 — DSL 生成")
    from tools.dsl_generator import generate
    case, yaml_path = generate(task, product)
    print(f"[DSL] 生成完成: {yaml_path}")
    print(f"[DSL] case_id: {case.get('id')}  level: {case.get('level')}")
    return yaml_path, case.get("id")


# ── Step 2: DSL evaluation ────────────────────────────────────────────────────

def step_dsl_evaluate(yaml_path: Path) -> None:
    _section("Step 2 / 4 — DSL 评分")
    from tools.dsl_evaluator import evaluate, VERDICT_LABEL, DIM_ORDER
    result = evaluate(yaml_path)
    print(f"  Overall : {result['overall']}/2  [{VERDICT_LABEL.get(result['verdict'], result['verdict'])}]")
    for dim in DIM_ORDER:
        v   = result["scores"].get(dim, {})
        bar = f"[{v.get('score', '?')}]"
        print(f"  {bar}  {dim:<20}  {v.get('reason', '')}")
    if result["verdict"] == "reject":
        _abort("DSL 质量不达标（verdict=reject），请修改任务描述后重试。")
    print()


# ── Step 3: Benchmark run ─────────────────────────────────────────────────────

def step_run_benchmark(
    case_id: str,
    contact: str,
    group: str,
    meeting_id: str,
    delay: int,
) -> Path:
    _section("Step 3 / 4 — GUI 执行")
    from tests.run_benchmark import load_cases, run_case, save_results, print_summary

    cases = load_cases(case_ids_filter={case_id})
    if not cases:
        _abort(f"找不到 case_id={case_id}，请检查 DSL 生成是否成功。")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_screenshot_dir = config.SCREENSHOT_DIR / f"run_{ts}"

    print(f"[bench] 即将运行: {case_id}")
    print(f"[bench] {delay} 秒后开始，请切换到飞书窗口...")
    time.sleep(delay)

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case['id']} — {case['title']}")
        r = run_case(case, contact, group, meeting_id,
                     dry_run=False, run_screenshot_dir=run_screenshot_dir)
        results.append(r)
        print(f"  => {r['status'].upper()}  steps={r['total_steps']}  "
              f"time={r['elapsed_ms']/1000:.1f}s")

    print_summary(results)
    result_path = save_results(results, ts)
    print(f"\n[bench] 结果已保存: {result_path}")
    return result_path


# ── Step 4: Report ────────────────────────────────────────────────────────────

def step_report(result_path: Path, publish: bool, no_insights: bool, folder: str) -> None:
    _section("Step 4 / 4 — 报告生成")
    from report import md, feishu_doc, insight_agent
    from tools.report_publisher import REPORTS_DIR

    with open(result_path, encoding="utf-8") as f:
        result_json = json.load(f)

    ts = result_json.get("timestamp", result_path.stem.replace("benchmark_", ""))
    REPORTS_DIR.mkdir(exist_ok=True)
    shot_base = config.SCREENSHOT_DIR / f"run_{ts}"

    md_path = REPORTS_DIR / f"benchmark_{ts}.md"
    md.render(result_json, output_path=md_path)
    print(f"[report] MD  → {md_path}")

    if publish:
        _folder = folder or config.FEISHU_REPORT_FOLDER
        if not _folder:
            print("[report] WARNING: FEISHU_REPORT_FOLDER 未设置，跳过飞书发布。")
            return

        insights = None
        if not no_insights:
            try:
                insights = insight_agent.generate(
                    result_json,
                    screenshot_base=shot_base if shot_base.exists() else None,
                )
            except Exception as e:
                print(f"[report] WARNING: AI 分析失败 ({e})，继续发布...")

        url = feishu_doc.publish(
            result_json,
            md_path=md_path,
            folder_token=_folder,
            screenshot_base=shot_base if shot_base.exists() else None,
            insights=insights,
        )
        print(f"[report] Feishu doc → {url}")
    else:
        print("[report] 未指定 --publish，跳过飞书发布。")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lark-CUA 全流程：自然语言 → DSL → GUI 执行 → 报告"
    )
    parser.add_argument("--task",        required=True,
                        help="自然语言任务描述（将生成对应 DSL 用例）")
    parser.add_argument("--product",     default="im",
                        choices=["im", "docs", "calendar", "base", "vc", "mail"],
                        help="飞书产品线（默认 im）")
    parser.add_argument("--contact",     default="<<TEST_CONTACT>>",
                        help="替换 <<TEST_CONTACT>> 占位符")
    parser.add_argument("--group",       default="CUA-Lark课题-6",
                        help="替换 <<TEST_GROUP>> 占位符")
    parser.add_argument("--meeting-id",  default="<<MEETING_ID>>",
                        help="替换 <<MEETING_ID>> 占位符")
    parser.add_argument("--delay",       type=int, default=5,
                        help="GUI 操作前等待秒数（默认 5，切换到飞书窗口）")
    parser.add_argument("--publish",     action="store_true",
                        help="完成后发布飞书云文档报告")
    parser.add_argument("--folder",      default="",
                        help="飞书报告文件夹 token（覆盖 .env）")
    parser.add_argument("--skip-eval",   action="store_true",
                        help="跳过 DSL 质量评分")
    parser.add_argument("--no-insights", action="store_true",
                        help="跳过 AI 分析（报告更快）")
    args = parser.parse_args()

    print("\nLark-CUA Pipeline")
    print(f"  任务: {args.task}")
    print(f"  产品: {args.product}  联系人: {args.contact}")

    # Step 1: DSL generation
    yaml_path, case_id = step_dsl_generate(args.task, args.product)

    # Step 2: DSL evaluation (optional)
    if not args.skip_eval:
        step_dsl_evaluate(yaml_path)

    # Step 3: Benchmark run
    result_path = step_run_benchmark(
        case_id=case_id,
        contact=args.contact,
        group=args.group,
        meeting_id=args.meeting_id,
        delay=args.delay,
    )

    # Step 4: Report
    step_report(
        result_path=result_path,
        publish=args.publish,
        no_insights=args.no_insights,
        folder=args.folder,
    )

    print("\n[pipeline] 全流程完成。")


if __name__ == "__main__":
    main()
