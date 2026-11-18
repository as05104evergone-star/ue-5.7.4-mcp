# 在 Codex 中接入 Blueprint Agent Toolkit

**中文** · [English](#english)

先把插件安装到 UE 项目的 `Plugins/ComboMCP` 目录。Codex 通过本机 stdio 启动 MCP 服务，配置中填两条绝对路径。下文以 Windows 为例，替换成实际引擎和项目路径。[插件安装](quickstart.md#install)

| 字段 | 填什么 |
| --- | --- |
| 服务名 | `combo` |
| Command | `<UE 安装目录>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe` |
| Arguments | 一个参数 `<UE 项目>\Plugins\ComboMCP\mcpserver\server.py` |

## 桌面端

1. 打开 **Settings → MCP servers → Add server**。
2. 选择 **STDIO**，填入上表的服务名、Command 和 Arguments。路径里有空格时，仍分别填到对应字段。
3. 保存后点击 **Restart**。重启后在聊天输入框输入 `/mcp`，查看 `combo` 是否已连接。[OpenAI MCP 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=desktop)

## CLI

在 PowerShell 中执行下方命令。已有 `combo` 配置时，先用 `codex mcp get combo` 核对它指向的项目。

```powershell
codex mcp add combo -- "C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" "D:\Unreal Projects\MyGame\Plugins\ComboMCP\mcpserver\server.py"
codex mcp get combo
```

打开新的 Codex 会话后输入 `/mcp`，查看已加载的服务和工具。[OpenAI CLI 配置文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

## 手动配置 config.toml

也可以在 `~/.codex/config.toml` 中添加或更新以下配置。需要仅在一个受信任项目中加载时，把同一段配置放进该项目的 `.codex/config.toml`。示例中的 TOML 单引号会保留 Windows 路径里的反斜杠。

```toml
[mcp_servers.combo]
command = 'C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe'
args = ['D:\Unreal Projects\MyGame\Plugins\ComboMCP\mcpserver\server.py']
```

## 验证与使用

在 Codex 中先发一条读指令，确认 Agent 通过 `combo` 拿到插件自己的返回结果。

> 调用 `combo` 的 `status`，告诉我 MCP 指向的 UE 项目、引擎版本和缓存状态。再调用 `help`，读取 `blueprint_edit` 的使用说明。

实时查看或修改蓝图前，打开目标 K2 图页并启用 UE 的 Python Remote Execution。写入期间结束 PIE 或模拟。下指令时说明目标图页、预期行为、修改范围，以及编译后是否授权保存。默认写入策略下，`apply` 和 `save` 分别使用单次 `confirm=true` 授权。[Remote Execution](quickstart.md#remoteexec) · [蓝图编辑指令示例](quickstart.md#blueprint-edit-prompts)

## English

Install the toolkit under `Plugins/ComboMCP` in your Unreal project. Connect it to Codex over local stdio using one of these options.

- **Desktop**. Go to **Settings → MCP servers → Add server**, choose **STDIO**, and name it `combo`. Use Unreal Engine's bundled `python.exe` as the command and pass the absolute path to `Plugins/ComboMCP/mcpserver/server.py` as a single argument. Save, then select **Restart**.
- **CLI**. Run the [PowerShell example above](#cli) with your own paths. Use `codex mcp get combo` to confirm the registered command and argument.
- **TOML**. Add the `[mcp_servers.combo]` table from the [configuration example](#手动配置-configtoml) to `~/.codex/config.toml`, or to `.codex/config.toml` in a trusted project.

In a new chat, use `/mcp` to confirm that `combo` is connected. Ask Codex to run `combo`'s `status` and `help` with `topic=blueprint_edit`. For live graph editing, open the target K2 graph, enable Python Remote Execution, and end PIE or simulation. State the edit scope and whether Codex should save after compiling and reviewing the graph. [Blueprint editing workflow](quickstart.md#blueprint_edit) · [OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
