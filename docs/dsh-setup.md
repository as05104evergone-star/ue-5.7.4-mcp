# 把 ComboMCP 挂进 DeepSeek Harness

ComboMCP 是标准 MCP stdio server，**任何 MCP 客户端都能用**。这份文档记录在 DSH 上
挂载的确切方式；其他客户端看 [README](../README.md) 的通用配置即可。

> **路径约定**
> `<引擎>` = UE 安装目录（如 `C:\Program Files\Epic Games\UE_5.7`）
> `<项目>` = 你的 UE 项目根目录
> `<DSH_HOME>` = DSH 配置目录（默认 `%USERPROFILE%\.dsh`）

---

## 方式一：preset（推荐）

在 `<DSH_HOME>\.agent-presets\` 下新建一个 preset 目录，例如 `combo\`：

```
<DSH_HOME>\.agent-presets\combo\
├── agent.cordis.yml     ← 编排文件，末尾增加一行 mcp-combomcp
└── preset.yml           ← 显示名与描述
```

**基线选择很重要**：请从 `standard` 复制，**不要**从 `cordis` 复制。原因是技术性的：

> `tool-cordis` 会注册一个 Host inspect provider。同一个进程里两个 preset 都携带
> `tool-cordis` 时，第二次挂载会因为 provider 重名而失败——已实测确认（校验 `cordis`
> 通过，校验它的副本失败）。

代价是 `combo` 会话里没有 `cordis_*` 动态插件工具。要用那套能力时切回 `cordis`
preset 即可；两者互斥，不能在同一进程共存。

---

## 怎么用

1. 启动编辑器（也可以直接双击 `.uproject`——插件会自动启用 Python）：

   ```
   <项目>\Plugins\ComboMCP\start_editor.cmd
   ```

2. 在 DSH 里**新建一个会话**，preset 选你的 Combo preset

3. 会话里会出现 `mcp__combo__*` 工具。先问一句"检查一下蓝图连接状态"，
   等价于调用 `status`

> 注意：MCP 工具在**会话启动时**装载。当前会话中途新增 preset 不会让工具出现，
> 需要新开一个会话。

---

## 那一行配置

`agent.cordis.yml` 末尾新增的行：

```yaml
- id: mcp-combomcp
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: combo
    transport: stdio
    command: '<引擎>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe'
    args:
      - '<项目>\Plugins\ComboMCP\mcpserver\server.py'
    toolCallTimeoutMs: 180000
    failOnStartupError: false
```

逐个字段的理由：

| 字段 | 值 | 为什么 |
|---|---|---|
| `serverName` | `combo` | 决定工具名 `mcp__combo__<tool>`。会话历史与权限规则按这个名字稳定 |
| `transport` | `stdio` | 本地进程，不需要端口、不需要鉴权、不需要网络 |
| `command` | UE 自带的 python.exe | 不该往系统 Python 或引擎解释器里装第三方包——所以 server 是纯标准库实现 |
| `toolCallTimeoutMs` | `180000` | `sync` 要冷启动引擎加载项目，默认 60 秒可能不够 |
| `failOnStartupError` | `false` | 编辑器没开时 harness 仍要能启动；工具会返回明确的错误，而不是让整个会话起不来 |

这一行**不发布 service**（它消费 host 的 `tools` 与 `subprocess`），所以它落在
顶层、不带 `isolate` realm。放进 realm 反而会解析不到 `subprocess`。

---

## 校验方式

```
preset_admin(action="check", id="combo")   → mount OK: combo
```

`standingKeyFor` 会真实地 compose 这棵插件子树（等同一次会话启动），能查出
包无法解析、配置非法、行未激活、以及服务落进 root realm 这四类失败。

补充验证（不需要编辑器）：

```powershell
& "<引擎>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" `
    "<项目>\Plugins\ComboMCP\tools\mcp_protocol_test.py"
```

它以真实 MCP 客户端的方式跑完 `initialize` → `tools/list` → `tools/call` → 错误路径，
20 项断言全绿说明协议层与工具装载都正常。

---

## 不想用 preset 的时候

在任意会话里也可以直接用 CLI，效果与工具调用等价：

```powershell
$py  = "<引擎>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
$srv = "<项目>\Plugins\ComboMCP\mcpserver\server.py"

# 列工具
& $py $srv --list-tools

# 直接调用某个工具（换成你自己的资产路径）
& $py $srv --selftest class_summary --args '{\"asset_path\":\"/Game/YourContent/BP_Example\"}'
```

这条路径不依赖任何 MCP 客户端，适合临时排查。

---

## 其他 MCP 客户端

一样能用，配置形态相同：

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

Claude Desktop 的 `claude_desktop_config.json`、Cursor 的 `.cursor/mcp.json`、
Claude Code 的 `.mcp.json` 都是这个结构。

> 唯一要自己补的是超时：DSH 用 `toolCallTimeoutMs`，其他客户端可能没有对应字段。
> 若 `sync` 因超时失败，在客户端配置里调大超时，或改用 CLI 先采集一次。
