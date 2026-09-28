# -*- coding: utf-8 -*-
r"""
ComboMCP / UE 内只读运行时
==========================

本模块在 Unreal Editor 的 Python 环境里执行，负责把 UE 的反射对象安全地
转换成 JSON 可序列化的紧凑结构。

设计原则
--------
1. **只读**：绝不调用任何会修改资产 / 图 / 变量 / 文件夹的 API。本文件里
   不存在 save / modify / compile / set_editor_property 之类的调用。
2. **防御**：每一次属性读取都经过 ``safe_get``。读不到不抛异常，而是记入
   diagnostics。原因是 UPROPERTY 在不同 UE 小版本间的 Python 暴露面会变化
   （例如 UEdGraphPin 从 struct 改成 UObject 就是一次破坏性变更），
   一次性写死的读取代码在版本漂移时必然静默出错。
3. **紧凑**：面向"给模型看"，不是"给机器看"。空值、默认值、冗余字段一律剔除。
4. **可追溯**：每条读取失败都记录 ``(对象, 属性, 异常)``，便于事后校正。

约定
----
* 所有对外返回的 dict 都可以直接 ``json.dumps``。
* 任何函数都不接受"可能不存在"的输入而不做检查。
"""

import json
import traceback

try:
    import unreal
except ImportError:  # 在编辑器外被 import 时不应崩溃
    unreal = None


# ====================================================================== 诊断


class Diagnostics(object):
    """收集读取失败与提示，随结果一并返回，便于事后校正读取器。"""

    def __init__(self):
        self.misses = []
        self.notes = []
        self._seen = set()

    def miss(self, owner, prop, err):
        key = (str(owner), str(prop))
        if key in self._seen:
            return
        self._seen.add(key)
        self.misses.append({
            "owner": str(owner)[:120],
            "prop": str(prop)[:80],
            "error": str(err)[:200],
        })

    def note(self, msg):
        self.notes.append(str(msg)[:300])

    def as_dict(self):
        out = {"miss_count": len(self.misses)}
        if self.misses:
            out["misses"] = self.misses[:60]
        if self.notes:
            out["notes"] = self.notes[:40]
        return out


# ====================================================================== 基础转换


def as_text(value):
    """把 UE 的 Name / Text / String / Enum 统一成 Python str。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    try:
        return str(value)
    except Exception:
        return None


def obj_label(obj):
    """给对象一个人类可读的短标签，用于诊断输出。"""
    if obj is None:
        return "<None>"
    try:
        name = obj.get_name()
        cls = obj.get_class().get_name()
        return "{0}({1})".format(cls, name)
    except Exception:
        try:
            return str(obj)[:80]
        except Exception:
            return "<unprintable>"


def safe_get(obj, prop, default=None, diag=None):
    """防御性读取 UPROPERTY。

    读取失败时返回 ``default`` 并记录诊断，绝不抛出。
    """
    if obj is None:
        return default
    try:
        return obj.get_editor_property(prop)
    except Exception as exc:
        if diag is not None:
            diag.miss(obj_label(obj), prop, exc)
        return default


def safe_call(obj, method, *args, **kwargs):
    """防御性调用方法；失败返回 ``(False, None, error)``。"""
    if obj is None:
        return False, None, "obj is None"
    fn = getattr(obj, method, None)
    if fn is None:
        return False, None, "no such method: {0}".format(method)
    try:
        return True, fn(*args, **kwargs), None
    except Exception as exc:
        return False, None, str(exc)


def jsonable(value, depth=0, diag=None):
    """递归转换成 JSON 可序列化的值。"""
    if depth > 6:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v, depth + 1, diag) for v in value]
    if isinstance(value, dict):
        return dict((str(k), jsonable(v, depth + 1, diag)) for k, v in value.items())

    # UE 常见数学结构：优先取分量，比 str() 更干净
    for attrs, keys in (
        (("x", "y", "z", "w"), ("x", "y", "z", "w")),
        (("r", "g", "b", "a"), ("r", "g", "b", "a")),
    ):
        try:
            if all(hasattr(value, a) for a in attrs[:3]):
                out = {}
                for a, k in zip(attrs, keys):
                    if hasattr(value, a):
                        out[k] = round(float(getattr(value, a)), 4)
                if out:
                    return out
        except Exception:
            pass

    text = as_text(value)
    return text if text is not None else "<unserializable>"


# ====================================================================== 引脚类型


_PIN_CATEGORY = {
    "bool": "bool",
    "byte": "byte",
    "class": "class",
    "int": "int",
    "int64": "int64",
    "real": "float",
    "float": "float",
    "double": "double",
    "name": "Name",
    "string": "String",
    "text": "Text",
    "object": "Object",
    "softobject": "SoftObject",
    "softclass": "SoftClass",
    "struct": "Struct",
    "exec": "exec",
    "interface": "Interface",
    "delegate": "Delegate",
    "wildcard": "Wildcard",
    "mcdelegate": "MulticastDelegate",
}

_CONTAINER = {
    "NONE": "",
    "ARRAY": "[]",
    "SET": "{}",
    "MAP": "",
}


def pin_type_str(pin_type, diag=None):
    """把 FEdGraphPinType 渲染成可读的类型字符串。

    例：``TArray<AActor*>` ``、``FGameplayTag``、``bool``、``exec``。
    """
    if pin_type is None:
        return "?"

    cat = as_text(safe_get(pin_type, "PinCategory", diag=diag)) or ""
    sub = as_text(safe_get(pin_type, "PinSubCategory", diag=diag)) or ""
    sub_obj = safe_get(pin_type, "PinSubCategoryObject", diag=diag)
    container = as_text(safe_get(pin_type, "ContainerType", diag=diag)) or "NONE"
    is_ref = safe_get(pin_type, "bIsReference", diag=diag)
    is_const = safe_get(pin_type, "bIsConst", diag=diag)
    is_array = safe_get(pin_type, "bIsArray", diag=diag)

    base = _PIN_CATEGORY.get(cat.lower(), cat or "?")

    # 结构体 / 类 / 对象：用真实类型名取代泛化的 "struct"/"object"
    if sub_obj is not None:
        try:
            sub_name = sub_obj.get_name()
            if sub_name:
                base = sub_name
        except Exception:
            pass
    elif sub and sub.lower() not in ("self", "none"):
        base = sub

    # 容器包装（ContainerType 是新版字段，bIsArray 是旧版回退）
    if container == "ARRAY" or (is_array and container == "NONE"):
        base = "TArray<%s>" % base
    elif container == "SET":
        base = "TSet<%s>" % base
    elif container == "MAP":
        base = "TMap<%s, ?> " % base

    if is_const:
        base = "const " + base
    if is_ref:
        base += "&"
    return base


def pin_direction_str(pin, diag=None):
    raw = as_text(safe_get(pin, "Direction", diag=diag)) or ""
    if "Output" in raw:
        return "out"
    if "Input" in raw:
        return "in"
    return raw.lower() or "?"


# ====================================================================== 图与节点


# K2Node_ 前缀去掉后更短，也更接近蓝图里显示的名字
_NODE_PREFIX = "K2Node_"


def node_class_short(node):
    """返回去掉 K2Node_ 前缀的节点类名。"""
    try:
        full = node.get_class().get_name()
    except Exception:
        return "Unknown"
    if full.startswith(_NODE_PREFIX):
        return full[len(_NODE_PREFIX):]
    return full


def node_title(node, diag=None):
    """尽力取到蓝图里显示在节点顶部的标题。

    UE 的 ``GetNodeTitle`` 是 C++ 虚函数而非 UFUNCTION，Python 拿不到，
    因此这里退化为读取若干已知的标题类 UPROPERTY。
    """
    for prop in ("NodeTitle", "CustomFunctionName", "VariableReference"):
        raw = safe_get(node, prop, diag=None)
        if raw is None:
            continue
        if prop == "VariableReference":
            member = as_text(safe_get(raw, "MemberName", diag=None))
            if member:
                return member
            continue
        text = as_text(raw)
        if text:
            return text
    return None


def extract_pins(node, diag=None):
    """提取节点引脚，附带连接目标。

    返回 ``(pins, index_of_pin)``：``index_of_pin`` 把 pin 对象 id 映射到
    引脚在列表中的下标，供建立连线时使用。
    """
    raw_pins = safe_get(node, "Pins", diag=diag)
    if not raw_pins:
        return [], {}

    out = []
    index = {}
    for i, pin in enumerate(raw_pins):
        if pin is None:
            continue
        name = as_text(safe_get(pin, "PinName", diag=diag)) or "?"
        entry = {
            "i": i,
            "name": name,
            "dir": pin_direction_str(pin, diag),
            "type": pin_type_str(safe_get(pin, "PinType", diag=diag), diag),
        }
        default_value = as_text(safe_get(pin, "DefaultValue", diag=None))
        if default_value:
            entry["default"] = default_value
        default_obj = safe_get(pin, "DefaultObject", diag=None)
        if default_obj is not None:
            try:
                entry["default_obj"] = default_obj.get_name()
            except Exception:
                pass
        if safe_get(pin, "bHidden", diag=None):
            entry["hidden"] = True
        if safe_get(pin, "bAdvancedView", diag=None):
            entry["advanced"] = True

        out.append(entry)
        index[id(pin)] = i

    return out, index


def extract_links(node, pin_index, diag=None):
    """提取连线，表示为 ``[源引脚下标, 目标节点序号, 目标引脚下标]``。

    目标节点序号需要上层提供（图内索引），因此这里先返回原始 pin 对象引用，
    由调用方在拿到全图节点索引后再解析。
    """
    raw_pins = safe_get(node, "Pins", diag=diag)
    if not raw_pins:
        return []
    links = []
    for i, pin in enumerate(raw_pins):
        if pin is None:
            continue
        linked = safe_get(pin, "LinkedTo", diag=None)
        if not linked:
            continue
        for other in linked:
            if other is None:
                continue
            links.append((id(pin), other))
    return links


# ====================================================================== 图集合


def get_blueprint_graphs(bp, diag=None):
    """取出蓝图的三类图：事件图(ubergraph) / 函数图 / 宏图。

    返回 ``[(kind, graph), ...]``，kind ∈ {event, function, macro}。
    """
    result = []
    for prop, kind in (
        ("UbergraphPages", "event"),
        ("FunctionGraphs", "function"),
        ("MacroGraphs", "macro"),
    ):
        graphs = safe_get(bp, prop, diag=diag)
        if not graphs:
            continue
        for g in graphs:
            if g is not None:
                result.append((kind, g))
    return result


def graph_name(graph):
    try:
        return graph.get_name()
    except Exception:
        return "<unnamed-graph>"


# ====================================================================== 结果封装


def ok(payload, diag):
    payload = dict(payload or {})
    payload["_diag"] = diag.as_dict() if diag else {}
    return payload


def fail(message, diag=None, exc=None):
    out = {"error": str(message)[:2000]}
    if exc is not None:
        out["traceback"] = traceback.format_exc()[-4000:]
    if diag is not None:
        out["_diag"] = diag.as_dict()
    return out


def dumps(payload):
    """统一出口：保证输出是紧凑 JSON（不产生 NaN / 非 ASCII 转义膨胀）。"""
    return json.dumps(payload, ensure_ascii=False, default=str)
