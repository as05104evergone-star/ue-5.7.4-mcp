---
name: ue57-knowledge-base
description: Answer Unreal Engine 5.7 semantics questions with evidence instead of memory — blueprint interface functions vs events, parameter/pin direction, AnimNotify and AnimNotifyState timing, UPROPERTY/UFUNCTION specifiers, and any "why does the editor do this" question. Use it BEFORE asserting anything about UE behaviour, and whenever a blueprint or animation problem needs a file:line citation. Covers a local engine-source index, a mirrored copy of the official 5.7 documentation, and a distilled rule set of high-frequency traps.
---

# UE 5.7 事实底座

**先取证据，再下结论。**

这个技能存在的唯一理由：在这个项目里，关于 UE 语义的断言曾经**连续答错三次**，
用户因此浪费了数小时。错的不是"不知道"，是**用绝对语气说了没查证的话**。

## 三层证据，各管一段

| 层 | 回答什么 | 工具 |
|---|---|---|
| 引擎源码索引（28,574 文件 / 726,540 符号） | 在哪、第几行、属于哪个类、说明符原文 | `engine_source` |
| 官方 5.7 文档镜像（3,468 页） | Epic 的**意图与工作流**、限制 | `ue_docs` |
| 蒸馏规则（6 条，带出处） | 高频坑的结论 | `engine_facts` |

**冲突时源码优先。** 文档是意图，源码是事实。

## 硬规则

任何关于蓝图 / 事件 / 接口 / 动画通知语义的断言，**说出口之前**必须满足其一：

1. `engine_source` 拿到了 `file:line`；
2. `interface_check` 在**具体资产**上验证过；
3. 明说"查不到，我不能据此断言"。

取不到证据时的正确措辞是「**索引里没有这条，我不能据此断言**」，
**不是**「引擎不支持这个」。这两句差别很大，后者曾经就是那三次错误答案之一。

## 工作流

1. `engine_source(action="symbol", name=...)` → 拿文件、行号、归属类。
2. `engine_source(action="read", name=...)` → **读声明原文**（下结论前必须做）。
3. 需要说明符真相时 `engine_source(action="reflected", name=...)`。
4. 需要看引擎自己怎么用时 `engine_source(action="usage", ...)`——
   说明符的含义正解是**看真实用法**，不是查字典。
5. 涉及具体蓝图资产时，再跑 `interface_check(asset_path=...)`。
6. 要 Epic 的设计意图时补一次 `ue_docs(action="search", query=...)`。

## 工具动作表

| 工具 | 动作 | 用于 | 不要 |
|---|---|---|---|
| `engine_source` | `symbol` | 定位声明 + 行号 + 归属 | 拿到行号就下结论，跳过 `read` |
| | `read` | 读声明原文 | 只读一行就推断整段语义 |
| | `usage` | 看引擎里实际怎么用 | 用它代替读定义 |
| | `reflected` | UPROPERTY/UFUNCTION 说明符 | 拿它当"这个 API 存在"的证明 |
| | `grep` | 全源码盘扫（**含 cpp**） | 只查索引——关键证据常在 cpp 注释里 |
| `ue_docs` | `search` | 找相关文档页 | 查询词全是常见词时把结果当答案（看 `confidence`） |
| | `read` | 读全文；接受连字符 slug / 整条 url / 文件名 | —— |
| `engine_facts` | `query` | 命中已知高频坑 | 期望它能穷举所有问题 |
| `interface_check` | `asset_path` | 判定接口函数做成事件还是函数图 | 忽略 `trustworthy: false` |

**`trustworthy: false` 表示结论不可信**（缓存比资产旧、或签名没读全）——
这种情况绝不能拿来当依据。加 `collect=true` 会自动重收集。

## 已经踩过的坑（别再犯）

这三条都是**当时说得斩钉截铁、后来被证伪**的：

* ❌「接口函数必须无返回值才是事件候选」——
  真实判定是 `!HasFunctionAnyOutputParameter()`，**任何**输出参数都会挡住转换。
* ❌「函数图占了同名位置，所以变成函数」——那是**结果**不是原因。
* ❌「编辑器没有把接口函数转成事件的路径」——
  只读了 `BlueprintActionDatabase.cpp` 的右键菜单注册，漏了
  `BlueprintEditor.cpp:6604` 明确允许接口函数转事件（UE-85687）。

共同病根：**只读源码的一处，就用绝对语气下结论。**

还有一个纯工具坑：T3D / Python 里看到的引脚 `Direction` 是**图上的线方向**，
不是参数方向，直接读会读反。参数方向由它挂在哪个节点决定——
`FunctionEntry` 上的引脚是输入，`FunctionResult` 上的才是输出。

## 判定依据（可复核）

* `EdGraphSchema_K2.h:915` — `FunctionCanBePlacedAsEvent()` 声明
* `EdGraphSchema_K2.cpp:925-941` — 实现，`return !HasFunctionAnyOutputParameter()`
* `EdGraphSchema_K2.cpp:910-923` — `HasFunctionAnyOutputParameter()`
* `K2Node_FunctionEntry.cpp:447-459` — 入口节点不允许输入引脚
* `BlueprintEditor.cpp:6513-6518` — 输出参数拦住转换时的报错原文
* `BlueprintEditor.cpp:6604-6618` — 接口函数可以转成事件
* `AnimNotifyState.h:37-38` / `AnimInstance.cpp:1682-1697` — 逐帧 NotifyTick

## 没有 MCP 时的等价命令

知识库的 Python 在 `Plugins/KBaseUE/Content/Python/`，命令行入口 `check.cmd`：

```cmd
check.cmd --index symbol FunctionCanBePlacedAsEvent   # 定位 + 行号
check.cmd --index def FunctionCanBePlacedAsEvent      # 读原文
check.cmd --index reflected --specifier BlueprintReadWrite
check.cmd --index grep "some exact string"            # 盘扫，含 cpp
check.cmd --facts "接口函数 为什么不是事件"            # 蒸馏规则（中文可用）
check.cmd --docs search "anim notify state"           # 文档检索
check.cmd --docs read blueprint-interface             # 读全文
check.cmd /Game/Path/BPI_YourInterface                # 资产校验
check.cmd --selftest                                  # 工具链自检（41 项）
```

**自检失败时不要用这套工具的输出下结论。** 它断言的是"能查到已知答案"
（行号必须是 915、owner 必须是 `UEdGraphSchema_K2`），而不是"模块能 import"。

## 依赖

MCP 工具由 `Plugins/ComboMCP` 提供，事实数据由 `Plugins/KBaseUE` 提供。
缺少 `KBaseUE` 时这 4 个工具会返回 `{"error": "KBaseUE module not found", ...}`
——那是**降级**，不是崩溃；此时改用上面的 `check.cmd`，或直接读
`E:\UE_5.7\Engine\Source`。
