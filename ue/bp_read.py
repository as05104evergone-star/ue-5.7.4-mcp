# -*- coding: utf-8 -*-
r"""
ComboMCP / 蓝图读取器（只读）
=============================

把 ``UBlueprint`` 读成四层由粗到细的结构，供模型按需取用：

* **L1 类摘要** ``class_summary``   —— 父类 / 变量 / 函数 / 事件 / 组件 / 图清单
* **L2 图概览** ``graph_overview``  —— 节点直方图 + 入口点（不展开连线）
* **L3 执行流** ``exec_flow``       —— 从入口出发的执行流树（含数据来源内联）
* **L3' DSL**  ``graph_dsl``        —— 同上的 S 表达式文本，最紧凑
* **L4 节点详情** ``node_detail``   —— 单个节点的全部引脚 / 属性 / 连接

分层的目的很实际：一个 800 节点的图原样 dump 是十几万 token，而回答
"这个连招为什么接不上"通常只需要 L2 + 某个分支的 L3。

本模块的一切操作都是只读的。
"""

import unreal  # noqa: F401  (由编辑器注入)

from . import runtime as rt


# ====================================================================== 图模型


class GraphModel(object):
    """一个 ``UEdGraph`` 的结构化模型。

    构建过程分两遍：先收集节点与引脚并为每个引脚登记 ``id(pin) -> 所属节点``，
    再解析 ``LinkedTo`` 把连接还原成 ``[节点下标, 引脚名]``。
    第一遍必须持有引脚对象的强引用，否则 Python 侧对象被回收后 ``id`` 会被复用。
    """

    def __init__(self, graph, diag):
        self.graph = graph
        self.diag = diag
        self.name = rt.graph_name(graph)
        self.nodes = []          # [{obj, cls, title, pos, pins:[{...}], meta}]
        self.by_guid = {}        # node guid -> index
        self.entry_indices = []  # 入口节点下标

        self._build()

    # ---------------------------------------------------------------- 构建

    def _build(self):
        raw_nodes = rt.safe_get(self.graph, "Nodes", diag=self.diag) or []

        pin_owner = {}    # id(pin) -> (node_idx, pin_name)
        keepalive = []    # 保持引脚对象存活

        for node in raw_nodes:
            if node is None:
                continue
            idx = len(self.nodes)
            cls_short = rt.node_class_short(node)
            pins_raw = rt.safe_get(node, "Pins") or []

            pins = []
            for p in pins_raw:
                if p is None:
                    continue
                keepalive.append(p)
                name = rt.as_text(rt.safe_get(p, "PinName")) or "?"
                entry = {
                    "name": name,
                    "dir": rt.pin_direction_str(p),
                    "type": rt.pin_type_str(rt.safe_get(p, "PinType")),
                    "_pin": p,
                }
                default_value = rt.as_text(rt.safe_get(p, "DefaultValue"))
                if default_value:
                    entry["default"] = default_value
                default_obj = rt.safe_get(p, "DefaultObject")
                if default_obj is not None:
                    try:
                        entry["default_obj"] = default_obj.get_name()
                    except Exception:
                        pass
                pins.append(entry)
                pin_owner[id(p)] = (idx, name)

            guid = rt.as_text(rt.safe_get(node, "NodeGuid")) or "node-%d" % idx
            self.by_guid[guid] = idx

            self.nodes.append({
                "obj": node,
                "cls": cls_short,
                "title": rt.node_title(node),
                "guid": guid,
                "pos": [
                    rt.safe_get(node, "NodePosX") or 0,
                    rt.safe_get(node, "NodePosY") or 0,
                ],
                "comment": rt.as_text(rt.safe_get(node, "NodeComment")) or None,
                "enabled": rt.safe_get(node, "bCommentBubbleVisible"),
                "pins": pins,
                "meta": extract_node_semantics(node, cls_short),
                "_pin": None,
            })

        # 第二遍：解析连线
        for idx, entry in enumerate(self.nodes):
            for pin in entry["pins"]:
                linked = rt.safe_get(pin["_pin"], "LinkedTo") or []
                targets = []
                for other in linked:
                    owner = pin_owner.get(id(other))
                    if owner is not None:
                        targets.append([owner[0], owner[1]])
                if targets:
                    pin["to"] = targets

        self._keepalive = keepalive
        self._find_entries()

    # ---------------------------------------------------------------- 查询

    def _find_entries(self):
        """识别执行入口：事件、函数入口、自定义事件。"""
        for idx, node in enumerate(self.nodes):
            if node["cls"] in ("Event", "CustomEvent", "FunctionEntry",
                               "FunctionResult", "Tunnel", "Composite"):
                self.entry_indices.append(idx)

    def pin(self, node_idx, pin_name, direction=None):
        for pin in self.nodes[node_idx]["pins"]:
            if pin["name"] == pin_name:
                if direction is None or pin["dir"] == direction:
                    return pin
        return None

    def exec_out_targets(self, node_idx):
        """返回该节点所有 exec 输出引脚连到的 ``(目标节点下标, 引脚名)``。"""
        out = []
        for pin in self.nodes[node_idx]["pins"]:
            if pin["dir"] != "out":
                continue
            if pin["type"] != "exec":
                continue
            for target in pin.get("to", []):
                out.append((pin["name"], target[0], target[1]))
        return out

    def exec_in_sources(self, node_idx):
        srcs = []
        for pin in self.nodes[node_idx]["pins"]:
            if pin["dir"] != "in" or pin["type"] != "exec":
                continue
            for source in pin.get("to", []):
                srcs.append((pin["name"], source[0], source[1]))
        return srcs

    def data_sources(self, node_idx, pin_name):
        """某个数据输入引脚的来源 ``[(源节点下标, 源引脚名)]``。"""
        pin = self.pin(node_idx, pin_name, direction="in")
        if not pin:
            return []
        return [(t[0], t[1]) for t in pin.get("to", [])]

    def node_brief(self, node_idx):
        node = self.nodes[node_idx]
        brief = {"n": node_idx, "cls": node["cls"]}
        if node["title"]:
            brief["title"] = node["title"]
        brief.update(node["meta"])
        if node["comment"]:
            brief["comment"] = node["comment"]
        return brief


# ====================================================================== 节点语义


_MEMBER_NAME_PROPS = (
    ("FunctionReference", "fn"),
    ("VariableReference", "var"),
    ("EventReference", "event"),
    ("MacroGraphReference", "macro"),
    ("DelegateReference", "delegate"),
    ("TargetType", "cast_to"),
)

# 这些节点类的语义从成员引用里取
_FN_NODES = {
    "CallFunction", "CallParentFunction", "CallInterfaceFunction",
    "CallFunctionProxy", "CommutativeAssociativeBinaryOperator",
    "PromotableOperator", "CallArrayFunction", "CreateDelegate",
    "CallDelegate", "AddDelegate", "RemoveDelegate", "ClearDelegate",
    "CallFunctionProxy",
}
_VAR_NODES = {
    "VariableGet", "VariableSet", "Self", "ClassVariable", "LocalVariable",
    "GetVariable", "SetVariable", "PropertyAccess", "StructMemberSet",
    "StructMemberGet", "InstancedStructGet", "InstancedStructMake",
}


def _member_name(ref):
    """从 ``FMemberReference`` 里取成员名与所属类。"""
    if ref is None:
        return None, None
    name = rt.as_text(rt.safe_get(ref, "MemberName", diag=None))
    parent = rt.safe_get(ref, "MemberParent", diag=None)
    parent_name = None
    if parent is not None:
        try:
            parent_name = parent.get_name()
        except Exception:
            parent_name = None
    return name, parent_name


def extract_node_semantics(node, cls_short):
    """提取节点的"这是什么操作"信息。

    只读取已知且稳定的 UPROPERTY；读不到就留空，由上层决定是否降级。
    """
    meta = {}

    if cls_short in _FN_NODES:
        name, parent = _member_name(rt.safe_get(node, "FunctionReference", diag=None))
        if name:
            meta["fn"] = name
        if parent:
            meta["fn_class"] = parent
        # 纯函数 / 常量节点
        if rt.safe_get(node, "bIsPureFunc", diag=None):
            meta["pure"] = True

    elif cls_short in _VAR_NODES:
        name, parent = _member_name(rt.safe_get(node, "VariableReference", diag=None))
        if name:
            meta["var"] = name
        if parent:
            meta["var_class"] = parent

    elif cls_short == "Event":
        name, _ = _member_name(rt.safe_get(node, "EventReference", diag=None))
        custom = rt.as_text(rt.safe_get(node, "CustomFunctionName", diag=None))
        meta["event"] = name or custom or "?"
    elif cls_short == "CustomEvent":
        meta["event"] = rt.as_text(rt.safe_get(node, "CustomFunctionName", diag=None)) or "?"

    elif cls_short in ("MacroInstance", "Composite"):
        ref = rt.safe_get(node, "MacroGraphReference", diag=None)
        graph = rt.safe_get(ref, "MacroGraph", diag=None) if ref is not None else None
        if graph is not None:
            meta["macro"] = rt.graph_name(graph)
        else:
            meta["macro"] = rt.node_title(node) or "?"

    elif cls_short == "DynamicCast":
        target = rt.safe_get(node, "TargetType", diag=None)
        if target is not None:
            try:
                meta["cast_to"] = target.get_name()
            except Exception:
                pass
        meta["pure"] = bool(rt.safe_get(node, "bIsPureCast", diag=None))

    elif cls_short == "IfThenElse":
        pass  # 分支：条件在 Condition 引脚上，由上层处理

    elif cls_short == "ExecutionSequence":
        pass

    # 通用补充：时间轴、异步节点这些自带名字的
    if "timeline" not in meta and cls_short == "Timeline":
        meta["timeline"] = rt.as_text(rt.safe_get(node, "TimelineName", diag=None)) or "?"

    # 潜行 / 异步节点（Montage 播放走的就是这一类）——对连招系统很关键
    if cls_short in ("AsyncAction", "LatentGameplayTaskCall", "BaseAsyncTask",
                     "PlayMontage", "PlayMontageAndWait", "Delay"):
        proxy = rt.safe_get(node, "ProxyFactoryClass", diag=None)
        if proxy is not None:
            try:
                meta["proxy"] = proxy.get_name()
            except Exception:
                pass

    return meta


# ====================================================================== 资产装载


def load_asset(asset_path):
    """按对象路径装载资产。返回 ``(asset, error)``。"""
    if not asset_path:
        return None, "empty asset path"
    path = asset_path
    if path.startswith("/Game/") and "." not in path.split("/")[-1]:
        # /Game/A/B  ->  /Game/A/B.B
        path = path + "." + path.rsplit("/", 1)[-1]
    try:
        asset = unreal.load_asset(path)
    except Exception as exc:
        return None, "load_asset raised: %s" % exc
    if asset is None:
        return None, "asset not found: %s" % path
    return asset, None


def asset_class_name(asset):
    try:
        return asset.get_class().get_name()
    except Exception:
        return None


# ====================================================================== L1 类摘要


def _read_variables(bp, diag):
    out = []
    raw = rt.safe_get(bp, "NewVariables", diag=diag) or []
    for var in raw:
        if var is None:
            continue
        name = rt.as_text(rt.safe_get(var, "VarName"))
        if not name:
            continue
        entry = {
            "name": name,
            "type": rt.pin_type_str(rt.safe_get(var, "VarType")),
        }
        default_value = rt.as_text(rt.safe_get(var, "DefaultValue"))
        if default_value:
            entry["default"] = default_value
        category = rt.as_text(rt.safe_get(var, "Category"))
        if category:
            entry["category"] = category
        friendly = rt.as_text(rt.safe_get(var, "FriendlyName"))
        if friendly and friendly != name:
            entry["friendly"] = friendly

        flags = rt.safe_get(var, "PropertyFlags", diag=None)
        if isinstance(flags, int) and flags:
            marks = []
            if flags & 0x0000000000000001:
                marks.append("InstanceEditable")
            if flags & 0x0000000000000002:   # CPF_BlueprintVisible
                marks.append("BlueprintReadWrite")
            if flags & 0x0000000000000004:   # CPF_BlueprintReadOnly
                marks.append("BlueprintReadOnly")
            if flags & 0x0000000000000008:   # CPF_Net
                marks.append("Replicated")
            if flags & 0x0000000000000010:   # CPF_Edit
                marks.append("EditAnywhere")
            if flags & 0x0000000000000020:   # CPF_ConstParm
                marks.append("Const")
            if marks:
                entry["flags"] = marks

        rep = rt.as_text(rt.safe_get(var, "RepNotifyFunc"))
        if rep and rep != "None":
            entry["rep_notify"] = rep

        metadata = rt.safe_get(var, "MetaDataArray", diag=None) or []
        meta = {}
        for m in metadata:
            key = rt.as_text(rt.safe_get(m, "DataKey"))
            value = rt.as_text(rt.safe_get(m, "DataValue"))
            if key:
                meta[key] = value
        if meta:
            entry["meta"] = meta

        out.append(entry)
    return out


def _read_functions(bp, diag):
    """从函数图里读函数签名。

    参数来自 ``FunctionEntry`` 的输出引脚，返回值来自 ``FunctionResult`` 的输入引脚。
    """
    out = []
    for kind, graph in rt.get_blueprint_graphs(bp, diag):
        if kind != "function":
            continue
        model = GraphModel(graph, diag)
        params, returns = [], []
        is_pure = False
        for idx, node in enumerate(model.nodes):
            if node["cls"] == "FunctionEntry":
                for pin in node["pins"]:
                    if pin["dir"] == "out" and pin["type"] != "exec":
                        params.append({"name": pin["name"], "type": pin["type"]})
                if rt.safe_get(node["obj"], "bIsPureFunc", diag=None):
                    is_pure = True
            elif node["cls"] == "FunctionResult":
                for pin in node["pins"]:
                    if pin["dir"] == "in" and pin["type"] != "exec":
                        returns.append({"name": pin["name"], "type": pin["type"]})

        entry = {
            "name": model.name,
            "nodes": len(model.nodes),
        }
        if params:
            entry["params"] = params
        if returns:
            entry["returns"] = returns
        if is_pure:
            entry["pure"] = True
        out.append(entry)
    return out


def _read_events(bp, diag):
    """从事件图里读事件与自定义事件入口。"""
    out = []
    for kind, graph in rt.get_blueprint_graphs(bp, diag):
        if kind != "event":
            continue
        model = GraphModel(graph, diag)
        for node in model.nodes:
            if node["cls"] in ("Event", "CustomEvent", "InputAction",
                               "InputTouch", "InputAxis", "InputKey"):
                entry = {
                    "name": node["meta"].get("event") or node["title"] or node["cls"],
                    "cls": node["cls"],
                }
                if node["meta"].get("event"):
                    entry["event"] = node["meta"]["event"]
                out.append(entry)
    return out


def _read_components(bp, diag):
    """读 SimpleConstructionScript（蓝图自带的组件层级）。"""
    scs = rt.safe_get(bp, "SimpleConstructionScript", diag=None)
    if scs is None:
        return []

    nodes = rt.safe_get(scs, "AllNodes", diag=None) or []
    by_var = {}
    for n in nodes:
        if n is None:
            continue
        var_name = rt.as_text(rt.safe_get(n, "VariableName"))
        comp_class = rt.safe_get(n, "ComponentClass", diag=None)
        class_name = None
        if comp_class is not None:
            try:
                class_name = comp_class.get_name()
            except Exception:
                pass
        entry = {
            "name": var_name or "?",
            "class": class_name or "?",
            "children": [],
        }
        template = rt.safe_get(n, "ComponentTemplate", diag=None)
        if template is not None:
            try:
                entry["obj"] = template.get_name()
            except Exception:
                pass
        by_var[var_name] = entry

    # 组装父子关系
    roots = []
    for n in nodes:
        if n is None:
            continue
        var_name = rt.as_text(rt.safe_get(n, "VariableName"))
        parent = rt.safe_get(n, "ParentComponentOrVariableName", diag=None)
        parent_name = rt.as_text(parent) if parent is not None else None
        entry = by_var.get(var_name)
        if entry is None:
            continue
        if parent_name and parent_name in by_var:
            by_var[parent_name]["children"].append(entry)
        else:
            roots.append(entry)

    def strip(e):
        out = {"name": e["name"], "class": e["class"]}
        if e["children"]:
            out["children"] = [strip(c) for c in e["children"]]
        return out

    return [strip(r) for r in roots]


def _read_interfaces(bp, diag):
    out = []
    for iface in rt.safe_get(bp, "ImplementedInterfaces", diag=None) or []:
        obj = rt.safe_get(iface, "Interface", diag=None)
        if obj is not None:
            try:
                out.append(obj.get_name())
            except Exception:
                pass
    return out


def _read_data_tables(bp, diag):
    """从变量的默认值里挑出数据表引用（连招系统常用 DT_ 配置驱动）。"""
    out = []
    for var in rt.safe_get(bp, "NewVariables", diag=None) or []:
        if var is None:
            continue
        default_value = rt.as_text(rt.safe_get(var, "DefaultValue"))
        if default_value and ("DataTable" in default_value or "/Game/" in default_value):
            out.append({
                "var": rt.as_text(rt.safe_get(var, "VarName")),
                "default": default_value,
            })
    return out


def class_summary(asset_path):
    """L1：类摘要。"""
    diag = rt.Diagnostics()
    asset, err = load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls_name = asset_class_name(asset)
    if cls_name != "Blueprint":
        return rt.fail(
            "not a Blueprint (class=%s). Use a Blueprint asset path." % cls_name, diag)

    bp = asset
    parent = rt.safe_get(bp, "ParentClass", diag=diag)
    parent_name = None
    if parent is not None:
        try:
            parent_name = parent.get_name()
        except Exception:
            parent_name = None

    graphs = []
    for kind, graph in rt.get_blueprint_graphs(bp, diag):
        nodes = rt.safe_get(graph, "Nodes", diag=None) or []
        graphs.append({
            "name": rt.graph_name(graph),
            "kind": kind,
            "nodes": len(nodes),
        })

    payload = {
        "asset": asset_path,
        "class": cls_name,
        "parent": parent_name,
        "interfaces": _read_interfaces(bp, diag),
        "variables": _read_variables(bp, diag),
        "functions": _read_functions(bp, diag),
        "events": _read_events(bp, diag),
        "components": _read_components(bp, diag),
        "graphs": graphs,
    }

    tables = _read_data_tables(bp, diag)
    if tables:
        payload["data_table_refs"] = tables

    bp_type = rt.as_text(rt.safe_get(bp, "BlueprintType", diag=None))
    if bp_type:
        payload["blueprint_type"] = bp_type

    return rt.ok(payload, diag)


# ====================================================================== L2 图概览


def graph_overview(asset_path, graph_name=None):
    """L2：一个图的节点直方图与入口点，不含连线细节。"""
    diag = rt.Diagnostics()
    asset, err = load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    targets = []
    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        name = rt.graph_name(graph)
        if graph_name is None or name == graph_name:
            targets.append((kind, graph, name))

    if not targets:
        return rt.fail(
            "graph not found: %s. available: %s" % (
                graph_name,
                [rt.graph_name(g) for _, g in rt.get_blueprint_graphs(asset, diag)]),
            diag)

    out = []
    for kind, graph, name in targets:
        model = GraphModel(graph, diag)
        histogram = {}
        for node in model.nodes:
            histogram[node["cls"]] = histogram.get(node["cls"], 0) + 1

        entries = []
        for idx in model.entry_indices:
            node = model.nodes[idx]
            entries.append({
                "n": idx,
                "cls": node["cls"],
                "label": node["meta"].get("event") or node["meta"].get("fn") or node["title"],
            })

        out.append({
            "graph": name,
            "kind": kind,
            "node_count": len(model.nodes),
            "node_types": dict(sorted(histogram.items(), key=lambda kv: -kv[1])),
            "entries": entries,
        })

    return rt.ok({"asset": asset_path, "graphs": out}, diag)


# ====================================================================== L3 执行流


_MAX_FLOW_NODES = 400


def _inline_data(model, node_idx, pin_name, depth, budget):
    """把一个数据输入引脚解析成紧凑表达式。"""
    sources = model.data_sources(node_idx, pin_name)
    if not sources:
        pin = model.pin(node_idx, pin_name, direction="in")
        if pin and pin.get("default"):
            return pin["default"]
        return None
    if depth > 4 or budget[0] <= 0:
        return "<deep>"

    src_idx, src_pin = sources[0]
    budget[0] -= 1
    return _node_expr(model, src_idx, depth + 1, budget)


def _node_expr(model, node_idx, depth, budget):
    """把一个节点渲染成表达式 dict（含其数据来源）。"""
    if budget[0] <= 0:
        return "<budget>"
    node = model.nodes[node_idx]
    cls = node["cls"]
    meta = node["meta"]
    budget[0] -= 1

    expr = {"n": node_idx}

    if cls in _VAR_NODES:
        expr["op"] = "get"
        expr["var"] = meta.get("var") or node["title"] or cls
        if cls == "Self":
            expr["op"] = "self"
    elif cls in _FN_NODES or meta.get("fn"):
        expr["op"] = "call"
        expr["fn"] = meta.get("fn") or node["title"] or cls
        if meta.get("fn_class"):
            expr["on"] = meta["fn_class"]
        args = {}
        for pin in node["pins"]:
            if pin["dir"] != "in" or pin["type"] in ("exec", "delegate", "MulticastDelegate"):
                continue
            value = _inline_data(model, node_idx, pin["name"], depth, budget)
            if value is not None:
                args[pin["name"]] = value
        # 目标对象（self / 别的对象）
        target = _inline_data(model, node_idx, "self", depth, budget)
        if target is not None and target != "<deep>":
            args["self"] = target
        if args:
            expr["args"] = args
    elif cls == "DynamicCast":
        expr["op"] = "cast"
        expr["to"] = meta.get("cast_to") or "?"
        for pin in node["pins"]:
            if pin["dir"] == "in" and pin["type"] not in ("exec", "delegate"):
                value = _inline_data(model, node_idx, pin["name"], depth, budget)
                if value is not None:
                    expr["of"] = value
                    break
    elif cls == "IfThenElse":
        expr["op"] = "branch"
        cond = _inline_data(model, node_idx, "Condition", depth, budget)
        if cond is not None:
            expr["cond"] = cond
    elif cls in ("MacroInstance",):
        expr["op"] = "macro"
        expr["macro"] = meta.get("macro") or node["title"] or "?"
    else:
        expr["op"] = cls
        if node["title"]:
            expr["title"] = node["title"]

    return expr


def _walk_exec(model, start_idx, budget, visited, depth=0):
    """沿 exec 流走出一条语句序列。

    返回 ``[{node, expr, branches?}, ...]``。分支节点带 ``then`` / ``else``。
    遇到已访问过的节点时停止（避免循环图无限展开）。
    """
    stmts = []
    idx = start_idx
    guard = 0

    while idx is not None and budget[0] > 0 and guard < _MAX_FLOW_NODES:
        guard += 1
        if idx in visited:
            stmts.append({"n": idx, "op": "goto", "label": model.nodes[idx]["cls"]})
            return stmts
        visited.add(idx)

        node = model.nodes[idx]
        cls = node["cls"]
        entry = {"n": idx, "expr": _node_expr(model, idx, depth, budget)}

        if cls == "IfThenElse":
            outs = model.exec_out_targets(idx)
            named = {}
            for pin_name, tgt, _ in outs:
                named[pin_name] = tgt
            then_idx = named.get("then")
            else_idx = named.get("else")
            if then_idx is not None:
                entry["then"] = _walk_exec(model, then_idx, budget, visited, depth + 1)
            if else_idx is not None:
                entry["else"] = _walk_exec(model, else_idx, budget, visited, depth + 1)
            stmts.append(entry)
            return stmts

        if cls == "ExecutionSequence":
            outs = sorted(model.exec_out_targets(idx), key=lambda t: t[0])
            entry["seq"] = [
                _walk_exec(model, tgt, budget, visited, depth + 1)
                for _, tgt, _ in outs
            ]
            stmts.append(entry)
            return stmts

        stmts.append(entry)

        # 继续沿 exec 走：优先 then，其次第一个未命名的 exec 输出
        outs = model.exec_out_targets(idx)
        if not outs:
            return stmts
        preferred = None
        for pin_name, tgt, _ in outs:
            if pin_name in ("then", "execute", "Out", ""):
                preferred = tgt
                break
        if preferred is None:
            # 潜行节点（Delay / PlayMontage）有 completed 之类的输出
            preferred = outs[0][1]
        idx = preferred

    if budget[0] <= 0:
        stmts.append({"op": "truncated"})
    return stmts


def exec_flow(asset_path, graph_name=None, entry=None):
    """L3：从入口出发的执行流树。"""
    diag = rt.Diagnostics()
    asset, err = load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    results = []
    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        name = rt.graph_name(graph)
        if graph_name is not None and name != graph_name:
            continue

        model = GraphModel(graph, diag)
        budget = [1200]
        visited = set()
        flows = []

        for idx in model.entry_indices:
            node = model.nodes[idx]
            if node["cls"] == "FunctionResult":
                continue
            if entry:
                label = (node["meta"].get("event") or node["meta"].get("fn")
                         or node["title"] or "")
                if entry.lower() not in str(label).lower():
                    continue
            outs = model.exec_out_targets(idx)
            body = []
            for _, tgt, _ in outs:
                body.extend(_walk_exec(model, tgt, budget, visited))
            flows.append({
                "entry": node["meta"].get("event") or node["meta"].get("fn") or node["title"] or node["cls"],
                "entry_n": idx,
                "body": body,
            })

        results.append({
            "graph": name,
            "kind": kind,
            "node_count": len(model.nodes),
            "flows": flows,
        })

    if not results:
        return rt.fail("no graph matched: %s" % graph_name, diag)

    return rt.ok({
        "asset": asset_path,
        "graphs": results,
        "budget_left": 0,
    }, diag)


# ====================================================================== L3' DSL


def _expr_to_dsl(expr, indent=0):
    """把表达式 dict 渲染成 S 表达式片段。"""
    if not isinstance(expr, dict):
        return _lit(expr)
    op = expr.get("op", "?")

    if op == "get":
        return str(expr.get("var", "?"))
    if op == "self":
        return "self"
    if op == "call":
        head = "(call %s" % expr.get("fn", "?")
        if expr.get("on"):
            head += " :on %s" % expr["on"]
        for key, value in (expr.get("args") or {}).items():
            if key == "self":
                continue
            head += " :%s %s" % (key, _lit(value))
        target = (expr.get("args") or {}).get("self")
        if target and target != "self":
            head += " :target %s" % _lit(target)
        return head + ")"
    if op == "cast":
        return "(cast %s %s)" % (_lit(expr.get("of")), expr.get("to", "?"))
    if op == "branch":
        return "(if %s)" % _lit(expr.get("cond"))
    if op == "macro":
        return "(macro %s)" % expr.get("macro", "?")
    if op == "goto":
        return "(goto %s)" % expr.get("label", "?")
    if op == "truncated":
        return "..."
    title = expr.get("title") or op
    return "(%s)" % title


def _lit(value):
    if isinstance(value, str):
        return '"%s"' % value.replace('"', '\\"')
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return _expr_to_dsl(value)
    return str(value)


def _stmt_to_dsl(stmt, indent):
    pad = "  " * indent
    if not isinstance(stmt, dict):
        return pad + _lit(stmt)

    if stmt.get("op") == "truncated":
        return pad + "... (truncated)"
    if stmt.get("op") == "goto":
        return "%s(goto n%s)" % (pad, stmt.get("n"))

    expr = stmt.get("expr", {})
    lines = ["%s%s" % (pad, _expr_to_dsl(expr))]

    if "then" in stmt or "else" in stmt:
        lines[-1] = "%s(if %s" % (pad, _lit(expr.get("cond")))
        if stmt.get("then"):
            lines[-1] += "\n%s  (:then" % pad
            for s in stmt["then"]:
                lines.append(_stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        if stmt.get("else"):
            lines[-1] += "\n%s  (:else" % pad
            for s in stmt["else"]:
                lines.append(_stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        lines[-1] += ")"

    if "seq" in stmt:
        lines = ["%s(seq" % pad]
        for i, branch in enumerate(stmt["seq"]):
            lines.append("%s  (:then_%d" % (pad, i))
            for s in branch:
                lines.append(_stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        lines.append("%s)" % pad)

    return "\n".join(lines)


def graph_dsl(asset_path, graph_name=None, entry=None, max_flows=12):
    """L3'：把执行流渲染成 S 表达式文本（最紧凑的读法）。"""
    flow = exec_flow(asset_path, graph_name=graph_name, entry=entry)
    if "error" in flow:
        return flow

    chunks = []
    for graph in flow.get("graphs", []):
        chunks.append("; ==== graph: %s (%s, %d nodes)" % (
            graph["graph"], graph["kind"], graph["node_count"]))
        for f in graph["flows"][:max_flows]:
            chunks.append("(event %s" % f.get("entry", "?"))
            for stmt in f.get("body", []):
                chunks.append(_stmt_to_dsl(stmt, 1))
            chunks.append(")")
        if len(graph["flows"]) > max_flows:
            chunks.append("; ... %d more entries omitted" % (len(graph["flows"]) - max_flows))

    return rt.ok({
        "asset": asset_path,
        "dsl": "\n".join(chunks),
        "_diag": flow.get("_diag", {}),
    }, None)


# ====================================================================== L4 节点详情


def node_detail(asset_path, graph_name, node_index):
    """L4：单个节点的完整信息（引脚、连接、属性）。"""
    diag = rt.Diagnostics()
    asset, err = load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        if rt.graph_name(graph) != graph_name:
            continue
        model = GraphModel(graph, diag)
        try:
            idx = int(node_index)
        except Exception:
            return rt.fail("node index must be an integer", diag)
        if idx < 0 or idx >= len(model.nodes):
            return rt.fail("node index out of range (0..%d)" % (len(model.nodes) - 1), diag)

        node = model.nodes[idx]
        pins = []
        for pin in node["pins"]:
            entry = {
                "name": pin["name"],
                "dir": pin["dir"],
                "type": pin["type"],
            }
            for key in ("default", "default_obj", "to"):
                if key in pin:
                    entry[key] = pin[key]
            pins.append(entry)

        return rt.ok({
            "asset": asset_path,
            "graph": graph_name,
            "node": idx,
            "cls": node["cls"],
            "title": node["title"],
            "guid": node["guid"],
            "pos": node["pos"],
            "comment": node["comment"],
            "meta": node["meta"],
            "pins": pins,
        }, diag)

    return rt.fail("graph not found: %s" % graph_name, diag)


def node_reflect(asset_path, graph_name, node_index):
    """诊断用：反射枚举节点的全部 Python 可读属性。

    用于在 UE 版本漂移后重新发现正确的属性名。
    """
    diag = rt.Diagnostics()
    asset, err = load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    for _, graph in rt.get_blueprint_graphs(asset, diag):
        if rt.graph_name(graph) != graph_name:
            continue
        model = GraphModel(graph, diag)
        idx = int(node_index)
        node = model.nodes[idx]["obj"]

        attrs = {}
        for name in dir(node):
            if name.startswith("_"):
                continue
            try:
                value = getattr(node, name)
            except Exception:
                continue
            if callable(value):
                continue
            try:
                rendered = rt.jsonable(value)
            except Exception:
                rendered = "<unreadable>"
            # 诊断输出要短：超长值截断，避免把一个节点撑成几万 token
            text = rt.dumps(rendered)
            attrs[name] = rendered if len(text) <= 400 else text[:400] + "..."
        return rt.ok({
            "cls": rt.node_class_short(node),
            "python_attrs": attrs,
        }, diag)

    return rt.fail("graph not found: %s" % graph_name, diag)
