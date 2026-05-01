"""
DSL Evaluator — score a generated benchmark YAML on 6 dimensions (0/1/2 each).

Usage:
    python -m dsl.evaluator tests/benchmark/generated/im/IM_GEN_001.yaml
    python -m dsl.evaluator tests/benchmark/generated/im/IM_GEN_001.yaml --save
"""

import sys
import re
import yaml
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from llm.doubao_client import chat

ROOT_DIR       = Path(__file__).parent.parent
UI_CONTEXT_DIR = ROOT_DIR / "docs" / "ui_context"

# ── Scoring rubric ────────────────────────────────────────────────────────────

SYSTEM = """\
你是飞书GUI测试DSL质量评审专家。对提供的测试用例YAML按6个维度各打0/1/2分。

## 评分标准

**ui_accuracy（UI路径准确性）**
2：ui_hints 中的控件名称、位置与参考界面文档完全一致，无虚构元素
1：大部分准确，但有模糊描述或位置不精确
0：描述了不存在的控件，或交互路径明显错误

**executability（目标可执行性）**
2：task 描述的目标清晰，ui_hints 提供有效辅助，Agent 可自主完成
1：task 目标基本清晰，但存在歧义或缺少关键前提
0：task 目标不明确，或 ui_hints 包含错误路径会误导 Agent

**verifiability（成功标准可验证性）**
2：success_criteria 可从截图直接判断，含具体可见元素或文字
1：success_criteria 较宽泛，需主观判断
0：无法从截图判断，或含主观描述（如"操作流畅"）

**checkpoint_quality（检查点路径无关性）**
2：所有 checkpoint 均为完成任务的必经状态——无论 Agent 走哪条合法路径（搜索/直接点击/快捷键），
   每个 checkpoint 都必然会出现；描述的是结果状态而非操作过程
1：大部分 checkpoint 是必经状态，但有 1 个含路径特定特征
   （如"搜索结果中出现"、"搜索框中已输入"、"下拉候选项显示"等）
0：存在明显路径特定的 checkpoint，走另一条合法路径就会跳过它；
   或 checkpoint 描述的是操作动作而非视觉状态

**difficulty（难度校准）**
2：level 定级、expected_steps、timeout_steps 与任务复杂度高度匹配
1：定级基本合理但步骤数偏差 >30%，或 timeout 过紧/过松
0：定级明显错误，或 timeout_steps < expected_steps

**completeness（完整性）**
2：所有字段填写完整，占位符使用正确，tags 合理
1：有 1~2 个字段缺失或为空，不影响执行
0：缺少 task、ui_hints、success_criteria 等关键字段之一

## 输出格式
严格输出 JSON，不加任何说明。
overall = 各维度最低分。
verdict：overall=2 → ready，overall=1 → needs_review，overall=0 → reject
suggestions：仅当 verdict != ready 时输出，列出 1~3 条具体可执行的修改建议（直接指出要改什么）；ready 时输出空列表。

{
  "scores": {
    "ui_accuracy":        {"score": <0|1|2>, "reason": "<一句话>"},
    "executability":      {"score": <0|1|2>, "reason": "<一句话>"},
    "verifiability":      {"score": <0|1|2>, "reason": "<一句话>"},
    "checkpoint_quality": {"score": <0|1|2>, "reason": "<一句话>"},
    "difficulty":         {"score": <0|1|2>, "reason": "<一句话>"},
    "completeness":       {"score": <0|1|2>, "reason": "<一句话>"}
  },
  "overall": <int>,
  "verdict": "<ready|needs_review|reject>",
  "suggestions": ["<具体建议1>", "<具体建议2>"]
}
"""


def _load_ui_context(product: str) -> str:
    path = UI_CONTEXT_DIR / f"{product}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _infer_product(case: dict, source_hint: str = "") -> str:
    case_id = case.get("id", "")
    prefix_map = {"IM_": "im", "DOC_": "docs", "CAL_": "calendar",
                  "BASE_": "base", "VC_": "vc", "MAIL_": "mail", "GUI_": "gui"}
    for prefix, prod in prefix_map.items():
        if case_id.startswith(prefix):
            return prod
    return Path(source_hint).parent.name if source_hint else "im"


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text.strip())
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in response:\n{text}")
    return json.loads(m.group())


def _call_evaluator(yaml_text: str, product: str) -> dict:
    ui_context = _load_ui_context(product)
    ui_section = f"## 飞书 {product.upper()} 界面参考\n{ui_context}\n\n" if ui_context else ""
    user_content = f"{ui_section}## 待评审的测试用例 YAML\n{yaml_text}\n"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_content},
    ]
    raw = chat(messages, max_tokens=1000)
    result = _extract_json(raw)

    # Recompute overall as min (guard against model error)
    scores = result.get("scores", {})
    if scores:
        mins = min(v["score"] for v in scores.values())
        result["overall"] = mins
        result["verdict"] = {2: "ready", 1: "needs_review"}.get(mins, "reject")

    result.setdefault("suggestions", [])
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def evaluate(yaml_path: Path) -> dict:
    """Evaluate a DSL YAML file on disk."""
    yaml_path = Path(yaml_path)
    with open(yaml_path, encoding="utf-8") as f:
        case = yaml.safe_load(f)
    yaml_text = yaml_path.read_text(encoding="utf-8")

    product = _infer_product(case, str(yaml_path))
    print(f"[evaluator] scoring {yaml_path.name}...")
    result = _call_evaluator(yaml_text, product)
    result["case_id"]   = case.get("id", yaml_path.stem)
    result["yaml_path"] = str(yaml_path)
    return result


def evaluate_text(yaml_text: str, product: str, case_id: str = "") -> dict:
    """Evaluate a DSL YAML string in memory (no file needed)."""
    result = _call_evaluator(yaml_text, product)
    result["case_id"] = case_id
    return result


def save_result(result: dict, yaml_path: Path) -> Path:
    out = yaml_path.with_suffix(".eval.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

VERDICT_LABEL = {"ready": "READY [OK]", "needs_review": "NEEDS REVIEW [?]", "reject": "REJECT [NG]"}
DIM_ORDER = ["ui_accuracy", "executability", "verifiability", "checkpoint_quality",
             "difficulty", "completeness"]


def main():
    parser = argparse.ArgumentParser(description="Score a generated Feishu DSL YAML")
    parser.add_argument("yaml_path", help="Path to the YAML file to evaluate")
    parser.add_argument("--save", action="store_true",
                        help="Save evaluation result as <case>.eval.json alongside the YAML")
    args = parser.parse_args()

    result = evaluate(Path(args.yaml_path))

    print(f"\n{'=' * 60}")
    print(f"Case    : {result['case_id']}")
    print(f"Overall : {result['overall']}/2  [{VERDICT_LABEL.get(result['verdict'], result['verdict'])}]")
    print(f"{'--' * 30}")
    for dim in DIM_ORDER:
        v = result["scores"].get(dim, {})
        bar = "[2]" if v.get("score") == 2 else ("[1]" if v.get("score") == 1 else "[0]")
        print(f"  {bar}  {dim:<22} {v.get('score', '?')}/2  {v.get('reason', '')}")
    if result.get("suggestions"):
        print(f"{'--' * 30}")
        print("  Suggestions:")
        for s in result["suggestions"]:
            print(f"    - {s}")
    print(f"{'=' * 60}\n")

    if args.save:
        out = save_result(result, Path(args.yaml_path))
        print(f"[evaluator] saved -> {out}")


if __name__ == "__main__":
    main()
