# ComboMCP

**把 Unreal Engine 的蓝图与动画资产，读成 AI 能真正理解的紧凑文本。**

[English](README.en.md) | **中文**

一个只读的 MCP 服务：把蓝图图结构、Montage 时序、AnimBP 状态机导出成可离线解析的
T3D 文本，让模型能回答"为什么连招接不上""这段逻辑到底怎么走"这类问题——并且给出
**带证据**的答案，而不是泛泛而谈。

> **它不修改任何东西。** 不写入资产、不编译、不保存、不创建蓝图节点。
> 全部实现里不存在 `set_editor_property` / `save_asset` / `compile_blueprint` 这类调用。

---

## 兼容性（先看这一节）

| 组件 | 状态 |
|---|---|
| **Unreal Engine 5.7.4** | ✅ **实测**（本项目的全部结论都来自 5.7.4） |
| UE 5.0 – 5.6 | ⚠️ **未实测**。所依赖的 API（`ToolMenuEntryScript`、`PluginBlueprintLibrary`、`unreal.uclass`）都是 UE 5.0+ 引入，理论可用；但 T3D 导出细节可能随小版本变化 |
| UE 4.x | ❌ **不支持**。菜单 API 与引脚序列化格式都不同 |
| **Windows** | ✅ **实测**。`.cmd` 脚本与引擎路径推导都按 Windows 验证过 |
| Linux / macOS | ⚠️ **未实测**。Python 侧已改为按平台推导引擎路径（不再写死 `Win64`），但 `.cmd` 脚本需你自行执行等价命令 |
| **DeepSeek Harness** | ✅ **实测** |
| 其他 MCP 客户端 | ⚠️ **未实测**。协议是标准 MCP stdio（JSON-RPC 2.0），声明支持 `2025-06-18` / `2025-03-26` / `2024-11-05`；Claude Desktop / Cursor / Cline 等的配置结构相同，但没在 DSH 之外跑过 |

**它不需要**：Node.js、系统 Python、编译工具链、C++ 插件、Remote Execution 开关、
任何第三方 Python 包。

**它需要**：UE 自带的 Python（`Engine/Binaries/ThirdParty/Python3/`，UE 5.x 都自带）。

> **一句话**：**UE 5.7.4 + Windows + DSH 是目前唯一被完整验证的组合。**
> 其他组合大概率可用，但请自测——尤其是 T3D 解析器，它对引擎导出格式敏感。

---

## 它为什么这样设计

调研过 GitHub 上 10 个同类项目后做的取舍：

| | 主流方案（C++ 插件 + Node/Python 服务） | **ComboMCP** |
|---|---|---|
| 形态 | C++ 插件 + 外部服务 | UE 插件，但 `"Modules": []`——**不含任何 C++** |
| 需要编译 | 是（VS 工具链） | **否**，纯 Python |
| 需要安装 | Node.js 或 Python + uv | **什么都不用装** |
| 需要改项目 | 往 `.uproject` 加插件声明 | **不用改**，`EnabledByDefault: true` |
| 编辑器没开时 | 不可用 | **主力路径照常可用**（T3D 缓存） |
| 读图准确性 | 引擎反射 | **比反射更全**（见下文"两条取数路径"） |

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

### 3. 采集蓝图缓存

读取的主力是本地 T3D 缓存。在 MCP 客户端里调用 `sync` 工具，给一个 `scope`：

```json
{ "scope": "/Game", "classes": ["Blueprint", "AnimMontage"], "limit": 200 }
```

它会以 `UnrealEditor-Cmd -run=PythonScript` **另起一个进程**完成导出——
**编辑器开着也能跑**，两者互不干扰（实测：编辑器与 commandlet 两个实例并存）。

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

### 工具清单（16 个）

工具面刻意精简：工具定义会进入**每一次**模型请求。

| 工具 | 用途 |
|---|---|
| `status` | 引擎 / 索引 / 缓存状态。任何连接类报错先调它 |
| `sync` | 采集资产到 T3D 缓存（走 headless commandlet） |
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

### 推荐读取顺序（由粗到细，避免一次拉爆上下文）

1. `status` — 确认通道与索引状态
2. `project_map` — 建立整体认知
3. `class_summary` — 看某个类里有什么
4. `graph_overview` — 看某个图有哪些节点、入口在哪
5. `flow`（`format=dsl`）— 读执行逻辑
6. `node_detail` — 只在已定位到具体节点时用

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
│   ├── runtime.py          防御性反射读取
│   ├── t3d_read.py         T3D 导出与读取（UE 侧）
│   ├── domain.py           连招领域诊断规则
│   └── dispatch.py         命令分发 + 自动择路
├── engine/                 MCP 侧（普通 Python 进程）
│   ├── offline.py          离线读取：缓存命中时的全部实现
│   ├── collector.py        commandlet 采集器
│   ├── bridge.py           Remote Execution 桥接（可选路径）
│   ├── pure.py             把纯数据模块装进 MCP 进程
│   └── search.py           索引磁盘缓存
├── mcpserver/              MCP 协议层（零依赖）
│   ├── server.py           JSON-RPC over stdio
│   └── tools.py            工具定义与实现
├── tools/                  测试与诊断脚本（见下表）
├── cache/                  生成物：T3D 缓存（**不入库**，可随时删）
└── docs/
```

### `tools/` 里哪些是通用的

| 脚本 | 通用？ | 说明 |
|---|---|---|
| `test_t3d.py`（60 项） | ✅ | 解析器单测，用合成样本 |
| `test_t3d_real.py`（54 项） | ✅ | 真实引擎导出回归，夹具随仓库提供 |
| `test_wiring.py`（44 项） | ✅ | 模块装载 + 命令表接线 |
| `mcp_protocol_test.py`（20 项） | ✅ | MCP 协议端到端 |
| `probe_plugin_load.py` | ✅ | 插件验收：是否被引擎计入启用列表、菜单 API 是否可用 |
| `probe_remote.py` | ✅ | Remote Execution 通道探测 |
| `inspect_macro.py` | ✅ | 读任意引擎宏的内部实现（判断宏有无副作用） |
| `inspect_var_refs.py` | ⚠️ | 默认资产是作者项目的，传参即可用于任何资产 |
| `dump_combo_graph.py` | ⚠️ | 同上 |
| `test_e2e_offline.py`（23 项） | ⚠️ | 资产列表是作者项目的，需先 `sync` 过同名资产才能跑；否则会失败 |
| `verify_all.py` / `wait_and_verify.py` | ❌ | 作者项目的端到端验证，需按你的资产改写 |

---

## 测试与验证

**不需要编辑器**（共 201 项断言）：

```powershell
$py = "<引擎>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
$root = "<项目>\Plugins\ComboMCP"

& $py "$root\tools\test_t3d.py"           # 60 项
& $py "$root\tools\test_t3d_real.py"      # 54 项
& $py "$root\tools\test_wiring.py"        # 44 项
& $py "$root\tools\mcp_protocol_test.py"  # 20 项
```

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

---

## 只读承诺

它不是"尽量不改"，而是**结构上没有改的能力**：

- 全部实现里不存在 `set_editor_property` / `save_asset` / `compile_blueprint` / `spawn_actor`
- T3D 导出走 `ObjectExporterT3D`，是纯序列化
- commandlet 只调用 `unreal.load_asset()` 与导出器

实测核对方式：跑一次 `sync`，然后看 `Content/` 下有没有文件的修改时间晚于采集开始时间。

---

## 许可

[MIT](LICENSE)
