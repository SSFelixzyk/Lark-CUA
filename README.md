# Lark-CUA

> 飞书桌面端 GUI 自动化测试 Agent — 飞书 AI 产品创新赛道 · CUA-Lark 课题 · 第 6 组

用多模态大模型（豆包 2.0 Vision）像真实用户一样看着屏幕操作飞书，完成自动化功能测试，无需元素选择器。

---

## 项目结构

```
Lark-Agent/
├── config.py              # 全局配置，从 .env 加载密钥
├── requirements.txt       # Python 依赖
├── ui_tars/               # ByteDance UI-TARS action parser（Apache-2.0）
│   ├── action_parser.py   # VLM 输出解析 + pyautogui 代码生成
│   └── prompt.py          # 原始 prompt 模板参考
├── llm/
│   └── doubao_client.py   # 火山引擎 ARK（豆包）API 封装
├── agent/
│   ├── prompts.py         # 飞书专用 system prompt
│   ├── screenshot.py      # 全屏截图（mss）
│   ├── executor.py        # 解析 + 执行 pyautogui，DPI 自动处理
│   └── loop.py            # ReAct 主循环，管理历史与报告
├── dsl/                   # M3：测试用例 DSL（待实现）
├── report/                # M4：结构化测试报告（待实现）
├── tests/
│   ├── test_api.py        # API 连通性 + 坐标格式验证
│   └── test_loop.py       # 端到端运行入口
└── docs/
    ├── design.md          # 系统架构设计文档
    └── analysis_report.md # UI-TARS 技术分析报告
```

---

## 快速开始

### 1. 克隆与安装

```bash
git clone https://github.com/SSFelixzyk/Lark-CUA.git
cd Lark-CUA
pip install -r requirements.txt
```

### 2. 配置 API 密钥

在项目根目录（`Lark-Agent/` 的**上一级**）创建 `.env` 文件：

```
EP-ID = ep-xxxxxxxxxxxxxxxx-xxxxx
DOUBAO-API-KEY = ark-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> 从火山引擎 ARK 控制台获取：推理接入点 ID（EP-ID）和 API Key。
> `.env` 已在 `.gitignore` 中，不会被提交。

### 3. 验证 API 与坐标格式

```bash
cd Lark-Agent
python tests/test_api.py
```

正常输出应包含：
- `[OK] API connection OK`
- `FORMAT: Likely 0-1000 relative (doubao style)` — 确认 `model_type='doubao'`

### 4. 运行第一个任务

确保飞书桌面客户端已打开并可见，然后：

```bash
python tests/test_loop.py
```

脚本倒计时 3 秒，请在此期间切换到飞书窗口。也可传入自定义任务：

```bash
python tests/test_loop.py "在飞书 IM 中搜索群聊 XXX 并发送消息 Hello"
```

---

## VM 模式（OSWorld 风格隔离评测）

### 概述

默认模式下 Agent 直接在本机运行，截图和操作都在本机执行。VM 模式将执行环境迁移到 VMware 虚拟机中，Agent 逻辑仍在宿主机运行，所有 GUI 操作通过 HTTP 发送到 VM 内的 Action Server 执行，每次测试前可还原快照保证环境干净。

```
宿主机（Agent + LLM 调用）
    ↕ HTTP
VM（飞书 + Action Server）
```

### 手动配置 VM（推荐）

> ⚠️ `vm/provision.py` 目前不够稳定，建议按以下步骤手动完成 VM 配置。

**第一步：准备 VM**

1. 安装 VMware Workstation Pro（个人免费）
2. 新建 Windows 11 虚拟机，分配 6GB 内存、4 核 CPU
3. 安装 VMware Tools（VM → Install VMware Tools）
4. 在 VM 内安装 Python 3.11+（勾选 "Add Python to PATH"）
5. 在 VM 内安装飞书并登录账号

**第二步：在 VM 内部署 Action Server**

将 `vm/action_server.py` 复制到 VM 内（如 `C:\LarkCUA\`），安装依赖：

```bash
pip install flask pyautogui mss pillow pyperclip
```

注册为开机自启任务（在 VM 内 PowerShell 中运行）：

```powershell
$a = New-ScheduledTaskAction -Execute 'python.exe' -Argument 'C:\LarkCUA\action_server.py'
$t = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName 'LarkCUAActionServer' -Action $a -Trigger $t -RunLevel Highest -Force
```

**第三步：打 base 快照**

飞书处于正常登录状态时，在 VMware 菜单中：

```
VM → Snapshot → Take Snapshot → 名字填 base → OK
```

**第四步：配置宿主机 .env**

```
VM_MODE=true
VM_SERVER_URL=http://<VM的IP>:8765
```

VM 的 IP 可在 VM 内运行 `ipconfig` 查看，或在 VMware 菜单中查看。

**第五步：验证连接**

```powershell
curl http://<VM的IP>:8765/health
# 返回 {"status":"ok"} 即成功
```

### 运行 VM 模式 Benchmark

```bash
# 每次 run 开始时还原一次快照（推荐）
python tests/vm_runner.py \
  --vmx "C:\path\to\vm.vmx" \
  --snapshot base \
  --product gui \
  --reset never

# 每个 case 前都还原快照（最严格隔离）
python tests/vm_runner.py \
  --vmx "C:\path\to\vm.vmx" \
  --snapshot base \
  --product gui \
  --reset each
```

### 切换回本地模式

将 `.env` 中 `VM_MODE` 改为 `false`，直接运行：

```bash
python tests/run_benchmark.py --product gui --level L1
```

---

## 架构概览

```
自然语言任务
    ↓
[ReAct 循环]
  截图（mss）
    → 豆包 2.0 Vision（Thought + Action）
    → action_parser 解析坐标
    → pyautogui 执行
    → 等待 UI 渲染
    → 再截图 ...
    ↓
轻量验证（截图像素变化检测）
    ↓
检查点验证（VLM 判断具体视觉断言）
    ↓
StepRecord 报告
```

**坐标系统**：豆包 2.0 输出相对坐标（0~1000），`action_parser` 归一化为 [0,1]，乘以 `pyautogui.size()` 逻辑分辨率得到实际像素坐标，自动处理 DPI 缩放。

---

## 里程碑进度

| 阶段 | 内容 | 状态 |
|------|------|------|
| M1 | 截图→豆包→解析→执行闭环，单步操作验证 | 完成 |
| M2 | 多轮对话历史管理，IM 子产品 E2E 流程 | 进行中 |
| M3 | 自然语言→测试 DSL 生成层 | 待实现 |
| M4 | 结构化断言验证 + JSON 测试报告 | 待实现 |
| M5 | 失败自愈，Electron AT 辅助验证 | 待实现 |

---

## 队友开发指南

### 关键文件入口

| 想改什么 | 看哪里 |
|----------|--------|
| 系统 prompt / 飞书界面知识 | `agent/prompts.py` |
| 动作执行逻辑 | `agent/executor.py` |
| ReAct 循环 / 历史管理 | `agent/loop.py` |
| 豆包 API 调用 | `llm/doubao_client.py` |
| 坐标解析（不建议改） | `ui_tars/action_parser.py` |
| 测试用例 DSL 设计 | `dsl/`（待实现） |
| 报告生成 | `report/`（待实现） |

### 添加新测试场景

在 `tests/test_loop.py` 里的 `TASKS` 列表添加任务字符串，或直接命令行传参：

```python
TASKS = [
    "点击飞书左侧导航栏中的「消息」图标",
    "在日历中创建一个明天下午 3 点的会议",
    # 在这里添加你的测试场景
]
```

### DRY_RUN 模式

不想真正操作屏幕时，在 `test_loop.py` 顶部设置：

```python
DRY_RUN = True
```

Agent 会打印 Thought 和 Action 但不执行 pyautogui。

### 注意事项

- 运行测试前确保飞书是前台窗口，否则点击坐标会打到其他窗口
- Windows 高 DPI 屏幕已自动处理（使用 `pyautogui.size()` 逻辑坐标）
- `type()` 动作默认走剪贴板粘贴（`Ctrl+V`），对中文友好；飞书搜索框若不响应，可在 `executor.py` 中切换为 `pyautogui.write()`

---

## 技术参考

- [UI-TARS（ByteDance）](https://github.com/bytedance/UI-TARS) — action_parser 来源，Apache-2.0
- [火山引擎 ARK 文档](https://www.volcengine.com/docs/82379) — 豆包 API
- `docs/design.md` — 本项目完整架构设计
- `docs/analysis_report.md` — UI-TARS 技术分析

---

## License

Apache-2.0（`ui_tars/` 部分遵循 ByteDance 原始协议）
