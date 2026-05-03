"""
文档驱动测试用例生成器。

通过 lark-cli 读取飞书云文档，提取可测功能点，
自动调用 generate_loop() 生成结构化 DSL 测试用例。

Usage:
    python tools/doc_case_generator.py --doc "https://xxx.feishu.cn/docx/..." --product im
    python tools/doc_case_generator.py --doc "https://xxx.feishu.cn/docx/..." --product docs --levels L1,L2 --max-cases 5
    python tools/doc_case_generator.py --doc "https://xxx.feishu.cn/docx/..." --product im --no-evaluate
"""

import sys
import re
import json
import subprocess
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config  # noqa
from llm.doubao_client import chat
from dsl.generator import generate_loop, generate


# ── lark-cli helper ───────────────────────────────────────────────────────────

def _run_lark_cli(*args: str, timeout: int = 30) -> tuple[bool, dict | list | None, str]:
    """Run a lark-cli command, return (success, parsed_json, raw_output)."""
    cmd = "lark-cli " + " ".join(args)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=True, encoding="utf-8", errors="replace",
        )
        raw = r.stdout.strip() or r.stderr.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        return r.returncode == 0, parsed, raw
    except subprocess.TimeoutExpired:
        return False, None, "timeout"
    except Exception as e:
        return False, None, str(e)


# ── Document fetching ─────────────────────────────────────────────────────────

def fetch_doc_markdown(doc: str) -> str:
    """
    Fetch a Feishu cloud document as Markdown via lark-cli.

    Args:
        doc: document URL (https://xxx.feishu.cn/docx/...) or token string
    Returns:
        Markdown content string
    Raises:
        RuntimeError: if lark-cli call fails
    """
    ok, data, raw = _run_lark_cli(
        f'docs +fetch --api-version v2 --doc "{doc}" --doc-format markdown --format json'
    )
    if not ok:
        err = raw
        if isinstance(data, dict):
            err_obj = data.get("error", {})
            if isinstance(err_obj, dict):
                err = err_obj.get("message", raw)
        raise RuntimeError(f"lark-cli docs +fetch failed: {err[:300]}")

    if isinstance(data, dict):
        content = (
            data.get("data", {}).get("document", {}).get("content")
            or data.get("content")
            or raw
        )
        return content
    return raw


# ── Feature extraction ────────────────────────────────────────────────────────

_FEATURE_EXTRACTION_SYSTEM = """\
你是飞书GUI自动化测试专家。阅读以下飞书产品功能文档片段，提取其中用户可以在桌面客户端 GUI 上执行的操作功能点。

## 提取原则
- 只提取可通过鼠标/键盘在界面上实际操作并验证结果的功能
- 每个功能点对应一个独立的、可单独测试的操作
- 跳过：纯概念说明、权限/管理员配置、API 文档、不可见的后端行为

## 难度定义
L1：1~2 步即可完成（如点击按钮、切换开关）
L2：3~10 步，单产品内的完整流程（如创建内容、搜索并操作）
L3：跨产品联动（如 IM 收到日历邀请后确认，暂不生成）

## description 写法
- 用"做什么"的角度，不描述"怎么做"
- 用「」标注操作对象或内容（如「测试消息」）
- 涉及联系人用 <<TEST_CONTACT>>，群聊用 <<TEST_GROUP>>

## 输出格式（严格 JSON 数组，不加其他文字）
[
  {
    "feature": "功能名（6字以内）",
    "description": "用一句话描述用户可以做什么",
    "level": "L1或L2",
    "tags": ["tag1", "tag2"]
  }
]

若文档片段不包含可测 GUI 操作，返回空数组 []。
"""


def _extract_features_from_text(content: str, product: str) -> list[dict]:
    """Call LLM to extract testable features from a document section."""
    messages = [
        {"role": "system", "content": _FEATURE_EXTRACTION_SYSTEM},
        {"role": "user", "content": f"飞书产品：{product}\n\n文档内容：\n{content}"},
    ]
    try:
        raw = chat(messages, max_tokens=1500)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\[.*\]", raw_clean, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"  [doc-gen] feature extraction error: {e}")
    return []


def _split_sections(markdown: str, max_chars: int = 6000) -> list[str]:
    """
    Split markdown into sections by ## headings.
    Adjacent sections are merged until max_chars is reached, so short sections
    don't get isolated (which hurts feature extraction context quality).
    6000 chars ≈ 3000 Chinese characters — comfortably within Doubao's context
    while keeping each chunk focused enough for accurate feature extraction.
    """
    parts = re.split(r"\n(?=## )", markdown.strip())
    sections: list[str] = []
    pending = ""

    for part in parts:
        part = part.strip()
        if not part:
            continue
        combined = (pending + "\n\n" + part).strip() if pending else part
        if len(combined) <= max_chars:
            pending = combined
        else:
            if pending:
                sections.append(pending[:max_chars])
            pending = part[:max_chars]

    if pending:
        sections.append(pending)

    return sections or [markdown[:max_chars]]


def extract_all_features(markdown: str, product: str) -> list[dict]:
    """
    Split doc into sections, extract features from each, deduplicate by feature name.
    Returns a flat list of unique feature dicts.
    """
    sections = _split_sections(markdown)
    print(f"  [doc-gen] document split into {len(sections)} section(s)")

    all_features: list[dict] = []
    seen: set[str] = set()

    for i, section in enumerate(sections, 1):
        print(f"  [doc-gen] extracting from section {i}/{len(sections)} "
              f"({len(section)} chars)...")
        features = _extract_features_from_text(section, product)
        # print("从文档解析出的功能: " + str(features))
        added = 0
        for feat in features:
            key = feat.get("feature", "").strip()
            if key and key not in seen:
                seen.add(key)
                all_features.append(feat)
                added += 1
        print(f"    -> {len(features)} found, {added} new")

    return all_features


# ── Main pipeline ─────────────────────────────────────────────────────────────

def generate_from_doc(
    doc: str,
    product: str,
    levels: list[str] | None = None,
    max_cases: int = 10,
    evaluate: bool = True,
    max_retries: int = 3,
) -> list[Path]:
    """
    Full pipeline: fetch doc → extract features → generate DSL test cases.

    Args:
        doc:         Feishu document URL or token
        product:     Target product (im/docs/calendar/base/vc/mail/gui)
        levels:      Level filter, default ["L1", "L2"]. Pass ["L1","L2","L3"] for all.
        max_cases:   Max number of test cases to generate
        evaluate:    If True, run generate→evaluate→refine loop (higher quality, slower)
        max_retries: Max retries in the evaluate loop

    Returns:
        List of Paths to saved YAML files
    """
    if levels is None:
        levels = ["L1", "L2"]

    # 1. Fetch document as Markdown
    print(f"[doc-gen] fetching: {doc}")
    markdown = fetch_doc_markdown(doc)
    print(f"[doc-gen] fetched {len(markdown)} chars")

    # 2. Extract features
    features = extract_all_features(markdown, product)
    print(f"[doc-gen] {len(features)} unique feature(s) extracted")

    # 3. Filter by level
    targets = [f for f in features if f.get("level") in levels]
    if not targets:
        print(f"[doc-gen] no features matched levels={levels}, using all {len(features)}")
        targets = features
    targets = targets[:max_cases]
    print(f"[doc-gen] generating {len(targets)} case(s) "
          f"(levels={levels}, max={max_cases}, evaluate={evaluate})\n")

    # 4. Generate test cases one by one
    saved: list[Path] = []
    for i, feat in enumerate(targets, 1):
        desc = feat.get("description", "")
        level = feat.get("level", "?")
        name = feat.get("feature", "?")
        print(f"── ({i}/{len(targets)}) [{level}] {name}")
        print(f"   {desc}")
        try:
            if evaluate:
                _, path = generate_loop(desc, product, max_retries=max_retries)
            else:
                _, path = generate(desc, product)
            saved.append(path)
            print(f"   -> saved: {path.name}\n")
        except Exception as e:
            print(f"   -> FAILED: {e}\n")

    print(f"[doc-gen] complete: {len(saved)}/{len(targets)} saved.")
    return saved


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate Feishu DSL test cases from a cloud document"
    )
    parser.add_argument(
        "--doc", required=True,
        help="Feishu document URL (https://xxx.feishu.cn/docx/...) or token",
    )
    parser.add_argument(
        "--product", required=True,
        choices=["im", "docs", "calendar", "base", "vc", "mail", "gui"],
        help="Target Feishu product",
    )
    parser.add_argument(
        "--levels", default="L1,L2",
        help="Comma-separated difficulty levels to generate (default: L1,L2)",
    )
    parser.add_argument(
        "--max-cases", type=int, default=10,
        help="Max test cases to generate (default: 10)",
    )
    parser.add_argument(
        "--no-evaluate", action="store_true",
        help="Skip generate→evaluate loop — faster but lower quality",
    )
    parser.add_argument(
        "--max-retries", type=int, default=3,
        help="Max refinement retries in evaluate loop (default: 3)",
    )
    args = parser.parse_args()

    levels = [lv.strip() for lv in args.levels.split(",")]

    saved = generate_from_doc(
        doc=args.doc,
        product=args.product,
        levels=levels,
        max_cases=args.max_cases,
        evaluate=not args.no_evaluate,
        max_retries=args.max_retries,
    )

    print(f"\n{'=' * 60}")
    print(f"Generated {len(saved)} test case(s):")
    for p in saved:
        print(f"  {p}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

