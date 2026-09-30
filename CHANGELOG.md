# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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

[1.1.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.1.0
[1.0.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.0.0
