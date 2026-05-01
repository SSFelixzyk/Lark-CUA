"""
DSL Evaluator — score a generated benchmark YAML on 5 dimensions (0/1/2 each).

Usage:
    python tools/dsl_evaluator.py tests/benchmark/generated/im/IM_GEN_001.yaml
    python tools/dsl_evaluator.py tests/benchmark/generated/im/IM_GEN_001.yaml --save
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
你是飞书GUI测试DSL质量评审专家。对提供的测试用例YAML按5个维度各打0/1/2分。

## 评分标准

**ui_accuracy（UI路径准确性）**
2：ui_hints 中的控件名称、位置与参考界面文档完全一致，无虚构元素
1：大部分准确，但有模糊描述（如"点击那个按钮"）或位置不精确
0：描述了不存在的控件，或交互路径明显错误

**executability（步骤可执行性）**
2：每步操作原子且明确，GUI Agent 可直接执行，无需猜测
1：步骤基本清晰，但有 1~2 处需要推断，Agent 可能卡住
0：存在无法执行的步骤，或缺少关键中间步骤

**verifiability（成功标准可验证性）**
2：success_criteria 和 checkpoints 均可从截图直接判断，含具体可见元素或文字
1：部分 checkpoint 需主观判断，或 success_criteria 较宽泛
0：无法从截图判断，或含主观描述（如"操作流畅"）

**difficulty（难度校准）**
2：level 定级、expected_steps、timeout_steps 与任务复杂度高度匹配
1：定级基本合理但步骤数偏差 >30%，或 timeout 明显过紧/过松
0：定级明显错误（如 5 步任务定为 L1），或 timeout_steps < expected_steps

**completeness（完整性）**
2：所有字段填写完整，占位符使用正确，tags 合理
1：有 1~2 个字段缺失或为空，但不影响执行
0：缺少 task、ui_hints、success_criteria 等关键字段之一

## 输出格式
严格输出 JSON，不加任何说明。overall = 各维度最低分。
verdict 规则：overall=2 → ready，overall=1 → needs_review，overall=0 → reject

{
  "scores": {
    "ui_accuracy":   {"score": <0|1|2>, "reason": "<一句话>"},
    "executability": {"score": <0|1|2>, "reason": "<一句话>"},
    "verifiability": {"score": <0|1|2>, "reason": "<一句话>"},
    "difficulty":    {"score": <0|1|2>, "reason": "<一句话>"},
    "completeness":  {"score": <0|1|2>, "reason": "<一句话>"}
  },
  "overall": <int>,
  "verdict": "<ready|needs_review|reject>"
}
"""


def _load_ui_context(product: str) -> str:
    path = UI_CONTEXT_DIR / f"{product}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _infer_product(case: dict, yaml_path: Path) -> str:
    """Infer product from case id prefix or parent directory name."""
    case_id = case.get("id", "")
    prefix_map = {"IM_": "im", "DOC_": "docs", "CAL_": "calendar",
                  "BASE_": "base", "VC_": "vc", "MAIL_": "mail", "GUI_": "gui"}
    for prefix, prod in prefix_map.items():
        if case_id.startswith(prefix):
            return prod
    # Fallback: parent dir name
    return yaml_path.parent.name


def _extract_json(text: str) -> dict:
    """Extract JSON from model response, tolerating markdown fences."""
    text = re.sub(r"^```json\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$",  "", text.strip())
    # Find first { ... } block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON object found in response:\n{text}")
    return json.loads(m.group())


# ── Core ──────────────────────────────────────────────────────────────────────

def evaluate(yaml_path: Path) -> dict:
    yaml_path = Path(yaml_path)
    with open(yaml_path, encoding="utf-8") as f:
        case = yaml.safe_load(f)
    yaml_text = yaml_path.read_text(encoding="utf-8")

    product    = _infer_product(case, yaml_path)
    ui_context = _load_ui_context(product)

    ui_section = f"## 飞书 {product.upper()} 界面参考\n{ui_context}\n\n" if ui_context else ""

    user_content = f"""\
{ui_section}## 待评审的测试用例 YAML
{yaml_text}
"""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_content},
    ]

    print(f"[evaluator] scoring {yaml_path.name}...")
    raw = chat(messages, max_tokens=800)

    try:
        result = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"Failed to parse evaluator response: {e}\n\nRaw:\n{raw}")

    # Recompute overall as min score (guard against model error)
    scores = result.get("scores", {})
    if scores:
        mins = min(v["score"] for v in scores.values())
        result["overall"] = mins
        result["verdict"] = {2: "ready", 1: "needs_review"}.get(mins, "reject")

    result["case_id"]   = case.get("id", yaml_path.stem)
    result["yaml_path"] = str(yaml_path)
    return result


def save_result(result: dict, yaml_path: Path) -> Path:
    out = yaml_path.with_suffix(".eval.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

VERDICT_LABEL = {"ready": "READY ✓", "needs_review": "NEEDS REVIEW ⚠", "reject": "REJECT ✗"}
DIM_ORDER = ["ui_accuracy", "executability", "verifiability", "difficulty", "completeness"]


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
    print(f"{'─' * 60}")
    for dim in DIM_ORDER:
        v = result["scores"].get(dim, {})
        bar = "██" if v.get("score") == 2 else ("█░" if v.get("score") == 1 else "░░")
        print(f"  {bar}  {dim:<20} {v.get('score', '?')}/2  {v.get('reason', '')}")
    print(f"{'=' * 60}\n")

    if args.save:
        out = save_result(result, Path(args.yaml_path))
        print(f"[evaluator] saved → {out}")


if __name__ == "__main__":
    main()
