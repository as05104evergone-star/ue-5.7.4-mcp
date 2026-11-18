# Blueprint Agent Toolkit for Unreal Engine

**Give AI agents the tools to understand Unreal projects, debug combat combos, and build Blueprint logic in the editor.**

[中文](README.md) · **English** · [Quickstart (Chinese)](docs/quickstart.md) · [Releases](https://github.com/as05104evergone-star/unreal-blueprint-agent-toolkit/releases)

Formerly **ComboMCP**, this toolkit connects an AI agent to Unreal Engine through MCP. You describe the behavior and the edit scope. The agent reads the project and chooses how to implement the logic. The Unreal Editor module makes the actual node, pin, compile, and save changes.

## What you can do

| Task | What you get | MCP tools |
| --- | --- | --- |
| **Implement Blueprint logic** | Inspect an open K2 graph and its selection. Add nodes, wire pins, set defaults, lay out and comment the edited region, and compile in a single undo transaction. Review, save, and verify the result on disk | `blueprint_edit` |
| **Understand existing Blueprints** | Inspect classes, variables, functions and graphs. Follow execution from an entry point, check pin values and links, trace references, and find isolated nodes | `class_summary`, `graph_overview`, `flow`, `node_detail`, `index`, `audit`, `reflect` |
| **Diagnose combat combos** | Compare Blueprint Montage calls with animation sections and notify timing to find where a combo stops | `diagnose`, `anim_calls`, `montage`, `anim_asset` |
| **Resolve animation FX mounts** | Inspect a notify's mount and offset, list bones and sockets at the notify frame, and calculate positional and rotational offsets for the target | `fx_notify_map`, `fx_mounts`, `fx_solve_offset` |
| **Inspect assets and dependencies** | Search project assets, find dependencies and referencers, summarize materials, textures, meshes, levels and other assets, and collect an on-disk T3D cache | `project_map`, `search`, `find_refs`, `asset`, `sync` |
| **Inspect runtime state and operate the editor** | Read live PIE state, including the Pawn, combat component, the active Montage, and attachments. Control PIE and levels, list actors, or read logs | `pie_state`, `editor` |
| **Check setup and get help** | Inspect the resolved engine, project, cache and write policy. Ask the agent for setup, editing, PIE, or log instructions | `status`, `help` |
| **Check UE 5.7 engine behavior** | Find source lines, query a mirror of the official docs, and verify whether a Blueprint interface member is implemented as an event or function graph. Requires the sibling `KBaseUE` plugin | `engine_source`, `ue_docs`, `engine_facts`, `interface_check` |

The server exposes **28 MCP tools**. Four engine reference tools depend on the optional `Plugins/KBaseUE` plugin. Use `help` or the [quickstart](docs/quickstart.md) for operating instructions. The repository also includes a [UE 5.7 knowledge-base Skill](skills/ue57-knowledge-base/SKILL.md) for source checks and evidence collection.

## Ask the agent to write Blueprint logic

Open the target Blueprint and K2 graph in Unreal Editor, enable Python Remote Execution, and end PIE or simulation. Specify the entry point, expected behavior, edit scope, and save policy.

> Use ComboMCP to implement the attack condition in the open graph. Start at the selected `TryAttack` event. Continue into the existing attack logic when `bCanAttack` is True and end execution when it is False. Limit changes to this wiring and the new nodes, place them to the right of the entry, and add an “Attack condition” comment. I authorize editing and saving. After a successful compile and wiring review, save and report the compile and disk readback results.

| Step | What the agent does |
| --- | --- |
| `inspect` | Read the live graph, including unsaved nodes and links, and get the current `revision` |
| `apply` | Apply a batch of nodes, links, defaults, layout and comments, then compile and return diagnostics |
| `review` | Focus the modified area and check the branches, links, overlaps, and possible obstructions |
| `save` | With save authorization, write the Blueprint package and re-export it from disk to verify node IDs, positions, comment titles, and links |

You can also request a read-only diagnosis or ask the agent to edit and compile while leaving the graph unsaved for canvas review. [More examples](docs/quickstart.md#blueprint-edit-prompts)

Graph editing covers existing K2 graphs. Create new Blueprint assets, member declarations or event entry points, and remove existing nodes in Unreal Editor. [Supported graph operations](docs/quickstart.md#blueprint_edit)

## Two more workflows

### Find why a combo stops

1. Use `diagnose` to compare calls from the combat component against Montage sections and notify timing.
2. Use `flow` to trace the relevant event and `node_detail` to verify the exact pins.
3. Use `editor` to start PIE and `pie_state` to inspect the live objects and state. End PIE before applying any graph edits.

### Find the right FX mount

1. Use `fx_notify_map` to see the notify's current socket and offsets.
2. Use `fx_mounts` to list the available bones and sockets at the notify frame.
3. Use `fx_solve_offset` to calculate `SocketName`, `LocationOffset`, and `RotationOffset`. Enter the result in Unreal Editor and review the effect orientation in the viewport.

## Installation

1. Download the [release source](https://github.com/as05104evergone-star/unreal-blueprint-agent-toolkit/releases). Extract it into `<UE Project>/Plugins/ComboMCP/` with `ComboMCP.uplugin` at that directory's root. Use `ComboMCP` as the destination directory when cloning, too.
2. Build the project's **Development Editor** target for your engine and platform, or install a matching build of `ComboMCPEditor`. The plugin enables `PythonScriptPlugin` through its descriptor.
3. Restart UE and find **Blueprint Agent Toolkit for Unreal Engine** in **Edit → Plugins**. The `v1.4.0` source archive uses **Combo Blueprint MCP** as the display name. Find the MCP configuration under **Tools → ComboMCP → 显示 MCP 客户端配置**.
4. Connect your MCP client and run `status` to confirm the project and engine. Use `sync` to collect assets from disk. Enable Remote Execution for live editor operations.

```json
{
  "mcpServers": {
    "combo": {
      "command": "<Engine>/Engine/Binaries/ThirdParty/Python3/Win64/python.exe",
      "args": ["<Project>/Plugins/ComboMCP/mcpserver/server.py"]
    }
  }
}
```

[DeepSeek Harness setup](docs/dsh-setup.md) · [Installation guide](docs/quickstart.md#install) · [Remote Execution setup](docs/quickstart.md#remoteexec) · [Windows setup check](check.cmd)

## Offline reads and live editor operations

| Path | When to use it | Requirements |
| --- | --- | --- |
| **T3D cache on disk** | Read saved Blueprints, Montages, AnimBPs, and other assets. Collect by asset path or scope and parse locally | `sync` uses `UnrealEditor-Cmd`. Cache hits work with the editor closed |
| **Live editor** | Inspect unsaved graphs, apply Blueprint edits, control PIE or levels, and read live editor data | Unreal Editor running with **Project Settings → Plugins → Python → Enable Remote Execution** enabled |
| **PIE runtime** | Read the actual Pawn, combat component, attachments, and active animation state | An active PIE session. `editor action=logs` can read log files offline |

`sync` collects all asset classes in the scope when `classes` is omitted. Start with a few assets or a specific directory. Use `sync force=true` to refresh saved assets. [Cache collection](docs/quickstart.md#sync) · [Read order](docs/quickstart.md#readorder)

## Safety boundary

- **Read tools collect project data.** Live `inspect` and `review` can also switch the graph tab or focus the canvas.
- **Writes require authorization.** Pass `confirm=true` separately to `apply` and `save`, or configure `COMBO_ALLOW_ACTIONS=1` for persistent authorization. The write policy also covers PIE start, stop and simulation, and level load, new, save and add.
- **A write targets the current graph.** The editor checks the `.uproject`, Blueprint, graph tab, and latest `revision`. Each successful edit creates one undo entry. Graph writes pause during PIE or simulation.
- **Compile, save and runtime checks report different results.** `apply` returns compile diagnostics. `saved` reports the UE package save, and `readback.ok` reports the disk comparison. Use PIE to verify gameplay behavior.

[Write authorization](docs/quickstart.md#actions) · [Blueprint editing workflow](docs/quickstart.md#blueprint_edit)

## Compatibility

| Environment | Verification |
| --- | --- |
| **UE 5.7.4 · Windows** | Win64 Development Editor build and live graph editing have verification records |
| **UE 5.6.1 · Windows** | Win64 Development Editor module builds. Asset reads and basic editor actions were verified in v1.3 |
| UE 5.0–5.5 / Linux / macOS | Unverified for the current version |
| UE 4.x | Unsupported |
| DeepSeek Harness | MCP connection verified. Other clients can use the standard stdio configuration |

The toolkit selects the engine from the project's `.uproject` `EngineAssociation`. Run `status` to check the engine path and version. Set `COMBO_UE_ENGINE` if you need to specify the engine root. [Version history and verification](CHANGELOG.md)

## Documentation and source

- [Quickstart and troubleshooting](docs/quickstart.md)
- [DeepSeek Harness setup](docs/dsh-setup.md)
- [UE 5.7 knowledge-base Skill](skills/ue57-knowledge-base/SKILL.md)
- [MCP server and tools](mcpserver) · [UE Editor module](Source/ComboMCPEditor) · [Tests and diagnostics](tools)
- [Changelog](CHANGELOG.md) · [MIT license](LICENSE)
