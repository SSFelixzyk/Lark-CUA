"""
Self-healing module for Lark-CUA agent.

Currently only CHECKPOINT_TIMEOUT is wired up.
EXPLICIT_FAIL / IMPLICIT_STUCK / EXPLICIT_STUCK are defined but not yet active.
"""

import re
import json
import math
from enum import Enum
from pathlib import Path

from llm.doubao_client import chat, build_user_message


class HealTrigger(Enum):
    CHECKPOINT_TIMEOUT = "checkpoint_timeout"   # checkpoint not reached in budget


_STRATEGY_HINTS: dict[HealTrigger, str] = {
    HealTrigger.CHECKPOINT_TIMEOUT: (
        "你连续多步未能确认中间状态。请先判断当前处于哪种情况，再决定策略：\n"
        "- 如果你仍在正确的功能区域内，但操作方式有误（如反复输入无效内容、"
        "点击了错误控件、等待无响应的 UI）→ 换一种交互方式（快捷键、右键菜单、"
        "工具栏按钮、双击等），在当前位置继续完成该状态，不要回首页；\n"
        "- 如果当前界面已与任务完全无关，确认已完全偏离正确路径 → "
        "再回到飞书主界面（点击左侧导航栏首页图标）重新开始。"
    ),
}

_HEAL_SYSTEM = """\
你是飞书桌面客户端自动化测试的自愈专家。

当前任务执行失败或陷入循环，需要你分析根因并提出一条全新的探索路径。

规则：
- 不要重复之前已经失败的操作路径
- 严格按照【策略方向】给出的思路出发，再结合截图实际情况调整

输出严格 JSON，无其他内容：
{
  "diagnosis": "失败根因（一句话，说明为什么之前的路径不通）",
  "new_strategy": "本次尝试的新路径思路（结合策略方向）"
}
"""


def build_heal_context(recent_steps: list[dict]) -> str:
    lines = []
    for i, s in enumerate(recent_steps, 1):
        lines.append(f"Step -{len(recent_steps) - i + 1}: Thought: {s.get('thought', '')}")
        lines.append(f"         Action: {s.get('action', '')}")
        status = s.get("status", "")
        if status not in ("", "executed", "wait"):
            lines.append(f"         Status: {status}")
    return "\n".join(lines)


def diagnose_and_heal(
    task: str,
    recent_history: list[dict],
    screenshot_path: Path,
    trigger: HealTrigger = HealTrigger.EXPLICIT_FAIL,
) -> dict:
    """
    Analyze failure and propose a recovery strategy.

    Args:
        task:            The original task description.
        recent_history:  Last N turns of message history (user+assistant pairs).
        screenshot_path: Current screen state.
        trigger:         Which trigger fired — determines strategy hint injected.

    Returns:
        {"diagnosis": str, "new_strategy": str}
    """
    strategy_hint = _STRATEGY_HINTS[trigger]
    user_text = (
        f"测试任务：{task}\n\n"
        f"触发原因：{trigger.value}\n"
        f"【策略方向】{strategy_hint}\n\n"
        "请根据以上对话历史和当前截图，提出全新的操作方案。"
    )
    user_msg = build_user_message(user_text, screenshot_path)
    messages = [{"role": "system", "content": _HEAL_SYSTEM}]
    messages.extend(recent_history)
    messages.append(user_msg)
    try:
        raw = chat(messages, max_tokens=200)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            result = json.loads(m.group())
            if "diagnosis" in result and "new_strategy" in result:
                return result
    except Exception:
        pass
    return {"diagnosis": "无法解析", "new_strategy": ""}


_ENTRY_SYSTEM = """\
你是飞书自动化测试专家。
以下是一次自愈过程的记录，请生成两行内容供后续任务参考：

1. 解决（不超过30字）：本次具体用了哪个操作路径解决了问题
2. 启发（不超过40字）：从本次失败和修复中提炼出的可泛化操作规律，能指导后续类似场景

输出严格 JSON，无其他内容：
{"solution": "...", "heuristic": "..."}
"""


def generate_memory_entry(
    heal_context: dict,
    heal_step_records: list[dict],
    case_id: str,
    case_title: str,
    trigger: HealTrigger,
    checkpoint_idx: int | None,
) -> str:
    """Call LLM to generate a structured memory entry for the product memory file."""
    from datetime import date
    from llm.doubao_client import chat, build_user_message

    steps_text = "\n".join(
        f"{i}. Thought: {s.get('thought', '')}  Action: {s.get('action', '')}"
        for i, s in enumerate(heal_step_records, 1)
    )
    user_text = (
        f"失败根因：{heal_context.get('diagnosis', '')}\n"
        f"新策略：{heal_context.get('new_strategy', '')}\n\n"
        f"实际执行步骤：\n{steps_text}"
    )

    solution = heal_context.get("new_strategy", "")
    heuristic = ""
    try:
        msg = build_user_message(user_text)
        raw = chat([{"role": "system", "content": _ENTRY_SYSTEM}, msg], max_tokens=120)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            data = json.loads(m.group())
            solution  = data.get("solution",  solution)
            heuristic = data.get("heuristic", "")
    except Exception:
        pass

    cp_tag = f"（checkpoint {checkpoint_idx}）" if checkpoint_idx is not None else ""
    today = date.today().isoformat()
    lines = [
        f"### {today} | {case_id} — {case_title}",
        f"**触发**: {trigger.value}{cp_tag}",
        f"**问题**: {heal_context.get('diagnosis', '')}",
        f"**解决**: {solution}",
    ]
    if heuristic:
        lines.append(f"**启发**: {heuristic}")
    return "\n".join(lines)


# ── Stuck detectors ───────────────────────────────────────────────────────────

def is_implicit_stuck(steps: list, window: int = 3) -> bool:
    """
    Returns True if the last `window` steps all share the same click action_type
    AND their coordinates cluster within a 60 px radius.
    """
    if len(steps) < window:
        return False
    recent = steps[-window:]
    types = [s.action_type for s in recent]
    if len(set(types)) != 1 or types[0] not in ("click", "left_double", "right_single", "double_click"):
        return False
    coords = []
    for s in recent:
        m = re.search(r"(\d+)\s+(\d+)", s.pyautogui_code or "")
        if m:
            coords.append((int(m.group(1)), int(m.group(2))))
    if len(coords) < window:
        return False
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    return all(math.hypot(c[0] - cx, c[1] - cy) < 60 for c in coords)


def is_explicit_stuck(prev_shot: Path | None, curr_shot: Path, threshold: float = 0.005) -> bool:
    """
    Returns True if fewer than `threshold` fraction of pixels changed between
    prev_shot and curr_shot (effectively: the screen looks identical).

    threshold=0.005 filters cursor blink and minor animations while catching
    cases where actions have zero visible effect.
    Requires Pillow + numpy; returns False silently if unavailable.
    """
    if prev_shot is None or not prev_shot.exists() or not curr_shot.exists():
        return False
    try:
        from PIL import Image, ImageChops
        import numpy as np
        img_prev = Image.open(prev_shot).convert("RGB")
        img_curr = Image.open(curr_shot).convert("RGB")
        if img_prev.size != img_curr.size:
            return False
        diff = np.array(ImageChops.difference(img_prev, img_curr))
        changed = int(np.count_nonzero(diff.sum(axis=2)))
        total = diff.shape[0] * diff.shape[1]
        return (changed / total) < threshold
    except Exception:
        return False
