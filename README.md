# Blueprint Agent Toolkit for Unreal Engine

**让 AI Agent 读懂 UE 项目、调试连招，并在 Unreal Editor 里直接编写蓝图。**

**中文** · [English](README.en.md) · [快速上手](docs/quickstart.md) · [下载 Release](https://github.com/as05104evergone-star/unreal-blueprint-agent-toolkit/releases)

项目原名 **ComboMCP**。这是一套通过 MCP 接入的 UE 开发工具。你描述目标和允许改动的范围，Agent 读取项目中的事实，决定怎么搭逻辑，再由 UE 编辑器模块实际建节点、接引脚、编译和保存。

## 可以做什么

| 任务 | 能拿到的结果 | MCP 工具 |
| --- | --- | --- |
| **直接编写蓝图** | 查看当前 K2 图页、选区和引脚，在同一撤销事务内建节点、改连线、设默认值、局部排版、加注释并编译。复核后可保存并从磁盘回读 | `blueprint_edit` |
| **读懂现有蓝图** | 列出类、变量、函数和图页，展开某个入口的执行链，核对单节点引脚、跨蓝图调用和悬空节点 | `class_summary`、`graph_overview`、`flow`、`node_detail`、`index`、`audit`、`reflect` |
| **诊断战斗连招** | 对照蓝图里的 Montage 调用和动画段落、通知窗口，定位某一段为什么接不上 | `diagnose`、`anim_calls`、`montage`、`anim_asset` |
| **定位动画特效** | 查每条 Notify 当前的挂点与偏移，列出触发帧的骨骼和 Socket，求出目标挂点的位移和旋转偏移 | `fx_notify_map`、`fx_mounts`、`fx_solve_offset` |
| **检查资产与引用** | 搜索项目资产，查依赖和被引用关系，读取材质、贴图、网格、关卡及其他资产的结构摘要，从磁盘采集 T3D 缓存 | `project_map`、`search`、`find_refs`、`asset`、`sync` |
| **检查运行表现与编辑器** | 读取正在运行的 PIE 世界、Pawn、组件状态、当前 Montage 和挂点。还可控制 PIE、操作关卡、查看 Actor 和日志 | `pie_state`、`editor` |
| **排查连接与查用法** | 核对引擎、项目、缓存和写入策略，直接向 Agent 查询安装、写入、PIE 或日志用法 | `status`、`help` |
| **查证 UE 5.7 引擎语义** | 查询源码行号、查官方文档镜像、验证蓝图接口会实现为事件还是函数图。需要同级 `KBaseUE` 插件 | `engine_source`、`ue_docs`、`engine_facts`、`interface_check` |

共提供 **28 个 MCP 工具**，其中 4 个引擎查证工具依赖可选的 `Plugins/KBaseUE`。操作步骤可查 [快速上手](docs/quickstart.md) 或 `help`。仓库附带 [UE 5.7 知识库 Skill](skills/ue57-knowledge-base/SKILL.md)，它提供源码查证顺序和证据要求。

## 让 Agent 直接编写蓝图

在 UE 中打开目标 Blueprint 和 K2 图页，启用 Python Remote Execution，结束 PIE 或模拟。把入口、预期行为、可改范围和保存要求说清楚。

> 用 ComboMCP 在当前图页实现攻击条件。从我选中的 `TryAttack` 事件开始，只有已有的 `bCanAttack` 为 True 时才继续原攻击流程，False 时结束这次执行。只改这段接线和新增节点。新增节点放在入口右侧，加注释「检查攻击条件」。我授权编辑和保存。编译通过、复核连线后保存，告诉我编译和磁盘回读结果。

| 步骤 | Agent 的动作 |
| --- | --- |
| `inspect` | 读取打开的图页，包括尚未保存的节点和连线。定位入口、引脚及当前 `revision` |
| `apply` | 在一批事务里应用节点、接线、默认值和排版。编译并返回诊断 |
| `review` | 在画布上定位改动区域，复查分支、连线及疑似遮挡 |
| `save` | 在任务授权保存时写入当前 Blueprint 包，再从磁盘导出 T3D 核对节点、坐标、注释标题和连线 |

可以把交付要求改成「只读检查」或「改图并编译，保持未保存，等我在画布上复核」。[三种指令示例](docs/quickstart.md#blueprint-edit-prompts)

当前图表写入范围是已有 Blueprint 的 K2 图页。新建蓝图资产、添加变量或函数声明、新建事件入口和删除已有节点在 UE 内完成。[支持的节点与引脚操作](docs/quickstart.md#blueprint_edit)

## 另外两条常用工作流

### 排查连招接不上

1. 用 `diagnose` 对照战斗组件的调用点和 Montage 的 Section、Notify 时间。根据结果的 `evidence` 定位问题。
2. 用 `flow` 读相关事件的执行链，再用 `node_detail` 核对分支条件和具体引脚。
3. 需要确认实战状态时，用 `editor` 开启 PIE，再用 `pie_state` 检查运行中的对象或状态。结束 PIE 后可按授权进入蓝图编辑流程。

### 修正动画特效挂点

1. 用 `fx_notify_map` 看当前通知填写的 Socket 和偏移。
2. 用 `fx_mounts` 查通知帧实际有哪些骨骼和 Socket。
3. 用 `fx_solve_offset` 计算 `SocketName`、`LocationOffset`、`RotationOffset`，在 UE 中填写并目视复核朝向。

## 安装

1. 下载 [Release 源码](https://github.com/as05104evergone-star/unreal-blueprint-agent-toolkit/releases)，解压后把包含 `ComboMCP.uplugin` 的目录命名为 `ComboMCP`，放到 `<UE 项目>/Plugins/ComboMCP/`。从仓库克隆时也使用这个目录名。
2. 为当前引擎和平台构建项目的 **Development Editor** 目标，或使用匹配的 `ComboMCPEditor` 模块二进制。插件会声明并启用 `PythonScriptPlugin`。
3. 重启 UE 后，可在 **Edit → Plugins** 搜索 **Blueprint Agent Toolkit for Unreal Engine**。`v1.4.0` 源码包里的名称为 **Combo Blueprint MCP**。**Tools → ComboMCP → 显示 MCP 客户端配置** 可生成下方配置。
4. 在客户端接入 MCP 后执行 `status`，确认解析到的项目和引擎。读取磁盘资产前，用 `sync` 采集所需的资产。需要实时编辑时开启 Remote Execution。

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

[DeepSeek Harness 配置](docs/dsh-setup.md) · [安装步骤](docs/quickstart.md#install) · [Remote Execution 设置](docs/quickstart.md#remoteexec) · [Windows 自检脚本](check.cmd)

## 离线读取与实时操作

| 路径 | 什么时候用 | 前提 |
| --- | --- | --- |
| **磁盘 T3D 缓存** | 读取已保存的 Blueprint、Montage、AnimBP 和其他资产。`sync` 按对象路径或范围采集，读取工具本地解析 | `sync` 使用 `UnrealEditor-Cmd` 采集。在缓存命中时可关着编辑器读取 |
| **实时编辑器** | 读取未保存的图页，写入蓝图，控制 PIE 或关卡，读取部分实时信息 | UE 正在运行，同时启用 **Project Settings → Plugins → Python → Enable Remote Execution** |
| **PIE 运行时** | 读取正在运行的 Pawn、战斗组件、装备、挂点等实际状态 | 已有 PIE 会话。磁盘日志可通过 `editor action=logs` 离线读取 |

`sync` 省略 `classes` 会采集范围内的所有资产类。第一次跑建议按资产或小范围同步。磁盘上的资产更新后，`sync force=true` 可重采。详细说明见 [缓存采集](docs/quickstart.md#sync) 和 [读取顺序](docs/quickstart.md#readorder)。

## 安全边界

- **读取工具读取项目数据。** 在线 `inspect` 和 `review` 也可以切换图页或定位画布。
- **写入需授权。** 默认按调用授权，`apply` 和 `save` 分别传 `confirm=true`。客户端也可通过 `COMBO_ALLOW_ACTIONS=1` 开启持久写入权限。这一策略同时覆盖 PIE 的启动、停止、模拟，以及关卡的加载、新建、保存和添加。
- **写入核对目标。** 蓝图操作匹配 `.uproject`、目标 Blueprint、当前图页和最新 `revision`。每次成功改图对应一个 UE 撤销项。PIE 或模拟期间暂停图表写入。
- **区分编译、保存和实战。** `apply` 返回编译结果。`saved` 表示 UE 保存成功，`readback.ok` 表示磁盘回读比对成功。实际玩法表现可另跑 PIE 验收。

[授权配置](docs/quickstart.md#actions) · [蓝图编辑步骤](docs/quickstart.md#blueprint_edit)

## 兼容性

| 环境 | 验证记录 |
| --- | --- |
| **UE 5.7.4 · Windows** | Win64 Development Editor 构建与蓝图编辑流程有验证记录 |
| **UE 5.6.1 · Windows** | Win64 Development Editor 模块构建通过。读取与基础编辑器动作有 v1.3 实测记录 |
| UE 5.0–5.5 / Linux / macOS | 当前版本待验证 |
| UE 4.x | 不支持 |
| DeepSeek Harness | MCP 接入已实测。其他客户端可按标准 stdio 配置接入 |

插件优先按项目 `.uproject` 的 `EngineAssociation` 找引擎。`status` 会返回解析到的引擎路径和版本，必要时可以用 `COMBO_UE_ENGINE` 指定引擎根目录。[更新与验证记录](CHANGELOG.md)

## 文档和源码

- [快速上手与故障排查](docs/quickstart.md)
- [DeepSeek Harness 接入](docs/dsh-setup.md)
- [UE 5.7 知识库 Skill](skills/ue57-knowledge-base/SKILL.md)
- [MCP 服务和工具定义](mcpserver) · [UE 编辑器模块](Source/ComboMCPEditor) · [现有测试与诊断工具](tools)
- [更新日志](CHANGELOG.md) · [MIT 许可](LICENSE)
