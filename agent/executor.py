"""
Parses raw VLM output and executes via pyautogui.
Uses logical screen coordinates (pyautogui.size()) for DPI-correct execution.
"""

import re
import time
import pyautogui
from ui_tars.action_parser import parse_action_to_structure_output, parsing_response_to_pyautogui_code

# Slight pause before each action so the screen is stable
pyautogui.PAUSE = 0.3
# Move mouse to top-left corner to abort (pyautogui built-in failsafe)
pyautogui.FAILSAFE = True

WAIT_SECONDS = 3


def logical_size() -> tuple[int, int]:
    """Screen size — VM resolution in VM mode, host resolution otherwise."""
    import config
    if config.VM_MODE:
        from agent.screenshot import screen_size
        return screen_size()
    s = pyautogui.size()
    return s.width, s.height


def execute(raw_response: str) -> dict:
    """
    Parse a raw VLM Thought+Action string and execute it.

    Returns a dict:
      status  : "executed" | "done" | "failed" | "wait" | "error"
      thought : extracted Thought text
      action_type : e.g. "click", "type", "finished"
      code    : generated pyautogui code string (or special value)
      error   : error message if status=="error"
    """
    w, h = logical_size()
    result = {
        "status": "executed",
        "thought": _extract_thought(raw_response),
        "action_type": None,
        "code": "",
        "error": None,
    }

    # Handle failed() which isn't in the original action_parser
    if re.search(r"\bfailed\s*\(", raw_response):
        content = _extract_content_arg(raw_response, "failed")
        result["status"] = "failed"
        result["action_type"] = "failed"
        result["code"] = f"failed({content!r})"
        return result

    # Handle wait() — sleep and signal the loop to re-screenshot
    if re.search(r"\bwait\s*\(\s*\)", raw_response):
        result["status"] = "wait"
        result["action_type"] = "wait"
        time.sleep(WAIT_SECONDS)
        return result

    try:
        actions = parse_action_to_structure_output(
            raw_response,
            factor=1000,
            origin_resized_height=h,
            origin_resized_width=w,
            model_type="doubao",
        )
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"parse failed: {e}"
        # print(result["error"])
        return result

    if not actions:
        result["status"] = "error"
        result["error"] = "no actions parsed"
        return result

    result["action_type"] = actions[0]["action_type"]

    try:
        code = parsing_response_to_pyautogui_code(actions, h, w)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"code gen failed: {e}"
        return result

    result["code"] = code

    if code == "DONE":
        result["status"] = "done"
        return result

    try:
        import config
        if config.VM_MODE:
            from vm.vm_client import get_vm_client
            get_vm_client().execute(code)
        else:
            exec(code, {})  # noqa: S102
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"exec failed: {e}\ncode:\n{code}"
        return result

    return result


# ── helpers ──────────────────────────────────────────────────────────────────

def _extract_thought(text: str) -> str:
    m = re.search(r"Thought:\s*(.+?)(?=\s*Action:|$)", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def _extract_content_arg(text: str, func: str) -> str:
    m = re.search(rf"{func}\s*\(\s*content=['\"](.+?)['\"]\s*\)", text, re.DOTALL)
    return m.group(1) if m else ""
