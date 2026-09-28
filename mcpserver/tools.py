# -*- coding: utf-8 -*-
r"""
ComboMCP / 工具层
=================

工具面刻意保持精简（15 个）。原因很实际：工具定义会进入**每一次**模型请求，
一个 100 工具的服务在模型还没读到你的问题之前就已经花掉几万 token。

读取按**由粗到细**分层，模型可以先看摘要再决定要不要展开：
``class_summary`` → ``graph_overview`` → ``flow``/``node_detail``。

命名不带前缀，因为 MCP 客户端会自动加上 ``mcp__<server>__`` 命名空间。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import collector as collector_mod      # noqa: E402
from engine import offline                        # noqa: E402
from engine import search as index_search           # noqa: E402
from engine.bridge import UEBridge, BridgeError     # noqa: E402


# ====================================================================== 桥接单例


_bridge = None


def get_bridge():
    global _bridge
    if _bridge is None:
        _bridge = UEBridge()
    return _bridge


# 桥接失败的标记：出现这些就把请求转给离线缓存
_BRIDGE_FAILURES = ("disconnected", "transport_error", "command_error",
                    "protocol_error", "not_configured")


def _bridge_failed(result):
    return isinstance(result, dict) and result.get("_bridge") in _BRIDGE_FAILURES


def _call(command, args=None):
    """调用编辑器；连不上时**自动转离线缓存**。

    离线不是降级方案而是主力：Python 的 ``get_editor_property`` 会拒绝裸
    ``UPROPERTY()``（``UEdGraph::Nodes`` / ``UAnimMontage::CompositeSections``
    都是裸的），所以反射本来就读不出图连线与 Montage 段落；T3D 缓存反而更全。
    只要缓存里有这个资产，就直接走离线——更快，也不会因为编辑器没开而失败。
    """
    args = args or {}
    cache_key = args.get("asset_path")

    # 有缓存就优先离线：本地解析是毫秒级，且信息比反射更全
    if cache_key and offline.has_cache(cache_key):
        local = _offline_for(command, args)
        if local is not None:
            return local

    try:
        bridge = get_bridge()
    except Exception as exc:
        result = {"error": str(exc), "_bridge": "not_configured"}
    else:
        result = bridge.call(command, args)

    if _bridge_failed(result):
        local = _offline_for(command, args)
        if local is not None:
            if isinstance(local, dict):
                local.setdefault("_fallback_from", result.get("_bridge"))
            return local
    return result


def _offline_for(command, args):
    """把一个 UE 侧命令映射到等价的离线实现；没有等价实现则返回 None。

    ``montage_detail`` / ``montage_timeline_analysis`` 只能走这条——
    Montage 的 Section/Notify 在反射下是 PermissionDenied。
    """
    asset = args.get("asset_path")
    if not asset:
        return None
    try:
        if command == "class_summary":
            return offline.class_summary(asset)
        if command == "graph_overview":
            return offline.graph_overview(asset, args.get("graph_name"))
        if command in ("graph_dsl", "exec_flow"):
            return offline.graph_dsl(asset, args.get("graph_name"),
                                     args.get("entry"))
        if command == "node_detail":
            if args.get("graph_name") is None or args.get("node_index") is None:
                return None
            return offline.node_detail(asset, args["graph_name"],
                                       args["node_index"])
        if command == "audit_blueprint_logic":
            return offline.audit(asset, args.get("graph_name"))
        if command == "montage_detail":
            return offline.montage_detail(asset, analyze=bool(args.get("analyze")))
        if command == "montage_timeline_analysis":
            return offline.montage_analysis(asset)
        if command == "extract_animation_calls":
            return offline.find_animation_calls(asset)
        if command == "animblueprint_detail":
            return offline.anim_blueprint(asset, full=bool(args.get("full")))
        if command == "generic_asset_detail":
            return offline.generic_asset(asset)
    except Exception as exc:
        return {"error": "offline %s failed: %s: %s"
                         % (command, type(exc).__name__, exc),
                "_offline_error": True}
    return None


# ====================================================================== 工具实现


def t_status(params):
    """连通性 + 环境 + 索引/缓存状态。"""
    out = _call("env_info")
    try:
        out["bridge"] = get_bridge().stats()
    except Exception as exc:
        out["bridge_error"] = str(exc)
    out["index"] = index_search.index_status()
    cached = offline.list_cached()
    out["t3d_cache"] = {
        "count": len(cached),
        "total_kb": sum(c.get("bytes", 0) for c in cached) // 1024,
        "assets": len(offline.load_index()),
        "hint": ("T3D 缓存是主力读取路径：Python 反射会被裸 UPROPERTY 拒绝"
                 "（UEdGraph::Nodes / UAnimMontage::CompositeSections），"
                 "而 T3D 里这些数据是完整的。缓存存在时工具直接走本地解析，"
                 "不需要编辑器在线。用 sync 收集更多资产。"),
    }
    if "error" in out:
        out["hint"] = ("编辑器没有响应——但只要有 T3D 缓存，图逻辑与 Montage "
                       "时序仍然可读。用 sync 收集，然后照常提问。")
    return out


def t_sync(params):
    """收集资产到 T3D 缓存（headless commandlet，不需要 Remote Execution）。"""
    assets = params.get("assets") or []
    scope = params.get("scope")
    force = bool(params.get("force"))

    discovered = None
    if not assets:
        if not scope:
            return {"error": "请给 assets 列表，或给 scope 让我先发现。"}
        discovered = collector_mod.discover_assets(
            scope, params.get("classes"), limit=int(params.get("limit", 400)))
        if "error" in discovered:
            return discovered
        assets = [a["path"] for a in discovered.get("assets") or []]
        if not assets:
            return {"error": "在 %s 下没发现可收集的资产" % scope,
                    "discovered_total": discovered.get("total")}

    result = collector_mod.collect(assets, force=force)
    if discovered is not None:
        result["discovered_total"] = discovered.get("total")
    if "error" not in result:
        cached = offline.list_cached()
        result["cache_count"] = len(cached)
        result["hint"] = ("收集完成后，class_summary / graph_overview / flow / "
                          "node_detail / audit / montage 都会直接走本地缓存，"
                          "毫秒级返回且不需要编辑器在线。")
    return result


def t_project_map(params):
    return _call("project_overview", {
        "scope": params.get("scope", "/Game"),
    })


def t_search(params):
    query = params.get("query")
    if not query:
        return {"error": "query is required"}
    return _call("search_assets", {
        "query": query,
        "scope": params.get("scope", "/Game"),
        "class_filter": params.get("class_filter"),
        "limit": int(params.get("limit", 60)),
    })


def t_find_refs(params):
    asset_path = params.get("asset_path")
    if not asset_path:
        return {"error": "asset_path is required"}
    direction = (params.get("direction") or "referencers").lower()
    if direction.startswith("dep"):
        return _call("find_dependencies", {"asset_path": asset_path})
    return _call("find_references", {"asset_path": asset_path})


def t_index(params):
    """蓝图索引：build 让编辑器扫描，其余查询走本地缓存。"""
    action = (params.get("action") or "status").lower()

    if action == "status":
        return index_search.index_status()

    if action == "build":
        scope = params.get("scope", "/Game")
        result = _call("build_index", {
            "scope": scope,
            "limit": int(params.get("limit", 600)),
        })
        if "error" in result:
            return result
        record = index_search.save_index(result, scope=scope)
        return {
            "ok": True,
            "scope": scope,
            "blueprint_total": record.get("blueprint_total"),
            "scanned": record.get("scanned"),
            "truncated": record.get("truncated"),
            "built_at": record.get("built_at_text"),
            "failed": (result.get("failed") or [])[:10],
            "hint": "索引已落盘，之后的 index 查询不再需要编辑器。",
        }

    # 其余动作都基于本地缓存
    record, err = index_search.load_index()
    if record is None:
        return {"error": err}

    if action == "callers":
        name = params.get("query")
        if not name:
            return {"error": "query (function name) is required for action=callers"}
        return index_search.callers_of(record, name, limit=int(params.get("limit", 60)))

    if action == "variables":
        name = params.get("query")
        if not name:
            return {"error": "query (variable name) is required for action=variables"}
        return index_search.variables_of(record, name, limit=int(params.get("limit", 60)))

    if action == "query":
        name = params.get("query")
        if not name:
            return {"error": "query is required for action=query"}
        return index_search.query(
            record, name,
            mode=params.get("mode", "any"),
            limit=int(params.get("limit", 40)),
        )

    return {"error": "unknown action: %s" % action,
            "valid": ["status", "build", "query", "callers", "variables"]}


def t_class_summary(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    return _call("class_summary", {"asset_path": path})


def t_graph_overview(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    return _call("graph_overview", {
        "asset_path": path,
        "graph_name": params.get("graph_name"),
        "source": params.get("source", "auto"),
    })


def t_flow(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    fmt = (params.get("format") or "dsl").lower()
    result = _call("graph_dsl", {
        "asset_path": path,
        "graph_name": params.get("graph_name"),
        "entry": params.get("entry"),
        "source": params.get("source", "auto"),
        "format": "json" if fmt == "json" else "dsl",
    })
    if isinstance(result, dict) and "dsl" in result:
        result["format"] = "S-expression pseudo code"
    return result


def t_node_detail(params):
    path = params.get("asset_path")
    graph = params.get("graph_name")
    if not path or not graph:
        return {"error": "asset_path and graph_name are required"}
    if params.get("node_index") is None:
        return {"error": "node_index is required (see graph_overview / flow output)"}
    return _call("node_detail", {
        "asset_path": path,
        "graph_name": graph,
        "node_index": int(params["node_index"]),
        "source": params.get("source", "auto"),
    })


def t_montage(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    if params.get("analyze"):
        return _call("montage_timeline_analysis", {"asset_path": path})
    return _call("montage_detail", {
        "asset_path": path,
        "with_notify_props": bool(params.get("with_notify_props", True)),
    })


def t_anim_asset(params):
    """按资产类型分派到对应的动画读取器。"""
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    kind = (params.get("kind") or "auto").lower()

    # 有缓存就直接走离线：AnimBP 的状态机与 Chooser/PoseSearch 的内容
    # 在 T3D 里才完整（反射读不到图结构）
    if offline.has_cache(path):
        if kind in ("auto", "animblueprint"):
            probe = offline.generic_asset(path)
            cls = str((probe or {}).get("class") or "")
            if kind == "animblueprint" or cls in ("AnimBlueprint", "AnimationBlueprint"):
                return offline.anim_blueprint(path, full=bool(params.get("full")))
            if kind == "generic":
                return probe
            if cls in ("ChooserTable", "PoseSearchDatabase", "DataTable",
                       "UserDefinedStruct"):
                return probe
            # 其余类型继续往下走通用分派
        entry = {
            "montage": lambda: offline.montage_detail(path),
            "sequence": lambda: offline.generic_asset(path),
            "notify": lambda: offline.class_summary(path),
            "generic": lambda: offline.generic_asset(path),
        }.get(kind)
        if entry is not None:
            return entry()

    if kind == "auto":
        # 先探一次类名再决定用哪个读取器
        probe = _call("generic_asset_detail", {"asset_path": path})
        cls = ""
        if isinstance(probe, dict):
            cls = str(probe.get("class") or "")
        if cls == "AnimMontage":
            kind = "montage"
        elif cls == "AnimBlueprint":
            kind = "animblueprint"
        elif cls in ("AnimSequence", "AnimComposite", "BlendSpace", "BlendSpace1D"):
            kind = "sequence"
        elif cls == "Blueprint":
            kind = "notify"
        else:
            return probe

    dispatch = {
        "montage": ("montage_detail", {"asset_path": path}),
        "animblueprint": ("animblueprint_detail", {"asset_path": path}),
        "sequence": ("sequence_detail", {"asset_path": path}),
        "notify": ("notify_detail", {"asset_path": path}),
        "generic": ("generic_asset_detail", {"asset_path": path}),
    }
    entry = dispatch.get(kind)
    if entry is None:
        return {"error": "unknown kind: %s" % kind,
                "valid": sorted(dispatch.keys()) + ["auto"]}
    return _call(entry[0], entry[1])


def t_anim_calls(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    return _call("extract_animation_calls", {"asset_path": path})


def t_diagnose(params):
    """连招系统综合诊断——把蓝图侧与动画侧合起来看。"""
    component = params.get("component_path")
    if not component:
        return {"error": "component_path is required (e.g. "
                         "/Game/YourContent/Component/AC_Combat)"}
    return _call("analyze_combo_system", {
        "component_path": component,
        "montage_scope": params.get("montage_scope", "/Game"),
        "max_montages": int(params.get("max_montages", 24)),
    })


def t_audit(params):
    path = params.get("asset_path")
    if not path:
        return {"error": "asset_path is required"}
    return _call("audit_blueprint_logic", {
        "asset_path": path,
        "graph_name": params.get("graph_name"),
        "source": params.get("source", "auto"),
    })


def t_reflect(params):
    """诊断用：反射枚举一个节点的全部可读属性。

    当某个读取器返回的字段明显缺失时，用它来看引擎这一版到底暴露了什么。
    """
    path = params.get("asset_path")
    graph = params.get("graph_name")
    if not path or not graph or params.get("node_index") is None:
        return {"error": "asset_path, graph_name and node_index are required"}
    return _call("node_reflect", {
        "asset_path": path,
        "graph_name": graph,
        "node_index": int(params["node_index"]),
    })


# ====================================================================== 工具定义


_TOOLS = [
    {
        "name": "status",
        "description": (
            "检查与 Unreal Editor 的连接、引擎/项目信息、以及蓝图索引的新鲜度。"
            "任何其他工具报连接错误时先调这个。"),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_status,
    },
    {
        "name": "sync",
        "description": (
            "把资产导出成 T3D 落到本地缓存（走 headless commandlet，"
            "**不需要编辑器开着 Remote Execution**）。"
            "给 assets 列表直接收集；或给 scope 让我先发现该范围内的蓝图/动画资产。"
            "已缓存的默认跳过，force=true 才重导。"
            "收集后所有读取类工具都走本地缓存，毫秒级返回。"
            "注意：每次调用要加载一次项目（首次约 1 分钟，之后约 10 秒），"
            "所以请一次多给几个资产，不要一个资产调一次。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "assets": {"type": "array", "items": {"type": "string"},
                           "description": "要收集的资产对象路径列表"},
                "scope": {"type": "string",
                          "description": "没给 assets 时，先在这个目录下发现资产"},
                "classes": {"type": "array", "items": {"type": "string"},
                            "description": "发现的资产类过滤，默认蓝图与动画类"},
                "limit": {"type": "integer", "description": "发现上限，默认 400"},
                "force": {"type": "boolean", "description": "强制重导，默认 false"},
            },
        },
        "handler": t_sync,
    },
    {
        "name": "project_map",
        "description": (
            "项目资产地图：按资产类统计数量并列出全部蓝图路径。"
            "用来建立整体认知，回答'这个项目里有什么'。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string",
                          "description": "资产根路径，默认 /Game"},
            },
        },
        "handler": t_project_map,
    },
    {
        "name": "search",
        "description": "按路径子串搜索资产，可按资产类过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "路径子串，大小写不敏感"},
                "scope": {"type": "string", "description": "搜索根，默认 /Game"},
                "class_filter": {"type": "string",
                                 "description": "资产类名，如 Blueprint / AnimMontage"},
                "limit": {"type": "integer", "description": "默认 60"},
            },
            "required": ["query"],
        },
        "handler": t_search,
    },
    {
        "name": "find_refs",
        "description": (
            "查一个资产的引用关系。direction=referencers 回答'谁用了它'，"
            "direction=dependencies 回答'它用了谁'。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "direction": {"type": "string",
                              "enum": ["referencers", "dependencies"]},
            },
            "required": ["asset_path"],
        },
        "handler": t_find_refs,
    },
    {
        "name": "index",
        "description": (
            "蓝图索引用法：action=build 让编辑器扫描项目（较慢，之后一直有缓存）；"
            "action=callers 查'谁调用了某函数'；action=variables 查'谁读写某变量'；"
            "action=query 通用检索；action=status 看索引状态。"
            "除 build 外都不需要编辑器在线。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["status", "build", "query", "callers", "variables"]},
                "query": {"type": "string", "description": "函数名 / 变量名 / 关键词"},
                "scope": {"type": "string", "description": "build 的扫描范围，默认 /Game"},
                "mode": {"type": "string",
                         "enum": ["any", "call", "var", "function", "class"],
                         "description": "action=query 时的匹配模式"},
                "limit": {"type": "integer"},
            },
        },
        "handler": t_index,
    },
    {
        "name": "class_summary",
        "description": (
            "读一个蓝图类的结构（L1）：父类、接口、变量及类型/默认值/复制标记、"
            "函数签名、事件入口、组件层级、图清单与各图节点数。"
            "回答'这个类里有什么'从这里开始。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string",
                               "description": "如 /Game/YourContent/Component/AC_Combat"},
            },
            "required": ["asset_path"],
        },
        "handler": t_class_summary,
    },
    {
        "name": "graph_overview",
        "description": (
            "读一个或多个图的结构（L2）：节点类型直方图 + 入口点列表，不含连线。"
            "用于判断'该展开哪个图'。不传 graph_name 则列出所有图。"
            "source 控制取数路径：auto（默认，自动择路）/ reflect（引擎反射）/ t3d（T3D 文本导出）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "graph_name": {"type": "string",
                               "description": "如 EventGraph；省略则全部"},
                "source": {"type": "string", "enum": ["auto", "reflect", "t3d"],
                           "description": "取数路径，默认 auto"},
            },
            "required": ["asset_path"],
        },
        "handler": t_graph_overview,
    },
    {
        "name": "flow",
        "description": (
            "读一个图的执行逻辑（L3）。format=dsl 返回 S 表达式伪代码（推荐，"
            "最省 token 最好读）；format=json 返回结构化执行流树。"
            "用 entry 参数只展开某个事件，例如 entry='Attack'。"
            "取数会自动择路：引擎反射读不到引脚连线时（UEdGraphNode.Pins 没有 "
            "UPROPERTY 暴露），自动改用 T3D 导出，结果里的 source 字段说明实际用了哪条路。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "graph_name": {"type": "string"},
                "entry": {"type": "string",
                          "description": "只展开名称包含该子串的入口事件"},
                "format": {"type": "string", "enum": ["dsl", "json"]},
                "source": {"type": "string", "enum": ["auto", "reflect", "t3d"],
                           "description": "取数路径，默认 auto"},
            },
            "required": ["asset_path"],
        },
        "handler": t_flow,
    },
    {
        "name": "node_detail",
        "description": (
            "读单个节点的全部引脚、连线与属性（L4）。"
            "node_index 来自 graph_overview 或 flow 的输出。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "graph_name": {"type": "string"},
                "node_index": {"type": "integer"},
                "source": {"type": "string", "enum": ["auto", "reflect", "t3d"]},
            },
            "required": ["asset_path", "graph_name", "node_index"],
        },
        "handler": t_node_detail,
    },
    {
        "name": "montage",
        "description": (
            "读 AnimMontage 的完整结构：槽位与动画段、Section 及其 NextSection、"
            "以及全部 Notify 的绝对起止时间。analyze=true 时额外做时序诊断"
            "（输入禁用窗口覆盖率、窗口重叠、Section 结构问题）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "analyze": {"type": "boolean",
                            "description": "true 则附带时序诊断结论"},
                "with_notify_props": {"type": "boolean",
                                      "description": "是否读取 Notify 实例上的自定义属性，默认 true"},
            },
            "required": ["asset_path"],
        },
        "handler": t_montage,
    },
    {
        "name": "anim_asset",
        "description": (
            "读其它动画资产：AnimNotify/AnimNotifyState 蓝图（kind=notify）、"
            "AnimSequence/BlendSpace（kind=sequence）、AnimBlueprint 状态机"
            "（kind=animblueprint）、以及 Chooser/PoseSearch 等插件资产（kind=generic）。"
            "kind=auto 自动判断。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "kind": {"type": "string",
                         "enum": ["auto", "notify", "sequence", "animblueprint",
                                  "montage", "generic"]},
            },
            "required": ["asset_path"],
        },
        "handler": t_anim_asset,
    },
    {
        "name": "anim_calls",
        "description": (
            "从一个蓝图里提取所有动画相关调用：Montage_Play / Montage_JumpToSection / "
            "Montage_SetNextSection / Montage_Stop 等，并带出它们引用的目标资产与 "
            "Section 字面量。回答'连招是在哪里、按什么顺序触发的'。"),
        "inputSchema": {
            "type": "object",
            "properties": {"asset_path": {"type": "string"}},
            "required": ["asset_path"],
        },
        "handler": t_anim_calls,
    },
    {
        "name": "diagnose",
        "description": (
            "战斗连招系统综合体检：把组件蓝图里的动画调用点与目录下所有 Montage "
            "的时序放在一起核对，输出带严重级别、证据和修改方向的问题清单。"
            "回答'为什么第 N 段接不上'类问题的首选工具。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "component_path": {"type": "string",
                                   "description": "战斗组件蓝图，如 /Game/YourContent/Component/AC_Combat"},
                "montage_scope": {"type": "string",
                                  "description": "Montage 搜索范围，默认 /Game"},
                "max_montages": {"type": "integer", "description": "默认 24"},
            },
            "required": ["component_path"],
        },
        "handler": t_diagnose,
    },
    {
        "name": "audit",
        "description": (
            "蓝图逻辑审计：孤立节点（没有执行流到达、也不是任何可达节点的数据来源）、"
            "执行输出悬空（流程断头）、图规模统计。"
            "默认走 T3D 路径——它拿得到真正的连线，判活分析才准确。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "graph_name": {"type": "string", "description": "省略则审计所有图"},
                "source": {"type": "string", "enum": ["auto", "reflect", "t3d"]},
            },
            "required": ["asset_path"],
        },
        "handler": t_audit,
    },
    {
        "name": "reflect",
        "description": (
            "诊断用：反射枚举一个节点在当前引擎版本下所有可读的 Python 属性。"
            "当别的工具返回的字段明显缺失时，用它确认引擎到底暴露了什么。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "graph_name": {"type": "string"},
                "node_index": {"type": "integer"},
            },
            "required": ["asset_path", "graph_name", "node_index"],
        },
        "handler": t_reflect,
    },
]


def list_tools():
    """返回可发给客户端的工具定义（去掉内部 handler）。"""
    out = []
    for tool in _TOOLS:
        out.append({
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        })
    return out


_HANDLERS = dict((t["name"], t["handler"]) for t in _TOOLS)


def call_tool(name, arguments):
    """执行工具。返回 ``(payload_dict, is_error)``。"""
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": "unknown tool: %s" % name,
                "available": sorted(_HANDLERS.keys())}, True
    try:
        result = handler(arguments or {})
    except BridgeError as exc:
        return {"error": str(exc)}, True
    except Exception as exc:
        import traceback
        return {"error": "%s: %s" % (type(exc).__name__, exc),
                "traceback": traceback.format_exc()[-2000:]}, True

    if not isinstance(result, dict):
        return {"result": result}, False
    return result, ("error" in result)
