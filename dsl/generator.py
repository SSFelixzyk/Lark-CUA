"""
DSL Generator — natural language → Feishu benchmark YAML case.

Usage:
    python -m dsl.generator "向于凯成发送消息「Hello」" --product im
    python -m dsl.generator "打开日历创建明天下午2点的会议" --product calendar --evaluate
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
- task: 自然语言目标描述，说明"做什么"而非"怎么做"；用「」标注操作对象/内容；
        涉及联系人用 <<TEST_CONTACT>>，群聊用 <<TEST_GROUP>>，日期用 <<DATE>>
        ✓ 好例子: 「向<<TEST_CONTACT>>发送消息「全流程测试001」」
        ✗ 坏例子: 「使用Ctrl+K搜索<<TEST_CONTACT>>，点击单聊，输入消息，按Enter发送」（这是步骤，放ui_hints）
- ui_hints: 可选操作提示——关键控件位置（上/下/左/右/哪个区域）、快捷键等；
            Agent 可自行决定是否按此路径，不是强制步骤；
            快捷键/具体路径只放这里，不要写进 task
- preconditions: 执行前提列表
- success_criteria: 单句话，描述可从截图直接判断的成功状态
- checkpoints: Agent 导航用的必经状态节点，按执行顺序排列
    数量：L1（1个）/ L2（1~2个）/ L3（2~3个）——宁少勿多
    必经性测试（生成每个 checkpoint 前先问自己）：
      "如果 Agent 走了另一条合法路径完成任务，这个状态还会出现吗？"
      → 会 → 可以作为 checkpoint
      → 不一定 → 去掉，它只是某条路径的中间状态，不是必经点
    要求：① 描述截图可见的视觉状态，不描述操作动作
          ② 必经且路径无关：任何合法操作路径都会经过此状态
    好例子（发消息给<<TEST_CONTACT>>）:
      ["与<<TEST_CONTACT>>的单聊界面已打开",   ← 无论搜索还是直接点击，都会到这里
       "消息已出现在对话记录底部"]              ← 发送成功的唯一证明
    坏例子（路径特定，非必经）:
      "全局搜索结果中出现联系人<<TEST_CONTACT>>"  ← 直接点聊天列表就不经过这里
      "搜索框中已输入联系人名字"                   ← 同上，只在搜索路径上出现
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


def build_messages(
    user_input: str,
    product: str,
    case_id: str,
    previous_yaml: str | None = None,
    suggestions: list[str] | None = None,
) -> list[dict]:
    ui_ctx   = load_ui_context(product)
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
    if previous_yaml and suggestions:
        feedback_lines = "\n".join(f"- {s}" for s in suggestions)
        user_content += f"""
## 上一次生成的结果（请在此基础上修改，不要重新生成）
{previous_yaml}

## 评审反馈（必须修复以下问题后重新输出完整 YAML）
{feedback_lines}
"""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_content},
    ]


def _parse_raw(raw: str, case_id: str) -> dict:
    """Strip fences, parse YAML, enforce fixed fields."""
    raw = re.sub(r"^```ya?ml\s*", "", raw.strip(), flags=re.IGNORECASE)
    raw = re.sub(r"\s*```\s*$",   "", raw.strip())
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"Model output is not valid YAML:\n{e}\n\nRaw:\n{raw}")
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a YAML mapping, got: {type(parsed)}\n\nRaw:\n{raw}")
    parsed["id"]        = case_id
    parsed["generated"] = True
    return parsed


def _save(parsed: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(parsed, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ── Core ──────────────────────────────────────────────────────────────────────

def generate(user_input: str, product: str) -> tuple[dict, Path]:
    """Single-shot generation (no evaluate loop). Saves immediately."""
    case_id  = next_case_id(product)
    out_path = GENERATED_DIR / product / f"{case_id}.yaml"

    print(f"[generator] calling Doubao for case {case_id}...")
    raw    = chat(build_messages(user_input, product, case_id), max_tokens=1500)
    parsed = _parse_raw(raw, case_id)
    _save(parsed, out_path)
    return parsed, out_path


def generate_loop(
    user_input: str,
    product: str,
    max_retries: int = 3,
) -> tuple[dict, Path]:
    """
    Generate → evaluate → refine loop.
    Only the final passing (or last) version is written to disk.
    Intermediate attempts stay in memory.
    """
    from dsl.evaluator import evaluate_text, VERDICT_LABEL

    case_id  = next_case_id(product)
    out_path = GENERATED_DIR / product / f"{case_id}.yaml"

    previous_yaml: str | None = None
    suggestions:   list[str] | None = None
    parsed: dict = {}

    for attempt in range(1, max_retries + 1):
        print(f"[generator] attempt {attempt}/{max_retries} — {case_id}")
        messages = build_messages(
            user_input, product, case_id,
            previous_yaml=previous_yaml,
            suggestions=suggestions,
        )
        raw = chat(messages, max_tokens=1500)
        try:
            parsed = _parse_raw(raw, case_id)
        except ValueError as e:
            print(f"  [generator] parse error: {e}")
            continue

        yaml_text = yaml.dump(parsed, allow_unicode=True, default_flow_style=False, sort_keys=False)

        print(f"[evaluator] scoring attempt {attempt}...")
        result = evaluate_text(yaml_text, product, case_id)
        verdict = result["verdict"]
        overall = result["overall"]
        label   = VERDICT_LABEL.get(verdict, verdict)
        print(f"  => {label}  overall={overall}/2")

        for dim, v in result["scores"].items():
            mark = "[2]" if v["score"] == 2 else ("[1]" if v["score"] == 1 else "[0]")
            print(f"     {mark} {dim:<22} {v['reason']}")

        if result.get("suggestions"):
            print("  Suggestions:")
            for s in result["suggestions"]:
                print(f"    - {s}")

        if verdict == "ready":
            print(f"[generator] passed on attempt {attempt}, saving -> {out_path}")
            _save(parsed, out_path)
            return parsed, out_path

        # Prepare for next attempt
        previous_yaml = yaml_text
        suggestions   = result.get("suggestions") or []

    # Max retries exhausted — save best-effort result
    print(f"[generator] max retries reached, saving last attempt -> {out_path}")
    _save(parsed, out_path)
    return parsed, out_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a Feishu benchmark YAML from natural language")
    parser.add_argument("description", help="Natural language test description")
    parser.add_argument("--product", required=True,
                        choices=list(PRODUCT_PREFIX.keys()),
                        help="Target Feishu product")
    parser.add_argument("--evaluate", action="store_true",
                        help="Run generate→evaluate loop until DSL passes quality check")
    parser.add_argument("--max-retries", type=int, default=3,
                        help="Max refinement attempts in loop mode (default 3)")
    args = parser.parse_args()

    if args.evaluate:
        parsed, out_path = generate_loop(args.description, args.product, args.max_retries)
    else:
        parsed, out_path = generate(args.description, args.product)

    print(f"\n[generator] saved -> {out_path}\n")
    print("=" * 60)
    print(yaml.dump(parsed, allow_unicode=True, default_flow_style=False, sort_keys=False))
    print("=" * 60)

    if args.evaluate:
        from dsl.evaluator import evaluate
        result = evaluate(out_path)
        verdict_label = {"ready": "[READY]", "needs_review": "[REVIEW]", "reject": "[REJECT]"}.get(
            result["verdict"], result["verdict"]
        )
        print(f"\n[evaluator] overall={result['overall']}  {verdict_label}")
        for dim, v in result["scores"].items():
            print(f"  {dim:<20} {v['score']}/2  {v['reason']}")


if __name__ == "__main__":
    main()
