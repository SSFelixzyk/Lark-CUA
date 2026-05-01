"""
DSL Generator — natural language → Feishu benchmark YAML case.

Usage:
    python tools/dsl_generator.py "在IM中搜索于凯成并发送消息Hello" --product im
    python tools/dsl_generator.py "打开日历创建明天下午2点的会议" --product calendar
    python tools/dsl_generator.py "新建云文档并输入标题" --product docs --evaluate
"""

import sys
import re
import yaml
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from llm.doubao_client import chat

ROOT_DIR        = Path(__file__).parent.parent
UI_CONTEXT_DIR  = ROOT_DIR / "docs" / "ui_context"
BENCHMARK_DIR   = ROOT_DIR / "tests" / "benchmark"
GENERATED_DIR   = BENCHMARK_DIR / "generated"

PRODUCT_PREFIX = {
    "im": "IM", "docs": "DOC", "calendar": "CAL",
    "base": "BASE", "vc": "VC", "mail": "MAIL", "gui": "GUI",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_ui_context(product: str) -> str:
    path = UI_CONTEXT_DIR / f"{product}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"（暂无 {product} 的界面说明文档，请根据常识生成）"


def load_few_shot(product: str) -> str:
    """Pick one L1 + one L2 example from the product's benchmark YAML."""
    path = BENCHMARK_DIR / f"{product}.yaml"
    if not path.exists():
        return "（无参考示例）"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = data.get("cases", [])
    l1 = next((c for c in cases if c.get("level") == "L1"), None)
    l2 = next((c for c in cases if c.get("level") == "L2" and c.get("ui_hints")), None)
    picks = [c for c in [l1, l2] if c is not None]
    return "\n---\n".join(
        yaml.dump(c, allow_unicode=True, default_flow_style=False) for c in picks
    )


def next_case_id(product: str) -> str:
    prefix = PRODUCT_PREFIX.get(product, product.upper())
    gen_dir = GENERATED_DIR / product
    gen_dir.mkdir(parents=True, exist_ok=True)
    existing = list(gen_dir.glob(f"{prefix}_GEN_*.yaml"))
    nums = []
    for f in existing:
        m = re.search(r"_GEN_(\d+)", f.stem)
        if m:
            nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    return f"{prefix}_GEN_{n:03d}"


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM = """\
你是飞书GUI自动化测试DSL生成专家。根据用户的自然语言描述，生成一个完整的飞书测试用例YAML。

## 字段说明
- id: 已指定，不要修改
- level: L1（1~2步）/ L2（3~10步，单产品）/ L3（跨产品联动）
- title: 15字以内的简短标题
- task: 按步骤描述操作，用「」标注UI元素名称；涉及联系人用 <<TEST_CONTACT>>，群聊用 <<TEST_GROUP>>，日期用 <<DATE>>
- ui_hints: 关键控件的具体位置（上/下/左/右/哪个区域）和交互方式，帮助Agent直接找到目标元素
- preconditions: 执行前提列表
- success_criteria: 单句话，描述可从截图直接判断的成功状态
- checkpoints: Agent 导航用的关键路径状态，按执行顺序排列
    数量：L1（1~2个）/ L2（2~4个）/ L3（2~5个）
    要求：① 描述截图可见的视觉状态，不描述操作动作
          ② 路径无关：无论经搜索、直接点击等不同路径到达，同样满足
    好例子（发消息）: ["与张三的对话窗口已打开", "消息已出现在对话记录中"]
    坏例子: ["点击了搜索框", "输入了张三的名字"]（这是操作，不是状态）
- cli_verifications: 结构化验证列表，每项为以下类型之一：
    {type: im_message,     expected_text: "消息内容"}   # 验证IM消息已发送
    {type: calendar_event, title: "日程标题"}            # 验证日程已创建
    {type: drive_doc,      name: "文档名称"}             # 验证云文档已创建
    {type: im_chat,        name: "群聊名称"}             # 验证群聊已创建
  若任务不涉及以上可验证资源，则省略此字段
- expected_steps: 最少步骤数（整数）
- timeout_steps: 超时步骤数（建议为 expected_steps 的 2 倍）
- tags: 小写标签列表
- generated: true（不要修改）

## 输出规则
- 只输出YAML内容，不加 ```yaml 代码块，不加任何说明文字
- ui_hints 必须具体到控件所在区域（如"消息面板顶部header右上角"）
- cli_verifications 中的文字要与 task 中实际输入/创建的内容完全一致
"""


def build_messages(user_input: str, product: str, case_id: str) -> list[dict]:
    ui_ctx  = load_ui_context(product)
    few_shot = load_few_shot(product)
    user_content = f"""\
## 本次生成的 case id（请填入 id 字段）
{case_id}

## 飞书 {product.upper()} 界面说明
{ui_ctx}

## 已有用例参考（参考格式和风格，勿直接复制内容）
{few_shot}

## 用户的测试描述
{user_input}
"""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_content},
    ]


# ── Core ──────────────────────────────────────────────────────────────────────

def generate(user_input: str, product: str) -> tuple[dict, Path]:
    case_id  = next_case_id(product)
    messages = build_messages(user_input, product, case_id)

    print(f"[generator] calling Doubao for case {case_id}...")
    raw = chat(messages, max_tokens=1500)

    # Strip accidental markdown fences
    raw = re.sub(r"^```ya?ml\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```\s*$",   "", raw.strip())

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"Model output is not valid YAML:\n{e}\n\nRaw:\n{raw}")

    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a YAML mapping, got: {type(parsed)}\n\nRaw:\n{raw}")

    # Enforce fixed fields
    parsed["id"]        = case_id
    parsed["generated"] = True

    out_dir  = GENERATED_DIR / product
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{case_id}.yaml"

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(parsed, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return parsed, out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a Feishu benchmark YAML from natural language")
    parser.add_argument("description", help="Natural language test description")
    parser.add_argument("--product", required=True,
                        choices=list(PRODUCT_PREFIX.keys()),
                        help="Target Feishu product")
    parser.add_argument("--evaluate", action="store_true",
                        help="Run DSL evaluator immediately after generation")
    args = parser.parse_args()

    parsed, out_path = generate(args.description, args.product)

    print(f"\n[generator] saved → {out_path}\n")
    print("=" * 60)
    print(yaml.dump(parsed, allow_unicode=True, default_flow_style=False, sort_keys=False))
    print("=" * 60)

    if args.evaluate:
        from tools.dsl_evaluator import evaluate
        result = evaluate(out_path)
        verdict_label = {"ready": "[READY]", "needs_review": "[REVIEW]", "reject": "[REJECT]"}.get(
            result["verdict"], result["verdict"]
        )
        print(f"\n[evaluator] overall={result['overall']}  {verdict_label}")
        for dim, v in result["scores"].items():
            print(f"  {dim:<20} {v['score']}/2  {v['reason']}")


if __name__ == "__main__":
    main()
