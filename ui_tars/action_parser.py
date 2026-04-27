# Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
# SPDX-License-Identifier: Apache-2.0
import re
import ast
import math

IMAGE_FACTOR = 28
MIN_PIXELS = 100 * 28 * 28
MAX_PIXELS = 16384 * 28 * 28
MAX_RATIO = 200

# UI-TARS / some VLMs emit this even when the user prompt asks for plain (x,y).
_BOX_COORD_IN_MARKERS = re.compile(
    r"<\|box_start\|\>\s*\(([^)]+)\)\s*<\|box_end\|\>",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_box_coordinate_string(value: str) -> str:
    """Turn start_box='<|box_start|>(722,257)<|box_end|>' into '(722,257)' for parsing."""
    if not value or "<|box_start|>" not in value.lower():
        return value
    m = _BOX_COORD_IN_MARKERS.search(value)
    if m:
        return f"({m.group(1).strip()})"
    return value


_BOX_NUMBER_TOKEN = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers_from_box_string(box_param: str) -> list[float]:
    """
    Extract ordered numbers from (661,346), (661 346), (661，346), or four-tuple box strings.
    Avoids split(',') producing a single token like '661 346' which cannot float().
    """
    s = _normalize_box_coordinate_string((box_param or "").strip())
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1].strip()
    if not inner:
        return []
    return [float(t) for t in _BOX_NUMBER_TOKEN.findall(inner)]


def convert_point_to_coordinates(text, is_answer=False):
    # 匹配 <bbox> 后面的四个数字
    pattern = r"<point>(\d+)\s+(\d+)</point>"

    def replace_match(match):
        x1, y1 = map(int, match.groups())
        x = (x1 + x1) // 2  # 使用截断取整
        y = (y1 + y1) // 2  # 使用截断取整
        if is_answer:
            return f"({x},{y})"  # 只返回 (x, y) 格式
        return f"({x},{y})"  # 返回带标签的格式

    # 去掉 [EOS] 并替换 <bbox> 坐标
    text = re.sub(r"\[EOS\]", "", text)
    return re.sub(pattern, replace_match, text).strip()


# 定义一个函数来解析每个 action
def parse_action(action_str):
    try:
        # 解析字符串为 AST 节点
        node = ast.parse(action_str, mode='eval')

        # 确保节点是一个表达式
        if not isinstance(node, ast.Expression):
            raise ValueError("Not an expression")

        # 获取表达式的主体
        call = node.body

        # 确保主体是一个函数调用
        if not isinstance(call, ast.Call):
            raise ValueError("Not a function call")

        # 获取函数名
        if isinstance(call.func, ast.Name):
            func_name = call.func.id
        elif isinstance(call.func, ast.Attribute):
            func_name = call.func.attr
        else:
            func_name = None

        # 获取关键字参数
        kwargs = {}
        for kw in call.keywords:
            key = kw.arg
            # 处理不同类型的值，这里假设都是常量
            if isinstance(kw.value, ast.Constant):
                value = kw.value.value
            elif isinstance(kw.value, ast.Str):  # 兼容旧版本 Python
                value = kw.value.s
            else:
                value = None
            kwargs[key] = value

        return {'function': func_name, 'args': kwargs}

    except Exception as e:
        print(f"Failed to parse action '{action_str}': {e}")
        return None


def escape_single_quotes(text):
    # 匹配未转义的单引号（不匹配 \\'）
    pattern = r"(?<!\\)'"
    return re.sub(pattern, r"\\'", text)


def round_by_factor(number: int, factor: int) -> int:
    """Returns the closest integer to 'number' that is divisible by 'factor'."""
    return round(number / factor) * factor


def ceil_by_factor(number: int, factor: int) -> int:
    """Returns the smallest integer greater than or equal to 'number' that is divisible by 'factor'."""
    return math.ceil(number / factor) * factor


def floor_by_factor(number: int, factor: int) -> int:
    """Returns the largest integer less than or equal to 'number' that is divisible by 'factor'."""
    return math.floor(number / factor) * factor


def linear_resize(height: int,
                  width: int,
                  factor: int = IMAGE_FACTOR,
                  min_pixels: int = MIN_PIXELS,
                  max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    if width * height > max_pixels:
        resize_factor = math.sqrt(max_pixels / (width * height))
        width, height = int(width * resize_factor), int(height * resize_factor)
    if width * height < min_pixels:
        resize_factor = math.sqrt(min_pixels / (width * height))
        width, height = math.ceil(width * resize_factor), math.ceil(
            height * resize_factor)

    return height, width


def smart_resize(height: int,
                 width: int,
                 factor: int = IMAGE_FACTOR,
                 min_pixels: int = MIN_PIXELS,
                 max_pixels: int = MAX_PIXELS) -> tuple[int, int]:
    """
    Rescales the image so that the following conditions are met:

    1. Both dimensions (height and width) are divisible by 'factor'.

    2. The total number of pixels is within the range ['min_pixels', 'max_pixels'].

    3. The aspect ratio of the image is maintained as closely as possible.
    """
    if max(height, width) / min(height, width) > MAX_RATIO:
        raise ValueError(
            f"absolute aspect ratio must be smaller than {MAX_RATIO}, got {max(height, width) / min(height, width)}"
        )
    h_bar = max(factor, round_by_factor(height, factor))
    w_bar = max(factor, round_by_factor(width, factor))
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = floor_by_factor(height / beta, factor)
        w_bar = floor_by_factor(width / beta, factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = ceil_by_factor(height * beta, factor)
        w_bar = ceil_by_factor(width * beta, factor)
    return h_bar, w_bar


def parse_action_to_structure_output(text,
                                     factor,
                                     origin_resized_height,
                                     origin_resized_width,
                                     model_type="qwen25vl",
                                     max_pixels=16384 * 28 * 28,
                                     min_pixels=100 * 28 * 28):
    text = text.strip()

    if "<point>" in text:
        text = convert_point_to_coordinates(text)
    if "start_point=" in text:
        text = text.replace("start_point=", "start_box=")
    if "end_point=" in text:
        text = text.replace("end_point=", "end_box=")
    if "point=" in text:
        text = text.replace("point=", "start_box=")


    if model_type == "qwen25vl":
        smart_resize_height, smart_resize_width = smart_resize(
            origin_resized_height,
            origin_resized_width,
            factor=IMAGE_FACTOR,
            min_pixels=min_pixels,
            max_pixels=max_pixels)

    # 正则表达式匹配 Action 字符串
    if text.startswith("Thought:"):
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    elif text.startswith("Reflection:"):
        thought_pattern = r"Reflection: (.+?)Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Reflection: "
    elif text.startswith("Action_Summary:"):
        thought_pattern = r"Action_Summary: (.+?)(?=\s*Action: |$)"
        thought_hint = "Action_Summary: "
    else:
        thought_pattern = r"Thought: (.+?)(?=\s*Action: |$)"
        thought_hint = "Thought: "
    reflection, thought = None, None
    thought_match = re.search(thought_pattern, text, re.DOTALL)
    if thought_match:
        if len(thought_match.groups()) == 1:
            thought = thought_match.group(1).strip()
        elif len(thought_match.groups()) == 2:
            thought = thought_match.group(2).strip()
            reflection = thought_match.group(1).strip()
    # Normalize full-width colon (U+FF1A) that Chinese LLMs sometimes emit
    text = re.sub(r"Action：", "Action:", text)
    if "Action:" not in text:
        raise ValueError(f"No 'Action:' found in model response: {text[:120]!r}")
    action_str = text.split("Action: ")[-1]

    tmp_all_action = action_str.split(")\n\n")
    all_action = []
    for action_str in tmp_all_action:
        if "type(content" in action_str:
            pattern = r"type\(content='(.*?)'\)"
            m = re.search(pattern, action_str)
            if m:
                content = m.group(1)
            else:
                raise ValueError("Pattern not found in the input string.")
            action_str = "type(content='" + escape_single_quotes(content) + "')"
        if not action_str.strip().endswith(")"):
            action_str = action_str.strip() + ")"
        all_action.append(action_str)

    parsed_actions = [
        parse_action(action.replace("\n", "\\n").lstrip())
        for action in all_action
    ]
    actions = []
    for action_instance, raw_str in zip(parsed_actions, all_action):
        if action_instance is None:
            print(f"Action can't parse: {raw_str}")
            raise ValueError(f"Action can't parse: {raw_str}")
        action_type = action_instance["function"]
        params = action_instance["args"]

        action_inputs = {}
        for param_name, param in params.items():
            if param == "": continue
            param = param.lstrip()
            action_inputs[param_name.strip()] = param

            if "start_box" in param_name or "end_box" in param_name:
                float_nums = _numbers_from_box_string(param)
                if len(float_nums) >= 4:
                    float_nums = float_nums[:4]
                elif len(float_nums) == 2:
                    pass
                else:
                    raise ValueError(
                        f"Expected 2 or 4 numbers in {param_name}, got {len(float_nums)}: {param!r}"
                    )

                if model_type == "qwen25vl":
                    float_numbers = []
                    for num_idx, num in enumerate(float_nums):
                        if (num_idx + 1) % 2 == 0:
                            float_numbers.append(
                                float(num / smart_resize_height))
                        else:
                            float_numbers.append(
                                float(num / smart_resize_width))
                else:
                    float_numbers = [num / factor for num in float_nums]

                if len(float_numbers) == 2:
                    float_numbers = [
                        float_numbers[0], float_numbers[1], float_numbers[0],
                        float_numbers[1]
                    ]
                action_inputs[param_name.strip()] = str(float_numbers)

        actions.append({
            "reflection": reflection,
            "thought": thought,
            "action_type": action_type,
            "action_inputs": action_inputs,
            "text": text
        })
    return actions


def parsing_response_to_pyautogui_code(responses,
                                       image_height: int,
                                       image_width: int,
                                       input_swap: bool = True) -> str:
    pyautogui_code = f"import pyautogui\nimport time\n"
    if isinstance(responses, dict):
        responses = [responses]
    for response_id, response in enumerate(responses):
        observation = response.get("observation", "")
        thought = response.get("thought", "")

        if response_id == 0:
            pyautogui_code += f"'''\nObservation:\n{observation}\n\nThought:\n{thought}\n'''\n"
        else:
            pyautogui_code += f"\ntime.sleep(1)\n"

        action_type = response.get("action_type")
        action_inputs = response.get("action_inputs", {})

        if action_type == "hotkey":
            hotkey = action_inputs.get("key", "") or action_inputs.get("hotkey", "")
            if hotkey == "arrowleft": hotkey = "left"
            elif hotkey == "arrowright": hotkey = "right"
            elif hotkey == "arrowup": hotkey = "up"
            elif hotkey == "arrowdown": hotkey = "down"
            if hotkey:
                keys = [' ' if k == "space" else k for k in hotkey.split()]
                pyautogui_code += f"\npyautogui.hotkey({', '.join([repr(k) for k in keys])})"

        elif action_type in ["press", "keydown"]:
            key = action_inputs.get("key", "") or action_inputs.get("press", "")
            if key == "arrowleft": key = "left"
            elif key == "arrowright": key = "right"
            elif key == "arrowup": key = "up"
            elif key == "arrowdown": key = "down"
            elif key == "space": key = " "
            if key:
                pyautogui_code += f"\npyautogui.keyDown({repr(key)})"

        elif action_type in ["release", "keyup"]:
            key = action_inputs.get("key", "") or action_inputs.get("press", "")
            if key == "arrowleft": key = "left"
            elif key == "arrowright": key = "right"
            elif key == "arrowup": key = "up"
            elif key == "arrowdown": key = "down"
            elif key == "space": key = " "
            if key:
                pyautogui_code += f"\npyautogui.keyUp({repr(key)})"

        elif action_type == "type":
            content = escape_single_quotes(action_inputs.get("content", ""))
            stripped = content.rstrip("\\n").rstrip("\n")
            if content:
                if input_swap:
                    pyautogui_code += f"\nimport pyperclip"
                    pyautogui_code += f"\npyperclip.copy('{stripped}')"
                    pyautogui_code += f"\npyautogui.hotkey('ctrl', 'v')"
                    pyautogui_code += f"\ntime.sleep(0.5)\n"
                    if content.endswith("\n") or content.endswith("\\n"):
                        pyautogui_code += f"\npyautogui.press('enter')"
                else:
                    pyautogui_code += f"\npyautogui.write('{stripped}', interval=0.1)"
                    pyautogui_code += f"\ntime.sleep(0.5)\n"
                    if content.endswith("\n") or content.endswith("\\n"):
                        pyautogui_code += f"\npyautogui.press('enter')"

        elif action_type in ["drag", "select"]:
            start_box = action_inputs.get("start_box")
            end_box = action_inputs.get("end_box")
            if start_box and end_box:
                x1, y1, x2, y2 = eval(start_box)
                sx = round(float((x1 + x2) / 2) * image_width, 3)
                sy = round(float((y1 + y2) / 2) * image_height, 3)
                x1, y1, x2, y2 = eval(end_box)
                ex = round(float((x1 + x2) / 2) * image_width, 3)
                ey = round(float((y1 + y2) / 2) * image_height, 3)
                pyautogui_code += (
                    f"\npyautogui.moveTo({sx}, {sy})\n"
                    f"\npyautogui.dragTo({ex}, {ey}, duration=1.0)\n")

        elif action_type == "scroll":
            start_box = action_inputs.get("start_box")
            x, y = None, None
            if start_box:
                x1, y1, x2, y2 = eval(start_box)
                x = round(float((x1 + x2) / 2) * image_width, 3)
                y = round(float((y1 + y2) / 2) * image_height, 3)
            direction = action_inputs.get("direction", "").lower()
            scroll_val = 5 if "up" in direction else -5
            if x is None:
                pyautogui_code += f"\npyautogui.scroll({scroll_val})"
            else:
                pyautogui_code += f"\npyautogui.scroll({scroll_val}, x={x}, y={y})"

        elif action_type in ["click", "left_single", "left_double", "right_single", "hover"]:
            start_box = action_inputs.get("start_box")
            if start_box:
                box = eval(str(start_box))
                if len(box) == 4:
                    x1, y1, x2, y2 = box
                else:
                    x1, y1 = box; x2, y2 = x1, y1
                x = round(float((x1 + x2) / 2) * image_width, 3)
                y = round(float((y1 + y2) / 2) * image_height, 3)
                if action_type in ("click", "left_single"):
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='left')"
                elif action_type == "left_double":
                    pyautogui_code += f"\npyautogui.doubleClick({x}, {y}, button='left')"
                elif action_type == "right_single":
                    pyautogui_code += f"\npyautogui.click({x}, {y}, button='right')"
                elif action_type == "hover":
                    pyautogui_code += f"\npyautogui.moveTo({x}, {y})"

        elif action_type == "finished":
            pyautogui_code = "DONE"

        else:
            pyautogui_code += f"\n# Unrecognized action type: {action_type}"

    return pyautogui_code


def add_box_token(input_string):
    if "Action: " in input_string and "start_box=" in input_string:
        suffix = input_string.split("Action: ")[0] + "Action: "
        actions = input_string.split("Action: ")[1:]
        processed_actions = []
        for action in actions:
            action = action.strip()
            coordinates = re.findall(
                r"(start_box|end_box)='\((\d+),\s*(\d+)\)'", action)
            updated_action = action
            for coord_type, x, y in coordinates:
                updated_action = updated_action.replace(
                    f"{coord_type}='({x},{y})'",
                    f"{coord_type}='<|box_start|>({x},{y})<|box_end|>'")
            processed_actions.append(updated_action)
        return suffix + "\n\n".join(processed_actions)
    return input_string
