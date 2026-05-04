# 自愈式执行模块

> 关联文件：`agent/healer.py`、`agent/loop.py`、`tests/run_benchmark.py`

---

## 目标

当 Agent 陷入死路或卡死时，自动分析失败原因，将诊断结论和新策略注入上下文，让 Agent 在已有历史信息的基础上调整路径继续推进。自愈过程结束后，将成功路径总结为经验条目，写入产品级别的 memory 文件，供后续类似任务参考。

---

## 触发类型

| 触发类型 | 状态 | 检测方式 | 根因假设 | 策略方向 |
|----------|------|----------|----------|----------|
| `CHECKPOINT_TIMEOUT` | **已实现** | 当前 checkpoint 在 6 步内未到达 | 操作方式有误或已偏离正确路径 | 先自诊：仍在正确区域则换交互方式；已完全偏离则回飞书首页重来 |
| `EXPLICIT_FAIL` | 待实现 | VLM 主动调用 `failed()` | 当前路径走死，VLM 判断无法继续 | 换入口：从不同菜单/快捷键/侧边栏重新进入目标功能 |
| `IMPLICIT_STUCK` | 待实现 | 连续 N 步 action_type 相同且坐标聚集在 60px 内 | VLM 反复点击同一控件但无进展 | 换交互方式：右键/双击/键盘/滚动后再操作 |
| `EXPLICIT_STUCK` | 待实现 | 连续两张截图像素差低于 0.5% 阈值 | 操作发出但界面无响应，可能是焦点丢失或窗口被遮挡 | 重建控制权：点击主窗口空白区域重获焦点，或按 Escape 清除隐藏遮罩 |

---

## 执行流程

### 触发阶段

每步执行完毕后依次检查四种触发条件。检测到触发时，立即截图并调用 `diagnose_and_heal()`。

`diagnose_and_heal()` 接收：
- 当前任务描述
- 最近 5 轮对话历史（包含截图和 Thought+Action）
- 当前截图
- 触发类型（决定注入哪条策略方向提示）

VLM 返回两个字段：
- **diagnosis**：一句话说明之前路径为何不通
- **new_strategy**：结合策略方向提示和截图提出的新路径思路

### 上下文注入

诊断结果以 system message 的形式插入 history：

> `[自愈模式/触发类型] 之前的路径失败（diagnosis）。新策略：new_strategy。`

Agent 在下一步开始时会看到这条消息，结合完整的历史截图和操作记录，自行决定如何推进。不额外执行任何预置动作，完全由 Agent 根据注入的信息自主调整。

### CHECKPOINT_TIMEOUT 的特殊处理

其他触发类型不影响 checkpoint 状态。`CHECKPOINT_TIMEOUT` 触发自愈时，`cp_idx` 不向前推进，Agent 继续尝试完成同一个 checkpoint，而不是跳过它。只有在不启用自愈时，超时才直接跳过。

---

## 步骤追踪与经验生成

自愈触发后，从下一步开始累积 `heal_step_records`，记录每一步的 thought 和 action，直到目标 checkpoint 完成。

当目标 checkpoint 达成时（无论整体任务最终成功与否），立即调用 `generate_memory_entry()` 生成一条经验记录。LLM 根据 diagnosis、new_strategy 和实际步骤，生成四个字段：

| 字段 | 内容 |
|------|------|
| **触发** | 触发类型和对应的 checkpoint 编号 |
| **问题** | 失败根因（来自 diagnosis） |
| **解决** | 本次具体用了哪个操作路径（不超过 30 字） |
| **启发** | 从本次失败和修复中提炼的可泛化操作规律（不超过 40 字） |

经验记录追加写入 `memory/<product>.md`，同产品的所有用例共用一个文件，按时间顺序积累。

---

## 经验的使用

运行时加上 `--use-memory` 参数，执行任务前会读取对应产品的 `memory/<product>.md`，将内容以 `【历史经验 — 执行前必读，遇到类似问题优先参考】` 的格式拼接在 task 描述最前面注入 system prompt。

默认不读取，需显式开启。

---

## 预算控制

- `heal_max`（默认 2）：整个用例最多触发自愈的次数。超过上限后，下一次触发直接将用例标记为 `FAILED`。
- `_CP_TIMEOUT = 6`：单个 checkpoint 允许的最大步数，超出则触发 `CHECKPOINT_TIMEOUT`。
- 自愈期间步骤照常计入总步数，不额外扩展步骤预算。

---

## 用户开关

默认关闭，所有开关通过 CLI 控制：

| 参数 | 说明 | 默认 |
|------|------|------|
| `--heal` | 启用自愈模块 | 关闭 |
| `--heal-max N` | 每个用例最多自愈次数 | 2 |
| `--heal-no-patch` | 自愈后不写 memory 文件 | 写入 |
| `--heal-triggers T` | 启用的触发类型，逗号分隔，留空表示全部 | 全部 |
| `--use-memory` | 运行前将 memory 文件注入任务上下文 | 关闭 |

典型用法：

```
# 启用自愈 + 读取历史经验
python tests/run_benchmark.py --case-id DOC_L2_006 --contact 张三 --heal --use-memory

# 只在 VLM 主动放弃时自愈，不读 memory
python tests/run_benchmark.py --heal --heal-triggers explicit_fail

# 自愈但不写 memory（只跑，不积累）
python tests/run_benchmark.py --heal --heal-no-patch
```

---

## 验收标准

| 场景 | 期望行为 |
|------|----------|
| Checkpoint 6 步内未到达 | 触发 `CHECKPOINT_TIMEOUT`，cp_idx 不推进，注入自诊策略 |
| VLM 调用 `failed()` | 待实现（`EXPLICIT_FAIL`） |
| 连续 3 步点击同一区域 | 待实现（`IMPLICIT_STUCK`） |
| 截图像素差 < 0.5% | 待实现（`EXPLICIT_STUCK`） |
| Checkpoint 在自愈期间完成 | 立即生成 memory 条目写入文件，无论任务最终是否成功 |
| 超过 `heal_max` | 用例标记 `FAILED`，停止运行 |
| `--use-memory` 未开启 | memory 文件存在也不注入，任务上下文不受影响 |
