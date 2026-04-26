# CUA-Lark 技术分析报告

> 基于 UI-TARS 源码研究 + CUA-Lark 课题要求，输出技术方案分析与建议路径

---

## 一、UI-TARS 技术深度解析

### 1.1 核心架构：端到端单模型

UI-TARS 的核心设计哲学是**把感知、推理、定位、动作全部压进一个 VLM**，不存在单独的感知模块、规划模块——模型直接从截图+历史轨迹输出 `Thought: ... \n Action: ...`。

```
[截图 + 历史对话] → VLM → Thought（推理） + Action（操作指令字符串）
                                                    ↓
                                          action_parser.py 后处理
                                                    ↓
                                          pyautogui 可执行代码
```

这与传统模块化框架（感知→规划→执行分开的 Pipeline）完全不同。优点是推理和操作天然一致；缺点是难以单独替换某一环节。

---

### 1.2 动作空间（COMPUTER_USE）

`prompt.py` 中定义的桌面动作空间（`COMPUTER_USE_DOUBAO`）：

| 动作 | 格式 | 说明 |
|------|------|------|
| `click` | `click(point='<point>x y</point>')` | 左键单击 |
| `left_double` | `left_double(point=...)` | 左键双击 |
| `right_single` | `right_single(point=...)` | 右键单击 |
| `drag` | `drag(start_point=..., end_point=...)` | 拖拽 |
| `hotkey` | `hotkey(key='ctrl c')` | 快捷键，用空格分隔 |
| `type` | `type(content='xxx')` | 文本输入，`\n` 结尾表示回车提交 |
| `scroll` | `scroll(point=..., direction='down')` | 滚动 |
| `wait` | `wait()` | 等待 5s 后截图 |
| `finished` | `finished(content='xxx')` | 任务完成 |

**提示词结构要点**：
- `Thought` 部分用中文或指定语言，写短计划 + 下一步动作的一句话总结
- `Action` 部分严格按照动作空间格式

---

### 1.3 坐标系统（最关键的工程细节）

这是 UI-TARS 最复杂的地方，不同 model_type 坐标处理逻辑完全不同。

#### doubao / 旧版模型（相对坐标）
```
模型输出坐标范围：0 ~ 1000
解析：float_numbers = [float(num) / factor for num in numbers]  # factor=1000
最终值：[0, 1] 的归一化坐标
实际像素：x_pixel = normalized_x * screen_width
```

#### qwen25vl（绝对坐标，需要 smart_resize）
```
模型在 smart_resize 后的画布上输出绝对像素坐标
smart_resize：将图像缩放到满足：
  1. 宽高均可被 28 整除
  2. 总像素在 [100×28²，16384×28²] 范围内
  3. 保持宽高比

解析：
  smart_h, smart_w = smart_resize(原图高, 原图宽)
  normalized_x = model_output_x / smart_w
  normalized_y = model_output_y / smart_h

实际像素：x_pixel = normalized_x * 原图宽
```

**代码位置**：`codes/ui_tars/action_parser.py:146-276`

坐标归一化到 [0,1] 后，最终乘以屏幕实际尺寸转为像素坐标执行。

---

### 1.4 多轮对话的历史处理

UI-TARS 通过传入完整的对话历史（截图+文字交织）来维持上下文。关键细节：

**`add_box_token()` 的作用**：将助手历史消息里的坐标重新包上 `<|box_start|>...<|box_end|>` 标记，才能被模型正确识别。回调模型时必须调用，否则历史轨迹无法被正确解析。

```python
# 发给模型前，历史 assistant 消息需处理
for message in history:
    if message["role"] == "assistant":
        message["content"] = add_box_token(message["content"])
```

**消息格式**（每轮）：
```
[system prompt + task]
[user: screenshot_t0]
[assistant: Thought+Action_t0]  ← add_box_token 处理
[user: screenshot_t1]
[assistant: Thought+Action_t1]  ← add_box_token 处理
...
[user: screenshot_current]  ← 当前截图，待模型推理
```

---

### 1.5 `parsing_response_to_pyautogui_code` 的执行细节

`type` 动作默认使用剪贴板粘贴（`input_swap=True`），原因：`pyautogui.write()` 对中文等非 ASCII 字符支持差，剪贴板方案绕过这个问题：

```python
# input_swap=True（默认）
pyperclip.copy('文本内容')
pyautogui.hotkey('ctrl', 'v')  # Windows/Linux
# 如果内容以 \n 结尾，再额外按 Enter
```

坐标转换：所有 start_box/end_box 均归一化为 [0,1]，执行时乘以 `image_width/image_height` 还原像素位置。

---

### 1.6 模型版本谱系

| 版本 | 发布时间 | 特点 |
|------|----------|------|
| UI-TARS-7B-DPO | 2025.01 | 初代，OSWorld 22.7% |
| UI-TARS-72B-DPO | 2025.01 | 大模型，OSWorld 24.6% |
| **UI-TARS-1.5-7B** | 2025.04 | RL 加强推理，OSWorld 27.5%，已开源 |
| UI-TARS-1.5（未公开大小） | 2025.04 | OSWorld 42.5%，暂未完全开源 |
| UI-TARS-2 | 2025.09 | All-in-One（GUI+Game+Code+Tool Use），最新 |

**实际可用**：`UI-TARS-1.5-7B`（HuggingFace 开源），是目前开源最强的 GUI agent 模型之一。

---

### 1.7 部署方式总结

| 方式 | 适用场景 | 接口 |
|------|----------|------|
| HuggingFace TGI（云端） | 快速验证，无需本地 GPU | OpenAI-compatible REST API |
| vLLM 本地 | 有 GPU（7B 需 ≥16GB VRAM） | OpenAI-compatible REST API |
| API 服务（GPT-4o / Claude） | 开发阶段快速迭代 | 各厂商 SDK |

---

## 二、CUA-Lark 课题要求分析

### 2.1 核心能力矩阵

课题要求 5 层能力，对应我们需要实现的模块：

```
┌─────────────────────────────────────────────────┐
│  5. 评估报告层  成功率/耗时/步骤数/操作轨迹           │
├─────────────────────────────────────────────────┤
│  4. 状态验证层  操作后截图语义判断是否符合预期          │
├─────────────────────────────────────────────────┤
│  3. 执行操作层  PyAutoGUI 鼠标/键盘/快捷键            │
├─────────────────────────────────────────────────┤
│  2. 规划决策层  VLM 解析自然语言指令 → 步骤序列        │
├─────────────────────────────────────────────────┤
│  1. 视觉感知层  屏幕截图 + VLM 识别界面状态            │
└─────────────────────────────────────────────────┘
```

### 2.2 飞书子产品覆盖要求

必须覆盖至少 **2 个子产品**，建议优先级：

| 优先级 | 子产品 | 测试场景复杂度 | 建议 |
|--------|--------|--------------|------|
| 高 | IM 即时通讯 | 中，操作清晰 | 第一个做 |
| 高 | 云文档 Docs | 中，富文本交互 | 第二个做 |
| 中 | 日历 Calendar | 低，流程标准 | 可选 |
| 低 | 多维表格 Base | 高，交互复杂 | 进阶 |
| 低 | 视频会议 VC | 高，实时性要求 | 进阶 |

### 2.3 里程碑路径（M1→M5）

```
M1 单步操作    → 截图+VLM+执行单步（5个单步操作）
M2 流程串联    → 多步+状态验证（1个子产品3条E2E流程）
M3 多产品覆盖  → IM/Calendar/Docs 各2+用例
M4 评估体系    → 自动统计成功率/耗时/步骤数
M5 进阶优化    → 异常处理/自愈/跨产品联动
```

---

## 三、我们的技术方案设计

### 3.1 总体架构

采用**单 VLM 驱动的 ReAct 循环**（与 UI-TARS 架构对齐），但在飞书场景下叠加可选的混合定位层：

```
┌──────────────────────────────────────────────────────────┐
│                    Agent 主循环                            │
│                                                          │
│  自然语言指令                                              │
│       ↓                                                  │
│  [Task Planner]  高层任务拆解（可选，复杂任务用）            │
│       ↓                                                  │
│  ┌─────────────────────────────────────────────────┐     │
│  │              ReAct 执行循环                       │     │
│  │                                                 │     │
│  │  截图采集 → VLM 推理 → Action 解析 → 执行 → 验证  │     │
│  │     ↑                                    │      │     │
│  │     └────────────────────────────────────┘      │     │
│  └─────────────────────────────────────────────────┘     │
│       ↓                                                  │
│  [评估报告] 轨迹记录 + 指标统计                             │
└──────────────────────────────────────────────────────────┘

可选增强层（飞书 Electron 特有）：
  Accessibility Tree 辅助验证坐标 → 提升定位精度
```

### 3.2 VLM 选型建议

| 模型 | 优势 | 劣势 | 建议用途 |
|------|------|------|----------|
| **Claude claude-sonnet-4-6** | API 方便、指令跟随强、中文理解好 | 不是专门的 GUI 模型，坐标输出需 prompt 工程 | 开发阶段快速迭代 |
| **UI-TARS-1.5-7B** | 专门训练 GUI，坐标精度高，action 格式已有完整 parser | 需要 GPU 或 HF 部署 | 有 GPU 资源时优先 |
| GPT-4o | 性能与 Claude 相当 | 成本较高 | 备选 |
| Qwen-VL（Qwen2.5-VL）| 开源，qwen25vl 坐标模式被 UI-TARS parser 支持 | 需要自行部署 | 有本地算力时 |

**推荐策略**：
- M1~M2 阶段：Claude claude-sonnet-4-6 API（快速迭代，无需部署）
- M3~M5 阶段：切换到 UI-TARS-1.5-7B（HF TGI 云端部署），复用已有 parser

### 3.3 核心代码复用：UI-TARS action_parser

UI-TARS 开源代码中最有价值的就是 `action_parser.py`，**直接复用，不需要重写**：

```python
from ui_tars.action_parser import (
    parse_action_to_structure_output,
    parsing_response_to_pyautogui_code,
    add_box_token
)

# 1. VLM 输出 raw text
raw_response = "Thought: 点击发送按钮\nAction: click(start_box='(234,567)')"

# 2. 解析为结构化 action
actions = parse_action_to_structure_output(
    raw_response,
    factor=1000,
    origin_resized_height=screen_height,
    origin_resized_width=screen_width,
    model_type="doubao"  # 或 "qwen25vl"
)

# 3. 生成 pyautogui 代码
code = parsing_response_to_pyautogui_code(actions, screen_height, screen_width)
exec(code)
```

### 3.4 Prompt 设计

基于 `COMPUTER_USE_DOUBAO`，针对飞书测试场景调整：

```
你是一个飞书桌面客户端自动化测试 Agent。给定测试任务和当前屏幕截图，
输出下一步操作。

## 输出格式
Thought: [用中文，分析当前界面状态，写出下一步要做什么]
Action: [从动作空间中选择一个动作]

## 动作空间
click(start_box='(x,y)')
left_double(start_box='(x,y)')
right_single(start_box='(x,y)')
drag(start_box='(x1,y1)', end_box='(x2,y2)')
hotkey(key='ctrl a')
type(content='文本内容')  # \n 结尾表示回车
scroll(start_box='(x,y)', direction='down/up/left/right')
wait()
finished(content='测试结果说明')  # 任务完成时调用
failed(content='失败原因')        # 无法完成时调用

## 测试任务
{task_description}
```

**关键设计决策**：
- 坐标使用 `start_box` 格式（与 doubao model_type 对齐）
- 增加 `failed` 动作（测试场景必要）
- Thought 要求分析界面状态，便于调试

### 3.5 状态验证层设计

每次动作执行后，做轻量级验证：

```python
def verify_action(before_screenshot, after_screenshot, expected_state, vlm_client):
    """
    用 VLM 语义判断动作是否符合预期。
    比像素 diff 更鲁棒，比 OCR 更通用。
    """
    prompt = f"""
    执行操作前截图：[before]
    执行操作后截图：[after]
    预期状态：{expected_state}
    
    问题：操作后的界面是否符合预期状态？
    回答格式：
    Result: PASS/FAIL
    Reason: [简短说明]
    """
    response = vlm_client.query(prompt, [before_screenshot, after_screenshot])
    return parse_verify_response(response)
```

**三种验证策略**（按复杂度递增）：
1. **截图存在性**：before != after（证明界面有变化）
2. **VLM 语义判断**：描述期望状态，让 VLM 判断是否达到
3. **关键词 OCR**：用 pytesseract 验证特定文字出现在屏幕上

### 3.6 飞书 Electron 混合定位（进阶增强）

飞书基于 Electron，底层是 Chromium，可通过 CDP（Chrome DevTools Protocol）或 Accessibility Tree 获取 DOM 结构：

**方案一：Accessibility API（跨平台）**
```python
# Windows: pywinauto
from pywinauto import Application
app = Application(backend='uia').connect(title_re='.*飞书.*')
# 获取 UI 元素树辅助定位验证
```

**方案二：调试端口（Electron 特有）**
```
飞书启动时加 --remote-debugging-port=9222
→ 用 CDP 直接操作 DOM（仅作辅助验证，不替代视觉操作）
```

**重要**：课题要求核心操作决策必须基于视觉，DOM/AT 只能作为辅助验证手段。

### 3.7 评估报告设计

每条测试用例记录：

```json
{
  "task_id": "im_send_message_001",
  "task_desc": "在IM中搜索'测试群'，发送消息'Hello World'",
  "product": "IM",
  "result": "PASS",
  "steps": [
    {
      "step": 1,
      "thought": "当前在飞书主界面，需要点击左侧搜索图标",
      "action": "click(start_box='(40,60)')",
      "screenshot_before": "step1_before.png",
      "screenshot_after": "step1_after.png",
      "verify_result": "PASS",
      "elapsed_ms": 1230
    }
  ],
  "total_steps": 5,
  "success_steps": 5,
  "elapsed_total_ms": 8420
}
```

汇总指标：成功率、平均步骤数、平均耗时、各子产品分项统计。

---

## 四、与 PDF 建议方案的对比

| 维度 | PDF 建议 | 我们的方案 | 说明 |
|------|----------|-----------|------|
| VLM 选型 | GPT-4o / Claude / Qwen-VL / UI-TARS 均可 | 开发期 Claude API，后期 UI-TARS-1.5-7B | 兼顾开发效率和最终精度 |
| 架构 | 模块化五层 | 单 VLM ReAct 循环（与 UI-TARS 对齐） | 更简洁，减少模块间协调成本 |
| Action Parser | 自研 | 直接复用 UI-TARS `action_parser.py` | 成熟代码，省去大量工程量 |
| 状态验证 | VLM 语义比对 / 像素 diff / OCR | 优先 VLM 语义判断 | 最鲁棒，对飞书复杂 UI 友好 |
| Electron 特性 | 提到混合定位为加分项 | 作为进阶层选择性接入 | 先做视觉，再叠加 AT |
| 操作控制 | PyAutoGUI / NutJS | PyAutoGUI（Python 生态统一） | 与 UI-TARS parser 输出直接对接 |

---

## 五、风险与注意事项

### 5.1 坐标精度风险
飞书是高 DPI 应用（HiDPI / Retina），`pyautogui` 默认坐标可能需要除以缩放因子。需在初始化时检测：

```python
import ctypes
# Windows DPI 感知
ctypes.windll.shcore.SetProcessDpiAwareness(1)
```

macOS 下注意逻辑像素 vs 物理像素的差异（Retina 屏 2x）。

### 5.2 截图时机
操作后需等待 UI 渲染完成再截图。飞书某些操作（如打开文档）有网络加载，需要 `wait()` 或轮询判断加载完成。

### 5.3 type 动作的中文输入
UI-TARS parser 默认用 `pyperclip` + `Ctrl+V` 输入文字，这对中文输入友好。但飞书输入框有时拦截粘贴（如搜索框），需测试并可能回退到 `pyautogui.write()`。

### 5.4 飞书登录态
测试需要保持登录状态，建议在测试前手动登录，然后用 `--user-data-dir` 复用会话。

---

## 六、推荐开发路径（具体行动）

```
Week 1 - M1 单步操作：
  1. 安装飞书桌面客户端，了解界面结构
  2. 实现截图模块（mss 或 PIL）
  3. 接入 Claude claude-sonnet-4-6 API，配置 COMPUTER_USE prompt
  4. 复用 ui_tars/action_parser.py 解析输出
  5. 实现 PyAutoGUI 执行层
  6. 完成 5 个单步操作验证

Week 2 - M2 流程串联：
  1. 实现多轮对话历史管理（带 add_box_token）
  2. 实现简单状态验证（VLM 截图比对）
  3. 完成 IM 子产品 3 条 E2E 流程：
     - 发送文本消息
     - 创建群聊
     - 搜索并发送消息

Week 3 - M3 多产品覆盖：
  1. 扩展到云文档（创建/编辑）
  2. 扩展到日历（创建日程）
  3. 切换到 UI-TARS-1.5-7B（HF TGI 部署）提升精度

Week 4 - M4 评估体系 + M5 进阶：
  1. 实现 JSON 报告输出和统计面板
  2. 接入 Electron Accessibility Tree 辅助验证
  3. 实现简单异常处理（弹窗识别、超时重试）
```

---

## 七、参考代码入口

| 文件 | 作用 |
|------|------|
| `UI-TARS/codes/ui_tars/action_parser.py` | Action 解析 + pyautogui 代码生成，直接复用 |
| `UI-TARS/codes/ui_tars/prompt.py` | Prompt 模板参考，基于 `COMPUTER_USE_DOUBAO` 改造 |
| `UI-TARS/data/test_messages.json` | 多轮对话消息格式参考 |
| `UI-TARS/README_deploy.md` | HF TGI 部署步骤 + API 调用示例 |
| `UI-TARS/README_coordinates.md` | 坐标处理可视化示例 |
