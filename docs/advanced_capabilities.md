# Lark-CUA 进阶能力实施方案

> 基于当前代码架构，按优先级依次完成三项能力：异常场景处理 → 自愈式执行 → 多轮对话编排

---

## 优先级 1：异常场景处理

### 目标

让 Agent 在执行过程中自动识别并消除弹窗、权限提示、加载超时等干扰，避免因环境噪声造成假失败。

### 问题定位

当前 `agent/loop.py` 的每一步只做三件事：截图 → 问 VLM → 执行。如果弹窗覆盖了目标 UI，VLM 照样会尝试操作底层界面，导致坐标点击错误甚至任务失败。弹窗处理必须在 **截图之后、构建消息之前** 插入检测逻辑。

### 实施步骤

#### Step 1 — 新建 `agent/exception_handler.py`

定义异常类型枚举和单次检测函数：

```python
# agent/exception_handler.py
import re
import json
from enum import Enum
from pathlib import Path
from llm.doubao_client import chat, build_user_message

class ExceptionType(Enum):
    NONE = "none"
    DIALOG = "dialog"           # 通用弹窗 / 确认框
    PERMISSION = "permission"   # 权限请求
    LOADING = "loading"         # 加载转圈超时
    ERROR_TOAST = "error_toast" # 操作失败的 Toast 提示

_DETECTION_SYSTEM = """\
你是飞书界面异常检测器。观察截图，判断是否存在干扰正常操作的 UI 异常。

输出严格 JSON，无其他内容：
{
  "exception_type": "none|dialog|permission|loading|error_toast",
  "description": "一句话说明",
  "dismiss_action": "click(start_box='(x,y)')|hotkey(key='Escape')|null"
}

- dialog: 有"取消/关闭/×"按钮的弹窗或模态框
- permission: 含"允许/拒绝/授权"等字样的权限弹框
- loading: 全屏或大面积加载动画，无可点击内容
- error_toast: 红色/橙色错误提示条
- dismiss_action: 填写可以关闭该异常的单步动作；如无法关闭填 null
"""

def detect_exception(screenshot_path: Path) -> dict:
    """
    检测截图中是否存在 UI 异常。
    返回: {"exception_type": str, "description": str, "dismiss_action": str|None}
    """
    user_msg = build_user_message("请检测当前截图是否存在 UI 异常。", screenshot_path)
    messages = [{"role": "system", "content": _DETECTION_SYSTEM}, user_msg]
    try:
        raw = chat(messages, max_tokens=150)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"exception_type": "none", "description": "", "dismiss_action": None}
```

#### Step 2 — 在 `loop.py` 中插入异常处理钩子

在 `LarkAgent.run()` 的 step 循环中，截图之后、构建消息之前加入：

```python
# agent/loop.py — 在 Step 1（截图）之后插入 ↓

from agent.exception_handler import detect_exception, ExceptionType

# 异常处理：最多重试 2 次
_EXCEPTION_RETRIES = 2
exception_retries = 0

# ... 截图完成后 ...
shot_path = capture(self.screenshot_dir / f"step{step_num:02d}_pending.png")

# ── 异常检测 ──────────────────────────────────
exc = detect_exception(shot_path)
while exc["exception_type"] != "none" and exception_retries < _EXCEPTION_RETRIES:
    exception_retries += 1
    print(f"  [exception] {exc['exception_type']}: {exc['description']}")
    action_str = exc.get("dismiss_action")
    if action_str:
        # 直接执行关闭动作，不走完整 VLM 循环
        execute(f"Thought: 关闭异常弹窗\nAction: {action_str}")
        time.sleep(1.5)
        shot_path = capture(self.screenshot_dir / f"step{step_num:02d}_pending.png")
        exc = detect_exception(shot_path)
    else:
        # 无法关闭（如全屏 loading）—— 等待后重试
        if exc["exception_type"] == "loading":
            time.sleep(3)
            shot_path = capture(self.screenshot_dir / f"step{step_num:02d}_pending.png")
            exc = detect_exception(shot_path)
        else:
            break  # 未知异常，交给正常 VLM 处理
# ── 异常检测结束 ──────────────────────────────
```

#### Step 3 — 在 `StepRecord` 中记录异常信息（可选，用于报告）

```python
# StepRecord 新增字段
exception_type: str = "none"    # 本步检测到的异常类型
exception_dismissed: bool = False
```

#### Step 4 — 在 `prompts.py` 中强化 VLM 的感知能力

在系统提示中增加一段，让 VLM 在遇到弹窗时主动输出 dismiss 动作：

```
## 异常处理原则
- 若当前截图中存在弹窗、权限请求或错误提示，优先关闭它，再执行测试步骤
- 关闭弹窗后在 Thought 中注明：「检测到弹窗，已关闭，继续执行」
```

### 验收标准

| 场景 | 期望行为 |
|------|----------|
| 飞书升级弹窗 | 自动点击"稍后更新"或×，继续测试 |
| 权限请求 | 点击"允许"，继续 |
| 全屏加载 | 等待最多 3 × 2 = 6s，超时记录 exception_type=loading |
| 无异常 | 零额外耗时（检测函数仅 1 次 VLM 调用） |

---

## 优先级 2：自愈式执行

### 目标

操作失败（`status == "failed"` 或连续 error）后，不直接标记用例失败，而是让 VLM 分析原因并尝试替代路径。

### 问题定位

当前 `loop.py` 在 `exec_result["status"] == "failed"` 时直接 `break`，`result.status = "failed"`。自愈只需在这里插入一个 **诊断-重试** 分支，不影响正常路径。

### 实施步骤

#### Step 1 — 新建 `agent/healer.py`

```python
# agent/healer.py
import re
import json
from pathlib import Path
from llm.doubao_client import chat, build_user_message

_HEAL_SYSTEM = """\
你是飞书自动化测试的自愈专家。
给定：原始任务、失败步骤的 Thought+Action、当前屏幕截图。
分析失败原因，提出一个替代方案（仅单步动作）。

输出严格 JSON：
{
  "diagnosis": "失败原因（一句话）",
  "strategy": "替代策略描述",
  "action": "click(start_box='(x,y)')|type(content='...')|hotkey(key='...')|give_up",
  "confidence": 0.0~1.0
}

- 若无合理替代方案，action 填 "give_up"
- confidence < 0.5 时也应填 give_up
"""

def diagnose_and_heal(
    task: str,
    failed_thought: str,
    failed_action: str,
    screenshot_path: Path,
) -> dict:
    """
    分析失败原因并给出替代动作。
    返回: {"diagnosis": str, "strategy": str, "action": str, "confidence": float}
    """
    user_text = (
        f"测试任务：{task}\n\n"
        f"失败步骤 Thought：{failed_thought}\n"
        f"失败步骤 Action：{failed_action}\n\n"
        "请分析失败原因并给出替代方案。"
    )
    user_msg = build_user_message(user_text, screenshot_path)
    messages = [{"role": "system", "content": _HEAL_SYSTEM}, user_msg]
    try:
        raw = chat(messages, max_tokens=200)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"diagnosis": "无法解析", "strategy": "", "action": "give_up", "confidence": 0.0}
```

#### Step 2 — 在 `loop.py` 的失败分支插入自愈逻辑

替换原来的 `if exec_result["status"] == "failed": break`：

```python
# agent/loop.py — 替换原失败处理分支 ↓

from agent.healer import diagnose_and_heal

_MAX_HEAL_ATTEMPTS = 2   # 单个任务最多自愈次数
heal_attempts = 0        # 在 run() 方法开始处初始化

# ... 在 step 循环末尾的 terminal conditions 检查中 ...

if exec_result["status"] == "failed":
    if heal_attempts < _MAX_HEAL_ATTEMPTS:
        heal_attempts += 1
        # 重新截图（弹窗/状态可能已变）
        heal_shot = capture(self.screenshot_dir / f"step{step_num:02d}_heal.png")
        heal = diagnose_and_heal(
            task=task,
            failed_thought=exec_result["thought"],
            failed_action=exec_result["code"],
            screenshot_path=heal_shot,
        )
        print(f"  [heal #{heal_attempts}] {heal['diagnosis']} → {heal['strategy']}")

        if heal["action"] != "give_up":
            # 注入替代动作到历史，让下一步 VLM 知道发生了什么
            heal_msg = (
                f"Thought: 上一步失败（{heal['diagnosis']}）。"
                f"自愈策略：{heal['strategy']}\n"
                f"Action: {heal['action']}"
            )
            history.append({"role": "assistant", "content": heal_msg})
            # 执行替代动作
            heal_exec = execute(f"Thought: {heal['strategy']}\nAction: {heal['action']}")
            time.sleep(config.STEP_WAIT_MS / 1000)
            # 记录为特殊 step（heal 标记）
            rec.status = f"healed_{heal_attempts}"
            result.steps.append(rec)
            continue   # 继续正常循环
        else:
            print(f"  [heal] give_up after {heal_attempts} attempts")
    result.status = "failed"
    break
```

#### Step 3 — 连续 error 也触发自愈

在 `error` 分支同样接入：

```python
if exec_result["status"] == "error":
    consecutive_errors = getattr(result, '_consecutive_errors', 0) + 1
    result._consecutive_errors = consecutive_errors
    if consecutive_errors >= 2 and heal_attempts < _MAX_HEAL_ATTEMPTS:
        # 触发自愈（同上逻辑）
        ...
    elif consecutive_errors >= 3:
        result.status = "error"
        break
```

#### Step 4 — 在 `RunResult` 中记录自愈次数

```python
@dataclass
class RunResult:
    ...
    heal_attempts: int = 0      # 触发自愈的总次数
    heal_success: bool = False  # 自愈后是否最终完成
```

#### Step 5 — Benchmark 报告中展示自愈信息

在 `report/md.py` 的用例报告块中追加：

```markdown
> 自愈：触发 {heal_attempts} 次，{'成功' if heal_success else '失败'}
> 诊断：{diagnosis}
```

### 验收标准

| 场景 | 期望行为 |
|------|----------|
| 单次 failed（如点击位置偏移）| 自愈重试，记录 healed_1，继续执行 |
| 连续 2 次 error | 触发自愈，尝试替代路径 |
| 自愈 give_up | 记录失败原因，停止重试，最终 status=failed |
| 自愈后完成任务 | status=done，heal_success=true |
| 超过 _MAX_HEAL_ATTEMPTS | 不再重试，防止无限循环 |

---

## 优先级 3：多轮对话编排

### 目标

把当前"逐步执行固定 YAML 步骤"升级为"根据中间执行结果动态决定下一步"，支持条件分支和用例内的自适应路径。

### 问题定位

当前架构中，checkpoints 是线性数组，VLM 按顺序推进。多轮编排需要引入**条件节点**和**运行时状态感知**，让 Agent 可以在任意步骤判断"走哪条路"。

### 核心设计：条件执行树 DSL

在现有 YAML 格式中新增可选的 `branches` 字段：

```yaml
# 示例：带分支的 IM 用例
id: IM_GEN_010
task: "向张三发送消息，若发送失败则尝试通过搜索重新查找联系人"
checkpoints:
  - "消息输入框已打开"
  - "消息已发送成功"
branches:
  - after_checkpoint: 0          # checkpoint 0 满足后触发条件判断
    condition: "消息输入框是否已获得焦点"
    if_yes: null                 # null = 继续主流程
    if_no:
      inject_steps:
        - "重新点击消息输入框"
        - "等待获得焦点"
  - after_checkpoint: 1
    condition: "消息是否出现在对话列表中"
    if_yes: null
    if_no:
      inject_steps:
        - "使用 Ctrl+K 搜索联系人"
        - "重新发送消息"
```

### 实施步骤

#### Step 1 — 更新 `dsl_generator.py` 支持生成 branches

在 DSL 生成的 system prompt 中追加 branches 的格式说明和示例：

```python
# tools/dsl_generator.py — 在 GENERATOR_SYSTEM 中追加 ↓

"""
## branches 字段（可选）
当任务存在明显的"成功/失败分支"时，填写 branches 数组。每个分支节点：

  after_checkpoint: <int>    # 在第几个 checkpoint 满足后进行条件判断（从 0 开始）
  condition: "判断条件（一句话）"
  if_yes: null               # 继续主流程
  if_no:
    inject_steps:            # 注入的补救步骤列表
      - "步骤描述1"
      - "步骤描述2"

只在任务有实质性分支点时填写，简单线性任务不填。
"""
```

#### Step 2 — 新建 `agent/orchestrator.py`（编排引擎）

```python
# agent/orchestrator.py
from dataclasses import dataclass, field
from typing import Callable

@dataclass
class BranchNode:
    after_checkpoint: int
    condition: str
    if_yes: list[str] | None    # None = 继续主流程
    if_no: list[str] | None     # 注入步骤列表

@dataclass
class OrchestrationPlan:
    base_checkpoints: list[str]
    branches: list[BranchNode] = field(default_factory=list)
    # 运行时状态
    injected_steps: list[str] = field(default_factory=list)
    current_cp_idx: int = 0

    @classmethod
    def from_case(cls, case: dict) -> "OrchestrationPlan":
        cps = case.get("checkpoints", [])
        raw_branches = case.get("branches", [])
        nodes = []
        for b in raw_branches:
            if_no_data = b.get("if_no") or {}
            nodes.append(BranchNode(
                after_checkpoint=b["after_checkpoint"],
                condition=b["condition"],
                if_yes=b.get("if_yes"),
                if_no=if_no_data.get("inject_steps"),
            ))
        return cls(base_checkpoints=cps, branches=nodes)

    def active_checkpoint(self) -> str | None:
        """返回当前应追踪的 checkpoint 文本。"""
        all_cps = self.base_checkpoints + self.injected_steps
        if self.current_cp_idx < len(all_cps):
            return all_cps[self.current_cp_idx]
        return None

    def advance(self) -> None:
        self.current_cp_idx += 1

    def get_branch_for_current(self) -> BranchNode | None:
        """返回当前 cp_idx 对应的分支节点（若存在）。"""
        for b in self.branches:
            if b.after_checkpoint == self.current_cp_idx - 1:
                return b
        return None
```

#### Step 3 — 新建条件判断函数（调用 VLM）

```python
# agent/orchestrator.py — 追加 ↓

import re
import json
from pathlib import Path
from llm.doubao_client import chat, build_user_message

_CONDITION_SYSTEM = """\
你是飞书测试的条件判断器。根据截图判断给定条件是否满足。
输出严格 JSON：{"result": true/false, "reason": "一句话"}
"""

def evaluate_condition(condition: str, screenshot_path: Path) -> bool:
    user_msg = build_user_message(f"条件：{condition}", screenshot_path)
    messages = [{"role": "system", "content": _CONDITION_SYSTEM}, user_msg]
    try:
        raw = chat(messages, max_tokens=100)
        raw_clean = re.sub(r"^```json\s*|```\s*$", "", raw.strip(), flags=re.IGNORECASE)
        m = re.search(r"\{.*\}", raw_clean, re.DOTALL)
        if m:
            return bool(json.loads(m.group()).get("result", False))
    except Exception:
        pass
    return False
```

#### Step 4 — 改造 `loop.py`：使用 OrchestrationPlan 替代线性 cp_list

```python
# agent/loop.py — 改造 run() 方法 ↓

from agent.orchestrator import OrchestrationPlan, evaluate_condition

def run(self, task: str, case: dict | None = None,
        checkpoints: list[str] | None = None) -> RunResult:
    """
    case: 完整的 YAML case dict（含 branches）；
          若只传 checkpoints 则退化为原有线性行为。
    """
    plan = OrchestrationPlan.from_case(case) if case else OrchestrationPlan(
        base_checkpoints=checkpoints or []
    )
    ...

    for step_num in range(1, self.max_steps + 1):
        ...
        # 截图后检查是否触发分支
        active_cp = plan.active_checkpoint()

        # checkpoint 推进逻辑（保持原有 _CP_TIMEOUT 机制）
        if cp_reached:
            plan.advance()

            # ── 分支判断 ──────────────────────────────
            branch = plan.get_branch_for_current()
            if branch:
                cond_result = evaluate_condition(branch.condition, shot_path)
                print(f"  [branch] condition='{branch.condition}' → {cond_result}")
                inject = branch.if_yes if cond_result else branch.if_no
                if inject:
                    # 将补救步骤注入为新的 checkpoints
                    plan.injected_steps.extend(inject)
                    print(f"  [branch] injecting {len(inject)} steps: {inject}")
            # ── 分支判断结束 ──────────────────────────
        ...
```

#### Step 5 — 更新 `run_benchmark.py` 传递 case 对象

```python
# tests/run_benchmark.py — 修改 agent.run() 调用 ↓

# 原来：result = agent.run(task=case["task"], checkpoints=case.get("checkpoints"))
# 改为：
result = agent.run(task=case["task"], case=case)
```

#### Step 6 — DSL 评分新增 branches 维度（可选）

在 `tools/dsl_evaluator.py` 的 5 维度中追加 `branch_quality`：

```
branches 字段存在且分支条件明确：2 分
branches 存在但条件模糊：1 分
无 branches 字段（简单线性任务）：不扣分，也不加分
```

### 验收标准

| 场景 | 期望行为 |
|------|----------|
| 无 branches 字段的旧用例 | 行为与当前完全一致，零回归 |
| 条件为 true | 继续主流程，不注入额外步骤 |
| 条件为 false | inject_steps 追加到 checkpoint 队列，Agent 执行补救路径 |
| 嵌套分支（inject_steps 中再有分支）| 当前版本不支持，留给下一迭代 |
| 分支判断本身 VLM 调用失败 | 默认 false（保守：执行 if_no 补救路径）|

---

## 三项能力的集成时序

```
loop.py 每一步执行顺序：

1. 截图
2. ── 异常检测（P1）──────── detect_exception()
   └─ 若有异常 → 关闭 → 重新截图（最多 2 次）
3. 构建消息（注入当前 checkpoint）
4. 调用 Doubao VLM
5. 解析 CheckpointReached
6. ── 分支判断（P3）──────── evaluate_condition()
   └─ 若触发 → inject_steps 追加到队列
7. 执行动作
8. ── 自愈（P2）─────────── diagnose_and_heal()
   └─ 若 failed/error → 重试替代路径（最多 2 次）
9. 等待 UI 稳定
10. 记录 StepRecord
```

---

## 文件改动汇总

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `agent/exception_handler.py` | **新建** | 异常检测与 dismiss 逻辑 |
| `agent/healer.py` | **新建** | 自愈诊断与替代动作生成 |
| `agent/orchestrator.py` | **新建** | 编排计划、分支节点、条件判断 |
| `agent/loop.py` | **修改** | 插入异常处理、自愈、分支判断 |
| `agent/prompts.py` | **修改** | 追加异常处理原则 |
| `tools/dsl_generator.py` | **修改** | 生成 prompt 支持 branches 字段 |
| `tests/run_benchmark.py` | **修改** | 传 case 对象而非单独 checkpoints |
| `report/md.py` | **修改**（可选）| 展示自愈次数和分支路径 |

---

## 推荐开发顺序

1. **P1 异常处理**（2-3 天）
   - 实现 `exception_handler.py`
   - 在 `loop.py` 插入钩子
   - 用真实飞书弹窗手动验证

2. **P2 自愈执行**（3-4 天）
   - 实现 `healer.py`
   - 改造 `loop.py` 失败分支
   - 跑 5 个已知失败用例，验证自愈率

3. **P3 多轮编排**（4-5 天）
   - 实现 `orchestrator.py`
   - 更新 DSL 生成 prompt
   - 改造 `loop.py` 和 `run_benchmark.py`
   - 写 2-3 个带 branches 的新用例验证

> **P1 和 P2 可独立上线**，P3 依赖 P1/P2 已稳定，建议串行推进。
