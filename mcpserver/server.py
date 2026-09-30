# -*- coding: utf-8 -*-
r"""
ComboMCP / MCP stdio 服务器
===========================

MCP 的 stdio 传输就是"每行一个 JSON-RPC 2.0 消息"，因此这里用标准库直接实现，
不需要 ``mcp`` 包。这不是为了炫技——本机没有可用的系统 Python，也不该往引擎
自带的解释器里装第三方包，所以零依赖是硬约束。

同时提供 ``--selftest``：不经过 MCP 协议直接调用工具，用于挂载前验证。

注意：stdout 只能出现协议消息，一切调试输出走 stderr。
"""

import argparse
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcpserver import tools as tool_layer  # noqa: E402

SERVER_NAME = "combomcp"
SERVER_VERSION = "1.1.0"

# 支持的协议版本，新的在前
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = "2024-11-05"

# 单条响应文本上限；超出则截断并明确告知（静默截断比报错更危险）
MAX_TEXT_CHARS = 220_000

INSTRUCTIONS = """\
这是 ComboMCP：一个**只读**的 Unreal Engine 蓝图与动画资产读取服务，专门服务于
战斗连招系统的调试与答疑。它绝不修改任何资产。

## 它连接到哪里（两条路径，互不依赖）
它**不需要编辑器一直开着**，也不需要在编辑器里勾选任何选项：

1. **T3D 缓存（主力）** —— 蓝图结构、Montage 时序、AnimBP 状态机都在
   `Plugins/ComboMCP/cache/t3d/` 里。命中缓存时全部本地解析，毫秒级返回，
   编辑器开不开都一样。缓存由 `sync` 工具采集：它以
   `UnrealEditor-Cmd -run=PythonScript` 的方式**另起一个进程**把资产导成 T3D
   文本，与编辑器内的任何通道设置无关，编辑器开着也能正常跑。
2. **Unreal Editor 反射（可选）** —— 只有在缓存里没有该资产、又需要实时读取时
   才会用到。它走引擎的 Python Remote Execution 通道，需要编辑器在运行；
   该通道默认关闭，没开也不影响上面那条主力路径。

唯一读不到的情况：在编辑器里改了蓝图**但还没保存**——缓存读的是磁盘上的
`.uasset`，保存后再 `sync` 一次即可。

## 推荐的读取顺序（由粗到细，避免一次拉爆上下文）
1. `status` —— 确认通道与索引状态。任何连接类报错先调它。
2. `project_map` —— 建立项目地图，拿到蓝图清单。
3. `class_summary` —— 看某个类里有什么（变量 / 函数 / 事件 / 组件 / 图清单）。
4. `graph_overview` —— 看某个图有哪些节点类型、入口在哪，据此决定展开哪个。
5. `flow` —— 读执行逻辑。**默认用 format=dsl**，它比结构化 JSON 省一个数量级的 token，
   而且更接近伪代码。只在需要精确引脚信息时才用 format=json。
6. `node_detail` —— 只在你已经定位到具体节点、需要看它的引脚与默认值时用。

## 两条取数路径，以及结果里的 `source` 字段
读图有两条路，读取类工具默认 `source="auto"` 自动择路：

* `reflect` —— 引擎反射（``get_editor_property``）。快，但引擎对
  ``UEdGraphNode::Pins`` 并不友好：该成员在 EdGraphNode.h 里没有 ``UPROPERTY()`` 宏，
  官方 Python 文档里 ``unreal.EdGraphNode`` 也几乎不暴露属性。所以反射有时能列出节点、
  却读不到任何引脚与连线。
* `t3d` —— 引擎自己写的 T3D 文本导出。引脚（``CustomProperties Pin``）与连线
  （``LinkedTo``）在里面是完整的，因此图逻辑以它为准。

**看结果里的 `source` 字段**就知道实际走了哪条路。如果 `source=reflect` 却读不出执行流，
把 `source="t3d"` 显式传一次。`audit` 默认就走 T3D，因为判活分析必须有真正的连线。
第一次 T3D 读取会多花一两秒（导出 + 解析），之后有缓存。

## 回答"为什么连招接不上"的专门路径
1. `diagnose`（component_path 指向你的战斗组件，例如
   `/Game/YourContent/Component/AC_Combat`）
   —— 它会同时看蓝图侧的 Montage 调用点和动画侧的 Notify/Section 时序，直接给出
   带证据和修改方向的问题清单。
2. `montage`（analyze=true）—— 单个 Montage 的时序细节。
3. `anim_calls` —— 蓝图里所有 Montage 相关调用及其目标资产、Section 字面量。
4. `flow` —— 把某段连招的蓝图控制流读出来，对照时序看。

## 跨蓝图的引用问题
* `index`（action=callers）—— 谁调用了某函数，例如 Montage_Play、SetNextSection。
* `index`（action=variables）—— 谁读写了某变量，例如连招索引、状态枚举。
* `find_refs` —— 资产级的引用/依赖。
* `index`（action=build）在索引缺失或明显过期时需要先跑一次（较慢，之后有缓存）。

## 重要约定
* 所有资产路径用 UE 对象路径形式：`/Game/<目录>/<资产名>`。
* 每条读取结果都带 `_diag` 字段。如果它报告了 miss，说明该字段在当前引擎版本下
  读不到——这不代表资产有问题，用 `reflect` 可以看引擎实际暴露了什么。
* 输出里的 `findings` 条目带 `severity` / `evidence` / `hint`。`hint` 是**修改方向的
  建议**，不是结论；请结合证据和 `flow` 读出的真实控制流自行判断。
* 本服务不写入、不编译、不保存。若问题需要改动资产，请把修改方案讲清楚交给用户。
"""


# ====================================================================== 输出


def _emit(message):
    """写一条 JSON-RPC 消息到 stdout。"""
    try:
        line = json.dumps(message, ensure_ascii=False, default=str)
    except Exception as exc:
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32603, "message": "result not serializable: %s" % exc},
        })
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _log(message):
    sys.stderr.write("[combomcp] %s\n" % message)
    sys.stderr.flush()


def _result(request_id, result):
    _emit({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    _emit({"jsonrpc": "2.0", "id": request_id, "error": err})


# ====================================================================== 工具结果包装


def _to_content(payload):
    """把工具返回的 dict 变成 MCP content 数组。"""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
        except Exception as exc:
            text = json.dumps({"error": "serialization failed: %s" % exc})

    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + (
            "\n\n... [输出被截断：原文 %d 字符，上限 %d。"
            "请用更具体的参数缩小范围，例如指定 graph_name 或 entry。]"
            % (len(text), MAX_TEXT_CHARS))

    return [{"type": "text", "text": text}]


# ====================================================================== 协议处理


def handle_initialize(request_id, params):
    requested = (params or {}).get("protocolVersion")
    protocol = requested if requested in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
    _result(request_id, {
        "protocolVersion": protocol,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    })


def handle_tools_list(request_id):
    _result(request_id, {"tools": tool_layer.list_tools()})


def handle_tools_call(request_id, params):
    params = params or {}
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not name:
        _error(request_id, -32602, "tools/call requires 'name'")
        return

    payload, is_error = tool_layer.call_tool(name, arguments)
    result = {"content": _to_content(payload)}
    if is_error:
        result["isError"] = True
    _result(request_id, result)


def handle_request(message):
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params")

    # 通知（无 id）一律不回复
    if request_id is None:
        if method == "notifications/initialized":
            _log("client initialized")
        elif method == "notifications/cancelled":
            pass
        return

    if method == "initialize":
        handle_initialize(request_id, params)
    elif method == "tools/list":
        handle_tools_list(request_id)
    elif method == "tools/call":
        handle_tools_call(request_id, params)
    elif method == "ping":
        _result(request_id, {})
    elif method in ("resources/list", "prompts/list"):
        # 本服务只桥接工具能力；明确回答空集合而不是报错
        key = method.split("/")[0]
        _result(request_id, {key: []})
    else:
        _error(request_id, -32601, "method not found: %s" % method)


def serve_stdio():
    _log("listening on stdio (engine root: %s)" %
         os.environ.get("COMBO_UE_ENGINE", "<auto>"))
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception as exc:
            _log("bad JSON on stdin: %s" % exc)
            continue

        try:
            handle_request(message)
        except Exception:
            _log("handler crashed:\n%s" % traceback.format_exc())
            if message.get("id") is not None:
                _error(message["id"], -32603, "internal error",
                       traceback.format_exc()[-1500:])


# ====================================================================== 自检


def run_selftest(tool_name, args_json):
    """不经过 MCP 协议直接调工具，用于挂载前验证。"""
    try:
        arguments = json.loads(args_json) if args_json else {}
    except Exception as exc:
        _log("bad --args JSON: %s" % exc)
        return 2

    payload, is_error = tool_layer.call_tool(tool_name, arguments)
    text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write(text + "\n")
    return 1 if is_error else 0


def list_tool_names():
    for tool in tool_layer.list_tools():
        sys.stdout.write("%-16s %s\n" % (tool["name"], tool["description"].split("\n")[0][:90]))
    return 0


# ====================================================================== 入口


def main():
    parser = argparse.ArgumentParser(prog="combomcp", add_help=True)
    parser.add_argument("--selftest", metavar="TOOL",
                        help="直接调用某个工具并打印结果（不启动 MCP 协议）")
    parser.add_argument("--args", default="{}",
                        help="配合 --selftest 的 JSON 参数")
    parser.add_argument("--list-tools", action="store_true",
                        help="列出工具名与说明")
    parser.add_argument("--engine", default=None,
                        help="引擎根目录，覆盖自动探测")
    args = parser.parse_args()

    if args.engine:
        os.environ["COMBO_UE_ENGINE"] = args.engine

    if args.list_tools:
        return list_tool_names()
    if args.selftest:
        return run_selftest(args.selftest, args.args)

    serve_stdio()
    return 0


if __name__ == "__main__":
    sys.exit(main())
