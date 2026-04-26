"""
API connectivity test + coordinate format probe.

Sends a screenshot to Doubao with two prompts:
  1. A plain text prompt  → confirms API works
  2. A GUI action prompt  → reveals what coordinate format Doubao outputs

Run from Lark-Agent/:
    python tests/test_api.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config  # noqa: must be first to set up sys.path for ui_tars
from llm.doubao_client import chat, build_user_message
from agent.screenshot import capture, screen_size


PROBE_PROMPT = """\
你是一个GUI操作助手。请仔细观察截图，然后按照以下格式回答：

## 输出格式
Thought: [用中文描述你看到的界面内容，说明下一步要点击什么]
Action: click(start_box='(x,y)')

## 任务
点击截图中最显眼的一个可点击元素（按钮、图标或链接均可）。
坐标 x 和 y 是该元素在图片中的位置。

请直接输出 Thought 和 Action，不要有其他说明。
"""


def test_text_only():
    """Test 1: plain text, no image — confirms API key and endpoint work."""
    print("=" * 60)
    print("TEST 1: Text-only call")
    print("=" * 60)
    messages = [
        {"role": "user", "content": "你好，请用一句话介绍你自己。"},
    ]
    try:
        reply = chat(messages, max_tokens=100)
        print(f"Response: {reply}")
        print("[OK] API connection OK\n")
        return True
    except Exception as e:
        print(f"[FAIL] API call failed: {e}\n")
        return False


def test_coordinate_format():
    """
    Test 2: send a screenshot + GUI action prompt.
    Goal: inspect what coordinate format Doubao outputs so we can
    set the correct model_type in action_parser (doubao vs qwen25vl).
    """
    print("=" * 60)
    print("TEST 2: Screenshot + coordinate format probe")
    print("=" * 60)

    shot_path = capture()
    w, h = screen_size()
    print(f"Screenshot saved: {shot_path}")
    print(f"Screen size: {w} x {h}")

    messages = [build_user_message(PROBE_PROMPT, shot_path)]

    try:
        reply = chat(messages, max_tokens=200)
        print(f"\nRaw response:\n{reply}")
        print("\n" + "-" * 40)
        _analyze_coordinates(reply, w, h)
        return True
    except Exception as e:
        print(f"✗ Vision call failed: {e}\n")
        return False


def _analyze_coordinates(raw: str, screen_w: int, screen_h: int):
    """
    Parse coordinates from the raw response and determine format.
    Prints a diagnosis so we know which model_type to use.
    """
    import re

    # Match start_box='(x,y)' or start_box='(x1,y1,x2,y2)'
    pattern = r"start_box='?\(([0-9.,\s]+)\)'?"
    # Also try point='(x y)' or point='(x,y)'
    point_pattern = r"(?:point|start_box)=['\"]?[\(<]?([0-9]+)[,\s]+([0-9]+)[\)>]?['\"]?"

    found = re.findall(pattern, raw)
    if not found:
        found_pts = re.findall(point_pattern, raw)
        if found_pts:
            x_str, y_str = found_pts[0]
            found = [f"{x_str},{y_str}"]

    if not found:
        print("Could not find coordinate in response.")
        print("Full response printed above — check manually.")
        return

    coord_str = found[0]
    nums = [float(n.strip()) for n in coord_str.split(",") if n.strip()]
    x, y = nums[0], nums[1]

    print(f"Extracted coordinates: x={x}, y={y}")
    print()

    # Diagnosis
    if x <= 1.0 and y <= 1.0:
        print("FORMAT: Normalized [0,1] — model already outputs fractions.")
        print("→ No scaling needed; multiply by screen size for pixel coords.")
    elif x <= screen_w and y <= screen_h and (x > 1 or y > 1) and max(x, y) < 2000:
        # Could be absolute pixels or 0-1000 relative
        if max(x, y) <= 1000:
            pct_x = x / 10
            pct_y = y / 10
            pixel_x = round(x / 1000 * screen_w)
            pixel_y = round(y / 1000 * screen_h)
            print(f"FORMAT: Likely 0-1000 relative (doubao style)")
            print(f"→ model_type='doubao', factor=1000")
            print(f"→ Pixel coord would be: ({pixel_x}, {pixel_y})")
            print(f"   ({pct_x:.1f}% of width, {pct_y:.1f}% of height)")
        else:
            # Might be absolute pixel coordinates (qwen25vl style)
            print(f"FORMAT: Likely absolute pixels (qwen25vl style)")
            print(f"→ model_type='qwen25vl'")
            print(f"→ Coordinate ({x}, {y}) on {screen_w}x{screen_h} screen")
    else:
        print(f"FORMAT: Unknown — coordinate ({x}, {y}) vs screen ({screen_w}x{screen_h})")
        print("→ Check manually whether this makes visual sense.")


if __name__ == "__main__":
    ok1 = test_text_only()
    if ok1:
        test_coordinate_format()
    else:
        print("Skipping vision test due to API failure.")
        sys.exit(1)
