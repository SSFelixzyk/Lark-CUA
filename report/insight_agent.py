"""
AI insight generator for benchmark results.

Reads the result JSON + per-step VLM logs (step{N}_vlm.txt), then calls
Doubao to produce per-case analysis and recommendations in Markdown.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from llm.doubao_client import chat

_SYSTEM = """\
你是飞书GUI自动化测试分析专家。根据提供的benchmark测试记录和Agent每步的思维过程，
给出深入的分析和可操作的建议。

## 输出格式（严格Markdown）

### 整体评估
简要概括本次测试的整体表现，包括成功率、效率、Agent行为质量。

### 逐用例分析

对每个用例，给出：
- **执行质量**：步骤是否精准，有无绕路或多余操作
- **瓶颈识别**：哪一步耗时最长或最容易出错，原因是什么
- **Agent决策评估**：思维链是否清晰，有无误判

### 改进建议

针对测试用例设计和Agent表现，给出具体可操作的建议：
- ui_hints 是否需要补充或修正
- 哪些步骤可以简化
- 验证逻辑是否合理

要求：分析要具体，引用实际的步骤数据和Agent的原话，避免泛泛而谈。
"""


def _load_vlm_logs(case: dict, screenshot_base: Path | None) -> str:
    """Read all step{N}_vlm.txt files for a case and return concatenated text."""
    if not screenshot_base:
        return ""
    case_id  = case["id"]
    matching = [d for d in screenshot_base.iterdir()
                if d.is_dir() and d.name.startswith(case_id + "_")]
    if not matching:
        return ""
    case_dir = matching[0]
    logs = []
    for txt in sorted(case_dir.glob("step*_vlm.txt"), key=lambda p: p.name):
        content = txt.read_text(encoding="utf-8", errors="replace").strip()
        logs.append(f"--- {txt.stem} ---\n{content}")
    return "\n\n".join(logs)


def _fmt_case_summary(case: dict) -> str:
    """Format a case's key metrics as text for the prompt."""
    steps = case.get("steps", [])
    step_lines = []
    for s in steps:
        ms = s.get("elapsed_ms", 0)
        thought = (s.get("thought") or "").replace("\n", " ")[:120]
        step_lines.append(
            f"  步骤{s['step']:02d} [{s['action_type']}] {ms/1000:.1f}s — {thought}"
        )

    vr = case.get("verification", {}) or {}
    checks = []
    for chk in vr.get("checks", []):
        mark = "OK" if chk.get("passed") else ("NG" if chk.get("passed") is False else "??")
        checks.append(f"  {mark} [{chk.get('method')}] {chk.get('checkpoint','')[:50]} — {chk.get('reason','')}")

    return "\n".join([
        f"用例ID: {case['id']}  状态: {case['status']}  "
        f"总步骤: {case.get('total_steps',0)}  "
        f"预期步骤: {case.get('expected_steps','-')}  "
        f"耗时: {case.get('elapsed_ms',0)/1000:.1f}s",
        f"任务: {case.get('task','')[:200]}",
        "操作步骤:",
        *step_lines,
        "验证结果:",
        *checks,
    ])


def generate(result_json: dict, screenshot_base: Path | None = None) -> str:
    """
    Call Doubao to generate AI insights for the benchmark run.

    Args:
        result_json:     Parsed benchmark result JSON.
        screenshot_base: Parent dir with per-run screenshot subdirs (for VLM logs).

    Returns:
        Markdown string with analysis and recommendations.
    """
    cases = [c for c in result_json.get("cases", []) if c.get("status") != "skipped"]
    if not cases:
        return "无有效用例数据。"

    # Build user message
    parts = [
        f"## 测试概览",
        f"时间戳：{result_json.get('timestamp','')}",
        f"总用例：{result_json.get('total',0)}  通过：{result_json.get('done',0)}  "
        f"TSR：{result_json.get('tsr',0):.1f}%",
        "",
    ]

    for case in cases:
        parts.append(f"## 用例数据：{case['id']}")
        parts.append(_fmt_case_summary(case))
        parts.append("")

        vlm_logs = _load_vlm_logs(case, screenshot_base)
        if vlm_logs:
            parts.append(f"## VLM 思维过程：{case['id']}")
            parts.append(vlm_logs)
            parts.append("")

    user_content = "\n".join(parts)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_content},
    ]

    print("[insight] 调用 Doubao 生成 AI 分析...")
    raw = chat(messages, max_tokens=1500)
    print("[insight] 完成")
    return raw
