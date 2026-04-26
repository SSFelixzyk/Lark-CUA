"""
M1 test: run a single Feishu task through the full ReAct loop.

Usage (make sure Feishu desktop is open and visible):
    cd Lark-Agent
    python tests/test_loop.py

The script prints a step-by-step summary and saves all screenshots
to Lark-Agent/screenshots/.

Set DRY_RUN = True to parse + print actions without actually executing them.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: initialises env + dirs

DRY_RUN = False   # set True to skip pyautogui execution

# ── patch executor for dry-run ────────────────────────────────────────────────
if DRY_RUN:
    import agent.executor as _exec
    _orig = _exec.execute

    def _dry_execute(raw):
        r = _orig.__wrapped__(raw) if hasattr(_orig, "__wrapped__") else None
        from ui_tars.action_parser import parse_action_to_structure_output, parsing_response_to_pyautogui_code
        import re, agent.executor as ex
        thought = ex._extract_thought(raw)
        print(f"\n[DRY-RUN] Thought: {thought}")
        print(f"[DRY-RUN] Raw    : {raw.splitlines()[-1]}")
        return {
            "status": "executed",
            "thought": thought,
            "action_type": "dry_run",
            "code": "[skipped]",
            "error": None,
        }

    _exec.execute = _dry_execute
# ─────────────────────────────────────────────────────────────────────────────

from agent.loop import LarkAgent


# ── M1 test tasks ─────────────────────────────────────────────────────────────
TASKS = [
    "点击飞书左侧导航栏中的「消息」图标，进入 IM 页面",
]


def run_task(task: str, max_steps: int = 5):
    print("=" * 65)
    print(f"TASK: {task}")
    print(f"MAX STEPS: {max_steps}  |  DRY_RUN: {DRY_RUN}")
    print("=" * 65)

    if not DRY_RUN:
        import time
        print("Starting in 3 seconds — switch to Feishu window now...")
        time.sleep(3)

    agent = LarkAgent(max_steps=max_steps)
    result = agent.run(task)

    print()
    print(result.summary())
    print()

    # Print step details
    for step in result.steps:
        print(f"--- Step {step.step:02d} [{step.status.upper()}] ---")
        print(f"  Thought : {step.thought}")
        print(f"  Action  : {step.action_type}")
        if step.error:
            print(f"  Error   : {step.error}")
        print(f"  Code    :\n{step.pyautogui_code}")
        print(f"  Screenshot: {step.screenshot}")
        print()

    return result


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else TASKS[0]
    run_task(task, max_steps=5)
