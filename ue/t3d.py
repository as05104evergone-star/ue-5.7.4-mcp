# -*- coding: utf-8 -*-
r"""
ComboMCP / T3D 文本解析（只读）
===============================

T3D 是 UE 的文本序列化格式。蓝图资产导出成 T3D 后，节点、属性以及**引脚与连线**
都会以 ``CustomProperties Pin (...)`` 的形式落在文本里。

这条路为什么重要：引擎反射对 ``UEdGraphNode::Pins`` 并不友好（该成员在
EdGraphNode.h 里没有 ``UPROPERTY()`` 宏，官方 Python 文档里 ``unreal.EdGraphNode``
也几乎没有属性）。而 T3D 是引擎自己写出来的文本，引脚与连线在里面是完整的。

这个模块只做一件事：把 T3D 文本解析成 :mod:`graph_ir` 定义的 IR。
它**不依赖 unreal**，因此可以在任何 Python 进程里单测。

T3D 形状（蓝图）::

    Begin Object Class=/Script/Engine.Blueprint Name="AC_Combat"
       Begin Object Class=/Script/Engine.EdGraph Name="EventGraph"
          Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0"
             EventReference=(MemberName="ReceiveBeginPlay")
             NodePosX=100
             CustomProperties Pin (PinId=AB..,PinName="then",PinType.PinCategory="exec",
                 Direction="EGPD_Output",LinkedTo=(K2Node_CallFunction_0 CD..,),)
          End Object
       End Object
    End Object

注意引脚可能跨行——UE 会把长引脚写成多行。
"""

import re

from . import graph_ir


# ====================================================================== 词法


_RE_BEGIN = re.compile(r'^\s*Begin Object\b(.*)$')
_RE_END = re.compile(r'^\s*End Object\b\s*$')
_RE_ASSIGN = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$')
_RE_CLASS = re.compile(r'Class\s*=\s*([^\s]+)')
_RE_NAME = re.compile(r'Name\s*=\s*"((?:[^"\\]|\\.)*)"')
# ExportPath 是跨两遍认人的钥匙：第二遍的 Begin Object 没有 Class=，
# 只有 ExportPath 能把它和第一遍的那个对象对上。
_RE_EXPORT_PATH = re.compile(r'ExportPath\s*=\s*"((?:[^"\\]|\\.)*)"')

# 属性数组：CompositeSections(0)=(...)、Notifies(3)=(...)、SlotAnimTracks(0)=(...)
# 这类属性在蓝图里没有，但在 Montage/AnimSequence 里是核心数据。
_RE_INDEXED_PROP = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*(\d+)\s*\)\s*=\s*\(')
# 结构体字段：Key=... （用于区分 `(Key=V,...)` 结构 与 `((...),(...))` 数组）
_RE_STRUCT_FIELD = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\s*=')

# 引脚在 T3D 里可能出现几种形态，按可能性排序逐个匹配。
#
# 首选是 ``CustomProperties Pin (...)``：这是 UE 把引脚当作 tagged property
# 序列化时的文本形态（``UEdGraphPin::ExportTextItem`` 负责括号里的内容，
# ``UEdGraphNode::Serialize`` 通过 ``SerializeAsOwningNode`` 把引脚挂在节点上）。
# 后面几种是防御性退路——万一某个引擎版本把它写成普通属性数组，
# 解析器不至于整个失效。实际命中哪一种会记在诊断信息里。
_RE_PIN_FORMS = (
    ("custom_properties", re.compile(r'CustomProperties\s+Pin\s*\(')),
    ("bare_pin", re.compile(r'^\s*Pin\s*\(')),
    ("pins_indexed", re.compile(r'^\s*Pins\s*\(\s*\d+\s*\)\s*=\s*\(')),
    ("pins_array", re.compile(r'^\s*Pins\s*=\s*\(')),
)


def _match_pin_form(line):
    """返回 ``(形态名, 左括号下标)`` 或 ``(None, -1)``。"""
    for form_name, pattern in _RE_PIN_FORMS:
        match = pattern.search(line)
        if not match:
            continue
        # 定位该匹配里的第一个 '('
        open_index = line.find("(", match.start())
        if open_index >= 0:
            return form_name, open_index
    return None, -1


def _strip_quotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def _short_class(class_path):
    """``/Script/BlueprintGraph.K2Node_Event`` -> ``K2Node_Event``"""
    if not class_path:
        return "?"
    tail = class_path.rsplit(".", 1)[-1]
    tail = tail.rsplit("/", 1)[-1]
    if tail.startswith("K2Node_"):
        return tail[len("K2Node_"):]
    return tail


def _split_top_level(text, separator=","):
    """按顶层分隔符切分，忽略括号内的分隔符。"""
    parts, depth, current = [], 0, []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            current.append(ch)
        elif ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _find_balanced(text, start):
    """从 ``text[start]`` 处的左括号开始，返回匹配右括号的下标。"""
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# ====================================================================== 对象


class T3DObject(object):
    """一个 ``Begin Object ... End Object`` 块。

    UE 把一次导出写成**两遍**：第一遍给每个对象写完整头
    （``Class=`` / ``Name=`` / ``ExportPath=``）但不带属性；第二遍用
    ``Begin Object Name="..." ExportPath="..."``（**没有 Class=**）重新打开
    同一个对象并写属性与引脚。

    所以同一个对象会被遇到两次。``path``（ExportPath）是跨两遍认人的钥匙——
    它是全局唯一的对象路径。两遍合并后，``cls`` 来自第一遍、``props``/``pins``
    来自第二遍。
    """

    __slots__ = ("cls_path", "cls", "name", "props", "children", "pins",
                 "parent", "order", "path", "indexed_props")

    def __init__(self, cls_path, name, path=None):
        self.cls_path = cls_path
        self.cls = _short_class(cls_path)
        self.name = name
        self.props = {}
        self.children = []
        self.pins = []
        self.parent = None
        self.order = 0
        self.path = path or ""
        # 属性数组：key -> {index: 解析后的结构体/dict}
        self.indexed_props = {}

    def indexed(self, key):
        """按出现顺序返回 ``Key(N)=(...)`` 的解析结果列表。"""
        bucket = self.indexed_props.get(key) or {}
        return [bucket[i] for i in sorted(bucket.keys())]

    def indexed_count(self, key):
        return len(self.indexed_props.get(key) or {})

    def set_class_if_missing(self, cls_path):
        """第二遍遇到同一个对象时，类名已在第一遍拿到，这里只做补齐。"""
        if not self.cls_path or self.cls_path == "?":
            if cls_path:
                self.cls_path = cls_path
                self.cls = _short_class(cls_path)

    def __repr__(self):
        return "<T3DObject %s %s props=%d pins=%d children=%d>" % (
            self.cls, self.name, len(self.props), len(self.pins), len(self.children))


def _parse_pin(content):
    """解析一个 ``CustomProperties Pin (...)`` 的内容。"""
    pin = {"attrs": {}, "linked": []}
    for chunk in _split_top_level(content, ","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        value = value.strip()

        if key == "LinkedTo":
            inner = value.strip()
            if inner.startswith("(") and inner.endswith(")"):
                inner = inner[1:-1]
            for link in _split_top_level(inner, ","):
                link = link.strip()
                if not link:
                    continue
                bits = link.split()
                if len(bits) >= 2:
                    pin["linked"].append((bits[0], bits[1]))
                elif len(bits) == 1:
                    pin["linked"].append((bits[0], ""))
            continue

        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = _strip_quotes(value)
        pin["attrs"][key] = value
    return pin


def parse(text, stats=None):
    """把 T3D 文本解析成对象树，返回顶层对象列表。

    关键点：UE 的导出是**两遍**的（真实 5.7 导出实测）——

    * 第一遍：``Begin Object Class=/Script/Engine.EdGraph Name="EventGraph" ExportPath="..."``
      只给结构，没有属性。
    * 第二遍：``Begin Object Name="EventGraph" ExportPath="..."``（**没有 Class=**）
      重新打开同一个对象，写入属性与 ``CustomProperties Pin``。

    所以这里用 ``ExportPath`` 作为对象身份：两遍遇到同一个路径时合并到同一个
    :class:`T3DObject` 上——类名取第一遍的，属性与引脚取第二遍的。若不这样合并，
    第二遍的对象会因为缺 ``Class=`` 而被当成类名未知的野对象，整个图就读不出来。

    ``stats`` 传入一个 dict 时，会在其中记录实际命中的引脚形态与计数，
    便于在引擎版本变化后立即看出格式是否变了。
    """
    roots = []
    stack = []
    current = None
    pending = None       # 跨行属性
    by_path = {}         # ExportPath -> T3DObject

    # UE 把 T3D 写成 **UTF-8 with BOM**（实测：文件头是 EF BB BF）。
    # 若调用方用普通 utf-8 读进来，首行会带上 \ufeff，而 Python 3 的 \s 不匹配它，
    # 于是 "^\s*Begin Object" 在首行失配 —— 整个根对象（Blueprint）会被静默丢掉，
    # 所有子图变成顶层。这里显式剥掉，任何来源的文本都不会踩这个坑。
    if text and text[0] == "\ufeff":
        text = text[1:]

    def identity(parent, name, path):
        """给对象算一个跨两遍稳定的身份。"""
        if path:
            return path
        parent_path = parent.path if parent is not None else ""
        return "%s:%s" % (parent_path, name)

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n").rstrip("\r")

        # ---- 属性续行：上一行括号未闭合
        if pending is not None:
            merged = pending["text"] + " " + line.strip()
            if merged.count("(") - merged.count(")") > 0:
                pending["text"] = merged
                continue
            line = merged
            target = pending["obj"]
            kind = pending.get("kind")
            pending = None
            if kind == "pin":
                open_index = line.find("(")
                close_index = _find_balanced(line, open_index)
                if close_index > open_index:
                    target.pins.append(_parse_pin(line[open_index + 1:close_index]))
                    continue
            if kind == "indexed":
                if _finish_indexed(target, line):
                    continue
            _assign(target, line)
            continue

        begin = _RE_BEGIN.match(line)
        if begin:
            header = begin.group(1)
            cls_match = _RE_CLASS.search(header)
            name_match = _RE_NAME.search(header)
            path_match = _RE_EXPORT_PATH.search(header)

            cls_path = cls_match.group(1) if cls_match else None
            name = name_match.group(1) if name_match else "?"
            path = path_match.group(1) if path_match else None

            parent = stack[-1] if stack else None
            key = identity(parent, name, path)

            obj = by_path.get(key)
            if obj is None:
                obj = T3DObject(cls_path or "?", name, path=key)
                obj.order = len(by_path)
                if parent is not None:
                    obj.parent = parent
                    parent.children.append(obj)
                else:
                    roots.append(obj)
                by_path[key] = obj
                if stats is not None:
                    stats["_objects"] = stats.get("_objects", 0) + 1
                    if not cls_path:
                        stats["_objects_without_class"] = \
                            stats.get("_objects_without_class", 0) + 1
            else:
                # 第二遍（或重复出现）：补齐类名即可，属性照常写进同一个对象
                obj.set_class_if_missing(cls_path)
                if stats is not None:
                    stats["_merged_second_pass"] = stats.get("_merged_second_pass", 0) + 1

            stack.append(obj)
            current = obj
            continue

        if _RE_END.match(line):
            if stack:
                stack.pop()
            current = stack[-1] if stack else None
            continue

        if current is None:
            continue

        # ---- 引脚（可能跨行）
        form_name, open_index = _match_pin_form(line)
        if form_name is not None:
            if stats is not None:
                stats[form_name] = stats.get(form_name, 0) + 1
            close_index = _find_balanced(line, open_index)
            if close_index < 0:
                pending = {"obj": current, "kind": "pin", "text": line}
                continue
            current.pins.append(_parse_pin(line[open_index + 1:close_index]))
            continue

        # ---- 属性数组 Key(Index)=(...)（Montage 的 Section/Notify/槽位）
        indexed_match = _RE_INDEXED_PROP.match(line)
        if indexed_match:
            open_index = line.index("(", indexed_match.end() - 1)
            close_index = _find_balanced(line, open_index)
            if close_index < 0:
                pending = {"obj": current, "kind": "indexed", "text": line}
                continue
            if _finish_indexed(current, line):
                continue

        # ---- 普通属性
        assign = _RE_ASSIGN.match(line)
        if assign:
            key, value = assign.group(1), assign.group(2).strip()
            if value.count("(") - value.count(")") > 0:
                pending = {"obj": current, "kind": "prop", "text": line}
                current.props[key] = value
                continue
            current.props[key] = _strip_quotes(value)
            continue

    return roots


def _assign(obj, line):
    """处理跨行属性的收尾赋值。"""
    assign = _RE_ASSIGN.match(line)
    if not assign:
        return
    key, value = assign.group(1), assign.group(2).strip()
    obj.props[key] = _strip_quotes(value)


# ====================================================================== 结构体


def _parse_value(value):
    """把一个 T3D 值解析成 Python 值。

    括号里的东西有两种，靠"去掉一层括号后顶层切分出来的块是否都是 ``Key=``"
    来区分：

        (Key=V,Key2=V2)        -> dict
        ((...),(...))          -> list
    """
    text = value.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return _strip_quotes(text)

    inner = text[1:-1].strip()
    if not inner:
        return {}

    parts = [p for p in _split_top_level(inner, ",") if p.strip()]
    if parts and all(_RE_STRUCT_FIELD.match(p.strip()) for p in parts):
        return _parse_struct(inner)
    return [_parse_value(p) for p in parts]


def _parse_struct(content):
    """解析 ``Key=Value,Key2=(...),...`` 成 dict（嵌套递归）。"""
    out = {}
    for chunk in _split_top_level(content, ","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = _parse_value(value)
    return out


def _finish_indexed(obj, text):
    """处理 ``Key(Index)=(...)``：找到配对的右括号并解析成结构体。"""
    match = _RE_INDEXED_PROP.match(text)
    if not match:
        return False
    key = match.group(1)
    index = int(match.group(2))
    open_index = text.index("(", match.end() - 1)
    close_index = _find_balanced(text, open_index)
    if close_index < 0:
        return False
    parsed = _parse_value(text[open_index:close_index + 1])
    bucket = obj.indexed_props.setdefault(key, {})
    bucket[index] = parsed
    return True


# ====================================================================== 语义


_RE_MEMBER_NAME = re.compile(r'MemberName\s*=\s*"((?:[^"\\]|\\.)*)"')
_RE_SINGLE_QUOTED = re.compile(r"'([^']*)'")
_RE_DOUBLE_QUOTED = re.compile(r'"([^"]*)"')


def _member_name(value):
    if not value:
        return None
    match = _RE_MEMBER_NAME.search(value)
    if match:
        return match.group(1)
    return None


def _object_short_name(value):
    """从 UE 的对象引用文本里取出短名。

    真实 T3D 里出现的写法（都取自 UE 5.7 实际导出）：

        MemberParent="/Script/CoreUObject.Class'/Script/Engine.Actor'"   -> Actor
        PinType.PinSubCategoryObject="/Script/CoreUObject.Class'/Script/Engine.Actor'" -> Actor
        Class'"/Script/Engine.AnimMontage"'                              -> AnimMontage
        AnimMontage'"/Game/.../AS_Combo01_01.AS_Combo01_01"'             -> AS_Combo01_01
        EdGraph'"/Game/X.Y:MacroGraph_0"'                                -> MacroGraph_0
        /Script/Engine.Actor                                             -> Actor

    规则：先取引号里的那段路径（单引号优先，因为外层路径常被双引号包着），
    再取冒号之后、最后一个点之后的部分。
    """
    if not value:
        return None
    text = value.strip()

    match = _RE_SINGLE_QUOTED.search(text) or _RE_DOUBLE_QUOTED.search(text)
    if match:
        text = match.group(1)

    text = text.strip().strip("'\"")
    if not text:
        return None

    tail = text.rsplit(":", 1)[-1] if ":" in text else text
    tail = tail.rsplit(".", 1)[-1]
    tail = tail.strip().strip("'\"")
    return tail or None


# 兼容旧调用点
_obj_name_from_path = _object_short_name


def _member_parent(value):
    return _object_short_name(value)


# 这些节点类，语义从成员引用里取
_FN_NODES = graph_ir.FN_CLASSES
_VAR_NODES = graph_ir.VAR_CLASSES


def t3d_object_semantics(obj):
    """从 T3D 属性里提取节点的"这是什么操作"。"""
    meta = {}
    props = obj.props
    cls = obj.cls

    if cls in _FN_NODES or "FunctionReference" in props:
        ref = props.get("FunctionReference")
        name = _member_name(ref)
        if name:
            meta["fn"] = name
        parent = _member_parent(ref)
        if parent:
            meta["fn_class"] = parent
        if props.get("bIsPureFunc") in ("True", "true", "1"):
            meta["pure"] = True

    elif cls in _VAR_NODES or "VariableReference" in props:
        name = _member_name(props.get("VariableReference"))
        if name:
            meta["var"] = name

    if cls == "Event":
        name = _member_name(props.get("EventReference"))
        meta["event"] = name or props.get("CustomFunctionName") or "?"
    elif cls == "CustomEvent":
        meta["event"] = props.get("CustomFunctionName") or "?"

    if cls in ("MacroInstance", "Composite"):
        macro = _obj_name_from_path(props.get("MacroGraphReference"))
        if not macro:
            macro = props.get("MacroGraphName")
        meta["macro"] = macro or "?"

    if cls == "DynamicCast":
        target = props.get("TargetType")
        if target:
            meta["cast_to"] = target.rsplit(".", 1)[-1].rstrip("'\"")
        if props.get("bIsPureCast") in ("True", "true", "1"):
            meta["pure"] = True

    if cls == "Timeline":
        meta["timeline"] = props.get("TimelineName") or "?"

    return meta


# ====================================================================== IR 构建


def _graph_kind(graph_obj, parent_blueprint):
    """判断一个 EdGraph 属于事件图 / 函数图 / 宏图。"""
    name = graph_obj.name or ""
    if parent_blueprint is not None:
        for key, kind in (("UbergraphPages", "event"),
                          ("FunctionGraphs", "function"),
                          ("MacroGraphs", "macro")):
            # T3D 里数组以 +UbergraphPages=... 形式出现
            for prop_key, prop_value in parent_blueprint.props.items():
                if prop_key.lstrip("+").startswith(key) and name in str(prop_value):
                    return kind
    if name.lower().startswith("macrograph") or "macro" in name.lower():
        return "macro"
    if name == "EventGraph" or name.lower().endswith("ubergraph"):
        return "event"
    if name.lower().startswith("executeubergraph"):
        return "event"
    return "function"


# 这些类本身就是"图"，它们的子对象是节点，而不是嵌套图里的节点
GRAPH_CLASSES = frozenset((
    "EdGraph", "AnimationGraph", "AnimationStateMachineGraph",
    "AnimationStateGraph", "AnimationTransitionGraph",
    "AnimationConduitGraph", "AnimationCustomTransitionGraph",
    "EdGraphPin", "EdGraphSchema",
))


def graph_to_ir(graph_obj, kind_hint=None, index_map=None):
    """把一个 EdGraph 对象转成 IR 图。"""
    nodes = []
    pin_owner = {}     # (node_name, pin_id) -> (node_index, pin_name)
    keepalive = []

    # 第一遍：节点与引脚
    for child in graph_obj.children:
        cls = child.cls
        if not cls or cls in GRAPH_CLASSES:
            continue
        node = {
            "id": len(nodes),
            "cls": cls,
            "title": child.props.get("NodeComment") or None,
            "pos": [_to_int(child.props.get("NodePosX")),
                    _to_int(child.props.get("NodePosY"))],
            "comment": child.props.get("NodeComment") or None,
            "meta": t3d_object_semantics(child),
            "pins": [],
            "_name": child.name,
        }
        for raw_pin in child.pins:
            attrs = raw_pin["attrs"]
            name = attrs.get("PinName") or "?"
            direction = attrs.get("Direction") or ""
            pin = {
                "name": name,
                "dir": "out" if "Output" in direction else "in",
                "type": _pin_type_from_attrs(attrs),
                "_id": attrs.get("PinId") or "",
                "_linked": raw_pin["linked"],
            }
            default_value = attrs.get("DefaultValue")
            if default_value:
                pin["default"] = default_value
            default_object = attrs.get("DefaultObject")
            if default_object and default_object not in ("None", ""):
                pin["default_obj"] = _obj_name_from_path(default_object) or default_object
            node["pins"].append(pin)
            pin_owner[(child.name, pin["_id"])] = (node["id"], name)
        keepalive.append(node)
        nodes.append(node)

    # 第二遍：解析连线
    for node in nodes:
        for pin in node["pins"]:
            resolved = []
            for target_node_name, target_pin_id in pin.get("_linked") or []:
                owner = pin_owner.get((target_node_name, target_pin_id))
                if owner is None:
                    # 有些 T3D 只给节点名（单引脚节点），退化为按节点名找
                    for (nname, _pid), o in pin_owner.items():
                        if nname == target_node_name:
                            owner = o
                            break
                if owner is not None:
                    resolved.append([owner[0], owner[1]])
            if resolved:
                pin["to"] = resolved
            pin.pop("_linked", None)
            pin.pop("_id", None)

    for node in nodes:
        node.pop("_name", None)

    entries = [n["id"] for n in nodes if graph_ir.looks_like_entry(n)]

    return {
        "name": graph_obj.name,
        "kind": kind_hint or "unknown",
        "nodes": nodes,
        "entries": entries,
    }


def _to_int(value):
    try:
        return int(float(value))
    except Exception:
        return 0


_PIN_CATEGORY = {
    "exec": "exec",
    "bool": "bool",
    "byte": "byte",
    "int": "int",
    "int64": "int64",
    "real": "float",
    "float": "float",
    "double": "double",
    "name": "Name",
    "string": "String",
    "text": "Text",
    "object": "Object",
    "class": "class",
    "struct": "Struct",
    "interface": "Interface",
    "delegate": "Delegate",
    "wildcard": "Wildcard",
}


def _pin_type_from_attrs(attrs):
    """把 T3D 的 PinType.* 属性渲染成可读类型字符串。"""
    category = (attrs.get("PinType.PinCategory") or "").lower()
    base = _PIN_CATEGORY.get(category, category or "?")

    sub_object = attrs.get("PinType.PinSubCategoryObject") or ""
    if sub_object and sub_object not in ("None", ""):
        tail = _obj_name_from_path(sub_object) or sub_object
        base = tail
    else:
        sub = attrs.get("PinType.PinSubCategory") or ""
        if sub and sub.lower() not in ("self", "none"):
            base = sub

    container = (attrs.get("PinType.ContainerType") or "None").lower()
    if container == "array":
        base = "TArray<%s>" % base
    elif container == "set":
        base = "TSet<%s>" % base
    elif container == "map":
        base = "TMap<%s, ?> " % base

    if (attrs.get("PinType.bIsConst") or "").lower() in ("true", "1"):
        base = "const " + base
    if (attrs.get("PinType.bIsReference") or "").lower() in ("true", "1"):
        base += "&"
    return base


def build_graphs(text, stats=None):
    """T3D 文本 -> ``[IRGraph, ...]``。

    每个对象只访问一次：蓝图自身与它下面任意深度的图都会被走到，
    嵌套图（状态机里的子图）也会各自成为一张 IR 图。

    ``stats`` 会收到引脚形态计数（见 :data:`_RE_PIN_FORMS`）。
    """
    roots = parse(text, stats=stats)
    graphs = []
    seen = set()

    def walk(obj, blueprint):
        if id(obj) in seen:
            return
        seen.add(id(obj))
        if obj.cls in GRAPH_CLASSES:
            kind = _graph_kind(obj, blueprint)
            graphs.append(graph_ir.IRGraph(graph_to_ir(obj, kind)))
        for child in obj.children:
            walk(child, blueprint)

    for root in roots:
        bp = root if root.cls in ("Blueprint", "AnimBlueprint") else None
        walk(root, bp)

    return graphs


def build_graphs_from_file(path):
    """从磁盘读 T3D 并构建 IR。

    UE 写出的 T3D 是 **UTF-8 with BOM**，所以这里用 ``utf-8-sig`` 读；
    ``parse`` 里还会再剥一次 BOM 作为兜底。
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return build_graphs(handle.read())
