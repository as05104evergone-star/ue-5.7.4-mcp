# ComboMCP

**Read Unreal Engine assets into compact text an AI can actually reason about.**

**English** | [中文](README.md)

An MCP server that exports blueprint graphs, montage timing, AnimBP state machines and
**materials / textures / meshes / levels** into offline-parseable T3D text — so a model can
answer questions like *"why won't my combo chain continue?"*, *"what does this graph
actually do?"* or *"where does this material's roughness come from?"* with **evidence**.

It also handles **basic operations**: read logs, inspect levels, drive PIE, load and save
levels. Those run in the editor process, and **write actions are off by default** — see
[Safety boundary](#safety-boundary).

> **Reading tools are read-only.** No asset writes, no compilation, no saving, no node
> creation. The only things that change state are 7 explicitly authorised editor actions,
> and they are blocked by default.

---

## Compatibility (read this first)

| Component | Status |
|---|---|
| **Unreal Engine 5.7.4** | ✅ **Tested** |
| **Unreal Engine 5.6.1** | ✅ **Tested** in a freshly created, isolated verification project: plugin load, T3D export of eight asset classes, `sync` collection, offline `asset` reads, log reading, write-action gating |
| UE 5.0 – 5.5 | ⚠️ **Untested.** The APIs it relies on (`ToolMenuEntryScript`, `PluginBlueprintLibrary`, `unreal.uclass`, `get_editor_subsystem`) all landed in UE 5.0, so it should work |
| UE 4.x | ❌ **Unsupported.** Different menu API and different pin serialization format |
| **Windows** | ✅ **Tested.** The `.cmd` scripts and engine path resolution are verified on Windows |
| Linux / macOS | ⚠️ **Untested.** The Python side now derives engine paths per platform (no hardcoded `Win64`), but the `.cmd` scripts have no equivalent — run the commands by hand |
| **DeepSeek Harness** | ✅ **Tested** |
| Other MCP clients | ⚠️ **Untested.** The wire protocol is standard MCP stdio (JSON-RPC 2.0) and it advertises `2025-06-18` / `2025-03-26` / `2024-11-05`; the config shape is identical for Claude Desktop / Cursor / Cline, but it has not been run outside DSH |

**Multiple engine versions need no environment variable.** The plugin picks the engine from
**the project's own `.uproject` `EngineAssociation`**: a 5.6 project is collected with the
5.6 `UnrealEditor-Cmd`, a 5.7 project with 5.7. `status` reports what it resolved
(`project_association` / `engine_root` / `engine_version`).

**It does NOT need**: Node.js, a system Python, a compiler toolchain, a C++ plugin, or any
third-party Python package.

**It DOES need**: the Python bundled with UE (`Engine/Binaries/ThirdParty/Python3/`,
shipped with every UE 5.x).

Editor actions (level / PIE / actor) additionally need Remote Execution enabled — that
setting is off by default, see [Safety boundary](#safety-boundary).

---

## How it differs from similar projects

Data read live via `gh api` on 2026-09-28, not estimated:

| Project | ★ | Approach | Compilation | Editor must stay open | Can edit assets |
|---|---|---|---|---|---|
| [mirno-ehf/ue5-mcp](https://github.com/mirno-ehf/ue5-mcp) | 75 | C++ plugin + local HTTP + MCP wrapper | Yes | Yes (headless fallback) | Yes |
| [ZiggyMar/unreal-mcp](https://github.com/ZiggyMar/unreal-mcp) | 42 | C++ editor plugin + Node/TS server | Yes | Yes | Yes |
| [cutehusky/ue5-mcp](https://github.com/cutehusky/ue5-mcp) | 13 | C++ plugin exposing a web API + FastMCP | Yes | Yes | Yes |
| [Cawb07/ue5-mcp](https://github.com/Cawb07/ue5-mcp) | 1 | C++ | Yes | Yes | Yes |
| Epic's official Unreal MCP | — | Built into the engine | No | Yes | Yes |
| **ComboMCP** | — | **Pure Python, `"Modules": []`** | **No** | **No** | **Reads never write; 7 opt-in editor actions** |

> Epic's official Unreal MCP only exists from **UE 5.8**. Verified on 5.7.4: there is no
> MCP-related plugin anywhere in the engine's plugin directory.

### Where this project is the better fit

* **You want to understand and ask questions, not let an AI touch your assets.** Read-only
  is a deliberate safety boundary, not a missing feature — for "why won't this combo chain
  continue?", being unable to write removes the risk entirely.
* **You don't want to install a toolchain.** The alternatives all require compiling a C++
  plugin with Visual Studio, and most also pull in Node.js or a `pip install`-able
  dependency such as FastMCP. This plugin has zero compilation and zero third-party deps.
* **You want to ask questions with the editor closed.** The main read path is a local T3D
  cache; it behaves identically whether or not the editor is running.
* **You're on UE 5.7.**

### Where it is the wrong tool

* **You want an AI to edit blueprints.** Use
  [ZiggyMar/unreal-mcp](https://github.com/ZiggyMar/unreal-mcp) or
  [mirno-ehf/ue5-mcp](https://github.com/mirno-ehf/ue5-mcp) — they create nodes, wire pins
  and change defaults. This plugin **cannot** do that and does not intend to.
* **You need 5.6 or 5.8 coverage.** ZiggyMar targets 5.6/5.8 specifically; this one has
  only been tested on **5.7.4**.
* **You want a large tool surface.** ZiggyMar ships 101 tools; this one ships 21
  (deliberately — tool definitions go into every model request).

The key decision: **Remote Execution is not the main path**. That switch
(`bRemoteExecution`) defaults to off and has no command-line flag and no console
command — a human has to tick a box in the editor settings. Building the primary read
path on a manually-toggled channel is a bad trade; `UnrealEditor-Cmd -run=PythonScript`
has no such precondition.

---

## Installation

### 1. Drop the plugin into your project

Copy the whole `ComboMCP` folder to:

```
<YourUEProject>/Plugins/ComboMCP/
```

No `.uproject` edit needed — `EnabledByDefault: true` in the `.uplugin` handles it.
The plugin always lives at `<project>/Plugins/ComboMCP`; the code derives the project
root from that, so **moving to another project is just a copy**.

### 2. Restart the editor

Plugins load only at **startup**. After restarting you should see **Combo Blueprint MCP**
under **Edit → Plugins**, and a **Tools → ComboMCP** menu.

One line in the `.uplugin` solves a problem you would otherwise hit:

```json
"Plugins": [{ "Name": "PythonScriptPlugin", "Enabled": true }]
```

This auto-enables PythonScriptPlugin — so **no** `-EnablePlugins=PythonScriptPlugin`
flag and **no** editor setting to tick.

### 3. Build the blueprint cache

Reads are served from a local T3D cache. Call the `sync` tool from your MCP client:

```json
{ "scope": "/Game", "classes": ["Blueprint", "AnimMontage"], "limit": 200 }
```

It runs `UnrealEditor-Cmd -run=PythonScript` as a **separate process** — which means
**it works while your editor is open** (verified: editor and commandlet coexist as two
instances).

### 4. Wire it into your MCP client

Standard config shape (substitute your own two paths):

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

> The **Tools → ComboMCP → Show MCP client config** menu prints exactly this for your
> machine, so you don't have to transcribe paths by hand.

For DeepSeek Harness, mounting goes through the Cordis layer — see
[`docs/dsh-setup.md`](docs/dsh-setup.md).

---

## Usage

### Tools (24, four of which need the optional KBaseUE)

The tool surface is deliberately small: tool definitions go into **every** model request.

| Tool | Purpose |
|---|---|
| `status` | Engine / index / cache / write-policy status. Call this first on any connection error |
| `sync` | Export assets into the T3D cache (headless commandlet). **Omitting `classes` means all assets** |
| `project_map` | Asset map: counts by class, plus blueprint list |
| `search` | Find assets by name |
| `find_refs` | Asset references / dependencies |
| `index` | Blueprint index: who calls a function, who reads/writes a variable |
| `class_summary` | What's in a class (variables / functions / events / components / graphs) |
| `graph_overview` | Nodes in a graph, and where its entries are |
| `flow` | **Read execution logic** (defaults to `format=dsl` — an order of magnitude fewer tokens than JSON) |
| `node_detail` | Pins and default values of a single node |
| `montage` | Montage sections / notifies / timeline |
| `anim_asset` | Anim sequences / blend spaces / PoseSearch databases |
| `anim_calls` | Every montage-related call in a blueprint |
| `diagnose` | **Combo diagnosis**: cross-checks blueprint call sites against animation timing |
| `audit` | Node liveness analysis (needs real links; uses T3D by default) |
| `reflect` | Fallback: see what engine reflection actually exposes |
| `pie_state` | **Runtime state**: read the live PIE world (editor must be playing) |
| **`asset`** | **Read any asset**: materials / material instances / textures / static meshes / skeletal meshes / levels / anything else |
| **`help`** | **Basic-operations Q&A**: 10 topics, accepts natural-language queries |
| **`editor`** | **Editor actions**: `capabilities` / `pie` / `level` / `actor` / `logs` |

#### What the three new tools answer

**`asset`** — the reading range is no longer limited to blueprints and animation. It picks
a semantic summariser per class:

| Asset class | What you get |
|---|---|
| `Material` | Blend / shading model, **whether each input is a constant or which expression it is wired to**, declared parameters with defaults, expression breakdown |
| `MaterialInstanceConstant` | Parent material, parameter overrides, property overrides |
| `Texture2D` | Size, mips, source format, compression, sRGB, LODGroup |
| `StaticMesh` | Material slots and names, bounds, collision type and profile, lightmap settings |
| `SkeletalMesh` | Skeleton, material slots |
| `World` (level) | Loaded levels, World Partition config, whether the level is partitioned |
| Anything else | Generic view: readable properties (export noise stripped) + child-class histogram |

**What it cannot read is stated explicitly**: mesh vertices, texture pixels, and the
per-actor serialization of a level. The tool never returns a silent empty value that would
make you think the asset is empty.

**`help`** — topics: `install` `menu` `sync` `pie` `level` `logs` `remoteexec` `actions`
`readorder` `troubleshoot`. Ask "how do I open PIE" or "where are the logs" directly.

**`editor`** — editor actions. Write actions are off by default, see
[Safety boundary](#safety-boundary).

#### Optional: the knowledge-base four (requires a sibling `KBaseUE` plugin)

These answer **"is this UE semantic actually true?", with a `file:line` to back it up.**
They depend on `Plugins/KBaseUE`, which is **not part of this repository**.

| Tool | Purpose |
|---|---|
| `engine_source` | Index of the whole UE 5.7 source: `symbol` / `read` / `usage` / `reflected` / `grep` / `files` / `status` |
| `ue_docs` | Mirror of Epic's official 5.7 docs: `status` / `search` / `read` / `version_check` |
| `engine_facts` | Distilled high-frequency traps, each with source line numbers |
| `interface_check` | On a real interface asset: does this function become an **event** or a **function graph**? |

**Without KBaseUE they return `{"error": "KBaseUE module not found", ...}` — that is
degradation, not a crash.** The other 20 core tools keep working and the protocol layer is
unaffected (`tools/mcp_protocol_test.py` section 9 verifies exactly this).

### Recommended read order (coarse to fine)

1. `status` — confirm channel and index state
2. `project_map` — build overall context
3. `class_summary` — what's inside one class
4. `graph_overview` — which nodes exist, where the entries are
5. `flow` (`format=dsl`) — read the actual logic
6. `node_detail` — only once you've located a specific node

### Two data paths

Read tools default to `source="auto"`; the `source` field in the result tells you which
path was taken. **On a cache hit everything is parsed locally** — millisecond latency,
editor open or not.

**`reflect` (engine reflection)** is fast but cannot see graph logic. Two layers of reason:

```cpp
// Engine/Source/Runtime/Engine/Classes/EdGraph/EdGraphNode.h
TArray<UEdGraphPin*> Pins;          // ← not even a UPROPERTY()
```

And even having `UPROPERTY()` is not enough — Python's `get_editor_property` goes through
`PropertyAccessUtil::CanGetPropertyValue`, which requires one of
`CPF_Edit` / `CPF_BlueprintVisible` / `CPF_BlueprintAssignable`:

```cpp
// CoreUObject/Private/UObject/PropertyAccessUtil.cpp
if (!InProp->HasAnyPropertyFlags(CPF_Edit | CPF_BlueprintVisible | CPF_BlueprintAssignable))
    return FPropertyAccessResult::PermissionDenied(...);
```

A bare `UPROPERTY()` has none of the three, so it is rejected anyway. `UEdGraph::Nodes`,
`UAnimMontage::CompositeSections`, `UAnimMontage::SlotAnimTracks` and
`UBlueprint::NewVariables` are all in this category — **all `PermissionDenied` from Python**.

**`t3d` (the engine's own text export)** can read what reflection cannot, because the
export filter is looser: `FProperty::ShouldPort` only requires `CPF_Edit` under
`PPF_PropertyWindow` (`Property.cpp`), so bare-`UPROPERTY()` members show up in T3D
regardless.

**So T3D is not a fallback — it is the more capable path.**

### Three T3D format traps (measured, not guessed)

Each of these makes a parser **silently produce wrong results**:

1. **The file is UTF-8 with BOM.** Python 3's `\s` does not match `\ufeff`; without
   stripping the BOM the root object on line 1 is lost and the object tree collapses
   into several roots.
2. **Export happens in two passes.** The second pass's `Begin Object` has **no `Class=`**,
   and `LinkedTo` links appear *only* in that second pass. You must merge by `ExportPath`,
   or every class name becomes `?` and all links vanish.
3. **`MemberParent` is a full object path**, e.g.
   `"/Script/CoreUObject.Class'/Script/Engine.Actor'"`. Naively taking the tail gives you
   `Class`, not `Actor`.

### Architecture: why two layers

```
┌─ Inside the editor (ue/) ── reflection reads + T3D export ──┐
│  Only spun up by the commandlet when needed                 │
└─────────────────────────────────────────────────────────────┘
                    ↓  T3D text lands in cache/
┌─ MCP process (engine/ + mcpserver/) ───────────────────────┐
│  Pure-data parsing (t3d.py / graph_ir.py never import      │
│  unreal) — milliseconds, works with the editor closed      │
└────────────────────────────────────────────────────────────┘
```

`ue/t3d.py`, `ue/graph_ir.py`, `ue/montage_t3d.py` and `ue/anim_bp_t3d.py` are
**pure-data modules** that never import `unreal`, so they can be loaded straight into the
MCP process — that is the root reason "no editor required" works at all.

---

## Repository layout

```
Plugins/ComboMCP/
├── ComboMCP.uplugin        Plugin descriptor ("Modules": [] — no C++, no compilation)
├── Content/Python/         UE plugin Python dir, auto-added to sys.path by the engine
│   ├── init_unreal.py      Startup hook: load evidence + menu registration
│   └── combomcp_editor.py  Tools → ComboMCP menu
├── start_editor.cmd        Launch the editor (no special flags)
├── check.cmd               One-shot self-check (first two steps need no editor)
├── ue/                     Read-only scripts run inside the editor
│   ├── t3d.py              [pure data] T3D text -> IR parser
│   ├── graph_ir.py         [pure data] IR + exec-flow walk + DSL renderer
│   ├── montage_t3d.py      [pure data] Montage timeline / window diagnosis
│   ├── anim_bp_t3d.py      [pure data] AnimBP state machine reader
│   ├── runtime.py          Defensive reflection reads
│   ├── t3d_read.py         T3D export + read (editor side)
│   ├── domain.py           Combo-domain diagnosis rules
│   └── dispatch.py         Command dispatch + automatic path selection
├── engine/                 MCP side (plain Python process)
│   ├── offline.py          Offline reads: everything used on a cache hit
│   ├── collector.py        Commandlet collector
│   ├── bridge.py           Remote Execution bridge (optional path)
│   ├── pure.py             Loads pure-data modules into the MCP process
│   └── search.py           On-disk index cache
├── mcpserver/              MCP protocol layer (zero dependencies)
│   ├── server.py           JSON-RPC over stdio
│   └── tools.py            Tool definitions and implementations
├── tools/                  Tests and diagnostics (see below)
├── cache/                  Generated: T3D cache (**not committed**, safe to delete)
└── docs/
```

### Which `tools/` scripts are reusable

| Script | Reusable? | Notes |
|---|---|---|
| `test_t3d.py` (60) | ✅ | Parser unit tests, synthetic samples |
| `test_t3d_real.py` (54) | ✅ | Real engine-export regression, fixture shipped |
| `test_wiring.py` (44) | ✅ | Module loading + command table wiring |
| `mcp_protocol_test.py` (20) | ✅ | End-to-end MCP protocol |
| `probe_plugin_load.py` | ✅ | Plugin acceptance: enabled-plugin list, menu API |
| `probe_remote.py` | ✅ | Remote Execution channel probe |
| `inspect_macro.py` | ✅ | Read any engine macro's internals (does it have side effects?) |
| `inspect_var_refs.py` | ⚠️ | Defaults to the author's asset; pass args to use it on yours |
| `dump_combo_graph.py` | ⚠️ | Same |
| `test_e2e_offline.py` (23) | ⚠️ | Its asset list is the author's; run `sync` on matching assets first, or it fails |
| `verify_all.py` / `wait_and_verify.py` | ❌ | Author's project-specific end-to-end checks; rewrite for your assets |

---

## Tests

**No editor required** (342 assertions total):

```powershell
$py = "<Engine>\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
$root = "<Project>\Plugins\ComboMCP"

& $py "$root\tools\test_t3d.py"             # 60
& $py "$root\tools\test_t3d_real.py"        # 54
& $py "$root\tools\test_wiring.py"          # 48
& $py "$root\tools\mcp_protocol_test.py"    # 41
& $py "$root\tools\test_asset_read.py"      # 73
& $py "$root\tools\test_editor_actions.py"  # 66
```

The two new suites are worth explaining, because their whole value is in using real things:

- **`test_asset_read.py`** fixtures are T3D exported from **a real UE 5.6.1 run**
  (material / material instance / texture / static mesh / skeletal mesh / level / font).
  The worst 1.3.0 defects — material data living on a `MaterialEditorOnlyData` child,
  hex payloads in textures, trailing commas in structs — **cannot be caught by synthetic
  samples at all**.
- **`test_editor_actions.py`** uses a **fake `unreal` module** (exposing no editor
  subsystem) to verify commandlet isolation: every action must return `_need_editor`
  instead of trying — because trying was measured to kill the process with
  `EXCEPTION_ACCESS_VIOLATION`.

**Needs the engine, but not a running editor** (plugin acceptance):

```powershell
# UE truncates -Script at whitespace, so copy the script to a space-free path first
Copy-Item "$root\tools\probe_plugin_load.py" "C:\Temp\probe_plugin_load.py" -Force
& "<Engine>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" `
    "<Project>\<Project>.uproject" `
    -run=PythonScript -Script=C:\Temp\probe_plugin_load.py `
    -unattended -nosplash -nop4 -nullrhi -stdout
```

Results land in `cache/plugin/probe_result.json`, verifying: the plugin appears in the
engine's enabled-plugin list, `Content/Python` reached `sys.path`, and the menu API call
chain works.

---

## Troubleshooting

**Plugin not listed under Edit → Plugins**

Plugins load only at **startup** — restart the editor after installing. Still missing?
Confirm `ComboMCP.uplugin` sits in `<Project>/Plugins/ComboMCP/` and that
`EnabledByDefault` is `true`.

**Did the engine actually load the plugin?**

Check `cache/plugin/loaded.json` — the engine rewrites it on every startup — or run
`check.cmd`.

**`sync` is slow**

Every call cold-starts the engine and loads the project. **Batch many assets per call**,
don't call it once per asset. Already-cached assets are skipped unless `force=true`.

**I changed a blueprint but still get the old logic**

The T3D cache reads the `.uasset` on disk. Make sure you **saved** (Ctrl+S), then `sync`
again. Conversely, unsaved editor changes are invisible to the commandlet — that case
requires Remote Execution (next item).

**`status` reports Remote Execution unresponsive**

First ask whether it matters: the main read path is the T3D cache and needs no running
editor. Only reading **unsaved** changes needs that channel. If you do need it:

1. Tick **Project Settings → Plugins → Python → Enable Remote Execution?** (off by
   default; it is the only switch, has no command-line flag, and applies immediately)
2. Make sure the editor has finished loading
3. With several engine instances open, the plugin prefers the one holding your project

**A field reads as empty, `_diag` shows a miss**

That `UPROPERTY` isn't exposed to Python in your engine build. Use `reflect` to see what
the engine actually exposes. This is expected by design: `UEdGraphPin` moving from struct
to UObject was a breaking change, and hardcoded readers inevitably fail silently across
version drift.

---

## Safety boundary

**Reading tools are read-only.** No writes, no compilation, no saving, no asset creation.
T3D export goes through `ObjectExporterT3D`, pure serialization; the commandlet only calls
`unreal.load_asset()` and the exporter.

**Only 7 write actions exist, and they are off by default:**

| Write action | What it does |
|---|---|
| `editor action=pie sub=start` / `stop` / `simulate` | Enter / leave PIE |
| `editor action=level sub=load` / `new` / `save` / `add` | Load / create / save a level, add a sublevel |

Enable them either way:

- **Environment variable** (persistent): set `COMBO_ALLOW_ACTIONS=1`, then restart the MCP
  client (the MCP server is a subprocess; it reads the variable at startup).
- **Single call**: pass `confirm=true`.

When not authorised they return a payload tagged `_actions_blocked` instead of failing
silently.

**Two gates, not one check.** The MCP side (`engine/actions.py`) stops accidental model
calls; the editor side (`ue/editor_actions.py::_guard_write`) stops commands sent straight
into the editor over Remote Execution, bypassing MCP. The second gate is necessary —
that channel is open by design, so the outer layer alone does not count.

**Deliberately not supported** (not "not yet", but by design):

- **Running arbitrary console commands.** That hands over the whole engine console.
- **Saving arbitrary assets.** Only the explicit `level sub=save`, which saves the current
  level and needs authorisation.
- **Creating / modifying / deleting assets.**

### How to verify reading really writes nothing

Run `sync`, then check whether any file under `Content/` has a modification time later
than when the collection started.

### Editor actions need Remote Execution enabled

Level / PIE / actor actions must run **inside the editor process**, over UE's Python Remote
Execution channel. That channel is **off by default**:

1. In the editor: **Project Settings → Plugins → Python → Enable Remote Execution?**
2. It takes effect immediately, no restart needed.
3. Confirm `bridge.connected` is `true` via `status`.

The setting has no command-line flag and no console command — which is exactly why this
plugin **does not make Remote Execution its main reading path**. Asset reading runs off the
T3D cache. **`logs` doesn't need it either**: it reads the log files from disk.

---

## License

[MIT](LICENSE) · See [CHANGELOG.md](CHANGELOG.md) for version history
