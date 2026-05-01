"""
ReAct execution loop for Lark-Agent.

Each step:
  1. Capture screenshot
  2. Build message history (last N turns)
  3. Call Doubao → raw Thought+Action          [timed → api_ms]
  4. Execute action via executor               [timed → exec_ms]
  5. Wait for UI to settle                     [timed → wait_ms]
  6. Record step result
  7. Repeat until finished / failed / max_steps
"""

import time
from pathlib import Path
from dataclasses import dataclass, field

import config
from agent.prompts import LARK_SYSTEM_PROMPT
from agent.screenshot import capture
from agent.executor import execute
from llm.doubao_client import chat, build_user_message
from ui_tars.action_parser import add_box_token


@dataclass
class StepRecord:
    step: int
    screenshot: str       # path
    thought: str
    action_type: str
    raw_response: str
    pyautogui_code: str
    status: str           # executed | done | failed | wait | error
    error: str | None
    elapsed_ms: int       # total wall-clock time for this step
    api_ms: int = 0       # time spent waiting for Doubao VLM response
    exec_ms: int = 0      # time spent in pyautogui execution
    wait_ms: int = 0      # time spent in post-action UI settle wait


@dataclass
class RunResult:
    task: str
    status: str           # done | failed | timeout | error
    steps: list[StepRecord] = field(default_factory=list)

    @property
    def total_steps(self):
        return len(self.steps)

    @property
    def elapsed_ms(self):
        return sum(s.elapsed_ms for s in self.steps)

    def summary(self) -> str:
        lines = [
            f"Task   : {self.task}",
            f"Result : {self.status.upper()}",
            f"Steps  : {self.total_steps}",
            f"Time   : {self.elapsed_ms / 1000:.1f}s",
            "",
        ]
        for s in self.steps:
            mark = {"done": "[DONE]", "failed": "[FAIL]", "error": "[ERR ]"}.get(
                s.status, "[ OK ]"
            )
            timing = f"total={s.elapsed_ms/1000:.1f}s api={s.api_ms/1000:.1f}s exec={s.exec_ms/1000:.1f}s wait={s.wait_ms/1000:.1f}s"
            lines.append(
                f"  Step {s.step:02d} {mark}  {s.action_type or '?':16s}  {timing}"
            )
        return "\n".join(lines)

    def timing_report(self) -> str:
        """Print a breakdown of where time is being spent across all steps."""
        if not self.steps:
            return "No steps recorded."
        total   = sum(s.elapsed_ms for s in self.steps)
        api     = sum(s.api_ms     for s in self.steps)
        exec_t  = sum(s.exec_ms    for s in self.steps)
        wait    = sum(s.wait_ms    for s in self.steps)
        other   = total - api - exec_t - wait

        def pct(ms):
            return f"{ms/1000:.1f}s ({ms/total*100:.0f}%)" if total else f"{ms/1000:.1f}s"

        lines = [
            "── 时间瓶颈分析 ──────────────────────",
            f"  总耗时   : {total/1000:.1f}s",
            f"  VLM API  : {pct(api)}",
            f"  GUI 执行 : {pct(exec_t)}",
            f"  UI 等待  : {pct(wait)}",
            f"  其他     : {pct(other)}",
            "  ──────────────────────────────────",
        ]
        for s in self.steps:
            lines.append(
                f"  Step {s.step:02d} [{s.action_type:12s}]"
                f"  api={s.api_ms/1000:.1f}s"
                f"  exec={s.exec_ms/1000:.1f}s"
                f"  wait={s.wait_ms/1000:.1f}s"
                f"  total={s.elapsed_ms/1000:.1f}s"
            )
        return "\n".join(lines)


def _sanitize_dirname(name: str) -> str:
    """Strip characters invalid in Windows directory names."""
    invalid = r'\/:*?"<>|'
    return "".join(c if c not in invalid else "_" for c in name)[:40]


class LarkAgent:
    def __init__(self, max_steps: int = config.MAX_STEPS, screenshot_dir: Path | str | None = None):
        self.max_steps = max_steps
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else config.SCREENSHOT_DIR

    def run(self, task: str) -> RunResult:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        result = RunResult(task=task, status="timeout")
        history: list[dict] = []
        system_prompt = LARK_SYSTEM_PROMPT.format(task=task)

        for step_num in range(1, self.max_steps + 1):
            t0 = time.time()

            # 1. Screenshot — saved as pending, renamed after action_type is known
            shot_path = capture(self.screenshot_dir / f"step{step_num:02d}_pending.png")

            # 2. Build messages
            user_msg = build_user_message("请观察当前屏幕，执行下一步操作。", shot_path)
            messages = _build_messages(system_prompt, history, user_msg)

            # 3. Call Doubao — timed
            t_api = time.time()
            try:
                raw = chat(messages, max_tokens=300)
            except Exception as e:
                rec = StepRecord(
                    step=step_num, screenshot=str(shot_path),
                    thought="", action_type="api_error",
                    raw_response="", pyautogui_code="",
                    status="error", error=str(e),
                    elapsed_ms=int((time.time() - t0) * 1000),
                    api_ms=int((time.time() - t_api) * 1000),
                )
                result.steps.append(rec)
                result.status = "error"
                break
            api_ms = int((time.time() - t_api) * 1000)

            # Log raw VLM output to file for post-run review
            try:
                (self.screenshot_dir / f"step{step_num:02d}_vlm.txt").write_text(
                    raw, encoding="utf-8"
                )
            except OSError:
                pass

            # 4. Execute — timed
            print(raw)
            t_exec = time.time()
            exec_result = execute(raw)
            exec_ms = int((time.time() - t_exec) * 1000)

            # Rename screenshot now that we know the action type
            action_type = exec_result["action_type"] or "unknown"
            final_shot = self.screenshot_dir / f"step{step_num:02d}_{action_type}.png"
            try:
                shot_path.rename(final_shot)
                shot_path = final_shot
            except OSError:
                pass

            # 5. Wait for UI to settle — timed
            t_wait = time.time()
            if exec_result["status"] != "wait":  # wait() already slept in executor
                time.sleep(config.STEP_WAIT_MS / 1000)
            wait_ms = int((time.time() - t_wait) * 1000)

            elapsed = int((time.time() - t0) * 1000)
            print(f"  [step {step_num:02d}] total={elapsed/1000:.1f}s  "
                  f"api={api_ms/1000:.1f}s  exec={exec_ms/1000:.1f}s  wait={wait_ms/1000:.1f}s")

            rec = StepRecord(
                step=step_num,
                screenshot=str(shot_path),
                thought=exec_result["thought"],
                action_type=action_type,
                raw_response=raw,
                pyautogui_code=exec_result["code"],
                status=exec_result["status"],
                error=exec_result.get("error"),
                elapsed_ms=elapsed,
                api_ms=api_ms,
                exec_ms=exec_ms,
                wait_ms=wait_ms,
            )
            result.steps.append(rec)

            # 6. Update history (keep last N turns)
            history.append(user_msg)
            history.append({"role": "assistant", "content": add_box_token(raw)})
            if len(history) > config.HISTORY_TURNS * 2:
                history = history[-(config.HISTORY_TURNS * 2):]

            # 7. Check terminal conditions
            if exec_result["status"] == "done":
                result.status = "done"
                break
            if exec_result["status"] == "failed":
                result.status = "failed"
                break
            if exec_result["status"] == "error":
                print(exec_result["error"])
                result.status = "error"
                break

        return result


# ── helpers ──────────────────────────────────────────────────────────────────

def _build_messages(system_prompt: str, history: list[dict], user_msg: dict) -> list[dict]:
    """Assemble the full message list for one API call."""
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append(user_msg)
    return messages
