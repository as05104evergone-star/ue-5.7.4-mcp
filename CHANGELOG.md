# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [1.3.0] — 2026-10-14

三件事：**读取范围不再限于蓝图与动画**、**能做基础操作**、**UE 5.6 实机验证并适配**。
全部结论来自一个新建的隔离验证项目（UE 5.6.1），没有碰作者的任何项目。

### 新增

- **`asset`（读任意资产的统一入口）**：按类自动选语义摘要器，工具面从 21 → 24。
  - `Material`：混合/着色模型、**每个输入是常量还是连着哪个表达式**、
    声明的参数及默认值、表达式构成；
  - `MaterialInstanceConstant`：父材质 + 参数覆盖（值缺失时显式说明为什么）；
  - `Texture2D`：尺寸 / Mip / 源格式 / 压缩 / sRGB / LODGroup；
  - `StaticMesh`：材质槽与槽名、包围盒、碰撞类型与预设、Lightmap 设置；
  - `SkeletalMesh`：骨架、材质槽；
  - `World`：已加载关卡、World Partition（runtime/editor hash、HLOD 层）、
    `bIsPartitioned`；
  - 其它任何类：通用视图（剔除导出噪音的属性 + 子对象类名直方图 + 大数组收敛）。
  - `AnimMontage` / `AnimBlueprint` 自动转各自的专用读取器。
- **`help`**：基础操作答疑。10 个主题（install / menu / sync / pie / level / logs /
  remoteexec / actions / readorder / troubleshoot），支持中英混合的自然语言查询
  （"怎么打开 PIE" → `pie`）。数据源是本地 `docs/quickstart.md`，不联网。
- **`editor`（编辑器动作）**：`capabilities` / `pie`（start/stop/simulate/status）/
  `level`（current/list/load/new/save/add）/ `actor`（list/selected）/
  `logs`（tail/pattern/severity/file/category）。
- **`ue/editor_actions.py`**：关卡与 PIE 动作的 UE 侧实现。
- **`ue/asset_read.py`**：上述摘要器。纯数据、不 import `unreal`，所以 MCP 进程与
  编辑器侧共用同一份源码——两边结果不会分裂。
- **`engine/actions.py`**：写操作授权门。**默认关闭**，需 `COMBO_ALLOW_ACTIONS=1`
  或单次 `confirm=true`。
- **`engine/logs.py`**：读 `<项目>/Saved/Logs/*.log`，分级 + 正则 + 类别过滤。
  **不需要编辑器在运行**——排查"编辑器刚崩过"时，能读的只有磁盘上的文件。

### 兼容性（本版的重点）

- **UE 5.6.1 实机验证通过**，与 5.7.4 并列成为"实测"组合。验证项目是新建的
  `E:\UE_5.6\Projects\ComboMCP_Verify56`，**未触碰作者自己的任何项目**。
  验证内容：插件加载、T3D 导出器存在、材质/材质实例/贴图/静态网格/骨骼网格/关卡/
  字体/音效全部导出成功、`sync` 采集、`asset` 离线读取。
- **按项目选引擎。** 原先引擎候选表把 5.7 恒排第一，于是 5.6 项目会被悄悄喂给
  5.7 的导出器。现在先读项目 `.uproject` 的 `EngineAssociation` 再解析引擎根，
  两个项目各用各的引擎，不需要环境变量。
- **多编辑器实例按项目路径匹配**，不再只比项目名。
- `status` 现在回报 `engine_version` / `project_association` / `engine_root`，
  以及写操作策略状态。

### 修复（都是"会安静地给错结果"的那一类）

- **结构体属性此前全是原始字符串。** 普通属性赋值只做了去引号，没有走值解析器，
  于是 `BaseColor=(Constant=...,Expression=...)`、`DefaultInstance=(...)`、
  `Source=(SizeX=32,...)` 都停在字符串形态——**这正是"读不出材质输入图"的直接原因**。
- **带尾逗号的结构体被静默降级成数组/字符串。** UE 普遍写
  `(OpacityMaskClipValue=0.333300,)`，而跨行属性是被空格拼接的，续行还带缩进；
  两条都会让"所有片段都是 `Key=`"的判定失败。改为过滤空片段 + `any()` 判定。
- **二进制载荷不再铺进结果。** `CustomProperties TextureSourceData PayloadSize=4096 ...`
  原先被当成普通属性字符串收下，一张 32×32 贴图就带 4096 字节十六进制，
  把真正有用的字段淹掉。现在只记大小（`<payload: N bytes>`）。
- **缓存键与用户输入不等价导致"刚 sync 完却读不到"。** 缓存里存的是包路径
  `/Game/A/M.M`，人习惯写 `/Game/A/M`。新增 `normalize_asset_path` +
  `read_cached`，所有离线读取统一走它。
- **`sync` 的默认类过滤把材质挡在门外。** 原先默认只发现蓝图与动画类，
  现在**不传 `classes` 就是全部资产**。
- **资产注册表为空时发现会失败。** 新建项目的注册表缓存还没建立，
  `list_assets` 返回空，而 `-run=PythonScript` 这种 commandlet 不会先做全量扫描。
  实测就撞上了（报"范围内没有资产"但 Content 里明明有 .uasset）。现在退回扫
  Content 目录，并在结果里说明走了哪条路。

### 安全边界的变化（重要）

**1.0–1.2 的"结构上没有改的能力"不再成立**，因为本版加入了可执行的编辑器动作
（PIE / 关卡）。取而代之的是一条更明确、可验证的边界：

- **读取类工具仍然只读**：不写入、不编译、不保存、不创建资产。
- **写操作只有 7 个**：`pie.start/stop/simulate`、`level.load/new/save/add`。
- **默认关闭**，需要 `COMBO_ALLOW_ACTIONS=1` 或单次 `confirm=true`；
  未授权时返回带 `_actions_blocked` 的说明，而不是静默失败。
- **两道门**：MCP 侧（`engine/actions.py`）挡模型误调，编辑器侧
  （`ue/editor_actions.py::_guard_write`）挡绕过 MCP 直接经 Remote Execution 送进来的命令。
- **明确不做**：执行任意 console 命令、保存任意资产、创建/修改/删除资产。

### 实测修正的 UE 5.6 API 差异

| 事项 | 5.6 实测结果 | 应对 |
|---|---|---|
| `unreal.log_capture_get` | **不存在**（5.7 才有） | 日志改读磁盘文件，两版行为一致 |
| `LevelEditorSubsystem` 构造 | 直接实例化会**崩进程** | 统一用 `get_editor_subsystem`，且取不到就直接拒绝执行 |
| commandlet 内的编辑器动作 | `EXCEPTION_ACCESS_VIOLATION`（`UnrealEditor-LevelEditor.dll`） | `_editor_ready` 先查编辑器世界，取不到即返回 `_need_editor` |
| `MaterialEditorOnlyData` | 材质的输入引脚与 `ExpressionCollection` 挂在这个**子对象**上 | `_merged_view` 统一合并 `*EditorOnlyData` |
| `WorldPartition` | 在 `World → Level → WorldSettings → WorldPartition` 第 4 层 | 摘要器走 6 层，并读 `bIsPartitioned` |

### 测试

**342 项断言全绿**（新增两组共 139 项）：

| 套件 | 项数 |
|---|---|
| `test_t3d.py` | 60 |
| `test_t3d_real.py` | 54 |
| `test_wiring.py` | 48 |
| `mcp_protocol_test.py` | 41 |
| `test_asset_read.py`（新） | 63 |
| `test_editor_actions.py`（新） | 66 |

`test_asset_read.py` 的夹具是 **UE 5.6.1 真实导出**的 T3D（材质 / 材质实例 /
贴图 / 静态网格 / 骨骼网格 / 关卡 / 字体），因为上面那几条修复**只有真数据才暴露得出来**。
`test_editor_actions.py` 用一个**假的 unreal 模块**（没有任何编辑器子系统）
验证 commandlet 隔离——即"这种情况下必须拒绝，而不是去试"。

## [1.2.0] — 2026-10-02

从"读蓝图"扩到"**读引擎事实**"：新增 4 个知识库工具，并修掉三个会让工具
**安静地给错结果**的缺陷。

### 新增

- **知识库四件套（第 18–21 个 MCP 工具）**，依赖兄弟目录 `Plugins/KBaseUE`
  （**不在本仓库**）：
  - `engine_source`：UE 5.7 全源码索引（28,574 文件 / 726,540 符号）。
    七个动作：`symbol` / `read` / `usage` / `reflected` / `grep` / `files` / `status`。
    用途是**下结论之前先拿到 `file:line`**。
  - `ue_docs`：Epic 官方 5.7 文档镜像（3,468 页 / 索引 63.7 MB）。
  - `engine_facts`：蒸馏的高频坑规则，每条带源码行号。中文查询可用。
  - `interface_check`：在**具体接口资产**上判定函数会做成事件还是函数图，
    并指出是哪个参数卡住的。
- **`skills/ue57-knowledge-base/`**：给 agent 用的技能包。它把"先取证据再下结论"
  写成**硬规则**，并记录了三次说错话的具体内容与病根。
- **`tools/mcp_probe.py`**：绕开 MCP 客户端直接驱动 stdio 协议，拿原始响应和
  `error.data` 里的 traceback。客户端只回一句 `internal error` 时的唯一出路。

### 修复

- **stdio 编码：MCP 服务器进程继承系统代码页（中文机器是 GBK）**，产生三个
  看起来互不相关的症状：中文查询 0 命中、中文输出乱码、
  **`interface_check` 直接 `-32603 internal error`**（结果里有一个 GBK 编不出的
  字符时 `sys.stdout.write` 抛异常，整条响应消失，而 traceback 只留在服务器的
  `error.data` 里）。
  修法：进程启动时把三个标准流自己钉成 UTF-8，**不依赖环境变量**——环境变量
  决定行为的东西，迟早会在别人机器上出问题。`_emit` 另加兜底：写不出去就退回
  ASCII 转义，一个字符不该让整个回答消失。
- **KBaseUE 模块按 mtime 热重载。** 原先只查 `sys.modules`，模块一旦加载就永不
  重载，改完 `docs_mirror.py` 必须重启整个会话。加载失败时会把半成品从
  `sys.modules` 移除，否则后续调用只报 `AttributeError`，看不出真正原因是
  "上次加载就失败了"。
- **工具数量写死导致的假失败（第三次复发）。** 这个数字被复制在四处
  （`test_wiring.py`、`mcp_protocol_test.py`、`README.md`、`README.en.md`），
  每次加工具都有人漏改；上一轮它让 `mcp_protocol_test.py` 长期处于"失败但没人看"
  的状态，**正好错过了真正的编码 bug**。现在职责分开：精确清单契约归
  `mcp_protocol_test.py`（按环境区分核心 17 与可选 4），`test_wiring.py` 只断言
  "不少于核心 17"。

### 兼容性

- **4 个新工具依赖可选的 `KBaseUE`。** 没有它时它们返回
  `{"error": "KBaseUE module not found", ...}`——**降级，不是崩溃**，其余 17 个
  工具与协议层不受影响。`mcp_protocol_test.py` 第 9 节专门验证这一点，
  所以本仓库在**没有** KBaseUE 的环境下测试仍然全绿。
- 注意区分两件事：这 4 个工具是**静态注册**的——`tools/list` 永远返回 21 个，
  KBaseUE 在不在都一样；它只决定调用时给结果还是给结构化错误。
  第一版把"是否有 KBaseUE"也算进了期望的工具数，于是**在新克隆的仓库里必然失败**
  （21 ≠ 17）。问题不是靠读代码发现的，是靠**真的克隆一份到临时目录跑测试**发现的。

### 测试

201 项断言（`test_t3d` 60 / `test_t3d_real` 54 / `test_wiring` 46 /
`mcp_protocol_test` 41），全绿。

## [1.1.0] — 2026-09-30

补上静态读取够不到的那一层：**运行时**。

### 新增

- **`pie_state`（第 17 个 MCP 工具）**：读取正在运行的 PIE 世界。静态读取只看得见
  磁盘资产与编辑器世界，因此无法区分**"图连对了但运行时没执行"**和**"连线本身错了"**——
  而这两者的修法完全相反。它读武器 Actor 当前挂载的插槽名（判断"在手上还是背上"的
  唯一可靠依据）、战斗组件的运行时变量、骨架插槽是否存在、Mesh 当前在播哪个 montage，
  末尾给出 `verdict` 结论。没有 PIE 时返回 `pie_running: false`（这不是错误）。
- **`ue/pie_state.py`**：上述能力的 UE 侧实现，只读。文件末尾记录了一张**实测的 API 可用性表**。
- **`tools/call_tool.py`**：直接调工具层（`python tools/call_tool.py pie_state`），
  绕开 Windows PowerShell 会吃掉 JSON 引号的问题。原 `--selftest --args` 在 pwsh 下基本没法用。

### 实测修正的四个 API 陷阱

写运行时读取时，这几个名字看起来都该存在，但**在 UE 5.7.4 的 Python 里都没有**——
调用会直接 `AttributeError`：

| 不存在的 | 正确做法 |
|---|---|
| `Actor.get_attach_parent()` | 经 `root_component.get_attach_parent()` 间接拿 |
| `SceneComponent.get_socket_name()` | 用 `Actor.get_attach_parent_socket_name()` |
| `SkeletalMeshComponent.get_active_montages()` / `get_current_active_montage()` | 读 Mesh 的 `anim_montage` 属性 |
| `unreal.find_all_objects()` / `find_all_objects_of_class()` | 用 `EditorActorSubsystem.get_all_level_actors()` 反查 |

拿 PIE 世界要走 `UnrealEditorSubsystem.get_game_world()`；
`EditorLevelLibrary.get_editor_world()` 已废弃，而且给的是**编辑器世界**，不是 game world。

### 修正

- **`mcp_protocol_test.py` 的两处假失败**（此前的写法会让"环境更好"时报错）：
  - 工具数量与工具名集合写死，加工具即失败 → 已随本次更新，并补了一条"必须含 `pie_state`"的断言。
  - `status` 断言写死 `isError == True`（即假设编辑器**没**连上）。编辑器连着时命令会成功，
    于是作者机器上反而失败。现在改为只约束"无论在线与否 payload 结构都得对"：
    在线必须有 `engine_version`，离线必须有 `error` 且指向编辑器通道。
- `test_wiring.py` 的工具数量断言同步更新，并注明**改这个数字时必须重新想一遍**
  "这个工具值得占用每次请求的 token 吗"。

### 测试

离线断言总数 185 项（`test_t3d` 60 / `test_t3d_real` 54 / `test_wiring` 46 / `mcp_protocol_test` 25），全绿。

## [1.0.0] — 2026-09-28

首个公开版本。

### 新增

- **MCP 服务（16 个工具）**：`status` `sync` `project_map` `search` `find_refs` `index`
  `class_summary` `graph_overview` `flow` `node_detail` `montage` `anim_asset`
  `anim_calls` `diagnose` `audit` `reflect`。零依赖 JSON-RPC over stdio，
  声明 `2025-06-18` / `2025-03-26` / `2024-11-05`。
- **T3D 导出与解析主路径**：蓝图图结构、Montage 时序、AnimBP 状态机 → 可离线解析的紧凑文本。
  导出走 `UnrealEditor-Cmd -run=PythonScript` 另起进程，编辑器开着也能跑。
- **`flow format=dsl`**：执行逻辑的文本化渲染，比 JSON 省一个数量级 token。
- **`diagnose`**：连招诊断——对照蓝图调用点与动画时序，输出问题清单。
- **纯数据解析层**：`ue/t3d.py`、`ue/graph_ir.py`、`ue/montage_t3d.py`、`ue/anim_bp_t3d.py`
  不 import `unreal`，因此 MCP 进程无需编辑器即可工作。
- **编辑器菜单**：Tools → ComboMCP（显示状态 / 列出已缓存资产 / 打印 MCP 客户端配置）。
- **`check.cmd` 一键自检**与 `probe_plugin_load.py` 插件验收探针。

### 已知限制

- **只读**：不写资产、不编译、不保存、不创建节点。这是刻意的安全边界，不是能力缺失。
- **实测仅 UE 5.7.4 + Windows + DeepSeek Harness**；其余组合理论可用但未验证。
- 工具面刻意精简到 16 个——工具定义会进入**每一次**模型请求。

### 实测修正的三个 T3D 格式陷阱

1. 文件是 UTF-8 **with BOM**——不清 BOM 会让首个根对象丢失，整棵对象树塌成多个根。
2. 导出分**两遍**：第二遍的 `Begin Object` 没有 `Class=`，而 `LinkedTo` 只出现在第二遍——
   必须按 `ExportPath` 合并，否则类名全是 `?`、连线全丢。
3. `MemberParent` 是**完整对象路径**（`"/Script/CoreUObject.Class'/Script/Engine.Actor'"`），
   直接取会得到 `Class` 而不是 `Actor`。

[1.3.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.3.0
[1.2.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.2.0
[1.1.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.1.0
[1.0.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.0.0
