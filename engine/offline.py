# -*- coding: utf-8 -*-
r"""
ComboMCP / 离线读取（从磁盘 T3D 缓存解析，不需要编辑器）
=======================================================

为什么这条路径是主力而不是兜底——两条引擎事实决定的：

1. ``UEdGraphNode::Pins`` 没有 ``UPROPERTY()``；``UEdGraph::Nodes``、
   ``UAnimMontage::CompositeSections`` / ``SlotAnimTracks`` 都是**裸** ``UPROPERTY()``。
2. Python 的 ``get_editor_property`` 走 :cpp:func:`CanGetPropertyValue`，要求属性带
   ``CPF_Edit`` / ``CPF_BlueprintVisible`` / ``CPF_BlueprintAssignable``；
   裸 ``UPROPERTY()`` 三个都没有 → **一定 PermissionDenied**。
   而 T3D 导出走 :cpp:func:`FProperty::ShouldPort`，只在 ``PPF_PropertyWindow`` 时
   才要求 ``CPF_Edit`` → **T3D 里这些数据是完整的**。

于是：只要 T3D 落到磁盘，图逻辑与 Montage 时序就能在本地毫秒级解析，
既不依赖 Remote Execution，也不依赖编辑器是否开着。

缓存布局（与 :mod:`ue.t3d_read` 的命名规则一致）::

    cache/t3d/_Game_Combo_Demo_Component_AC_Combat.t3d
"""

import json
import os
import re
import time

from engine import pure

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE_T3D = os.path.join(ROOT, "cache", "t3d")
INDEX_PATH = os.path.join(CACHE_T3D, "_index.json")


# ====================================================================== 缓存


def safe_name(asset_path):
    """与 ``ue/t3d_read._safe_filename`` 保持完全一致的命名规则。"""
    return asset_path.replace("/", "_").replace(":", "_").replace(".", "_") + ".t3d"


def cache_path(asset_path):
    return os.path.join(CACHE_T3D, safe_name(asset_path))


def has_cache(asset_path):
    return os.path.isfile(cache_path(asset_path))


def read_text(asset_path):
    """读取缓存 T3D。UE 写的是 UTF-8 with BOM，所以用 utf-8-sig。"""
    path = cache_path(asset_path)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def list_cached():
    """列出缓存里的资产（按文件推断原路径）。

    注意别把 ``_`` 前缀当作"内部文件"标记：资产路径都以 ``/`` 开头，
    ``safe_name`` 转义后就变成 ``_`` 开头，用前缀过滤会把**所有**资产滤掉。
    只有 ``_index.json`` 这一个真正的内部文件需要排除。
    """
    if not os.path.isdir(CACHE_T3D):
        return []
    out = []
    for name in sorted(os.listdir(CACHE_T3D)):
        if not name.endswith(".t3d") or name == "_index.json":
            continue
        path = os.path.join(CACHE_T3D, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append({
            "file": name,
            "bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(stat.st_mtime)),
            "age_seconds": int(time.time() - stat.st_mtime),
        })
    return out


def load_index():
    """收集时写入的资产索引（原路径 -> 文件 + 类名）。"""
    if not os.path.isfile(INDEX_PATH):
        return {}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def resolve_asset(asset_path=None, hint=None):
    """把用户给的路径解析到缓存条目。

    支持三种输入：完整对象路径、只有资产名、或什么都不给（配合 hint 模糊匹配）。
    """
    index = load_index()
    if asset_path:
        entry = index.get(asset_path)
        if entry:
            return asset_path, entry
        # 退一步：按文件名匹配（用户可能只给了 /Game/.../AC_Combat）
        target = safe_name(asset_path)
        for key, value in index.items():
            if value.get("file") == target or key == asset_path:
                return key, value
        if has_cache(asset_path):
            return asset_path, {"file": safe_name(asset_path), "class": None}

    if hint:
        low = hint.lower()
        for key, value in index.items():
            if low in key.lower():
                return key, value
    return None, None


# ====================================================================== 图


def _graphs(asset_path):
    text = read_text(asset_path)
    if text is None:
        return None, None, None
    graph_ir = pure.load()[0]
    t3d_mod = pure.load()[1]
    stats = {}
    graphs = t3d_mod.build_graphs(text, stats=stats)
    return graphs, stats, graph_ir


def graph_overview(asset_path, graph_name=None):
    graphs, stats, _ = _graphs(asset_path)
    if graphs is None:
        return {"error": "没有 %s 的 T3D 缓存。先调用 sync 收集，或指定 collect=true。"
                         % asset_path,
                "_cache_miss": True, "asset": asset_path}

    out = []
    for ir in graphs:
        if graph_name and ir.name != graph_name:
            continue
        hist = {}
        for node in ir.nodes:
            hist[node["cls"]] = hist.get(node["cls"], 0) + 1
        out.append({
            "graph": ir.name,
            "kind": ir.kind,
            "node_count": len(ir.nodes),
            "node_types": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
            "entries": [{"n": i, "cls": ir.nodes[i]["cls"],
                         "label": _entry_label(ir, i)}
                        for i in ir.entries if ir.nodes[i]["cls"] != "FunctionResult"],
        })

    if not out:
        return {"error": "graph not found: %s" % graph_name,
                "available": [g.name for g in graphs], "asset": asset_path}

    return {
        "asset": asset_path,
        "source": "t3d-cache",
        "graphs": out,
        "pin_form": _dominant(stats),
    }


def _dominant(stats):
    forms = dict((k, v) for k, v in stats.items() if not k.startswith("_"))
    if not forms:
        return None
    return sorted(forms.items(), key=lambda kv: -kv[1])[0][0]


def _entry_label(ir, node_id):
    meta = ir.nodes[node_id].get("meta") or {}
    return (meta.get("event") or meta.get("fn")
            or ir.nodes[node_id].get("title") or ir.nodes[node_id]["cls"])


def _pick_graph(graphs, graph_name, prefer_kind=None):
    if graph_name:
        for ir in graphs:
            if ir.name == graph_name:
                return ir
        return None
    if prefer_kind:
        best = None
        for ir in graphs:
            if ir.kind != prefer_kind or ir.name.endswith("_MERGED"):
                continue
            if best is None or len(ir.nodes) > len(best.nodes):
                best = ir
        if best is not None:
            return best
    best = None
    for ir in graphs:
        if ir.name.endswith("_MERGED"):
            continue
        if best is None or len(ir.nodes) > len(best.nodes):
            best = ir
    return best


def graph_dsl(asset_path, graph_name=None, entry=None, max_flows=12, budget=2600):
    graphs, stats, graph_ir = _graphs(asset_path)
    if graphs is None:
        return {"error": "没有 %s 的 T3D 缓存。先调用 sync 收集。" % asset_path,
                "_cache_miss": True, "asset": asset_path}

    selected = []
    if graph_name:
        selected = [g for g in graphs if g.name == graph_name]
        if not selected:
            return {"error": "graph not found: %s" % graph_name,
                    "available": sorted(g.name for g in graphs), "asset": asset_path}
    else:
        one = _pick_graph(graphs, None, prefer_kind="event")
        selected = [one] if one else []

    chunks = []
    for ir in selected:
        entries = ir.entries
        if entry:
            entries = [i for i in entries
                       if entry.lower() in str(_entry_label(ir, i)).lower()]
        saved, ir.entries = ir.entries, entries
        chunks.append("; ==== graph: %s (%s, %d nodes) ====" % (
            ir.name, ir.kind, len(ir.nodes)))
        chunks.append(graph_ir.render_graph_dsl(ir, max_flows=max_flows,
                                                budget=[budget]))
        ir.entries = saved

    return {
        "asset": asset_path,
        "source": "t3d-cache",
        "graph": selected[0].name if selected else None,
        "dsl": "\n".join(chunks),
        "format": "S-expression pseudo code",
    }


def node_detail(asset_path, graph_name, node_index):
    graphs, _, _ = _graphs(asset_path)
    if graphs is None:
        return {"error": "没有 T3D 缓存", "_cache_miss": True, "asset": asset_path}
    for ir in graphs:
        if ir.name != graph_name:
            continue
        try:
            idx = int(node_index)
        except (TypeError, ValueError):
            return {"error": "node_index must be an integer"}
        if idx < 0 or idx >= len(ir.nodes):
            return {"error": "node index out of range (0..%d)" % (len(ir.nodes) - 1)}
        node = ir.nodes[idx]
        pins = []
        for pin in node["pins"]:
            entry = {"name": pin["name"], "dir": pin["dir"], "type": pin["type"]}
            for key in ("default", "default_obj", "to"):
                if key in pin:
                    entry[key] = pin[key]
            pins.append(entry)
        return {
            "asset": asset_path, "source": "t3d-cache", "graph": graph_name,
            "node": idx, "cls": node["cls"], "title": node["title"],
            "comment": node["comment"], "pos": node["pos"],
            "meta": node["meta"], "pins": pins,
        }
    return {"error": "graph not found: %s" % graph_name}


def audit(asset_path, graph_name=None):
    graphs, _, graph_ir = _graphs(asset_path)
    if graphs is None:
        return {"error": "没有 T3D 缓存", "_cache_miss": True, "asset": asset_path}

    findings, stats = [], []
    subject = asset_path.rsplit("/", 1)[-1]

    for ir in graphs:
        if graph_name and ir.name != graph_name:
            continue
        if not graph_name and ir.name.endswith("_MERGED"):
            # UE 编译期生成的合并图，逻辑与源图重复，不参与审计
            continue
        reachable, orphans = graph_ir.reachable_nodes(ir)
        stats.append({
            "graph": ir.name, "kind": ir.kind, "nodes": len(ir.nodes),
            "reachable": len(reachable), "orphans": len(orphans),
            "entries": len(ir.entries),
        })
        if orphans:
            findings.append({
                "severity": "warning", "category": "deadcode",
                "subject": "%s :: %s" % (subject, ir.name),
                "message": "有 %d 个节点既没有执行流到达，也不是任何可达节点的数据来源"
                           % len(orphans),
                "evidence": {"orphans": [
                    {"n": i, "cls": ir.nodes[i]["cls"], "title": ir.nodes[i]["title"],
                     "meta": ir.nodes[i]["meta"], "pos": ir.nodes[i]["pos"]}
                    for i in orphans[:12]]},
                "hint": "多半是改逻辑时的残骸；判活已经沿数据连线反向走过一遍，"
                        "被用到的纯数据节点不会误报。",
            })

        for node_id in sorted(reachable):
            node = ir.nodes[node_id]
            exec_outs = [p for p in node["pins"]
                         if p["dir"] == "out" and p["type"] == "exec"]
            if not exec_outs or any(p.get("to") for p in exec_outs):
                continue
            if node["cls"] in ("FunctionResult",):
                continue
            findings.append({
                "severity": "warning", "category": "flow",
                "subject": "%s :: %s" % (subject, ir.name),
                "message": "节点 n%d(%s) 的执行输出没有接任何东西，流程在这里中断"
                           % (node_id, node["cls"]),
                "evidence": {"node": node_id, "cls": node["cls"],
                             "title": node["title"], "meta": node["meta"],
                             "pos": node["pos"],
                             "exec_pins": [p["name"] for p in exec_outs]},
                "hint": "若这里本该继续执行，把 exec 输出接上；否则它是多余的。",
            })

    return {"asset": asset_path, "source": "t3d-cache", "graphs": stats,
            "findings": findings, "finding_count": len(findings)}


# ====================================================================== 动画


def montage_detail(asset_path, analyze=False):
    text = read_text(asset_path)
    if text is None:
        return {"error": "没有 %s 的 T3D 缓存。Montage 的 Section/Notify 只能走 "
                         "T3D——反射读不到它们（CompositeSections 是裸 UPROPERTY）。"
                         % asset_path,
                "_cache_miss": True, "asset": asset_path}
    montage_t3d = pure.load()[2]
    if analyze:
        result = montage_t3d.analyze_montage_text(text)
    else:
        result = montage_t3d.parse_montage(text)
    if result is None:
        return {"error": "该资产的 T3D 不是 AnimMontage", "asset": asset_path}
    result["asset_path"] = asset_path
    result["source"] = "t3d-cache"
    if analyze:
        result["_analysis_only"] = False
    return result


def montage_analysis(asset_path):
    return montage_detail(asset_path, analyze=True)


def anim_blueprint(asset_path, full=False):
    """读 AnimBlueprint：状态机 / 状态 / 转换 / 关键节点清单。

    ``full=False`` 返回紧凑摘要（给模型看），``full=True`` 返回完整结构。
    """
    text = read_text(asset_path)
    if text is None:
        return {"error": "没有 %s 的 T3D 缓存。先调用 sync 收集。" % asset_path,
                "_cache_miss": True, "asset": asset_path}
    mod = pure.load()[3]
    result = (mod.parse_anim_blueprint(text) if full
              else mod.summarize_anim_blueprint(text))
    if result is None:
        return {"error": "该资产的 T3D 不是 AnimBlueprint", "asset": asset_path}
    result["asset_path"] = asset_path
    result["source"] = "t3d-cache"
    return result


def _compact(value, depth=0):
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return dict((k, _compact(v, depth + 1))
                    for k, v in list(value.items())[:12])
    if isinstance(value, list):
        return [_compact(v, depth + 1) for v in value[:8]]
    text = str(value)
    return text if len(text) <= 120 else text[:120] + "..."


def generic_asset(asset_path, max_props=60):
    """通用资产读取：顶层可读属性 + 索引属性 + 常见结构化字段。

    用于 Chooser / PoseSearch / 数据表这类没有专用读取器的资产——
    T3D 里它们同样是完整的（``ShouldPort`` 不按可编辑性过滤）。
    """
    text = read_text(asset_path)
    if text is None:
        return {"error": "没有 %s 的 T3D 缓存。先调用 sync 收集。" % asset_path,
                "_cache_miss": True, "asset": asset_path}
    t3d_mod = pure.load()[1]
    roots = t3d_mod.parse(text)
    if not roots:
        return {"error": "T3D 为空", "asset": asset_path}
    obj = roots[0]

    props = {}
    for key in sorted(obj.props.keys()):
        value = str(obj.props[key])
        props[key] = value if len(value) <= 160 else value[:160] + "..."
        if len(props) >= max_props:
            break

    indexed = dict((key, len(bucket))
                   for key, bucket in (obj.indexed_props or {}).items())

    result = {
        "asset_path": asset_path,
        "asset": obj.name,
        "class": obj.cls,
        "child_count": len(obj.children),
        "indexed_props": indexed,
        "props": props,
        "source": "t3d-cache",
    }
    # 结构化字段：Chooser 的列/行、PoseSearch 的动画清单等
    detail = {}
    for key in sorted(indexed.keys()):
        if key in ("Columns", "Rows", "ResultsStruct", "AnimationAssets",
                   "Schema", "Previews", "Tags", "DisablePoseSearch",
                   "TagsToMatch", "AssetSampling"):
            detail[key] = [_compact(item) for item in obj.indexed(key)[:10]]
    if detail:
        result["indexed_detail"] = detail
    return result


def find_animation_calls(asset_path):
    """在图里找所有 Montage 相关调用（播放/跳段/停止）及其目标资产、Section 字面量。"""
    graphs, _, _ = _graphs(asset_path)
    if graphs is None:
        return {"error": "没有 T3D 缓存", "_cache_miss": True, "asset": asset_path}

    play = ("montage_play", "play_montage", "playanimmontage",
            "play_montage_and_wait", "montage_play_custom")
    jump = ("montage_jump_to_section", "montage_set_next_section",
            "montage_set_position", "montage_set_play_rate")
    stop = ("montage_stop", "stop_anim_montage", "stop_all_montages")

    calls = []
    for ir in graphs:
        if ir.name.endswith("_MERGED"):
            continue
        for node in ir.nodes:
            fn = (node.get("meta") or {}).get("fn")
            if not fn:
                continue
            low = fn.lower()
            if any(f in low for f in play):
                role = "play"
            elif any(f in low for f in jump):
                role = "jump"
            elif any(f in low for f in stop):
                role = "stop"
            else:
                continue
            entry = {"graph": ir.name, "node": node["id"], "fn": fn, "role": role}
            on = (node.get("meta") or {}).get("fn_class")
            if on:
                entry["on"] = on
            for pin in node["pins"]:
                if pin["dir"] != "in":
                    continue
                if pin.get("default_obj"):
                    entry.setdefault("assets", {})[pin["name"]] = pin["default_obj"]
                elif pin.get("default") and pin["type"] in ("Name", "String"):
                    entry.setdefault("literals", {})[pin["name"]] = pin["default"]
            calls.append(entry)

    by_role = {}
    for c in calls:
        by_role[c["role"]] = by_role.get(c["role"], 0) + 1
    return {"asset": asset_path, "source": "t3d-cache", "calls": calls,
            "count": len(calls), "by_role": by_role}


def callers_of_function(asset_paths, needle):
    """在这些资产的图里找调用了某函数的节点。"""
    low = (needle or "").lower()
    hits = []
    for asset_path in asset_paths:
        graphs, _, _ = _graphs(asset_path)
        if graphs is None:
            continue
        for ir in graphs:
            if ir.name.endswith("_MERGED"):
                continue
            for node in ir.nodes:
                fn = (node.get("meta") or {}).get("fn")
                if fn and low in fn.lower():
                    hits.append({"asset": asset_path, "graph": ir.name,
                                 "node": node["id"], "fn": fn})
    return {"function": needle, "count": len(hits), "hits": hits[:80]}


# ====================================================================== 类结构


_PIN_CATEGORY = {
    "bool": "bool", "byte": "byte", "int": "int", "int64": "int64",
    "real": "float", "float": "float", "double": "double",
    "name": "Name", "string": "String", "text": "Text",
    "object": "Object", "class": "class", "struct": "Struct",
    "interface": "Interface", "delegate": "Delegate",
    "wildcard": "Wildcard", "exec": "exec",
}


def render_pin_type(pin_type):
    """把 T3D 里的 ``PinType`` 结构渲染成可读类型。

    对象/结构体一律用真实类型名（``AnimMontage`` / ``BS_MovementVector``），
    因为连招系统里"这个变量挂的是哪个类"正是最要紧的信息。
    """
    if not isinstance(pin_type, dict):
        return "?"
    category = (pin_type.get("PinCategory") or "").lower()
    base = _PIN_CATEGORY.get(category, category or "?")

    sub_obj = pin_type.get("PinSubCategoryObject")
    if sub_obj:
        name = pure.load()[1]._object_short_name(sub_obj)
        if name:
            base = name
    else:
        sub = pin_type.get("PinSubCategory") or ""
        if sub and sub.lower() not in ("self", "none"):
            base = sub

    container = (pin_type.get("ContainerType") or "None").lower()
    if container == "array":
        base = "TArray<%s>" % base
    elif container == "set":
        base = "TSet<%s>" % base
    elif container == "map":
        base = "TMap<%s, ?>" % base

    if str(pin_type.get("bIsConst", "")).lower() in ("true", "1"):
        base = "const " + base
    if str(pin_type.get("bIsReference", "")).lower() in ("true", "1"):
        base += "&"
    return base


def _localized_text(value):
    """``NSLOCTEXT("", "key", "连招")`` -> ``连招``"""
    if not value:
        return None
    text = str(value)
    if text.startswith("NSLOCTEXT"):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
        if parts:
            return parts[-1]
    return text or None


_PROPERTY_FLAGS = (
    (0x0000000000000001, "InstanceEditable"),
    (0x0000000000000002, "BlueprintVisible"),
    (0x0000000000000004, "BlueprintReadOnly"),
    (0x0000000000000008, "Replicated"),
    (0x0000000000000010, "Edit"),
)


def class_summary(asset_path):
    """从 T3D 读类结构。

    变量表必须走这条路：``UBlueprint::NewVariables`` 是裸 ``UPROPERTY()``，
    Python 反射会 PermissionDenied（同 ``UEdGraph::Nodes``）。
    """
    text = read_text(asset_path)
    if text is None:
        return {"error": "没有 %s 的 T3D 缓存。先调用 sync 收集。" % asset_path,
                "_cache_miss": True, "asset": asset_path}

    graph_ir = pure.load()[0]
    _t3d = pure.load()[1]
    roots = _t3d.parse(text)
    if not roots:
        return {"error": "T3D 为空", "asset": asset_path}
    bp = roots[0]

    parent = _t3d._object_short_name(bp.props.get("ParentClass"))
    generated = _t3d._object_short_name(bp.props.get("GeneratedClass"))

    variables = []
    for raw in bp.indexed("NewVariables"):
        if not isinstance(raw, dict):
            continue
        name = raw.get("VarName")
        if not name:
            continue
        entry = {"name": name, "type": render_pin_type(raw.get("VarType"))}
        friendly = _localized_text(raw.get("FriendlyName"))
        if friendly and friendly != name:
            entry["friendly"] = friendly
        category = _localized_text(raw.get("Category"))
        if category:
            entry["category"] = category
        default = raw.get("DefaultValue")
        if default:
            entry["default"] = default
        flags = raw.get("PropertyFlags")
        try:
            flags_int = int(str(flags), 10)
        except (TypeError, ValueError):
            flags_int = 0
        marks = [label for bit, label in _PROPERTY_FLAGS if flags_int & bit]
        if marks:
            entry["flags"] = marks
        rep = raw.get("RepNotifyFunc")
        if rep and rep not in ("None", ""):
            entry["rep_notify"] = rep
        variables.append(entry)

    interfaces = []
    for raw in bp.indexed("ImplementedInterfaces"):
        if isinstance(raw, dict):
            iface = _t3d._object_short_name(raw.get("Interface"))
            if iface:
                interfaces.append(iface)

    graphs, stats, _ = _graphs(asset_path)
    graph_list = []
    functions = []
    if graphs:
        for ir in graphs:
            graph_list.append({"name": ir.name, "kind": ir.kind,
                               "nodes": len(ir.nodes)})
            if ir.kind == "function" and not ir.name.endswith("_MERGED"):
                params = []
                for node in ir.nodes:
                    if node["cls"] == "FunctionEntry":
                        for pin in node["pins"]:
                            if pin["dir"] == "out" and pin["type"] != "exec":
                                params.append({"name": pin["name"],
                                               "type": pin["type"]})
                entry = {"name": ir.name, "nodes": len(ir.nodes),
                         "params": params}
                functions.append(entry)

    total_nodes = sum(g["nodes"] for g in graph_list)
    data_table_refs = []
    for var in variables:
        default = var.get("default") or ""
        if "DataTable" in default or "/Game/" in default:
            data_table_refs.append({"var": var["name"], "default": default})

    result = {
        "asset": asset_path,
        "class": bp.cls,
        "name": bp.name,
        "parent": parent,
        "generated_class": generated,
        "interfaces": interfaces,
        "variables": variables,
        "functions": functions,
        "graphs": graph_list,
        "total_nodes": total_nodes,
        "source": "t3d-cache",
    }
    if data_table_refs:
        result["data_table_refs"] = data_table_refs
    scs = bp.props.get("SimpleConstructionScript")
    if scs:
        result["has_construction_script"] = True
        result["construction_script"] = _t3d._object_short_name(scs)
    return result
