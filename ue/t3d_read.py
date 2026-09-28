# -*- coding: utf-8 -*-
r"""
ComboMCP / T3D 导出（在编辑器内执行，只读）
==========================================

把一个资产导出成 T3D 文本，然后就地解析成 IR。

为什么需要它：引擎反射对 ``UEdGraphNode::Pins`` 不友好——该成员在
``EdGraphNode.h`` 里没有 ``UPROPERTY()`` 宏，官方 Python 文档里
``unreal.EdGraphNode`` 也几乎不暴露属性。而 T3D 是引擎自己写出来的文本，
引脚（``CustomProperties Pin``）与连线（``LinkedTo``）在里面是完整的。

因此这里的策略是：

* **优先反射**（快，且能拿到变量与函数签名等 T3D 不完整的信息）
* **反射读不到图结构时，落到 T3D**（能拿到完整的节点、引脚、连线）

导出会写一个临时文件到 ``cache/t3d/``——这是本工具自己的缓存目录，
不是项目资产目录，且每次导出覆盖同名文件。

本模块的一切操作都是只读的（对项目资产而言）。
"""

import os
import sys

import unreal  # noqa: F401

from . import graph_ir
from . import runtime as rt
from . import t3d as t3d_parser


T3D_DIR_NAME = "t3d"

# T3D 导出器类名。UE 把 UCLASS 的 U 前缀去掉作为 Python 名，所以
# ``UObjectExporterT3D`` 在 Python 里是 ``unreal.ObjectExporterT3D``——
# 这个名字在 5.7 的 Engine/Source/Editor/UnrealEd/Classes/Exporters/ 下确认存在。
# 后两个是退路（不同引擎版本里导出器的归属与命名变过）。
_EXPORTER_CANDIDATES = (
    "ObjectExporterT3D",
    "TextBufferExporterTXT",
    "ObjectExporter",
    "Exporter",
)


def _set_prop(obj, names, value):
    """按候选名依次尝试写 UPROPERTY，任一成功即返回。

    UE 的 Python 绑定对 bool 属性会去掉 ``b`` 前缀（``bAutomated`` -> ``automated``），
    但不同版本对这个转换的处理并不一致，所以两种写法都试。
    """
    if isinstance(names, str):
        names = (names,)
    last_error = None
    for name in names:
        try:
            obj.set_editor_property(name, value)
            return name, None
        except Exception as exc:
            last_error = exc
    return None, last_error


def _get_prop(obj, names, default=None):
    if isinstance(names, str):
        names = (names,)
    for name in names:
        try:
            return obj.get_editor_property(name)
        except Exception:
            continue
    return default


def _cache_dir():
    """返回本工具自己的缓存目录。

    注意：这些模块是被 bootstrap 以 ``exec`` 送进编辑器的，``__file__`` 是
    一个假路径（``<combomcp/t3d_read>``），拿它推断目录会落到编辑器的工作目录下。
    所以优先使用 bootstrap 注入的真实路径，只有在编辑器外直接跑时才回退到
    按文件位置推断。
    """
    pkg = sys.modules.get("combomcp")
    root = getattr(pkg, "__cache_dir__", None) if pkg is not None else None
    if not root:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(os.path.dirname(here), "cache")
    path = os.path.join(root, T3D_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _safe_filename(asset_path):
    return asset_path.replace("/", "_").replace(":", "_").replace(".", "_") + ".t3d"


def _pick_exporter():
    for name in _EXPORTER_CANDIDATES:
        cls = getattr(unreal, name, None)
        if cls is None:
            continue
        try:
            return name, cls()
        except Exception:
            try:
                return name, cls
            except Exception:
                continue
    return None, None


def export_t3d(asset_path, use_cache=True):
    """导出资产为 T3D 文本。返回 ``(text, info)``；失败时 text 为 None。

    ``info`` 里带诊断信息（用了哪个导出器、文件多大、失败原因）。
    """
    info = {"asset": asset_path}

    asset = None
    for candidate in (asset_path, asset_path + "." + asset_path.rsplit("/", 1)[-1]):
        try:
            asset = unreal.load_asset(candidate)
        except Exception:
            asset = None
        if asset is not None:
            break
    if asset is None:
        info["error"] = "asset not found"
        return None, info

    out_dir = _cache_dir()
    out_path = os.path.join(out_dir, _safe_filename(asset_path))
    info["file"] = out_path

    if use_cache and os.path.isfile(out_path):
        try:
            mtime = os.path.getmtime(out_path)
            info["cached"] = True
            info["cached_mtime"] = mtime
            with open(out_path, "r", encoding="utf-8-sig", errors="replace") as handle:
                return handle.read(), info
        except Exception as exc:
            info["cache_read_error"] = str(exc)[:200]

    exporter_name, exporter = _pick_exporter()
    info["exporter"] = exporter_name
    if exporter is None:
        info["error"] = ("no T3D exporter class available; tried %s"
                         % ", ".join(_EXPORTER_CANDIDATES))
        return None, info

    attempts = []

    # 方式一：AssetExportTask + run_asset_export_task（标准路径）
    #
    # 属性名取自 Runtime/Engine/Public/AssetExportTask.h：
    #   Object / Exporter / Filename / bSelected / bReplaceIdentical /
    #   bPrompt / bAutomated / bUseFileArchive / bWriteEmptyFiles
    try:
        task = unreal.AssetExportTask()
        set_names = {}
        for candidate_names, value in (
            (("object", "Object"), asset),
            (("exporter", "Exporter"), exporter),
            (("filename", "Filename"), out_path),
            (("automated", "bAutomated"), True),
            (("replace_identical", "bReplaceIdentical"), True),
            (("prompt", "bPrompt"), False),
            (("use_file_archive", "bUseFileArchive"), True),
        ):
            used, error = _set_prop(task, candidate_names, value)
            set_names[candidate_names[0]] = used or ("FAILED: %s" % str(error)[:90])

        info["task_props"] = set_names
        result = unreal.Exporter.run_asset_export_task(task)
        attempts.append({"method": "run_asset_export_task", "result": bool(result)})
        errors = _get_prop(task, ("errors", "Errors"), None)
        if errors:
            info["export_errors"] = [str(e)[:200] for e in errors][:10]
    except Exception as exc:
        attempts.append({"method": "run_asset_export_task", "error": str(exc)[:220]})

    if os.path.isfile(out_path):
        try:
            with open(out_path, "r", encoding="utf-8-sig", errors="replace") as handle:
                text = handle.read()
            info["bytes"] = len(text)
            info["attempts"] = attempts
            return text, info
        except Exception as exc:
            attempts.append({"method": "read_file", "error": str(exc)[:220]})

    # 方式二：直接调用导出器的 export_to_file（部分版本可用）
    try:
        ok = exporter.export_to_file(asset, out_path)
        attempts.append({"method": "export_to_file", "result": bool(ok)})
        if os.path.isfile(out_path):
            with open(out_path, "r", encoding="utf-8-sig", errors="replace") as handle:
                text = handle.read()
            info["bytes"] = len(text)
            info["attempts"] = attempts
            return text, info
    except Exception as exc:
        attempts.append({"method": "export_to_file", "error": str(exc)[:220]})

    info["attempts"] = attempts
    info.setdefault("error", "all export methods failed")
    return None, info


# ====================================================================== 读取


def graphs_from_asset(asset_path, refresh=False):
    """导出并解析，返回 ``(ir_graphs, info)``。"""
    text, info = export_t3d(asset_path, use_cache=not refresh)
    if text is None:
        return None, info
    stats = {}
    try:
        graphs = t3d_parser.build_graphs(text, stats=stats)
    except Exception as exc:
        info["parse_error"] = "%s: %s" % (type(exc).__name__, exc)
        return None, info
    info["graph_count"] = len(graphs)
    info["total_nodes"] = sum(len(g.nodes) for g in graphs)
    info["pin_forms"] = stats
    if stats:
        info["pin_form_used"] = sorted(stats.items(), key=lambda kv: -kv[1])[0][0]
    else:
        info["pin_form_used"] = None
        info["warning"] = ("T3D 里没有匹配到任何引脚形态 —— 图逻辑可能读不出来，"
                           "请检查导出内容的实际格式")
    return graphs, info


def graph_overview(asset_path, graph_name=None, refresh=False):
    """T3D 路径的图概览（L2）。"""
    diag = rt.Diagnostics()
    graphs, info = graphs_from_asset(asset_path, refresh=refresh)
    if graphs is None:
        return rt.fail("T3D 导出/解析失败: %s" % info.get("error"), diag)

    out = []
    for ir in graphs:
        if graph_name and ir.name != graph_name:
            continue
        histogram = {}
        for node in ir.nodes:
            histogram[node["cls"]] = histogram.get(node["cls"], 0) + 1
        out.append({
            "graph": ir.name,
            "kind": ir.kind,
            "node_count": len(ir.nodes),
            "node_types": dict(sorted(histogram.items(), key=lambda kv: -kv[1])),
            "entries": [
                {"n": i, "cls": ir.nodes[i]["cls"],
                 "label": graph_ir.entry_label(ir, i)}
                for i in ir.entries if ir.nodes[i]["cls"] != "FunctionResult"
            ],
        })

    if not out:
        available = [g.name for g in graphs]
        return rt.fail("graph not found: %s (available: %s)" % (graph_name, available), diag)

    return rt.ok({
        "asset": asset_path,
        "source": "t3d",
        "export_info": {k: v for k, v in info.items() if k != "attempts"},
        "graphs": out,
    }, diag)


def graph_dsl(asset_path, graph_name=None, entry=None, max_flows=12, refresh=False):
    """T3D 路径的执行流 DSL（L3'）。"""
    diag = rt.Diagnostics()
    graphs, info = graphs_from_asset(asset_path, refresh=refresh)
    if graphs is None:
        return rt.fail("T3D 导出/解析失败: %s" % info.get("error"), diag)

    chunks = []
    matched = 0
    for ir in graphs:
        if graph_name and ir.name != graph_name:
            continue
        matched += 1

        if entry:
            saved = ir.entries
            kept = []
            for i in saved:
                label = graph_ir.entry_label(ir, i)
                if entry.lower() in str(label).lower():
                    kept.append(i)
            ir.entries = kept

        chunks.append("; ==== graph: %s (%s, %d nodes)" % (
            ir.name, ir.kind, len(ir.nodes)))
        chunks.append(graph_ir.render_graph_dsl(ir, max_flows=max_flows))
        ir.entries = ir.entries

    if matched == 0:
        available = [g.name for g in graphs]
        return rt.fail("graph not found: %s (available: %s)" % (graph_name, available), diag)

    return rt.ok({
        "asset": asset_path,
        "source": "t3d",
        "dsl": "\n".join(chunks),
        "export_info": {k: v for k, v in info.items() if k != "attempts"},
    }, diag)


def node_detail(asset_path, graph_name, node_index, refresh=False):
    """T3D 路径的节点详情（L4）。"""
    diag = rt.Diagnostics()
    graphs, info = graphs_from_asset(asset_path, refresh=refresh)
    if graphs is None:
        return rt.fail("T3D 导出/解析失败: %s" % info.get("error"), diag)

    for ir in graphs:
        if ir.name != graph_name:
            continue
        try:
            idx = int(node_index)
        except Exception:
            return rt.fail("node_index must be an integer", diag)
        if idx < 0 or idx >= len(ir.nodes):
            return rt.fail("node index out of range (0..%d)" % (len(ir.nodes) - 1), diag)

        node = ir.nodes[idx]
        pins = []
        for pin in node["pins"]:
            entry = {"name": pin["name"], "dir": pin["dir"], "type": pin["type"]}
            for key in ("default", "default_obj", "to"):
                if key in pin:
                    entry[key] = pin[key]
            pins.append(entry)

        return rt.ok({
            "asset": asset_path,
            "source": "t3d",
            "graph": graph_name,
            "node": idx,
            "cls": node["cls"],
            "title": node["title"],
            "comment": node["comment"],
            "pos": node["pos"],
            "meta": node["meta"],
            "pins": pins,
        }, diag)

    return rt.fail("graph not found: %s" % graph_name, diag)


def audit(asset_path, graph_name=None, refresh=False):
    """T3D 路径的逻辑审计：孤立节点 + 执行输出悬空 + 图规模。"""
    diag = rt.Diagnostics()
    graphs, info = graphs_from_asset(asset_path, refresh=refresh)
    if graphs is None:
        return rt.fail("T3D 导出/解析失败: %s" % info.get("error"), diag)

    findings = []
    stats = []
    subject = asset_path.rsplit("/", 1)[-1]

    for ir in graphs:
        if graph_name and ir.name != graph_name:
            continue
        reachable, orphans = graph_ir.reachable_nodes(ir)
        stats.append({
            "graph": ir.name,
            "kind": ir.kind,
            "nodes": len(ir.nodes),
            "reachable": len(reachable),
            "orphans": len(orphans),
            "entries": len(ir.entries),
        })

        if orphans:
            findings.append({
                "severity": "warning",
                "category": "deadcode",
                "subject": "%s :: %s" % (subject, ir.name),
                "message": "有 %d 个节点没有任何执行流到达，也不是任何可达节点的数据来源"
                           % len(orphans),
                "evidence": {
                    "orphans": [
                        {"n": i, "cls": ir.nodes[i]["cls"],
                         "title": ir.nodes[i]["title"],
                         "meta": ir.nodes[i]["meta"],
                         "pos": ir.nodes[i]["pos"]}
                        for i in orphans[:12]
                    ]
                },
                "hint": ("这些多半是改逻辑时的残骸。注意：判活已经沿着数据连线"
                         "反向走过一遍，所以纯数据节点若被用到不会被误报。"),
            })

        # 执行输出悬空 = 流程断头
        for node_id in sorted(reachable):
            node = ir.nodes[node_id]
            exec_outs = [p for p in node["pins"]
                         if p["dir"] == "out" and p["type"] == graph_ir.EXEC]
            if not exec_outs:
                continue
            if any(p.get("to") for p in exec_outs):
                continue
            if node["cls"] in ("FunctionResult",):
                continue
            findings.append({
                "severity": "warning",
                "category": "flow",
                "subject": "%s :: %s" % (subject, ir.name),
                "message": "节点 n%d(%s) 的执行输出没有接任何东西，流程在这里中断"
                           % (node_id, node["cls"]),
                "evidence": {
                    "node": node_id, "cls": node["cls"],
                    "title": node["title"], "meta": node["meta"],
                    "exec_pins": [p["name"] for p in exec_outs],
                },
                "hint": "如果这里本该继续执行，把 exec 输出接上；否则它是多余的。",
            })

    if graph_name and not stats:
        return rt.fail("graph not found: %s" % graph_name, diag)

    return rt.ok({
        "asset": asset_path,
        "source": "t3d",
        "graphs": stats,
        "findings": findings,
        "finding_count": len(findings),
    }, diag)


def list_graphs(asset_path, refresh=False):
    """只列出图名与规模，便于挑选要展开的图。"""
    diag = rt.Diagnostics()
    graphs, info = graphs_from_asset(asset_path, refresh=refresh)
    if graphs is None:
        return rt.fail("T3D 导出/解析失败: %s" % info.get("error"), diag)
    return rt.ok({
        "asset": asset_path,
        "source": "t3d",
        "graph_count": len(graphs),
        "graphs": [{"name": g.name, "kind": g.kind,
                    "nodes": len(g.nodes), "entries": len(g.entries)}
                   for g in graphs],
        "export_info": {k: v for k, v in info.items() if k != "attempts"},
    }, diag)
