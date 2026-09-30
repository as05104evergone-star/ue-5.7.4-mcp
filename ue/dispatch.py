# -*- coding: utf-8 -*-
r"""
ComboMCP / UE 内命令分发
========================

外部 MCP server 只发送 ``(command, args)``，由本模块路由到具体实现。
这样外部侧不需要知道任何 UE 反射细节，UE 版本相关的适配全部收敛在 ``ue/`` 下。
"""

import traceback

from . import anim_read
from . import bp_read
from . import domain
from . import index
from . import pie_state
from . import runtime as rt
from . import t3d_read


# ====================================================================== 择路


def _reflection_graph_usable(result):
    """判断反射路径拿到的图数据是否真的可用。

    引擎反射对 ``UEdGraphNode::Pins`` 并不友好（该成员没有 UPROPERTY 宏）。
    当引脚读不到时，反射路径仍会返回节点列表，但每个节点的 ``pins`` 都是空的——
    那种结果看起来"成功"，实际什么逻辑都读不出来。所以这里必须显式识别它。
    """
    if not isinstance(result, dict) or "error" in result:
        return False
    for graph in result.get("graphs") or []:
        for flow in graph.get("flows") or []:
            if flow.get("body"):
                return True
        if graph.get("node_count"):
            # 有节点但一条执行流都走不出来，说明连线是空的
            continue
    return False


def flow_auto(asset_path, graph_name=None, entry=None, source="auto", format="dsl"):
    """读取执行逻辑，自动在反射与 T3D 之间择路。

    ``source``:
      * ``auto``    —— 先试反射；若走不出执行流，改用 T3D（默认）
      * ``reflect`` —— 只用引擎反射
      * ``t3d``     —— 只用 T3D 导出
    """
    source = (source or "auto").lower()

    if source in ("auto", "reflect"):
        if format == "json":
            result = bp_read.exec_flow(asset_path, graph_name=graph_name, entry=entry)
        else:
            result = bp_read.graph_dsl(asset_path, graph_name=graph_name, entry=entry)

        if source == "reflect":
            result["source"] = "reflect"
            return result
        if _reflection_graph_usable(result):
            result["source"] = "reflect"
            return result
        fallback_note = ("反射路径读不到连线（UEdGraphNode.Pins 在当前引擎版本下"
                         "没有 UPROPERTY 暴露），已自动改用 T3D 导出。")

    if format == "json":
        result = t3d_read.graph_overview(asset_path, graph_name=graph_name)
    else:
        result = t3d_read.graph_dsl(asset_path, graph_name=graph_name, entry=entry)
    result["source"] = "t3d"
    if source == "auto":
        result["_fallback_note"] = fallback_note
    return result


def overview_auto(asset_path, graph_name=None, source="auto"):
    """图概览，同样自动择路。"""
    source = (source or "auto").lower()
    if source in ("auto", "reflect"):
        result = bp_read.graph_overview(asset_path, graph_name=graph_name)
        if source == "reflect" or "error" not in result:
            result["source"] = "reflect"
            return result
    result = t3d_read.graph_overview(asset_path, graph_name=graph_name)
    result["source"] = "t3d"
    return result


def node_auto(asset_path, graph_name, node_index, source="auto"):
    """节点详情，同样自动择路。"""
    source = (source or "auto").lower()
    if source in ("auto", "reflect"):
        result = bp_read.node_detail(asset_path, graph_name, node_index)
        if "error" not in result:
            pins = result.get("pins") or []
            if pins:
                result["source"] = "reflect"
                return result
            if source == "reflect":
                result["source"] = "reflect"
                return result
    result = t3d_read.node_detail(asset_path, graph_name, node_index)
    result["source"] = "t3d"
    return result


def audit_auto(asset_path, graph_name=None, source="auto"):
    """逻辑审计：T3D 路径的判活分析更完整（它有真正的连线）。"""
    source = (source or "auto").lower()
    if source == "reflect":
        result = domain.audit_blueprint_logic(asset_path, graph_name=graph_name)
        result["source"] = "reflect"
        return result
    result = t3d_read.audit(asset_path, graph_name=graph_name)
    result["source"] = "t3d"
    return result


# ====================================================================== 环境


def ping():
    """连通性自检：确认通道可用并回报环境信息。"""
    import sys
    info = {
        "ok": True,
        "python": sys.version.split()[0],
    }
    try:
        import unreal
        info["engine_version"] = unreal.SystemLibrary.get_engine_version()
        info["project_dir"] = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_dir())
        info["content_dir"] = unreal.Paths.convert_relative_path_to_full(
            unreal.Paths.project_content_dir())
        info["project_name"] = unreal.Paths.get_project_file_path().rsplit("/", 1)[-1]
    except Exception as exc:
        info["unreal_probe_error"] = str(exc)
    return info


def env_info():
    """项目与引擎的详细环境信息。"""
    import unreal
    out = ping()
    try:
        out["plugins"] = sorted([
            str(p) for p in unreal.Paths.get_extension_dirs("Plugins")
        ]) if hasattr(unreal.Paths, "get_extension_dirs") else None
    except Exception:
        pass
    try:
        # /Game 下的资产统计，作为开箱即用的项目地图
        paths = unreal.EditorAssetLibrary.list_assets("/Game", recursive=True)
        out["game_asset_count"] = len(paths or [])
    except Exception as exc:
        out["combo_demo_probe_error"] = str(exc)
    return out


# ====================================================================== 分发表


COMMANDS = {
    # 环境
    "ping": ping,
    "env_info": env_info,

    # 资产 / 索引
    "project_overview": index.project_overview,
    "list_assets": index.list_assets,
    "search_assets": index.search_assets,
    "find_references": index.find_references,
    "find_dependencies": index.find_dependencies,
    "build_index": index.build_index,

    # 蓝图（反射路径）
    "class_summary": bp_read.class_summary,
    "graph_overview": overview_auto,
    "exec_flow": flow_auto,
    "graph_dsl": flow_auto,
    "node_detail": node_auto,
    "node_reflect": bp_read.node_reflect,

    # 蓝图（T3D 路径，反射读不到引脚时的主力）
    "t3d_graphs": t3d_read.list_graphs,
    "t3d_overview": t3d_read.graph_overview,
    "t3d_dsl": t3d_read.graph_dsl,
    "t3d_node": t3d_read.node_detail,
    "t3d_audit": t3d_read.audit,

    # 动画资产
    "montage_detail": anim_read.montage_detail,
    "notify_detail": anim_read.notify_detail,
    "sequence_detail": anim_read.sequence_detail,
    "animblueprint_detail": anim_read.animblueprint_detail,
    "generic_asset_detail": anim_read.generic_asset_detail,

    # 领域诊断
    "montage_timeline_analysis": domain.montage_timeline_analysis,
    "extract_animation_calls": domain.extract_animation_calls,
    "audit_blueprint_logic": audit_auto,
    "analyze_combo_system": domain.analyze_combo_system,

    # PIE 运行时（唯一需要 game world 的命令；没有 PIE 时返回 pie_running=false）
    "pie_state": pie_state.pie_state,
}


def dispatch(command, args):
    """统一入口。永不抛出——错误以 ``{"error": ...}`` 形式返回。"""
    fn = COMMANDS.get(command)
    if fn is None:
        return {
            "error": "unknown command: %s" % command,
            "available": sorted(COMMANDS.keys()),
        }
    try:
        result = fn(**(args or {}))
        if result is None:
            return {"error": "command returned nothing", "command": command}
        return result
    except TypeError as exc:
        return {
            "error": "bad arguments for '%s': %s" % (command, exc),
            "command": command,
            "args_received": sorted((args or {}).keys()),
        }
    except Exception as exc:
        return {
            "error": "%s: %s" % (type(exc).__name__, exc),
            "command": command,
            "traceback": traceback.format_exc()[-3000:],
        }
