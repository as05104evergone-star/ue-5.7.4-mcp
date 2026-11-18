# ComboMCP 操作手册

这个文件是 `help` 工具的数据源。它按 `## ` 小节切分，小节标题就是 topic 名
（见文末「topic 与别名的对应」）。改这里不需要动代码——`help` 每次读取本文件。

写作原则：**只写能照着做的步骤**，不写"应该"和"通常"。每条都对应一次真实操作。

---

## install

### 装进一个 UE 项目

1. 把整个 `ComboMCP` 文件夹复制到 `<你的UE项目>/Plugins/ComboMCP/`。
   - 目录层级必须正好是 `<项目>/Plugins/ComboMCP`：代码靠"上溯两级"反推项目根，
     放错层级会找不到 `.uproject`。
2. 使用与引擎和平台匹配的 `ComboMCPEditor` 二进制。源码安装先构建项目的 Development Editor 目标。
3. 重启编辑器，打开 **Edit → Plugins** 查看 **Combo Blueprint MCP**。

`.uplugin` 的 `EnabledByDefault: true` 会启用插件。MCP 服务使用 UE 自带的 Python。

### 接进 MCP 客户端

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

编辑器菜单 **Tools → ComboMCP → 显示 MCP 客户端配置** 会按当前机器直接打印
这段 JSON，省得手抄路径。

### 怎么确认装好了

- 编辑器菜单里能看到 **Tools → ComboMCP**；
- 或者启动一次 commandlet 后看 `<插件>/cache/plugin/loaded.json`：
  里面的 `content_python_on_sys_path` 为 `true` 就说明插件与 Python 都接上了。

---

## menu

插件在编辑器里的入口是 **Tools → ComboMCP**，共三项：

| 菜单项 | 作用 |
|---|---|
| 显示缓存状态 | 在 Output Log 里打印插件目录、缓存目录、已缓存资产数量与体积 |
| 列出已缓存资产 | 在 Output Log 里逐个列出已缓存的 T3D 文件 |
| 显示 MCP 客户端配置 | 打印可直接粘贴进 MCP 客户端配置的 JSON |

三项都**只读文件、只打印日志**：不启动子进程、不编译、不碰资产。原因是它们跑在
编辑器主线程上，重活会把 UI 卡住——重新采集缓存请用 `sync` 工具或 `check.cmd`。

**找不到菜单**：插件只在启动时加载，确认已经重启过编辑器；再确认
`ComboMCP.uplugin` 确实在 `<项目>/Plugins/ComboMCP/` 下。

---

## sync

`sync` 把资产导出成 T3D 文本落到 `Plugins/ComboMCP/cache/t3d/`。之后所有读取
都在本地毫秒级完成，**不需要编辑器开着**。

### 用法

```json
{ "scope": "/Game", "classes": ["Blueprint", "AnimMontage"], "limit": 200 }
```

或直接给资产列表（一次多给几个，别一个资产调一次）：

```json
{ "assets": ["/Game/Combo_Demo/BP/Components/AC_Combat",
             "/Game/Combo_Demo/Animation/Montage/AS_Combo01_01"] }
```

### 几个要点

- **每次调用都要冷启动一次引擎**并加载项目（首次约 1 分钟，之后约 10 秒）。
  所以批量给资产，把加载成本摊薄。
- 已缓存的默认跳过，`force=true` 才重导。
- 编辑器开着也能跑：它另起一个 `UnrealEditor-Cmd` 进程，与编辑器互不干扰。
- **读的是磁盘上的 `.uasset`。** 在编辑器里改了蓝图但没保存，`sync` 看不到——
  先 Ctrl+S 再 sync。

### 缓存里读不到什么

- 未保存的改动（见上）；
- 网格的顶点数据、贴图的像素数据（它们以 BulkData / 十六进制载荷形式存在，
  工具只报大小，不铺开）；
- 顶点级别的几何信息——判断"这个网格长什么样"请直接在编辑器里看。

---

## pie

PIE = Play In Editor。这一组动作**需要编辑器正在运行**，并且要打开 Remote
Execution（见 topic `remoteexec`）。

| 动作 | 说明 |
|---|---|
| `editor action=pie sub=status` | 现在是否在 PIE 里、game world 是哪个 |
| `editor action=pie sub=start` | 以 **Simulate** 方式进入 Play（世界跑起来，但不接管输入） |
| `editor action=pie sub=stop` | 结束 PIE |
| `editor action=pie sub=simulate` | 走 BeginPlay 请求进入 Play |

`start` 用 Simulate 是刻意的：读运行时状态不需要手柄控制权，而 Simulate 最不容易
把编辑器搞成"半开着 PIE 又没响应"。要能操作的 PIE 请在编辑器里按 Play。

进入 PIE 之后，用 **`pie_state`** 工具读运行时事实：

- 武器 Actor 当前挂在哪个插槽（判断"在手上还是背上"的唯一可靠依据）；
- 战斗组件的运行时变量真实值（不是默认值）；
- 骨架插槽是否存在（插槽不存在会让 `AttachToComponent` **静默退化到 mesh 根节点**）；
- mesh 当前在播哪个 montage。

### 一个实战技巧

按完键**别急着重置**卡住的标志位。卡住的状态本身有信息量：`bWeaponBusy` 停在
`true`，说明那次操作的回调根本没触发（正常路径会在回调里把它复位）——这一条就能
把问题域从"插槽/动画"直接切到"回调/事件链"。

---

## level

关卡动作需要编辑器在运行。`load` / `new` / `save` / `add` 会**改变编辑器状态**，
所以默认被挡住，需要显式授权（见 topic `actions`）。

| 动作 | 写？ | 说明 |
|---|---|---|
| `editor action=level sub=current` | 否 | 当前关卡 + 已加载的关卡清单 |
| `editor action=level sub=list` | 否 | 所有已加载关卡（含可见性与是否 Persistent） |
| `editor action=level sub=load asset_path=/Game/Maps/X` | 是 | 加载关卡 |
| `editor action=level sub=new asset_path=/Game/Maps/New` | 是 | 新建空白关卡 |
| `editor action=level sub=save` | 是 | 保存当前关卡 |
| `editor action=level sub=add asset_path=/Game/Maps/Sub` | 是 | 把另一个关卡作为子关卡挂进当前世界 |
| `editor action=actor sub=list` | 否 | 当前关卡里的 Actor（可按 `filter` 过滤） |
| `editor action=actor sub=selected` | 否 | 当前选中的 Actor |

两个必须知道的坑：

1. **`load` 会关掉当前 PersistentLevel，而且不会提示保存。** 有未保存改动就先
   `level sub=save`。
2. **新建关卡会立刻存盘**（这是 UE 自身的既有行为，不是本插件加的）。所以
   `new` 要先把 `asset_path` 想清楚。

---

## logs

读编辑器日志，**不需要编辑器在运行**——它直接读磁盘上的日志文件：

```
<项目>/Saved/Logs/<项目名>.log
```

| 参数 | 说明 |
|---|---|
| `tail` | 取最后 N 行，默认 200 |
| `pattern` | 正则过滤 |
| `severity` | `error` / `warning` / `all`，默认 `all` |
| `file` | 指定日志文件名；省略则自动挑最新的 `.log` |

常见用法：

- 蓝图编译报错：`severity=error`
- 看 Python 侧报错：`pattern="LogPython"`
- 看某个资产被谁引用：`pattern="AC_Combat"`
- 看崩没崩：`pattern="Fatal error|Ensure"`

**为什么不用编辑器里的 Output Log**：UE 5.6 的 Python 里没有
`unreal.log_capture_get`（5.7 才有），而且要求编辑器开着。读文件在 5.6/5.7 上
行为一致，编辑器关了也还能查上一次的日志。

---

## remoteexec

关卡 / PIE / Actor 这三组动作必须在**编辑器进程里**执行，走的是 UE 自带的
Python Remote Execution 通道。这个通道**默认关闭**，必须手工打开：

1. 编辑器里 **Project Settings → Plugins → Python → Enable Remote Execution?** 打勾；
2. 改完**立即生效**，不用重启编辑器；
3. 用 `status` 确认 `bridge.connected` 变成 `true`。

这个开关没有命令行参数、也没有 console 命令，只能在设置界面里勾——这正是本插件
**不把 Remote Execution 当主力读取路径**的原因。读资产的主力是 T3D 缓存，
它不需要编辑器，也不需要这个开关。

### 连不上时逐项排查

- 编辑器确实启动了，而且已经加载完（不是还在编译 shader）；
- 同时开了多个引擎实例时，插件会优先选**打开了本项目**的那个（按项目路径匹配）；
- 防火墙没有拦 UDP `239.0.0.1:6766`；
- `tools/probe_remote.py` 可以单独验证这条通道。

### 多引擎混装（5.6 与 5.7 都在）

插件按**项目自己的 `.uproject` 里的 `EngineAssociation`** 决定用哪个引擎，
不需要设环境变量：5.6 的项目会用 5.6 的 `UnrealEditor-Cmd` 去采集。

`status` 会回报三件事，用来确认没搞错：

- `project_association` —— 你的 `.uproject` 声明的引擎版本；
- `engine_root` —— 实际解析到的引擎目录；
- `engine_version` —— 从 `Engine/Build/Build.version` 读出的真实版本号。

要强制指定引擎，设环境变量 `COMBO_UE_ENGINE` 指向引擎根目录。

---

## actions

**写操作默认需要授权。** 从 v1.4 起，除了 PIE 和关卡动作，AI 也可以在授权范围内写入蓝图图表。

### 哪些算写操作

| 动作 | 写？ |
|---|---|
| `pie sub=start` / `pie sub=stop` / `pie sub=simulate` | 是 |
| `level sub=load` / `new` / `save` / `add` | 是 |
| `blueprint_edit action=apply` / `save` | 是 |
| `blueprint_edit action=inspect` / `review`、`logs`、`level sub=current/list`、`actor`、读取类工具 | 否 |

### 怎么打开

任选其一：

1. **环境变量**（长期生效）：设 `COMBO_ALLOW_ACTIONS=1`，然后重启 MCP 客户端
   （MCP 服务是子进程，环境变量在它启动时读取）。
2. **单次授权**：调用时传 `confirm=true`，只对这一次调用生效。

没授权时写操作会返回 `_actions_blocked`，并列出授权方式。

---

## blueprint_edit

<a id="blueprint-edit-prompts"></a>

### 怎么下指令

把蓝图需求当成一段交付说明。写清楚下面四件事，节点类型、节点 GUID 和引脚内部名可以交给 AI 从现场图里查。

| 写什么 | 怎么说 |
|---|---|
| 目标 | 给 `/Game/.../BP_名称` 和 `EventGraph` 或函数图名。也可以先在 UE 里打开目标图页并选中入口节点 |
| 行为 | 说清楚从哪个事件、节点或引脚接入，用哪些已有变量或函数，各分支接到哪里 |
| 范围 | 指定可改的节点、连线和可移动范围。需要沿用已有排版时，提供参考图页或注释区域 |
| 交付 | 指定只检查、改图编译后等你复核，或编译和复核通过后直接保存并回读 |

**只检查。**

> 用 ComboMCP 读取当前蓝图的事件图，检查我选中的攻击入口为什么没有走到播放 Montage。
> 输出执行链、卡住的引脚和修改建议。只读检查。

**让 AI 直接改图并保存。**

> 用 ComboMCP 直接修改 `/Game/Combat/BP_PlayerCombat` 的 `EventGraph`。从已有的 `TryAttack`
> 事件出口插入 Branch，条件用已有的 `bCanAttack`。True 接回原来的攻击执行链，False 留空。
> 修改范围限于这个入口和新增节点，新增节点在入口右侧排开，加注释「检查攻击条件」。
> 我授权本次编辑和保存。编译通过、复核两条分支后保存，并报告改了哪些节点和连线、编译结果、磁盘回读是否通过。

**改图编译，留在画布上复核。**

> 用 ComboMCP 执行上面的改图范围，只授权改图和编译。做完把画布定位到新增的 Branch，
> 输出编译结果，保持未保存，等我在 UE 里复核。

路径、图名、事件名和变量名换成项目里的实际名称。「我授权编辑和保存」表示这项任务允许两步，
客户端仍按 [写操作授权](#actions) 调用工具。单次授权时，`apply` 和 `save` 各带一次 `confirm=true`。
`save` 保存整个当前 Blueprint 包，这个蓝图里其他尚未保存的修改也会一起落盘。

### 工具执行步骤

1. 打开目标 Blueprint 和图页。在线编辑用 **Project Settings → Plugins → Python → Enable Remote Execution**。
2. 调用 `blueprint_edit {"action":"inspect"}`，取到 `asset_path`、`graph`、`revision`、节点 GUID 和引脚。节点列表可用 `scope` 或 `next_offset` 分页查看。参考页也可以用 `inspect` 读取。传 `asset_path`、`graph` 和 `open_graph=true` 可以切到已打开 Blueprint 的指定图页。
3. 调用 `apply`，带上目标三项和 `confirm=true`。一次提交节点、引脚、位置和注释。UE 按 K2 Schema 校验连线，编译失败时尝试撤回整批并返回 `rolled_back` 和编译诊断。
4. 调用 `review`，把 `apply` 返回的 `affected` 给 `scope`。可传 `focus` 定位到节点。复核画布上的分支、重叠和绕线。遮挡探测用节点边缘间的直线估算，实际曲线以 UE 画布为准。
5. 任务授权落盘时，按修整后的最新 `revision` 调用 `save`，单次授权同样传 `confirm=true`。工具保存当前 Blueprint 包，再从磁盘导出 T3D 核对节点 GUID、坐标、注释标题和连线。`saved` 是 UE 的保存结果，`readback.ok` 是独立回读的比对结果。运行表现可另用 PIE 验收。

### 一次提交的字段

| 字段 | 用法 |
|---|---|
| `nodes` | 用 `$别名` 标记新节点。支持函数调用、变量读写、分支、序列、宏、动态转换和重路由 |
| `connect`、`disconnect` | 每条给 `from`、`out`、`to`、`in`。已有节点用 GUID。引脚用 GUID 或精确内部名。覆盖连线前先列出需要断开的线 |
| `defaults` | 给未连接的可编辑输入写 `value` 或对象路径 `object` |
| `moves`、`arrange` | 指定位置，或把指定已有节点加入自动排版。未给坐标的新节点自动排版 |
| `comments` | 每组给短中文 `title` 与 `nodes`。框的尺寸随组内节点计算 |
| `route_data` | 默认打开。符合间距条件时，为本次新接入的多个数据输入加一段共用的重路由 |

排版沿用 `PrepareAttackAssist` 的走线方式。白线从左到右，`ForEachLoop.Completed` 走下方，纯数据节点靠近消费者，注释按动作分组。显式坐标可用于复核后的局部修整。

一次 `apply` 对应一个 UE 撤销项。撤销或重做后，若 UE 标记为待编译，先编译，再用 `inspect` 取新 revision。PIE 和模拟运行中，UE 图表写入暂停。

当前编辑范围是**已有 Blueprint 的 K2 图页**。函数、变量和宏节点引用项目或引擎中已有的声明。
新建资产、添加函数或变量声明、新建事件入口和删除现有节点需在 UE 中完成。

---

## readorder

推荐的由粗到细读取顺序（避免一次拉爆上下文）：

1. `status` —— 确认引擎、缓存、通道状态。任何连接类报错先调它。
2. `project_map` —— 建立项目地图，看有哪些资产类。
3. `asset` —— 读单个资产。**这是现在读任意资产的入口**：
   材质 / 材质实例 / 贴图 / 静态网格 / 骨骼网格 / 关卡 / 数据表 /
   Chooser / PoseSearch / 蓝图……同一个工具按类自动换摘要器。
4. 蓝图往下钻：`class_summary` → `graph_overview` → `flow`（`format=dsl`）
   → 只在定位到具体节点时才用 `node_detail`。
5. 动画：`montage`（时序）、`anim_asset`（AnimBP 状态机 / 序列 / 混合空间）、
   `anim_calls`（蓝图里所有 Montage 调用）。
6. `diagnose` —— 连招系统综合体检。

### `asset` 能读到什么程度

| 资产类 | 读得到 |
|---|---|
| `Material` | 混合模式、着色模型、**每个输入是常量还是连着哪个表达式**、声明的参数及默认值、表达式构成 |
| `MaterialInstanceConstant` | 父材质、参数覆盖、属性覆盖 |
| `Texture2D` | 尺寸、Mip 数、源格式、压缩设置、sRGB、LODGroup |
| `StaticMesh` | 材质槽与槽名、包围盒、碰撞类型与碰撞预设、Lightmap 设置 |
| `SkeletalMesh` | 骨架、材质槽 |
| `World`（关卡） | 已加载关卡、World Partition 配置 |
| 其它任何类 | 通用视图：有效属性（已剔除导出噪音）+ 子对象类名直方图 |

**读不到**：网格顶点、贴图像素、关卡里 Actor 的逐个序列化。这些分别需要
BulkData、图像解码、或编辑器的 `actor` 动作。工具会明确告诉你读不到，
而不是给一个空值让你以为"这个资产是空的"。

---

## troubleshoot

### 改了蓝图但读到的是旧逻辑

T3D 缓存读的是磁盘上的 `.uasset`。确认已经保存（Ctrl+S），然后再 `sync` 一次。

### 某个字段读出来是空的，结果里有 `_diag` miss

说明该 `UPROPERTY` 在当前引擎版本下没有被 Python 暴露。用 `reflect` 看引擎实际
暴露了哪些属性名。这是设计时就预期的：引擎的反射可见性是会随版本变的。

### 结果里出现 `<payload: N bytes>`

那是二进制载荷（贴图像素、网格 BulkData）的占位标记。工具**刻意不展开**它：
一张 32×32 的贴图就有 4KB 十六进制，铺开会把有用的字段淹掉。

### `editor` 动作返回 `_need_editor`

说明命令没跑在编辑器进程里（比如在 commandlet 里），或者编辑器没开、
Remote Execution 没打开。见 topic `remoteexec`。

这类动作有一个硬约束：**在 commandlet 里构造编辑器子系统会让进程直接崩掉**
（实测 `EXCEPTION_ACCESS_VIOLATION`），所以工具宁可拒绝执行也不会去试。

### 引擎找不到

`status` 会报出它解析到的引擎路径与 `.uproject` 声明的版本。混装多版本时确认
两者对不对得上；要强制指定就设 `COMBO_UE_ENGINE`。

### 输出被截断

单条响应有字符上限。用更具体的参数缩小范围：`graph_name`、`entry`、`limit`、
`tail`。

---

## topic 与别名的对应

| topic | 也能用这些词命中 |
|---|---|
| `install` | 安装、接入、配置、怎么装、setup、mcp 配置 |
| `menu` | 菜单、入口、Tools、找不到菜单 |
| `sync` | 采集、缓存、收集、导出、cache |
| `pie` | 运行、播放、Play、模拟、运行时 |
| `level` | 关卡、地图、map、子关卡 |
| `logs` | 日志、报错、log、output log |
| `remoteexec` | 远程执行、连不上、通道、引擎选择、版本 |
| `actions` | 写操作、权限、安全、开关、能不能改 |
| `blueprint_edit` | 蓝图编辑、建节点、接线、排线、注释、撤销 |
| `readorder` | 读什么、先读哪个、工具清单、asset |
| `troubleshoot` | 排查、报错、没生效、读不到 |
