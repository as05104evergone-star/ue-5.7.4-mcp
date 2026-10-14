# ComboMCP

**把 Unreal Engine 的资产，读成 AI 能真正理解的紧凑文本。**

[English](README.en.md) | **中文**

一个 MCP 服务：把蓝图图结构、Montage 时序、AnimBP 状态机、**材质/贴图/网格/关卡**
导出成可离线解析的 T3D 文本，让模型能回答"为什么连招接不上""这段逻辑到底怎么走"
"这个材质的粗糙度是从哪来的"这类问题——并且给出**带证据**的答案。

它还能做**基础操作**：读日志、查关卡、控制 PIE、开关卡。这类动作走编辑器进程，
**写操作默认关闭**，见下面的[安全边界](#安全边界)。

> **读取类工具只读。** 不写资产、不编译、不保存、不创建节点。
> 唯一会改状态的是显式授权的编辑器动作（7 个），且默认被挡住。

---

## 兼容性（先看这一节）

| 组件 | 状态 |
|---|---|
| **Unreal Engine 5.7.4** | ✅ **实测** |
| **Unreal Engine 5.6.1** | ✅ **实测**（在一个新建的隔离验证项目上跑通：插件加载、八类资产 T3D 导出、`sync` 采集、`asset` 离线读取、日志读取、写操作授权门） |
| UE 5.0 – 5.5 | ⚠️ **未实测**。所依赖的 API（`ToolMenuEntryScript`、`PluginBlueprintLibrary`、`unreal.uclass`、`get_editor_subsystem`）都是 UE 5.0+ 引入，理论可用 |
| UE 4.x | ❌ **不支持**。菜单 API 与引脚序列化格式都不同 |
| **Windows** | ✅ **实测**。`.cmd` 脚本与引擎路径推导都按 Windows 验证过 |
| Linux / macOS | ⚠️ **未实测**。Python 侧已改为按平台推导引擎路径（不再写死 `Win64`），但 `.cmd` 脚本需你自行执行等价命令 |
| **DeepSeek Harness** | ✅ **实测** |
| 其他 MCP 客户端 | ⚠️ **未实测**。协议是标准 MCP stdio（JSON-RPC 2.0），声明支持 `2025-06-18` / `2025-03-26` / `2024-11-05`；Claude Desktop / Cursor / Cline 等的配置结构相同，但没在 DSH 之外跑过 |

**多引擎混装不用配环境变量。** 插件按**项目自己的 `.uproject` 里的
`EngineAssociation`** 决定用哪个引擎：5.6 的项目用 5.6 的 `UnrealEditor-Cmd`
去采集，5.7 的项目用 5.7。`status` 会回报解析结果（`project_association` /
`engine_root` / `engine_version`），方便你确认没搞错。

**它不需要**：Node.js、系统 Python、编译工具链、C++ 插件、任何第三方 Python 包。

**它需要**：UE 自带的 Python（`Engine/Binaries/ThirdParty/Python3/`，UE 5.x 都自带）。

编辑器动作（关卡 / PIE / Actor）另需开启 Remote Execution——该开关默认关闭，
见 [远程执行开关](#编辑器动作需要打开-remote-execution)。

---

## 同类项目与定位差异

数据取于 2026-09-28（`gh api` 实时读取，非估计）：

| 项目 | ★ | 实现方式 | 需编译 | 需编辑器常开 | 能编辑资产 |
|---|---|---|---|---|---|
| [mirno-ehf/ue5-mcp](https://github.com/mirno-ehf/ue5-mcp) | 75 | C++ 插件 + 本地 HTTP + MCP wrapper | 是 | 是（关闭时可转无头） | 是 |
| [ZiggyMar/unreal-mcp](https://github.com/ZiggyMar/unreal-mcp) | 42 | C++ 编辑器插件 + Node/TS 服务 | 是 | 是 | 是 |
| [cutehusky/ue5-mcp](https://github.com/cutehusky/ue5-mcp) | 13 | C++ 插件暴露 Web API + FastMCP | 是 | 是 | 是 |
| [Cawb07/ue5-mcp](https://github.com/Cawb07/ue5-mcp) | 1 | C++ | 是 | 是 | 是 |
| Epic 官方 Unreal MCP | — | 引擎内置 | 否 | 是 | 是 |
| **ComboMCP** | — | **纯 Python，`"Modules": []`** | **否** | **否** | **否（刻意只读）** |

> Epic 官方的 Unreal MCP 从 **UE 5.8** 起才有。这一点在 5.7.4 上验证过：引擎插件目录里
> 没有任何 MCP 相关插件。

### 我们更适合什么

* **你只想"读懂 + 答疑"，不想让 AI 碰你的资产。** 只读是刻意的安全边界，不是能力缺失——
  对"这个连招为什么接不上"这类问题，不可写恰好消除了风险。
* **你不想装工具链。** 那几个方案都要 Visual Studio 编译 C++ 插件，多半还要 Node.js，
  或者用 FastMCP 这类需要 `pip install` 的依赖。本插件零编译、零第三方依赖。
* **你希望编辑器没开时也能问。** 主力读取走本地 T3D 缓存，编辑器开不开都一样。
* **你在 UE 5.7。**

### 我们不适合什么

* **你想让 AI 直接改蓝图。** 那需要 [ZiggyMar/unreal-mcp](https://github.com/ZiggyMar/unreal-mcp)
  或 [mirno-ehf/ue5-mcp](https://github.com/mirno-ehf/ue5-mcp)——它们能建节点、连线、改默认值。
  本插件**没有**这个能力，也不打算有。
* **你需要覆盖 5.6 或 5.8。** ZiggyMar 明确支持 5.6/5.8；本插件只在 **5.7.4** 上实测过。
* **你需要大量现成工具。** ZiggyMar 有 101 个工具，本插件 24 个（刻意的：工具定义会进入
  每一次模型请求）。

关键决策是**不把 Remote Execution 当主路径**：那个开关（`bRemoteExecution`）默认关闭，
且没有命令行参数、没有 console 命令，只能靠人在编辑器设置里勾。把读取主路径建在一个
需要人工开关的通道上不划算——`UnrealEditor-Cmd -run=PythonScript` 没有这个前提。

---

## 安装

### 1. 把插件放进项目

把整个 `ComboMCP` 文件夹复制到：

```
<你的UE项目>/Plugins/ComboMCP/
```

不需要修改 `.uproject`——`.uplugin` 里 `EnabledByDefault: true` 会自行生效。
插件目录结构固定为 `<项目>/Plugins/ComboMCP`，代码据此反推项目根，因此**换项目只需复制**。

### 2. 重启编辑器

插件只在**启动时**加载。重启后在 **Edit → Plugins** 里应能看到 **Combo Blueprint MCP**，
菜单出现在 **Tools → ComboMCP**。

`.uplugin` 里的一行依赖声明顺带解决了一件事：

```json
"Plugins": [{ "Name": "PythonScriptPlugin", "Enabled": true }]
```

它让 PythonScriptPlugin **自动启用**——所以**不需要** `-EnablePlugins=PythonScriptPlugin`，
也不需要在编辑器里勾任何选项。

### 3. 采集资产缓存

读取的主力是本地 T3D 缓存。在 MCP 客户端里调用 `sync` 工具，给一个 `scope`：

```json
{ "scope": "/Game", "limit": 200 }
```

**不传 `classes` 就是全部资产**（蓝图 / 动画 / 材质 / 贴图 / 网格 / 关卡都包含）。
要缩小范围再传，例如 `"classes": ["Blueprint", "Material", "Texture2D"]`。

它会以 `UnrealEditor-Cmd -run=PythonScript` **另起一个进程**完成导出——
**编辑器开着也能跑**，两者互不干扰（实测：编辑器与 commandlet 两个实例并存）。

> 新建项目里资产注册表缓存可能还没建立，`list_assets` 会返回空。这种情况插件会
> **退回扫 `Content/` 目录**发现资产，并在结果里说明走了哪条路（`discovered_via`）。

### 4. 接进 MCP 客户端

标准配置形态（把两个路径换成你自己的）：

```json
{
  "mcpServers": {
    "combo": {
      "command": "<引擎>/Engine/Binaries/ThirdParty/Python3/Win64/python.exe",
      "args": ["<项目>/Plugins/ComboMCP/mcpserver/server.py"]
    }
  }
}
```

> 插件菜单 **Tools → ComboMCP → 显示 MCP 客户端配置** 会按当前机器直接打印这段，
> 省得手抄路径。

DeepSeek Harness 用 Cordis 层挂载，见 [`docs/dsh-setup.md`](docs/dsh-setup.md)。

---

## 用法

### 工具清单（24 个，其中 4 个需要可选的 KBaseUE）

工具面刻意精简：工具定义会进入**每一次**模型请求。

| 工具 | 用途 |
|---|---|
| `status` | 引擎 / 索引 / 缓存 / 写策略状态。任何连接类报错先调它 |
| `sync` | 采集资产到 T3D 缓存（走 headless commandlet）。**不传 `classes` 就是全部资产** |
| `project_map` | 项目资产地图：按类统计并列出蓝图 |
| `search` | 按名字找资产 |
| `find_refs` | 资产的引用 / 依赖关系 |
| `index` | 蓝图索引：查函数调用者、变量读写者 |
| `class_summary` | 类里有什么（变量 / 函数 / 事件 / 组件 / 图清单） |
| `graph_overview` | 某个图有哪些节点、入口在哪 |
| `flow` | **读执行逻辑**（默认 `format=dsl`，比 JSON 省一个数量级 token） |
| `node_detail` | 单节点的引脚与默认值 |
| `montage` | Montage 的段落 / 通知 / 时间轴 |
| `anim_asset` | 动画序列 / 混合空间 / PoseSearch 数据库 |
| `anim_calls` | 蓝图里所有 Montage 相关调用 |
| `diagnose` | **连招诊断**：对照蓝图调用点与动画时序给出问题清单 |
| `audit` | 节点判活分析（需要真实连线，默认走 T3D） |
| `reflect` | 兜底：直接看引擎反射暴露了什么 |
| `pie_state` | **运行时状态**：读正在跑的 PIE 世界（需编辑器在 Play） |
| **`asset`** | **读任意资产**：材质 / 材质实例 / 贴图 / 静态网格 / 骨骼网格 / 关卡 / 其它任何类 |
| **`help`** | **基础操作答疑**：10 个主题，支持中英混合的自然语言查询 |
| **`editor`** | **编辑器动作**：`capabilities` / `pie` / `level` / `actor` / `logs` |

#### 三个新工具能回答什么

**`asset`** —— 读取范围不再限于蓝图与动画。按类自动换摘要器：

| 资产类 | 读得到 |
|---|---|
| `Material` | 混合 / 着色模型、**每个输入是常量还是连着哪个表达式**、声明的参数及默认值、表达式构成 |
| `MaterialInstanceConstant` | 父材质、参数覆盖、属性覆盖 |
| `Texture2D` | 尺寸、Mip、源格式、压缩设置、sRGB、LODGroup |
| `StaticMesh` | 材质槽与槽名、包围盒、碰撞类型与预设、Lightmap 设置 |
| `SkeletalMesh` | 骨架、材质槽 |
| `World`（关卡） | 已加载关卡、World Partition 配置、是否分区关卡 |
| 其它任何类 | 通用视图：有效属性（已剔除导出噪音）+ 子对象类名直方图 |

**读不到的东西会明说**：网格顶点、贴图像素、关卡里 Actor 的逐个序列化。
工具不会给一个空值让你误以为"这个资产是空的"。

**`help`** —— 主题：`install` `menu` `sync` `pie` `level` `logs` `remoteexec`
`actions` `readorder` `troubleshoot`。可以直接问"怎么装插件""菜单在哪""日志怎么看"。

**`editor`** —— 编辑器动作。写操作默认关闭，见[安全边界](#安全边界)。

#### 可选：知识库四件套（需要同级的 `KBaseUE` 插件）

这 4 个工具回答的是**"这条 UE 语义到底是不是这样"，并要求 `file:line` 证据**。
它们依赖兄弟目录 `Plugins/KBaseUE`（**不在本仓库里**）。

| 工具 | 用途 |
|---|---|
| `engine_source` | UE 5.7 全源码索引：`symbol` / `read` / `usage` / `reflected` / `grep` / `files` / `status` |
| `ue_docs` | Epic 官方 5.7 文档镜像：`status` / `search` / `read` / `version_check` |
| `engine_facts` | 蒸馏的高频坑规则，每条带源码行号 |
| `interface_check` | 具体蓝图接口资产上验证：这个函数会做成**事件**还是**函数图** |

**没有 KBaseUE 时它们会返回 `{"error": "KBaseUE module not found", ...}`——**
那是**降级，不是崩溃**，其余 20 个工具照常工作，协议层不受影响
（`tools/mcp_protocol_test.py` 第 9 节专门验证这一点）。

### 推荐读取顺序（由粗到细，避免一次拉爆上下文）

1. `status` — 确认引擎、通道、索引与写策略状态
2. `project_map` — 建立整体认知
3. `asset` — **读单个资产**。这是现在读任意资产的入口：材质 / 材质实例 / 贴图 /
   静态网格 / 骨骼网格 / 关卡 / 数据表 / Chooser / PoseSearch / 蓝图……同一个工具
   按类自动换摘要器
4. 蓝图往下钻：`class_summary` → `graph_overview` → `flow`（`format=dsl`）
   → 只在定位到具体节点时才用 `node_detail`
5. 动画：`montage`（时序）、`anim_asset`（AnimBP 状态机 / 序列 / 混合空间）、
   `anim_calls`（蓝图里所有 Montage 调用）
6. `diagnose` — 连招系统综合体检

### 两条取数路径

读取类工具默认 `source="auto"` 自动择路，结果里的 `source` 字段会告诉你实际走了哪条。
**缓存命中时直接走本地解析**——毫秒级返回，编辑器开不开都一样。

**`reflect`（引擎反射）** 快，但读不到图逻辑。原因有两层：

```cpp
// Engine/Source/Runtime/Engine/Classes/EdGraph/EdGraphNode.h
TArray<UEdGraphPin*> Pins;          // ← 连 UPROPERTY() 都没有
```

而即使有 `UPROPERTY()` 也不够——Python 的 `get_editor_property` 走
`PropertyAccessUtil::CanGetPropertyValue`，它要求属性带
`CPF_Edit` / `CPF_BlueprintVisible` / `CPF_BlueprintAssignable`：

```cpp
// CoreUObject/Private/UObject/PropertyAccessUtil.cpp
if (!InProp->HasAnyPropertyFlags(CPF_Edit | CPF_BlueprintVisible | CPF_BlueprintAssignable))
    return FPropertyAccessResult::PermissionDenied(...);
```

裸 `UPROPERTY()` 三个标记都没有，照样被拒。`UEdGraph::Nodes`、
`UAnimMontage::CompositeSections`、`UAnimMontage::SlotAnimTracks`、`UBlueprint::NewVariables`
都属于这一类——**在 Python 里一律 `PermissionDenied`**。

**`t3d`（引擎自己的文本导出）** 能读到反射读不到的东西，因为导出的过滤条件更宽松：
`FProperty::ShouldPort` 只在 `PPF_PropertyWindow` 下才要求 `CPF_Edit`
（`Property.cpp`），所以裸 `UPROPERTY()` 的成员在 T3D 里照样出现。

**所以 T3D 不是退而求其次，它是能力更强的那条路。**

### 静态读取的边界：`pie_state`

`ue/` 下其余模块读的都是**磁盘上的资产**（T3D 缓存）或**编辑器世界**。有一类问题它们
永远答不了：**图连对了、审计也干净，运行起来却不对。**

这不是假设出来的场景。本工具的由来就是一个真实 case：某角色的装备武器链路——
查表、生成 Actor、`AttachToComponent` 全都接对，蓝图编译无错，`audit` 也查不出问题，
但按装备键武器就是不动。静态读取无法区分**"连对了但没执行"**和**"连线本身错了"**，
而这两者的修法完全相反。

`pie_state` 补的就是这一段。它连进正在运行的 PIE 世界，读**只有运行时才存在的事实**：

| 读什么 | 为什么这是关键 |
|---|---|
| 武器 Actor 当前挂载的插槽名 | 判断"在手上还是背上"的**唯一**可靠依据 |
| 战斗组件的运行时变量 | `CurrentWeapon` / 装备行 / 防连点标志的**真实值**，不是默认值 |
| 骨架插槽是否存在 | 插槽不存在会让 `AttachToComponent` **静默退化到 mesh 根节点**——蓝图里完全看不出来 |
| Mesh 当前在播哪个 montage | 区分"蒙太奇没播"和"播了但回调没来" |
| `verdict` | 把上面的事实压成"最可能是什么问题"，省得自己推 |

三条使用约定：

* **需要编辑器正在 Play。** 没有 PIE 时返回 `pie_running: false` 并给出提示——
  这是正常状态，不是错误。
* **跑在运行时，不是编译期。** 蓝图改了**没保存**也能读到最新行为（读的是内存里的类）。
* **只读。** 不改运行时状态、不调函数、不 spawn。和其余模块同一条安全边界。

关于防连点标志的一个实战技巧：**按完键别急着重置**。卡住的状态本身就有信息量——
`bWeaponBusy` 停在 `true` 说明那次操作的**回调根本没触发**（正常路径会在回调里把它复位），
这一条就能把问题域从"插槽/动画"直接切到"回调/事件链"。

### T3D 路径的三个格式陷阱（实测所得）

解析 T3D 时这三条会让你**静默产出错误结果**，值得单独记住：

1. **文件是 UTF-8 with BOM**。Python 3 的 `\s` 不匹配 `\ufeff`，若不清 BOM，
   第一行的根对象会丢失，整个对象树塌成多个根。
2. **导出分两遍**。第二遍的 `Begin Object` **没有 `Class=`**，而 `LinkedTo` 连线
   只出现在第二遍。必须按 `ExportPath` 合并，否则类名全是 `?`、连线全丢。
3. **`MemberParent` 是完整对象路径**，形如
   `"/Script/CoreUObject.Class'/Script/Engine.Actor'"`，直接取会得到 `Class` 而不是 `Actor`。

### 架构：为什么要分两层

```
┌─ 编辑器内（ue/）──── 反射读取 + T3D 导出 ────┐
│  只在需要时被 commandlet 拉起                │
└──────────────────────────────────────────────┘
                    ↓  T3D 文本落到 cache/
┌─ MCP 进程（engine/ + mcpserver/）───────────┐
│  纯数据解析（t3d.py / graph_ir.py 不依赖    │
│  unreal），毫秒级，编辑器没开也能用          │
└──────────────────────────────────────────────┘
```

`ue/t3d.py`、`ue/graph_ir.py`、`ue/montage_t3d.py`、`ue/anim_bp_t3d.py` 是**纯数据模块**，
不 import `unreal`，因此能被装进 MCP 进程直接跑——这也是"不需要编辑器"的根本原因。

---

## 目录结构

```
Plugins/ComboMCP/
├── ComboMCP.uplugin        UE 插件描述（"Modules": [] —— 不含 C++，不编译）
├── Content/Python/         UE 插件的 Python 约定目录，引擎自动挂进 sys.path
│   ├── init_unreal.py      启动钩子：写加载证据 + 注册菜单
│   └── combomcp_editor.py  Tools → ComboMCP 菜单
├── start_editor.cmd        启动编辑器（不加任何特殊参数）
├── check.cmd               一键自检（前两步不需要编辑器）
├── ue/                     编辑器内执行的只读脚本（由 bridge 打包送入）
│   ├── t3d.py              【纯数据】T3D 文本 -> IR 解析器
│   ├── graph_ir.py         【纯数据】IR 定义 + 执行流遍历 + DSL 渲染
│   ├── montage_t3d.py      【纯数据】Montage 时间轴与窗口诊断
│   ├── anim_bp_t3d.py      【纯数据】AnimBP 状态机读取
│   ├── asset_read.py       【纯数据】任意资产的语义摘要（材质/贴图/网格/关卡/通用）
│   ├── runtime.py          防御性反射读取
│   ├── t3d_read.py         T3D 导出与读取（UE 侧）
│   ├── domain.py           连招领域诊断规则
│   ├── editor_actions.py   编辑器动作：PIE / 关卡 / Actor（**唯一会改状态的模块**）
│   ├── pie_state.py        PIE 运行时状态读取（唯一需要 game world 的模块）
│   └── dispatch.py         命令分发 + 自动择路
├── engine/                 MCP 侧（普通 Python 进程）
│   ├── offline.py          离线读取：缓存命中时的全部实现
│   ├── collector.py        commandlet 采集器（含资产注册表为空时的扫盘兜底）
│   ├── bridge.py           Remote Execution 桥接（可选路径）+ 按 .uproject 选引擎
│   ├── pure.py             把纯数据模块装进 MCP 进程
│   ├── actions.py          写操作授权门（默认关闭）
│   ├── logs.py             日志文件读取（不需要编辑器）
│   └── search.py           索引磁盘缓存
├── mcpserver/              MCP 协议层（零依赖）
│   ├── server.py           JSON-RPC over stdio
│   └── tools.py            工具定义与实现
├── tools/                  测试与诊断脚本（见下表）
├── skills/                 给 agent 用的技能包（SKILL.md，非 MCP 必需）
│   └── ue57-knowledge-base/  知识库四件套的使用契约：先取 file:line 再下结论
├── cache/                  生成物：T3D 缓存（**不入库**，可随时删）
└── docs/
    └── quickstart.md       操作手册 —— `help` 工具的数据源（按 ## 小节切分）
```

### `tools/` 里哪些是通用的

| 脚本 | 通用？ | 说明 |
|---|---|---|
| `test_t3d.py`（60 项） | ✅ | 解析器单测，用合成样本 |
| `test_t3d_real.py`（54 项） | ✅ | 真实引擎导出回归，夹具随仓库提供 |
| `test_wiring.py`（48 项） | ✅ | 模块装载 + 命令表接线 |
| `mcp_protocol_test.py`（41 项） | ✅ | MCP 协议端到端。含 stdio 编码回归（**故意不设** `PYTHONIOENCODING`）与"可选依赖缺失时降级"两节 |
| `test_asset_read.py`（73 项） | ✅ | 资产摘要器回归，夹具是 **UE 5.6.1 真实导出**的 T3D（材质/材质实例/贴图/网格/关卡/字体） |
| `test_editor_actions.py`（66 项） | ✅ | commandlet 隔离（用假 unreal）、写操作授权门、日志解析。**不需要编辑器** |
| `mcp_probe.py` | ✅ | 绕开 MCP 客户端直接走 stdio 协议，拿原始响应与 `error.data` 里的 traceback。客户端只给一句 `internal error` 时用它 |
| `probe_plugin_load.py` | ✅ | 插件验收：是否被引擎计入启用列表、菜单 API 是否可用 |
| `probe_remote.py` | ✅ | Remote Execution 通道探测 |
| `inspect_macro.py` | ✅ | 读任意引擎宏的内部实现（判断宏有无副作用） |
| `call_tool.py` | ✅ | 直接调工具层（`python tools/call_tool.py pie_state`），绕开 Windows 命令行吃 JSON 引号的问题 |
| `pie_key.py` | ⚠️ | 只打印 `pie_state` 的几个关键字段，便于 PIE 中快速轮询；组件路径是作者项目的，改一行即可 |
| `inspect_var_refs.py` | ⚠️ | 默认资产是作者项目的，传参即可用于任何资产 |
| `dump_combo_graph.py` | ⚠️ | 同上 |
| `test_e2e_offline.py`（23 项） | ⚠️ | 资产列表是作者项目的，需先 `sync` 过同名资产才能跑；否则会失败 |
| `verify_all.py` / `wait_and_verify.py` | ❌ | 作者项目的端到端验证，需按你的资产改写 |

---

## 测试与验证

**不需要编辑器**（共 342 项断言）：

```powershell
$py = "<引擎>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
$root = "<项目>\Plugins\ComboMCP"

& $py "$root\tools\test_t3d.py"             # 60 项
& $py "$root\tools\test_t3d_real.py"        # 54 项
& $py "$root\tools\test_wiring.py"          # 48 项
& $py "$root\tools\mcp_protocol_test.py"    # 41 项
& $py "$root\tools\test_asset_read.py"      # 73 项
& $py "$root\tools\test_editor_actions.py"  # 66 项
```

两组新测试的夹具与手段值得说明，因为它们的价值全在"用真东西"上：

- **`test_asset_read.py`** 的夹具是 **UE 5.6.1 真实导出**的 T3D（材质 / 材质实例 /
  贴图 / 静态网格 / 骨骼网格 / 关卡 / 字体）。1.3.0 修的几条最要命的缺陷
  ——材质数据挂在 `MaterialEditorOnlyData` 子对象上、贴图十六进制载荷、
  结构体尾逗号——**合成样本一条都测不出来**。
- **`test_editor_actions.py`** 用一个**假的 `unreal` 模块**（不提供任何编辑器子系统）
  来验证 commandlet 隔离：这种情况下每个动作都必须返回 `_need_editor`，
  而不是去试——因为实测"去试"会让进程以 `EXCEPTION_ACCESS_VIOLATION` 崩掉。

**需要引擎、但不需要编辑器在运行**（插件验收）：

```powershell
# 注意：UE 会在空格处截断 -Script 的值，脚本要先复制到无空格路径
Copy-Item "$root\tools\probe_plugin_load.py" "C:\Temp\probe_plugin_load.py" -Force
& "<引擎>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
    "<项目>\<项目>.uproject" `
    -run=PythonScript -Script=C:\Temp\probe_plugin_load.py `
    -unattended -nosplash -nop4 -nullrhi -stdout
```

结果写入 `cache/plugin/probe_result.json`，验证：插件是否被引擎计入启用列表、
`Content/Python` 是否进了 `sys.path`、菜单 API 调用链是否可用。

---

## 故障排查

**插件在 Edit → Plugins 里找不到**

插件只在**启动时**加载，装上后必须重启编辑器。仍不行就确认 `ComboMCP.uplugin`
确实在 `<项目>/Plugins/ComboMCP/` 下，且 `EnabledByDefault` 为 `true`。

**想知道插件到底有没有被引擎加载**

看 `cache/plugin/loaded.json`（引擎每次启动都会重写它），或运行 `check.cmd`。

**`sync` 很慢**

每次都要冷启动一次引擎并加载项目。**一次多给几个资产**，不要一个资产调一次。
已缓存的默认跳过，`force=true` 才重导。

**改了蓝图但读到的还是旧逻辑**

T3D 缓存读的是磁盘上的 `.uasset`。确认你**已保存**（Ctrl+S），然后再 `sync` 一次。
反过来说：编辑器里未保存的改动，commandlet 是看不到的——那种情况只能走
Remote Execution（见下条）。

**`status` 提示 Remote Execution 无响应**

先判断这**是否影响你**：读蓝图的主力是 T3D 缓存，不需要编辑器在运行。
只有要读**未保存**的改动时才需要该通道。若确实需要：

1. 编辑器设置里勾 **Project Settings → Plugins → Python → Enable Remote Execution?**
   （默认关闭；这是唯一开关，没有命令行参数，改完即生效）
2. 确认编辑器已完成加载
3. 同时开多个引擎实例时，插件会优先选打开了本项目的那个

**某个字段读出来是空的，`_diag` 里有 miss**

说明该 `UPROPERTY` 在当前引擎版本下没有被 Python 暴露。用 `reflect` 看引擎实际暴露了
哪些属性名。这是设计时就预期的：`UEdGraphPin` 从 struct 改成 UObject 就是一次破坏性
变更，写死的读取代码在版本漂移时必然静默出错。

**结果里出现 `<payload: N bytes>`**

那是二进制载荷（贴图像素、网格 BulkData）的占位标记。工具**刻意不展开**它：
一张 32×32 的贴图就有 4KB 十六进制，铺开会把有用的字段淹掉。

**`editor` 动作返回 `_need_editor`**

说明命令没跑在编辑器进程里，或者编辑器没开 / Remote Execution 没打开。

这类动作有一条硬约束：**在 commandlet 里构造编辑器子系统会让进程直接崩掉**
（实测 `EXCEPTION_ACCESS_VIOLATION`，不是抛异常）。所以工具宁可拒绝执行也不会去试。

**`editor` 动作返回 `_actions_blocked`**

那是写操作授权门在起作用，这是**默认行为**。开启方式见下一节。

---

## 编辑器动作需要打开 Remote Execution

关卡 / PIE / Actor 这三组动作必须在**编辑器进程里**执行，走 UE 自带的 Python
Remote Execution 通道。该通道**默认关闭**：

1. 编辑器里 **Project Settings → Plugins → Python → Enable Remote Execution?** 打勾；
2. 改完**立即生效**，不用重启编辑器；
3. 用 `status` 确认 `bridge.connected` 变成 `true`。

这个开关没有命令行参数、也没有 console 命令，只能在设置界面里勾——这正是本插件
**不把 Remote Execution 当主力读取路径**的原因。读资产的主力是 T3D 缓存，
它不需要编辑器，也不需要这个开关。**`logs` 也不需要**：它直接读磁盘上的日志文件。

连不上时逐项排查：编辑器是否已完成加载、防火墙有没有拦 UDP `239.0.0.1:6766`、
以及（多实例时）插件会优先选**按项目路径匹配**的那个实例。

---

## 安全边界

**读取类工具只读。** 不写入、不编译、不保存、不创建资产。T3D 导出走
`ObjectExporterT3D`，是纯序列化；commandlet 只调用 `unreal.load_asset()` 与导出器。

**写操作只有 7 个，而且默认关闭：**

| 写操作 | 说明 |
|---|---|
| `editor action=pie sub=start` / `stop` / `simulate` | 进入 / 结束 PIE |
| `editor action=level sub=load` / `new` / `save` / `add` | 关卡加载 / 新建 / 保存 / 挂子关卡 |

开启方式（任选其一）：

- **环境变量**（长期生效）：设 `COMBO_ALLOW_ACTIONS=1`，然后重启 MCP 客户端
  （MCP 服务是子进程，环境变量在它启动时读取）。
- **单次授权**：调用时传 `confirm=true`。

未授权时返回带 `_actions_blocked` 的说明，而不是静默失败。

**两道门，不是一个检查。** MCP 侧（`engine/actions.py`）挡住模型误调；编辑器侧
（`ue/editor_actions.py::_guard_write`）挡住有人绕过 MCP、直接把命令经 Remote
Execution 送进编辑器。第二道是必要的——那条通道本身是开放的，只靠外部那层不算数。

**明确不做的事**（不是"暂时没做"，是刻意不做）：

- **不执行任意 console 命令。** 那等于把整个引擎控制台交出去，边界会失控。
- **不保存任意资产。** 只有显式的 `level sub=save`（保存当前关卡，且需要授权）。
- **不创建 / 修改 / 删除资产。**

### 怎么核对"读取真的没写东西"

跑一次 `sync`，然后看 `Content/` 下有没有文件的修改时间晚于采集开始时间。

### 两个会改变编辑器状态的坑

1. **`level sub=load` 会关掉当前 PersistentLevel，而且不提示保存。** 有未保存改动
   就先 `level sub=save`。
2. **`level sub=new` 会立刻存盘**——这是 UE 自身的既有行为，不是本插件加的。
   所以 `new` 要先把 `asset_path` 想清楚。

---

## 许可

[MIT](LICENSE) · 版本记录见 [CHANGELOG.md](CHANGELOG.md)
