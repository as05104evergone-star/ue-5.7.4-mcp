# -*- coding: utf-8 -*-
r"""
ComboMCP / 资产索引与引用查询（只读）
=====================================

回答三类问题：

* "项目里有什么"        —— ``project_overview`` / ``list_assets`` / ``search_assets``
* "谁引用了这个资产"     —— ``find_references``（走 AssetRegistry）
* "谁调用了这个函数 /
   谁读了这个变量"       —— ``query_index``（走自建蓝图索引）

为什么需要自建索引：AssetRegistry 只能告诉你资产之间的引用关系，回答不了
"哪些蓝图的图里调用了 Montage_Play"。要回答那个问题必须真的把每个蓝图的
图走一遍。全量走一遍不便宜，所以结果按蓝图缓存，并记录资产的修改时间戳
用于失效判断。

本模块的一切操作都是只读的。
"""

import unreal  # noqa: F401

from . import runtime as rt


# ====================================================================== 资产枚举


def _asset_registry():
    try:
        return unreal.AssetRegistryHelpers.get_asset_registry()
    except Exception:
        return None


def list_assets(scope="/Game", recursive=True, class_filter=None, limit=2000):
    """列出一个目录下的资产，可按资产类过滤。"""
    diag = rt.Diagnostics()
    out = []

    try:
        paths = unreal.EditorAssetLibrary.list_assets(scope, recursive=recursive)
    except Exception as exc:
        return rt.fail("list_assets failed: %s" % exc, diag)

    for path in paths or []:
        if len(out) >= limit:
            break
        try:
            data = unreal.EditorAssetLibrary.find_asset_data(path)
        except Exception:
            data = None
        class_name = None
        if data is not None:
            try:
                class_name = str(data.asset_class_path.asset_name)
            except Exception:
                class_name = None
        if class_filter and class_name != class_filter:
            continue
        out.append({"path": path, "class": class_name})

    return rt.ok({
        "scope": scope,
        "count": len(out),
        "truncated": len(paths or []) > len(out),
        "assets": out,
    }, diag)


def project_overview(scope="/Game", limit_per_class=40):
    """按资产类做分布统计，并列出蓝图。用于快速建立项目地图。"""
    diag = rt.Diagnostics()

    try:
        paths = unreal.EditorAssetLibrary.list_assets(scope, recursive=True)
    except Exception as exc:
        return rt.fail("list_assets failed: %s" % exc, diag)

    by_class = {}
    blueprints = []

    for path in paths or []:
        try:
            data = unreal.EditorAssetLibrary.find_asset_data(path)
        except Exception:
            data = None
        if data is None:
            continue
        class_name = "Unknown"
        try:
            class_name = str(data.asset_class_path.asset_name)
        except Exception:
            pass
        by_class.setdefault(class_name, []).append(path)

    for class_name in list(by_class.keys()):
        if class_name == "Blueprint":
            for path in by_class[class_name]:
                blueprints.append(path)

    summary = {}
    for class_name, items in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        summary[class_name] = {
            "count": len(items),
            "examples": items[:6],
        }

    return rt.ok({
        "scope": scope,
        "total_assets": sum(len(v) for v in by_class.values()),
        "classes": summary,
        "blueprints": blueprints[:400],
        "blueprint_count": len(blueprints),
    }, diag)


def search_assets(query, scope="/Game", class_filter=None, limit=60):
    """按路径子串搜索资产（大小写不敏感）。"""
    diag = rt.Diagnostics()
    q = (query or "").lower()
    if not q:
        return rt.fail("empty query", diag)

    try:
        paths = unreal.EditorAssetLibrary.list_assets(scope, recursive=True)
    except Exception as exc:
        return rt.fail("list_assets failed: %s" % exc, diag)

    hits = []
    for path in paths or []:
        if q not in path.lower():
            continue
        class_name = None
        try:
            data = unreal.EditorAssetLibrary.find_asset_data(path)
            class_name = str(data.asset_class_path.asset_name)
        except Exception:
            pass
        if class_filter and class_name != class_filter:
            continue
        hits.append({"path": path, "class": class_name})
        if len(hits) >= limit:
            break

    return rt.ok({"query": query, "count": len(hits), "hits": hits}, diag)


# ====================================================================== 引用查询


def find_references(asset_path, recursive=False):
    """谁引用了这个资产（反向依赖）。"""
    diag = rt.Diagnostics()
    try:
        referencers = unreal.EditorAssetLibrary.find_package_referencers_for_asset(
            asset_path, load_assets_to_confirm=False)
    except Exception as exc:
        return rt.fail("find_package_referencers_for_asset failed: %s" % exc, diag)

    return rt.ok({
        "asset": asset_path,
        "referencers": sorted(referencers or []),
        "count": len(referencers or []),
    }, diag)


def find_dependencies(asset_path):
    """这个资产依赖了谁（正向依赖）。"""
    diag = rt.Diagnostics()
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        options = unreal.AssetRegistryDependencyOptions(
            include_soft_package_references=True,
            include_hard_package_references=True,
            include_searchable_names=False,
            include_soft_management_references=False,
            include_hard_management_references=False,
        )
        deps = registry.get_dependencies(asset_path, options)
    except Exception as exc:
        return rt.fail("get_dependencies failed: %s" % exc, diag)

    return rt.ok({
        "asset": asset_path,
        "dependencies": sorted(deps or []),
        "count": len(deps or []),
    }, diag)


# ====================================================================== 蓝图索引


_LIGHT_GRAPH_LIMIT = 4000   # 单图节点上限，超过只统计不细扫


def scan_blueprint(path, diag=None):
    """轻量扫描一个蓝图：只取结构信息与节点用法，不解析连线。

    相比完整 ``GraphModel``，这里跳过了引脚与连线，速度快很多——索引场景
    不需要连线，只需要"这个类有什么"和"它调用了什么"。
    """
    diag = diag or rt.Diagnostics()
    try:
        bp = unreal.load_asset(path)
    except Exception as exc:
        diag.miss(path, "load_asset", exc)
        return None
    if bp is None:
        return None

    entry = {
        "path": path,
        "variables": [],
        "functions": [],
        "events": [],
        "graphs": {},
        "calls": {},      # 函数名 -> 出现次数
        "uses_vars": {},  # 变量名 -> 出现次数
        "node_types": {},
    }

    # 变量
    for var in rt.safe_get(bp, "NewVariables", diag=None) or []:
        if var is None:
            continue
        name = rt.as_text(rt.safe_get(var, "VarName"))
        if name:
            entry["variables"].append({
                "name": name,
                "type": rt.pin_type_str(rt.safe_get(var, "VarType")),
            })

    parent = rt.safe_get(bp, "ParentClass", diag=None)
    if parent is not None:
        try:
            entry["parent"] = parent.get_name()
        except Exception:
            pass

    # 图与节点
    for kind, graph in rt.get_blueprint_graphs(bp, diag):
        name = rt.graph_name(graph)
        nodes = rt.safe_get(graph, "Nodes", diag=None) or []
        entry["graphs"][name] = {"kind": kind, "nodes": len(nodes)}
        if kind == "function":
            entry["functions"].append(name)

        if len(nodes) > _LIGHT_GRAPH_LIMIT:
            continue

        for node in nodes:
            if node is None:
                continue
            cls = rt.node_class_short(node)
            entry["node_types"][cls] = entry["node_types"].get(cls, 0) + 1

            if cls in ("Event", "CustomEvent", "InputAction", "InputAxis", "InputKey"):
                ref = rt.safe_get(node, "EventReference", diag=None)
                evt = rt.as_text(rt.safe_get(ref, "MemberName", diag=None)) \
                    if ref is not None else None
                evt = evt or rt.as_text(rt.safe_get(node, "CustomFunctionName", diag=None))
                if evt:
                    entry["events"].append(evt)

            elif cls in ("CallFunction", "CallParentFunction", "CallInterfaceFunction"):
                ref = rt.safe_get(node, "FunctionReference", diag=None)
                fn = rt.as_text(rt.safe_get(ref, "MemberName", diag=None)) if ref else None
                if fn:
                    entry["calls"][fn] = entry["calls"].get(fn, 0) + 1

            elif cls in ("VariableGet", "VariableSet"):
                ref = rt.safe_get(node, "VariableReference", diag=None)
                var = rt.as_text(rt.safe_get(ref, "MemberName", diag=None)) if ref else None
                if var:
                    entry["uses_vars"][var] = entry["uses_vars"].get(var, 0) + 1

            elif cls == "MacroInstance":
                ref = rt.safe_get(node, "MacroGraphReference", diag=None)
                mg = rt.safe_get(ref, "MacroGraph", diag=None) if ref else None
                if mg is not None:
                    macro = rt.graph_name(mg)
                    entry["calls"]["macro:" + macro] = \
                        entry["calls"].get("macro:" + macro, 0) + 1

    return entry


def build_index(scope="/Game", limit=600, force=False):
    """构建/刷新蓝图索引。

    ``force=False`` 时只重扫"索引里没有的"蓝图（首次构建用），
    增量失效交给调用方按需触发，避免每次问答都做全量 mtime 比对。
    """
    diag = rt.Diagnostics()
    try:
        paths = unreal.EditorAssetLibrary.list_assets(scope, recursive=True)
    except Exception as exc:
        return rt.fail("list_assets failed: %s" % exc, diag)

    blueprints = []
    for path in paths or []:
        try:
            data = unreal.EditorAssetLibrary.find_asset_data(path)
            if data is not None and str(data.asset_class_path.asset_name) == "Blueprint":
                blueprints.append(path)
        except Exception:
            continue

    scanned, failed = [], []
    for path in blueprints[:limit]:
        entry = scan_blueprint(path, diag)
        if entry is None:
            failed.append(path)
        else:
            scanned.append(entry)

    return rt.ok({
        "scope": scope,
        "blueprint_total": len(blueprints),
        "scanned": len(scanned),
        "truncated": len(blueprints) > limit,
        "failed": failed[:20],
        "index": scanned,
    }, diag)


def query_index(index, query, mode="any", limit=40):
    """在已构建的索引里查询。

    ``mode``:
      * ``any``    —— 路径 / 变量 / 函数 / 调用 任一命中
      * ``call``   —— 谁调用了某函数
      * ``var``    —— 谁读写了某变量
      * ``class``  —— 谁的路径或父类匹配
    """
    q = (query or "").lower()
    if not q:
        return {"error": "empty query"}

    hits = []
    for bp in index or []:
        reasons = []

        if mode in ("any", "class"):
            if q in bp["path"].lower():
                reasons.append("path")
            if q in str(bp.get("parent", "")).lower():
                reasons.append("parent:%s" % bp.get("parent"))

        if mode in ("any", "call"):
            for fn, count in (bp.get("calls") or {}).items():
                if q in fn.lower():
                    reasons.append("calls %s x%d" % (fn, count))

        if mode in ("any", "var"):
            for var, count in (bp.get("uses_vars") or {}).items():
                if q in var.lower():
                    reasons.append("uses %s x%d" % (var, count))
            for var in bp.get("variables") or []:
                if q in str(var.get("name", "")).lower():
                    reasons.append("declares %s" % var.get("name"))

        if mode in ("any", "function"):
            for fn in bp.get("functions") or []:
                if q in fn.lower():
                    reasons.append("function %s" % fn)

        if reasons:
            hits.append({"path": bp["path"], "why": reasons[:6]})
            if len(hits) >= limit:
                break

    return {"query": query, "mode": mode, "count": len(hits), "hits": hits}


def callers_of(index, function_name, limit=60):
    """专门回答"谁调用了 X"——连招调试里最常用的一问。"""
    q = (function_name or "").lower()
    hits = []
    for bp in index or []:
        count = 0
        matched = []
        for fn, c in (bp.get("calls") or {}).items():
            if q in fn.lower():
                count += c
                matched.append(fn)
        if count:
            hits.append({
                "path": bp["path"],
                "count": count,
                "functions": matched[:8],
            })
    hits.sort(key=lambda h: -h["count"])
    return {"function": function_name, "blueprints": len(hits), "hits": hits[:limit]}
