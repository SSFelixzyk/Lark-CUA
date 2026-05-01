"""
Feishu open-platform API client.

Covers: tenant_access_token, DocX document creation, Drive image upload,
and DocX block appending. Intended for the report publisher.
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_BASE = "https://open.feishu.cn/open-apis"
_BATCH = 50  # max blocks per PATCH request

# ── Auth ──────────────────────────────────────────────────────────────────────

_token: str = ""
_token_exp: float = 0.0


def _get_token() -> str:
    global _token, _token_exp
    if _token and time.time() < _token_exp - 60:
        return _token
    r = requests.post(
        f"{_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": config.FEISHU_APP_ID, "app_secret": config.FEISHU_APP_SECRET},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Feishu token error: {data}")
    _token = data["tenant_access_token"]
    _token_exp = time.time() + data.get("expire", 7200)
    return _token


def _h(json_body: bool = False) -> dict:
    h = {"Authorization": f"Bearer {_get_token()}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


# ── Document ──────────────────────────────────────────────────────────────────

def create_document(title: str, folder_token: str) -> tuple[str, str, str]:
    """Create a Feishu Doc. Returns (document_id, root_block_id, web_url)."""
    r = requests.post(
        f"{_BASE}/docx/v1/documents",
        headers=_h(json_body=True),
        json={"folder_token": folder_token, "title": title},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"create_document: {data}")
    doc_id = data["data"]["document"]["document_id"]
    root_id = _get_root_block(doc_id)
    url = f"{config.FEISHU_HOST}/docx/{doc_id}"
    return doc_id, root_id, url


def _get_root_block(document_id: str) -> str:
    """Fetch the page (root) block_id of a document body."""
    r = requests.get(
        f"{_BASE}/docx/v1/documents/{document_id}/blocks",
        headers=_h(),
        params={"document_revision_id": -1, "page_size": 5},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"get_root_block: {data}")
    items = data["data"].get("items", [])
    for item in items:
        if item.get("block_type") == 1:  # Page block
            return item["block_id"]
    if items:
        return items[0]["block_id"]
    return document_id  # last-resort fallback


# ── Images ────────────────────────────────────────────────────────────────────

def upload_image(image_path: Path, document_id: str) -> str:
    """Upload a screenshot to Feishu Drive as a docx_image. Returns file_token."""
    image_path = Path(image_path)
    data_bytes = image_path.read_bytes()
    r = requests.post(
        f"{_BASE}/drive/v1/medias/upload_all",
        headers=_h(),
        data={
            "file_name": image_path.name,
            "parent_type": "docx_image",
            "parent_node": document_id,
            "size": str(len(data_bytes)),
        },
        files={"file": (image_path.name, data_bytes, "image/png")},
        timeout=30,
    )
    r.raise_for_status()
    result = r.json()
    if result.get("code") != 0:
        raise RuntimeError(f"upload_image: {result}")
    return result["data"]["file_token"]


# ── Blocks ────────────────────────────────────────────────────────────────────

def append_blocks(document_id: str, parent_block_id: str, blocks: list[dict]) -> None:
    """Append blocks to parent block, auto-batching at _BATCH per request."""
    for i in range(0, len(blocks), _BATCH):
        batch = blocks[i: i + _BATCH]
        r = requests.patch(
            f"{_BASE}/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children",
            headers=_h(json_body=True),
            json={"children": batch},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"append_blocks: {data}")


# ── Block builders ────────────────────────────────────────────────────────────

def _run(content: str, bold: bool = False, code: bool = False) -> dict:
    style: dict = {}
    if bold:
        style["bold"] = True
    if code:
        style["inline_code"] = True
    elem = {"content": content}
    if style:
        elem["text_element_style"] = style
    return {"text_run": elem}


def text_block(content: str, bold: bool = False) -> dict:
    return {"block_type": 2, "text": {
        "elements": [_run(content, bold=bold)],
        "style": {},
    }}


def heading_block(content: str, level: int = 1) -> dict:
    level = min(max(level, 1), 9)
    key = f"heading{level}"
    return {"block_type": 2 + level, key: {
        "elements": [_run(content)],
        "style": {},
    }}


def bullet_block(content: str) -> dict:
    return {"block_type": 12, "bullet": {
        "elements": [_run(content)],
        "style": {},
    }}


def ordered_block(content: str) -> dict:
    return {"block_type": 13, "ordered": {
        "elements": [_run(content)],
        "style": {},
    }}


def divider_block() -> dict:
    return {"block_type": 22}


def image_block(file_token: str, width: int = 760, height: int = 430) -> dict:
    return {"block_type": 27, "image": {
        "token": file_token,
        "align": 1,
        "width": width,
        "height": height,
    }}
