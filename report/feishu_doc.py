"""
Publish benchmark report as a real Feishu cloud document (飞书云文档).

Workflow:
  1. docs +create   — create cloud doc from the pre-rendered MD
  2. docs +media-insert (per step) — insert each step's screenshot immediately
                                      after its anchor line [CASE_ID-NN]
  3. docs +update --mode replace_range  — replace the AI placeholder with
                                          real AI insights from insight_agent

Uses user identity (lark-cli default) so the user's own folder permissions apply.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from report.md import step_anchor

_SHELL = True   # required on Windows for npm-installed CLIs


# ── lark-cli helpers ──────────────────────────────────────────────────────────

def _run(cmd: str, cwd: str | None = None, timeout: int = 45) -> dict:
    """Run a lark-cli command and return parsed JSON output."""
    r = subprocess.run(
        cmd, shell=_SHELL, capture_output=True, cwd=cwd,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    raw = r.stdout.strip()
    if r.returncode != 0:
        raise RuntimeError(f"lark-cli failed (exit {r.returncode}):\n{r.stderr or raw}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Unexpected lark-cli output:\n{raw}")


def _create_doc(md_path: Path, title: str, folder_token: str) -> tuple[str, str]:
    """Create a Feishu cloud doc from a local MD file. Returns (doc_id, doc_url)."""
    cmd = (
        f'lark-cli docs +create'
        f' --title "{title}"'
        f' --markdown "@{md_path.name}"'
        f' --folder-token "{folder_token}"'
    )
    print(f"[feishu] 创建云文档: {title}")
    data = _run(cmd, cwd=str(md_path.parent))
    if not data.get("ok"):
        raise RuntimeError(f"docs +create error: {data}")
    return data["data"]["doc_id"], data["data"]["doc_url"]


def _insert_image(doc_url: str, image_path: Path, anchor: str, caption: str = "") -> None:
    """Insert an image after the block whose text contains `anchor`."""
    cap_flag = f' --caption "{caption}"' if caption else ""
    cmd = (
        f'lark-cli docs +media-insert'
        f' --doc "{doc_url}"'
        f' --file "{image_path.name}"'
        f' --selection-with-ellipsis "{anchor}"'
        f' --align center'
        f'{cap_flag}'
    )
    data = _run(cmd, cwd=str(image_path.parent), timeout=60)
    if not data.get("ok"):
        raise RuntimeError(f"media-insert error: {data}")


def _replace_placeholder(doc_url: str, placeholder: str, content_md: str) -> None:
    """Replace a placeholder line in the doc with new markdown content."""
    cmd = (
        f'lark-cli docs +update'
        f' --doc "{doc_url}"'
        f' --mode replace_range'
        f' --selection-with-ellipsis "{placeholder}"'
        f' --markdown -'
    )
    r = subprocess.run(
        cmd, shell=_SHELL, capture_output=True, cwd=None,
        input=content_md, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    raw = r.stdout.strip()
    if r.returncode != 0:
        raise RuntimeError(f"docs +update failed:\n{r.stderr or raw}")
    try:
        data = json.loads(raw)
        if not data.get("ok"):
            raise RuntimeError(f"docs +update error: {data}")
    except json.JSONDecodeError:
        raise RuntimeError(f"Unexpected +update output:\n{raw}")


def _get_step_screenshot(case: dict, step_num: int, screenshot_base: Path) -> Path | None:
    """Find the screenshot file for a specific step of a case."""
    case_id  = case["id"]
    matching = [d for d in screenshot_base.iterdir()
                if d.is_dir() and d.name.startswith(case_id + "_")]
    if not matching:
        return None
    case_dir = matching[0]
    # Match step{N:02d}_*.png (not _pending)
    candidates = [
        p for p in case_dir.glob(f"step{step_num:02d}_*.png")
        if not p.stem.endswith("_pending")
    ]
    return candidates[0] if candidates else None


# ── Public API ────────────────────────────────────────────────────────────────

def publish(
    result_json: dict,
    md_path: Path,
    folder_token: str | None = None,
    screenshot_base: Path | None = None,
    insights: str | None = None,
) -> str:
    """
    Create a Feishu cloud document from the benchmark result.

    Args:
        result_json:     Parsed benchmark result JSON.
        md_path:         Path to the already-rendered MD report file.
        folder_token:    Target Drive folder token.
        screenshot_base: Parent dir containing per-run screenshot subdirectories.
        insights:        Pre-generated AI insights markdown (from insight_agent).

    Returns:
        Web URL of the created Feishu cloud document.
    """
    folder_token = folder_token or config.FEISHU_REPORT_FOLDER
    ts    = result_json.get("timestamp", md_path.stem.replace("benchmark_", ""))
    title = f"飞书GUI测试报告 {ts}"

    # ── Step 1: Create doc from MD ────────────────────────────────────────────
    doc_id, doc_url = _create_doc(md_path, title, folder_token)
    print(f"[feishu] 文档已创建: {doc_url}")

    # ── Step 2: Insert screenshots per step ───────────────────────────────────
    if screenshot_base and screenshot_base.exists():
        for case in result_json.get("cases", []):
            if case.get("status") == "skipped":
                continue
            steps = case.get("steps", [])
            if not steps:
                continue
            print(f"[feishu]   插入截图 {case['id']} ({len(steps)} 步)...")
            for s in steps:
                shot = _get_step_screenshot(case, s["step"], screenshot_base)
                if not shot:
                    continue
                anchor  = step_anchor(case["id"], s["step"])
                caption = f"{s['action_type']} · 步骤{s['step']:02d}"
                try:
                    _insert_image(doc_url, shot, anchor, caption=caption)
                except Exception as e:
                    print(f"[feishu]     跳过 {shot.name}: {e}")
    elif screenshot_base:
        print(f"[feishu] WARNING: 截图目录不存在 ({screenshot_base})，跳过图片插入")

    # ── Step 3: Replace AI placeholder with real insights ─────────────────────
    if insights:
        for case in result_json.get("cases", []):
            if case.get("status") == "skipped":
                continue
            placeholder = f"待生成 · {case['id']}"
            print(f"[feishu]   写入 AI 分析 {case['id']}...")
            try:
                _replace_placeholder(doc_url, placeholder, insights)
            except Exception as e:
                print(f"[feishu]     AI 分析写入失败: {e}")

    print(f"[feishu] 完成 → {doc_url}")
    return doc_url
