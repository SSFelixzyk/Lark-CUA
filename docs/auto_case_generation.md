# 测试用例自动生成方案

> 基于飞书功能文档或录屏，自动生成结构化 DSL 测试用例

---

## 整体设计

现有的 `dsl/generator.py` 已经有了 `generate_loop()`——接受一段自然语言 → 输出一个 YAML。新能力要做的是**把"人工写描述"这一步自动化**，变成从文档或录屏中自动提取描述，再批量调 `generate_loop()`。

所以核心新增是两个**上游解析模块**，不动现有生成/评分链路：

```
文档/录屏
    ↓  (新增)
提取"可测功能描述"列表
    ↓  (已有)
generate_loop() × N
    ↓  (已有)
evaluate_text() 质量过滤
    ↓  (新增)
去重 + 批量保存
```

---

## 模式一：文档驱动

> **已实现：** `tools/doc_case_generator.py`

**输入：** 飞书云文档 URL 或 token（通过 lark-cli 读取）
**输出：** 多个 YAML 用例

### 流程

```
1. lark-cli docs +fetch 读取飞书云文档 → Markdown 文本
2. 按 ## 标题切割为若干节（每节上限 3000 字）
3. 每节独立调 LLM 提取可测功能点列表
4. 按 feature 名去重，按 level 过滤
5. 每个功能点 → generate_loop()（含评分精炼）
6. 保存 YAML
```

### lark-cli 命令

```bash
# 读取完整文档（Markdown 格式）
lark-cli docs +fetch --api-version v2 --doc "<url_or_token>" --doc-format markdown --format json

# 返回结构
{
  "ok": true,
  "data": {
    "document": {
      "content": "# 文档标题\n\n## 功能介绍\n..."
    }
  }
}
# 内容字段路径：data["data"]["document"]["content"]
```

### CLI 用法

```bash
# 基本用法
python tools/doc_case_generator.py --doc "https://xxx.feishu.cn/docx/..." --product im

# 指定等级和上限
python tools/doc_case_generator.py --doc "https://..." --product docs --levels L1,L2 --max-cases 5

# 跳过评分循环（更快，质量略低）
python tools/doc_case_generator.py --doc "https://..." --product im --no-evaluate
```

### 核心函数

| 函数 | 说明 |
|------|------|
| `fetch_doc_markdown(doc)` | 调 lark-cli，返回文档 Markdown 文本 |
| `extract_all_features(markdown, product)` | 分节提取 + 去重，返回功能点列表 |
| `generate_from_doc(...)` | 完整流程入口，返回保存的 YAML 路径列表 |

---

## 模式二：录屏驱动

**输入：** MP4/AVI 视频文件（人工操作录屏）
**输出：** 一个 YAML 用例（一段录屏对应一个任务）

### 流程

```
1. 视频关键帧提取（基于画面变化幅度，不是固定采样）
2. 帧序列 → VLM 逐帧描述操作
3. 操作序列 → 提炼"任务意图"
4. (意图 + 操作细节) → generate_loop()
```

### 关键帧提取策略

不用固定帧率，用**像素差分检测场景切换**：

```python
# tools/video_case_generator.py
import cv2
import numpy as np

def extract_keyframes(video_path: str, diff_threshold: float = 0.05) -> list[np.ndarray]:
    """
    只在画面变化超过阈值时抽帧，
    过滤掉静止等待帧，控制在 10~20 帧以内。
    """
    cap = cv2.VideoCapture(video_path)
    prev_gray = None
    keyframes = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev_gray is not None:
            diff = np.mean(np.abs(gray.astype(float) - prev_gray.astype(float))) / 255
            if diff > diff_threshold:
                keyframes.append(frame)
        prev_gray = gray
    cap.release()
    return keyframes
```

### 两阶段 VLM 分析

**阶段 1**：给每帧 + 上下文，让 VLM 描述"发生了什么"

```python
# 每帧逐步分析，积累操作轨迹
for i, frame in enumerate(keyframes):
    prompt = f"这是第{i+1}帧（共{n}帧）。描述当前界面状态和用户刚刚执行的操作（一句话）。"
```

**阶段 2**：汇总所有帧描述，提炼任务意图

```python
prompt = f"""
以下是录屏的逐帧操作轨迹：
{操作轨迹}

请回答：
1. 用户的整体测试目标是什么（一句话，这将作为 task 字段）
2. 这是哪个飞书产品（im/docs/calendar/base/vc/mail）
3. 任务等级（L1/L2/L3）
"""
```

然后把**任务意图**传给 `generate_loop()`，把**操作轨迹**作为 `ui_hints` 的参考材料注入 prompt。

### 新文件：`tools/video_case_generator.py`

```python
def generate_from_video(
    video_path: str,
    product: str | None = None,   # None = 自动推断
) -> tuple[dict, Path]:
    frames = extract_keyframes(video_path)
    operation_trace = _analyze_frames(frames)   # 阶段1：逐帧描述
    intent = _extract_intent(operation_trace)   # 阶段2：提炼意图
    inferred_product = product or intent["product"]
    return generate_loop(intent["task"], inferred_product)
```

---

## 去重机制

两种模式都需要防止生成和已有用例重复的测试。新增 `dsl/deduplicator.py`：

```python
def is_duplicate(new_task: str, product: str, similarity_threshold: float = 0.85) -> bool:
    """
    加载已有 YAML，让 LLM 判断 new_task 与哪个已有任务语义重复。
    返回 True = 跳过生成，False = 允许生成。
    """
    existing = _load_existing_tasks(product)   # 从 benchmark/ 和 generated/ 读所有 task
    if not existing:
        return False
    # 直接用 LLM 判断语义相似度（比词向量更准）
    prompt = f"""
判断以下新任务与已有任务列表是否语义重复（做的是同一件事）。
新任务：{new_task}
已有任务：{existing}
只输出 JSON：{{"duplicate": true/false, "similar_to": "最相似的已有任务或null"}}
"""
```

---

## 统一入口

新建 `tools/auto_case_generator.py`，统一 CLI：

```bash
# 从文档 URL 生成（飞书官方文档）
python tools/auto_case_generator.py --from-doc "https://open.feishu.cn/..." --product im

# 从本地文档文件生成
python tools/auto_case_generator.py --from-doc docs/feishu_im_guide.md --product im --levels L1,L2

# 从录屏生成
python tools/auto_case_generator.py --from-video recordings/demo.mp4

# 限制生成数量，跳过评分不达标的
python tools/auto_case_generator.py --from-doc ... --max-cases 5 --min-score 1
```

---

## 文件改动汇总

| 文件 | 类型 | 说明 |
|------|------|------|
| `tools/auto_case_generator.py` | **新建** | 统一 CLI 入口，分发到两个模式 |
| `tools/doc_case_generator.py` | **新建** | 文档解析 → 功能点提取 → 批量生成 |
| `tools/video_case_generator.py` | **新建** | 关键帧提取 → VLM 分析 → 生成 |
| `dsl/deduplicator.py` | **新建** | 语义去重，防止和已有用例重复 |
| `dsl/generator.py` | **微改** | `build_messages()` 接受可选的 `operation_hints` 参数，供录屏模式注入操作轨迹 |
| `requirements.txt` | **修改** | 新增 `opencv-python`（录屏模式需要）|

`dsl/evaluator.py`、`agent/loop.py` 等**不需要改动**。

---

## 建议实施顺序

### 第一步（1-2 天）：文档模式 MVP

先做最小可用版：只支持本地 Markdown 文件输入，不做 URL 抓取，不做去重。验证"文档 → 功能点提取 → generate_loop()"这条链路跑通。

### 第二步（1 天）：去重 + URL 支持

加 `dsl/deduplicator.py`，再加 `requests` 抓取 URL 和 HTML 解析（`BeautifulSoup`）。

### 第三步（2-3 天）：录屏模式

录屏模式依赖 `opencv-python`，关键帧提取本身是纯工程问题，难点在阶段 2 的意图提炼 prompt 设计，需要几轮迭代调整。

---

## 最大风险点

录屏模式的帧数量控制——帧太多会超出 VLM 上下文，帧太少会丢失关键操作。建议上限设 15 帧，超出时按时间均匀降采样。
