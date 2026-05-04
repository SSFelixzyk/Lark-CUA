"""
Lark-CUA 全流程一键启动脚本。

流程 A（单任务）：
  1. DSL 生成  — 自然语言 → YAML 测试用例
  2. GUI 执行  — Benchmark 运行（ReAct 循环 + 双层验证）
  3. 报告发布  — MD 归档 + 飞书云文档（含步骤截图 + AI 分析）

流程 B（文档驱动）：
  1. 文档解析  — 读取飞书云文档 → 提取可测功能点
  2. 批量生成  — 每个功能点 → DSL 用例（含评分精炼）
  3. GUI 执行  — Benchmark 批量运行
  4. 报告发布  — 同上

用法示例：
    # 流程 A：单任务
    python run_pipeline.py \\
        --task "打开与张三的单聊，发送「测试消息 Hello」" \\
        --product im --contact "张三" --publish

    # 流程 B：文档驱动
    python run_pipeline.py \\
        --from-doc "https://xxx.feishu.cn/docx/..." \\
        --product im --contact "张三" --levels L1,L2 --max-cases 5 --publish
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


# ── Step 1-A: 单任务 DSL 生成 ─────────────────────────────────────────────────

def step_dsl_generate(task: str, product: str | None = None, max_retries: int = 3, skip_eval: bool = False) -> tuple:
    _section("Step 1 — DSL 生成 + 评审")
    if skip_eval:
        from dsl.generator import generate
        case, yaml_path = generate(task, product)
    else:
        from dsl.generator import generate_loop
        case, yaml_path = generate_loop(task, product, max_retries=max_retries)
    print(f"[DSL] saved: {yaml_path}")
    print(f"[DSL] case_id: {case.get('id')}  level: {case.get('level')}")
    return {case.get("id")}   # 返回 set，与文档模式接口统一


# ── Step 1-B: 文档驱动批量 DSL 生成 ──────────────────────────────────────────

def step_doc_generate(
    doc_url: str,
    product: str,
    levels: list[str],
    max_cases: int,
    skip_eval: bool,
    max_retries: int,
) -> set[str]:
    _section(f"Step 1 — 文档解析 + 批量 DSL 生成（最多 {max_cases} 个用例）")
    from tools.doc_case_generator import generate_from_doc

    saved_paths = generate_from_doc(
        doc=doc_url,
        product=product,
        levels=levels,
        max_cases=max_cases,
        evaluate=not skip_eval,
        max_retries=max_retries,
    )

    if not saved_paths:
        _abort("未能从文档中生成任何 DSL 用例，请检查文档内容或 --product 参数。")

    case_ids = set()
    for p in saved_paths:
        # 文件名即 case_id，如 DOC_GEN_003.yaml → DOC_GEN_003
        case_ids.add(p.stem)

    print(f"\n[doc-gen] 共生成 {len(case_ids)} 个用例: {', '.join(sorted(case_ids))}")
    return case_ids


# ── Step 2: Benchmark 运行（接受多个 case_id）────────────────────────────────

def step_run_benchmark(
    case_ids: set[str],
    contact: str,
    group: str,
    meeting_id: str,
    doc: str,
    delay: int,
    heal_config: dict | None = None,
    use_memory: bool = True,
) -> Path:
    _section(f"Step 2 — GUI 执行（{len(case_ids)} 个用例）")
    from tests.run_benchmark import load_cases, run_case, save_results, print_summary

    cases = load_cases(case_ids_filter=case_ids)
    if not cases:
        _abort(f"找不到 case_ids={case_ids}，请检查 DSL 生成是否成功。")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_screenshot_dir = config.SCREENSHOT_DIR / f"run_{ts}"

    print(f"[bench] 即将运行 {len(cases)} 个用例")
    print(f"[bench] {delay} 秒后开始，请切换到飞书窗口...")
    time.sleep(delay)

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case['id']} — {case['title']}")
        r = run_case(case, contact, group, meeting_id,
                     dry_run=False, run_screenshot_dir=run_screenshot_dir, doc=doc,
                     heal_config=heal_config, use_memory=use_memory)
        results.append(r)
        heal_tag = f"  heal={r['heal_attempts']}" if r.get("heal_attempts") else ""
        print(f"  => {r['status'].upper()}  steps={r['total_steps']}  "
              f"time={r['elapsed_ms']/1000:.1f}s{heal_tag}")
        if i < len(cases):
            time.sleep(3)   # 用例间等待界面稳定

    print_summary(results)
    result_path = save_results(results, ts)
    print(f"\n[bench] 结果已保存: {result_path}")
    return result_path


# ── Step 3: 报告 ──────────────────────────────────────────────────────────────

def step_report(result_path: Path, publish: bool, no_insights: bool, folder: str) -> None:
    _section("Step 3 — 报告生成")
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

        test_insights  = None
        agent_insights = None
        if not no_insights:
            shot_arg = shot_base if shot_base.exists() else None
            try:
                test_insights = insight_agent.generate_test_report(result_json, screenshot_base=shot_arg)
            except Exception as e:
                print(f"[report] WARNING: AI 测试报告生成失败 ({e})，继续...")
            try:
                agent_insights = insight_agent.generate(result_json, screenshot_base=shot_arg)
            except Exception as e:
                print(f"[report] WARNING: AI Agent 分析生成失败 ({e})，继续...")

        url = feishu_doc.publish(
            result_json,
            md_path=md_path,
            folder_token=_folder,
            screenshot_base=shot_base if shot_base.exists() else None,
            test_insights=test_insights,
            agent_insights=agent_insights,
        )
        print(f"[report] Feishu doc → {url}")
    else:
        print("[report] 未指定 --publish，跳过飞书发布。")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lark-CUA 全流程：DSL 生成 → GUI 执行 → 报告"
    )

    # ── 输入源（二选一）
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--task",
        help="自然语言任务描述（流程 A：生成单个用例）",
    )
    src.add_argument(
        "--from-doc",
        metavar="DOC_URL",
        help="飞书云文档 URL 或 token（流程 B：批量生成用例）",
    )

    # ── 产品 & 占位符
    parser.add_argument("--product", default=None,
                        choices=["im", "docs", "calendar", "base", "vc", "mail", "gui"],
                        help="飞书产品线（省略时自动从任务描述中识别）")
    parser.add_argument("--contact", default="<<TEST_CONTACT>>",
                        help="替换 <<TEST_CONTACT>> 占位符")
    parser.add_argument("--group",   default="CUA-Lark课题-6",
                        help="替换 <<TEST_GROUP>> 占位符")
    parser.add_argument("--meeting-id", default="<<MEETING_ID>>",
                        help="替换 <<MEETING_ID>> 占位符")
    parser.add_argument("--doc",     default="<<TEST_DOC>>",
                        help="替换 <<TEST_DOC>> 占位符（文档名称，docs 产品用）")

    # ── 文档模式专属参数
    parser.add_argument("--levels",    default="L1,L2",
                        help="[--from-doc] 生成的用例等级，逗号分隔（默认 L1,L2）")
    parser.add_argument("--max-cases", type=int, default=10,
                        help="[--from-doc] 最多生成几个用例（默认 10）")

    # ── 通用参数
    parser.add_argument("--delay",       type=int, default=5,
                        help="GUI 执行前等待秒数（默认 5，切换到飞书窗口）")
    parser.add_argument("--publish",     action="store_true",
                        help="完成后发布飞书云文档报告")
    parser.add_argument("--folder",      default="",
                        help="飞书报告文件夹 token（覆盖 .env）")
    parser.add_argument("--skip-eval",   action="store_true",
                        help="跳过 DSL 质量评审循环（更快，质量略低）")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="DSL 评审循环最大重试次数（默认 3）")
    parser.add_argument("--no-insights", action="store_true",
                        help="跳过报告 AI 分析（更快）")
    parser.add_argument("--use-memory", action="store_true",
                        help="将 memory/<product>.md 注入任务上下文")
    # ── 自愈模块
    parser.add_argument("--heal",           action="store_true",
                        help="启用自愈模块（默认关闭）")
    parser.add_argument("--heal-max",       type=int, default=2,
                        help="每个用例最多自愈次数（默认 2）")
    parser.add_argument("--heal-no-patch",  action="store_true",
                        help="自愈成功后不回写 ui_hints")
    parser.add_argument("--heal-triggers",  default="",
                        help="启用的触发类型，逗号分隔（默认全部）"
                             "可选：explicit_fail,implicit_stuck,explicit_stuck,checkpoint_timeout")
    args = parser.parse_args()

    # ── 打印启动信息
    print("\nLark-CUA Pipeline")
    if args.task:
        print(f"  模式: 单任务")
        print(f"  任务: {args.task}")
    else:
        print(f"  模式: 文档驱动")
        print(f"  文档: {args.from_doc}")
        print(f"  等级: {args.levels}  上限: {args.max_cases} 个")
    print(f"  产品: {args.product}  联系人: {args.contact}")

    # ── Step 1：DSL 生成
    if args.task:
        case_ids = step_dsl_generate(
            args.task, args.product,
            max_retries=args.max_retries,
            skip_eval=args.skip_eval,
        )
    else:
        levels = [lv.strip() for lv in args.levels.split(",")]
        case_ids = step_doc_generate(
            doc_url=args.from_doc,
            product=args.product,
            levels=levels,
            max_cases=args.max_cases,
            skip_eval=args.skip_eval,
            max_retries=args.max_retries,
        )

    # ── Step 2：GUI 执行
    heal_config: dict | None = None
    if args.heal:
        raw_triggers = args.heal_triggers.strip()
        heal_config = {
            "heal": True,
            "heal_max": args.heal_max,
            "heal_patch": not args.heal_no_patch,
            "heal_triggers": (
                {t.strip() for t in raw_triggers.split(",") if t.strip()}
                if raw_triggers else None
            ),
        }

    result_path = step_run_benchmark(
        case_ids=case_ids,
        contact=args.contact,
        group=args.group,
        meeting_id=args.meeting_id,
        doc=args.doc,
        delay=args.delay,
        heal_config=heal_config,
        use_memory=args.use_memory,
    )

    # ── Step 3：报告
    step_report(
        result_path=result_path,
        publish=args.publish,
        no_insights=args.no_insights,
        folder=args.folder,
    )

    print("\n[pipeline] 全流程完成。")


if __name__ == "__main__":
    main()
