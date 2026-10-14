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
2. **重启编辑器。** 插件只在启动时加载，热重载对它无效。
3. 重启后在 **Edit → Plugins** 里应能看到 **Combo Blueprint MCP**。

不需要改 `.uproject`——`.uplugin` 里 `EnabledByDefault: true` 会自行生效。
不需要编译：`.uplugin` 的 `"Modules": []` 表示它**不含 C++**。
不需要 Node.js，也不需要系统 Python：它用 UE 自带的 Python。

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

**写操作默认是关闭的。** 这条边界是刻意的：读资产占了日常使用的绝大多数，
不该为了少数场景让每一次问答都带着"能改我项目"的能力。

### 哪些算写操作

| 动作 | 写？ |
|---|---|
| `pie sub=start` / `pie sub=stop` / `pie sub=simulate` | 是 |
| `level sub=load` / `new` / `save` / `add` | 是 |
| 其余全部（`logs`、`level sub=current/list`、`actor`、所有读取类工具） | 否 |

### 怎么打开

任选其一：

1. **环境变量**（推荐，长期生效）：设 `COMBO_ALLOW_ACTIONS=1`，然后重启 MCP 客户端
   （MCP 服务是子进程，环境变量在它启动时读取）。
2. **单次授权**：调用时传 `confirm=true`，只对这一次调用生效。

没授权时写操作会返回一条带 `_actions_blocked` 的说明，而不是静默失败。

### 明确不做的事

- **不执行任意 console 命令。** 那等于把整个引擎控制台交出去，边界会失控。
- **不保存任意资产。** 只有显式 `level sub=save`（保存当前关卡，且需要授权）。
- **不创建/修改/删除资产。** 顶点级编辑、材质改参数这类操作不在范围内。

本插件**只读部分的承诺仍然成立**：读取类工具不写入、不编译、不保存。

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
| `readorder` | 读什么、先读哪个、工具清单、asset |
| `troubleshoot` | 排查、报错、没生效、读不到 |
