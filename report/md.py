"""
Markdown report renderer for benchmark results JSON.

Step lines use a unique anchor tag [CASE_ID-NN] so that feishu_doc.py can
locate each step precisely when inserting screenshots via +media-insert.
"""

from pathlib import Path

_STATUS = {"done": "PASS", "failed": "FAIL", "timeout": "TIMEOUT",
           "error": "ERROR", "skipped": "SKIP"}
_CHECK  = {True: "OK", False: "NG", None: "??"}


def _ms(ms: int | None) -> str:
    return f"{(ms or 0) / 1000:.1f}s"


def step_anchor(case_id: str, step_num: int) -> str:
    """Unique, doc-searchable tag for each step. Used as insertion anchor."""
    return f"[{case_id}-{step_num:02d}]"


def render(result_json: dict, output_path: Path | None = None) -> str:
    ts    = result_json.get("timestamp", "")
    cases = result_json.get("cases", [])
    total = result_json.get("total", len(cases))
    done  = result_json.get("done", sum(1 for c in cases if c["status"] == "done"))
    tsr   = result_json.get("tsr", done / max(total, 1) * 100)

    done_cases = [c for c in cases if c["status"] == "done"]
    avg_steps  = sum(c["total_steps"] for c in done_cases) / max(len(done_cases), 1)
    avg_time   = sum(c["elapsed_ms"]  for c in done_cases) / max(len(done_cases), 1) / 1000

    lines = [
        "# 飞书 GUI 测试报告",
        "",
        f"**执行时间**：{ts}",
        f"**总用例**：{total}  |  **通过**：{done}  |  **TSR**：{tsr:.1f}%",
        f"**平均步骤**：{avg_steps:.1f}  |  **平均耗时**：{avg_time:.1f}s",
        "",
    ]

    # Per-product summary table (only when multiple products present)
    products = sorted(set(c["product"] for c in cases))
    if len(products) > 1:
        lines += ["## 产品汇总", "",
                  "| 产品 | 用例数 | TSR |",
                  "|------|--------|-----|"]
        for p in products:
            pr    = [c for c in cases if c["product"] == p and c["status"] != "skipped"]
            p_tsr = sum(1 for c in pr if c["status"] == "done") / max(len(pr), 1) * 100
            lines.append(f"| {p} | {len(pr)} | {p_tsr:.1f}% |")
        lines += ["", "---", ""]

    # Per-case sections
    for case in cases:
        case_id = case["id"]
        icon    = _STATUS.get(case["status"], case["status"].upper())
        exp_str = f" / 预期 {case['expected_steps']}" if case.get("expected_steps") else ""
        lines += [
            f"## [{icon}] {case_id} — {case['title']}",
            "",
            f"**产品**：{case['product']}  |  **等级**：{case['level']}"
            f"  |  **开始**：{case.get('start_time', '-')}"
            f"  |  **耗时**：{_ms(case.get('elapsed_ms'))}"
            f"  |  **步骤**：{case.get('total_steps', 0)}{exp_str}",
            "",
            f"**任务**：{case.get('task', '')}",
            "",
        ]

        # Operation trace — each step gets a unique anchor tag for screenshot insertion
        if case.get("steps"):
            lines.append("### 操作轨迹")
            lines.append("")
            for s in case["steps"]:
                anchor  = step_anchor(case_id, s["step"])
                thought = (s.get("thought") or "").replace("\n", " ")[:100]
                elapsed = _ms(s.get("elapsed_ms"))
                # Format: [CASE-NN] action_type (Xs) — thought
                # The anchor tag [CASE-NN] is the insertion point for the screenshot
                lines.append(f"{anchor} {s['action_type']} ({elapsed}) — {thought}")
                lines.append("")  # blank line so each step is its own paragraph/block

        # Verification results
        vr = case.get("verification")
        if vr:
            overall = vr.get("overall", "?").upper()
            lines += [f"### 验证结果：{overall}", ""]
            for chk in vr.get("checks", []):
                mark   = _CHECK.get(chk.get("passed"))
                method = chk.get("method", "?")
                cp     = (chk.get("checkpoint") or "")[:60]
                reason = chk.get("reason", "")
                lines.append(f"- {mark} [{method}] {cp} — {reason}")
            lines.append("")

        # AI placeholders: test report first, then agent analysis
        lines += [
            "### AI 测试报告",
            "",
            f"（测试报告待生成 · {case_id}）",
            "",
            "### AI Agent 分析",
            "",
            f"（Agent分析待生成 · {case_id}）",
            "",
            "---",
            "",
        ]

    md = "\n".join(lines)

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")

    return md
