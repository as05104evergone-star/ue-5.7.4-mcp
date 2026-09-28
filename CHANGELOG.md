# 更新日志

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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

[1.0.0]: https://github.com/as05104evergone-star/ue-5.7.4-mcp/releases/tag/v1.0.0
