"""
Post-execution state verifier for Lark-CUA.

Two verification layers:
  1. CLI  — lark-cli subprocess calls for structured ground-truth checks
  2. VLM  — Doubao screenshot assertion for visual / unstructured checks

Usage (from code):
    from agent.verifier import verify
    vr = verify(case, final_screenshot_path, screenshot_dir=case_screenshot_dir)

Usage (CLI):
    python agent/verifier.py tests/benchmark/generated/im/IM_GEN_001.yaml \
        --screenshot screenshots/run_xxx/IM_GEN_001/step05_finished.png \
        --screenshot-dir screenshots/run_xxx/IM_GEN_001/
"""

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from llm.doubao_client import chat, build_user_message


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    checkpoint: str
    method: Literal["cli", "vlm", "skip"]
    passed: bool | None        # None = inconclusive
    reason: str
    raw: str = ""              # raw CLI output or VLM response snippet


@dataclass
class VerificationResult:
    case_id: str
    overall: Literal["pass", "fail", "inconclusive"]
    checks: list[CheckResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Verification [{self.overall.upper()}]  case={self.case_id}"]
        for c in self.checks:
            mark = "OK" if c.passed else ("NG" if c.passed is False else "??")
            lines.append(f"  {mark} [{c.method}] {c.checkpoint[:60]}  — {c.reason}")
        return "\n".join(lines)


# ── lark-cli helpers ──────────────────────────────────────────────────────────

import platform
_SHELL = platform.system() == "Windows"  # shell=True required on Windows for npm-installed CLIs


def _cli_available() -> bool:
    try:
        r = subprocess.run("lark-cli --version", capture_output=True, timeout=5,
                           shell=True, text=True)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _run_cli(*args: str, timeout: int = 15) -> tuple[bool, dict | list | None, str]:
    """Run lark-cli and return (success, parsed_json, raw_stdout)."""
    cmd = "lark-cli " + " ".join(args) + " --format json"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=True, encoding="utf-8", errors="replace")
        # lark-cli writes JSON errors to stdout with non-zero exit code
        raw = r.stdout.strip() or r.stderr.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if r.returncode != 0:
            return False, parsed, raw
        return True, parsed, raw
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception as e:
        return False, None, "lark-cli not found"


# ── CLI verification strategies ───────────────────────────────────────────────

def _cli_im_message(
    expected_text: str, chat_name: str = "", since: datetime | None = None
) -> CheckResult:
    """Search for a sent message by keyword, filtered to messages after `since`."""
    checkpoint = f"消息「{expected_text}」已发送"
    since_ts = since.timestamp() if since else None
    last_raw = ""
    for attempt in range(3):
        ok, data, raw = _run_cli(f'im +messages-search --query "{expected_text}"')
        last_raw = raw
        if not ok:
            err = (data or {}).get("error", {}).get("message", raw) if isinstance(data, dict) else raw
            return CheckResult(checkpoint, "cli", None, f"CLI 调用失败: {err}", raw)
        items = (data or {}).get("items", []) if isinstance(data, dict) else (data or [])
        if since_ts and items:
            # Feishu message create_time is a Unix timestamp string (seconds)
            items = [
                item for item in items
                if isinstance(item, dict)
                and float(item.get("create_time", 0)) >= since_ts
            ]
        if items:
            label = f"找到 {len(items)} 条匹配消息" + (f"（{since.strftime('%H:%M:%S')} 之后）" if since else "")
            return CheckResult(checkpoint, "cli", True, label, raw[:200])
        if attempt < 2:
            time.sleep(4)
    msg = "未找到测试开始后包含该关键词的消息" if since_ts else "未找到包含该关键词的消息"
    return CheckResult(checkpoint, "cli", False, msg, last_raw[:200])


def _cli_calendar_event(title: str) -> CheckResult:
    """Check calendar agenda for an event with the given title (uses +agenda, no extra scope)."""
    from datetime import datetime, timedelta, timezone
    checkpoint = f"日程「{title}」已创建"
    # Search today + next 7 days in ISO 8601 with local offset
    tz_offset = datetime.now(timezone.utc).astimezone().strftime("%z")
    tz_offset = tz_offset[:3] + ":" + tz_offset[3:]  # +0800 → +08:00
    start = datetime.now().strftime(f"%Y-%m-%dT00:00:00{tz_offset}")
    end   = (datetime.now() + timedelta(days=7)).strftime(f"%Y-%m-%dT23:59:59{tz_offset}")
    ok, data, raw = _run_cli(f'calendar +agenda --start "{start}" --end "{end}"')
    if not ok:
        err = (data or {}).get("error", {}).get("message", raw) if isinstance(data, dict) else raw
        return CheckResult(checkpoint, "cli", None, f"CLI 调用失败: {err}", raw)

    # data may be a list of event objects or wrapped in a key
    items = data if isinstance(data, list) else (data or {}).get("items", data or [])
    matched = [i for i in (items or [])
               if title in (i.get("summary", "") if isinstance(i, dict) else str(i))]
    if matched:
        return CheckResult(checkpoint, "cli", True, f"找到日程: {matched[0]}", raw[:200])
    # If raw text contains the title it's also a match (pretty format fallback)
    if title in raw:
        return CheckResult(checkpoint, "cli", True, "日程标题出现在议程输出中", raw[:200])
    return CheckResult(checkpoint, "cli", False, "近7天议程中未找到该日程", raw[:200])


def _cli_drive_doc(doc_name: str) -> CheckResult:
    """Search for a cloud document by name."""
    checkpoint = f"云文档「{doc_name}」已创建"
    ok, data, raw = _run_cli(f'drive +search --query "{doc_name}" --doc-types doc,docx --only-title')
    if not ok:
        err = (data or {}).get("error", {}).get("message", raw) if isinstance(data, dict) else raw
        return CheckResult(checkpoint, "cli", None, f"CLI 调用失败: {err}", raw)

    items = (data or {}).get("items", []) if isinstance(data, dict) else (data or [])
    if items:
        return CheckResult(checkpoint, "cli", True, f"找到文档: {items[0]}", raw[:200])
    return CheckResult(checkpoint, "cli", False, "未找到该文档", raw[:200])


def _cli_im_chat(chat_name: str) -> CheckResult:
    """Check if a group chat with the given name exists."""
    checkpoint = f"群聊「{chat_name}」已创建"
    ok, data, raw = _run_cli(f'im +chat-search --query "{chat_name}"')
    if not ok:
        err = (data or {}).get("error", {}).get("message", raw) if isinstance(data, dict) else raw
        return CheckResult(checkpoint, "cli", None, f"CLI 调用失败: {err}", raw)

    items = (data or {}).get("items", []) if isinstance(data, dict) else (data or [])
    matched = [i for i in items
               if chat_name in (i.get("name", "") if isinstance(i, dict) else str(i))]
    if matched:
        return CheckResult(checkpoint, "cli", True, f"找到群聊: {matched[0]}", raw[:200])
    return CheckResult(checkpoint, "cli", False, "未找到该群聊", raw[:200])


# ── VLM verification ──────────────────────────────────────────────────────────

_VLM_SYSTEM = """\
你是飞书GUI自动化测试的验证专家。根据提供的屏幕截图，判断指定的测试检查点是否满足。

## 输出格式（严格JSON，无其他内容）
{"passed": true/false, "reason": "<一句话说明>"}

## 注意
- 只看截图，不做假设
- 如果截图信息不足以判断，returned passed=false 并说明原因
- reason 要具体，引用截图中的可见内容
"""


def _vlm_check(checkpoint: str, screenshot_path: Path) -> CheckResult:
    """Use Doubao to assess a checkpoint against the final screenshot."""
    user_msg = build_user_message(
        f"请判断以下检查点是否满足：\n{checkpoint}",
        screenshot_path,
    )
    messages = [
        {"role": "system", "content": _VLM_SYSTEM},
        user_msg,
    ]
    try:
        raw = chat(messages, max_tokens=200)
        raw_clean = re.sub(r"^```json\s*", "", raw.strip(), flags=re.IGNORECASE)
        raw_clean = re.sub(r"\s*```\s*$", "", raw_clean)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            obj = json.loads(m.group())
            return CheckResult(checkpoint, "vlm", bool(obj.get("passed")),
                               obj.get("reason", ""), raw[:200])
        return CheckResult(checkpoint, "vlm", None, f"无法解析VLM响应: {raw[:80]}", raw[:200])
    except Exception as e:
        return CheckResult(checkpoint, "vlm", None, f"VLM调用异常: {e}", "")


# ── Routing ───────────────────────────────────────────────────────────────────

def _route_cli_verification(
    v: dict, since: datetime | None = None
) -> CheckResult | None:
    """
    Route a structured cli_verification entry to the appropriate CLI check.

    Supported types:
        im_message   — {"type": "im_message", "expected_text": "..."}
        calendar     — {"type": "calendar_event", "title": "..."}
        drive_doc    — {"type": "drive_doc", "name": "..."}
        im_chat      — {"type": "im_chat", "name": "..."}
    """
    t = v.get("type", "")
    if t == "im_message":
        return _cli_im_message(v.get("expected_text", ""), v.get("chat_name", ""), since=since)
    if t == "calendar_event":
        return _cli_calendar_event(v.get("title", ""))
    if t == "drive_doc":
        return _cli_drive_doc(v.get("name", ""))
    if t == "im_chat":
        return _cli_im_chat(v.get("name", ""))
    return None


def _infer_cli_checks(case: dict) -> list[CheckResult]:
    """
    Heuristic: if no cli_verifications field, try to infer from success_criteria.
    Extracts quoted strings and matches against product-level CLI commands.
    """
    product = case.get("product", "")
    criteria = case.get("success_criteria", "")
    quoted = re.findall(r"「([^」]+)」", criteria)
    checks = []

    for text in quoted:
        if product == "im" and len(text) > 2:
            # Could be a message or group name — try message search
            checks.append(_cli_im_message(text))
            break
        if product == "calendar":
            checks.append(_cli_calendar_event(text))
            break
        if product in ("docs", "drive"):
            checks.append(_cli_drive_doc(text))
            break

    return checks


# ── Public API ────────────────────────────────────────────────────────────────

def _collect_step_shots(screenshot_dir: Path | None) -> list[Path]:
    """Return sorted list of step screenshots from the case run directory."""
    if not screenshot_dir or not screenshot_dir.exists():
        return []
    shots = sorted(
        [p for p in screenshot_dir.glob("step*_*.png") if not p.stem.endswith("_pending")],
        key=lambda p: p.name,
    )
    return shots


def verify(
    case: dict,
    screenshot_path: Path | None = None,
    screenshot_dir: Path | None = None,
    run_start: datetime | None = None,
    checkpoint_shots: dict[int, str] | None = None,
) -> VerificationResult:
    """
    Run all verifications for a completed case.

    Args:
        case:             The benchmark YAML case dict (must have 'id', 'checkpoints', etc.)
        screenshot_path:  Path to the final step screenshot (fallback for VLM checks)
        screenshot_dir:   Directory with all per-step screenshots (fallback if no
                          checkpoint_shots mapping)
        run_start:        Datetime when the case started; CLI checks filter results to
                          only objects created after this time, preventing false positives
                          from previous runs with identical content.
        checkpoint_shots: Mapping from checkpoint index → screenshot path, recorded by
                          the agent exactly when it declared each checkpoint reached.
                          When present, VLM verification uses these precise screenshots
                          instead of a linear index approximation.

    Returns:
        VerificationResult with per-checkpoint results and overall verdict
    """
    case_id = case.get("id", "unknown")
    checks: list[CheckResult] = []
    cli_ok = _cli_available()

    # Fallback ordered step screenshots (used only when checkpoint_shots is absent)
    step_shots = _collect_step_shots(screenshot_dir)

    # 1. Structured CLI verifications (from yaml field)
    for v in case.get("cli_verifications", []):
        if cli_ok:
            result = _route_cli_verification(v, since=run_start)
            if result:
                checks.append(result)
                continue
        # CLI unavailable or unknown type — fall back to VLM (use final screenshot)
        if screenshot_path:
            desc = v.get("expected_text") or v.get("title") or v.get("name") or str(v)
            checks.append(_vlm_check(f"验证: {desc}", screenshot_path))

    # 2. Heuristic CLI checks if no structured verifications
    if not case.get("cli_verifications") and cli_ok:
        checks.extend(_infer_cli_checks(case))

    # 3. VLM checks for each checkpoint — prefer exact agent-recorded screenshot
    for i, cp in enumerate(case.get("checkpoints", [])):
        if checkpoint_shots and i in checkpoint_shots:
            # Best: the screenshot taken exactly when the agent declared this checkpoint reached
            shot = Path(checkpoint_shots[i])
        elif i < len(step_shots):
            # Fallback: linear index into step screenshots (approximate)
            shot = step_shots[i]
        else:
            # Last resort: final screenshot
            shot = screenshot_path
        if shot:
            checks.append(_vlm_check(cp, shot))

    # 4. Compute overall verdict
    if not checks:
        overall = "inconclusive"
    elif any(c.passed is False for c in checks):
        overall = "fail"
    elif all(c.passed is True for c in checks):
        overall = "pass"
    else:
        overall = "inconclusive"

    return VerificationResult(case_id=case_id, overall=overall, checks=checks)


# ── CLI entry ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="Verify a completed benchmark case")
    parser.add_argument("yaml_path", help="Path to the case YAML file")
    parser.add_argument("--screenshot", help="Path to final screenshot (for VLM checks)")
    parser.add_argument("--screenshot-dir", help="Directory with all per-step screenshots")
    args = parser.parse_args()

    with open(args.yaml_path, encoding="utf-8") as f:
        case = yaml.safe_load(f)

    shot = Path(args.screenshot) if args.screenshot else None
    shot_dir = Path(args.screenshot_dir) if args.screenshot_dir else None
    vr = verify(case, shot, screenshot_dir=shot_dir)
    print(vr.summary())


if __name__ == "__main__":
    main()
