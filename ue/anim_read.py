# -*- coding: utf-8 -*-
r"""
ComboMCP / 动画资产读取器（只读）
=================================

覆盖 Game Animation Sample 里与战斗连招相关的动画侧资产：

* ``UAnimMontage``         —— 槽位 / 段落(Section) / Notify 时序 / 混合设置
* ``UAnimNotify`` /
  ``UAnimNotifyState``     —— 通知蓝图的属性（例如 ANS_InputDisabled）
* ``UAnimBlueprint``       —— 状态机 / 状态 / 转换 / 状态内节点
* ``UChooserTable``        —— Chooser 表（Motion Matching 的选择逻辑）
* ``UPoseSearchDatabase``  —— 姿态搜索数据库
* ``UAnimSequence``        —— 序列时长 / 帧率 / 通知

设计取舍
--------
连招系统的绝大多数 bug 不在"连线画错了"，而在**时序**：取消窗口和
输入禁用窗口重叠、Section 的 NextSection 指错、Notify 的时长改了但
Montage 的 Section 边界没跟着改。因此本模块把"时间轴"作为一等输出，
每个 Notify 都带绝对起止时间，每个 Section 都带起始时间。
"""

import unreal  # noqa: F401

from . import runtime as rt


# ====================================================================== 通用


def _link_time(element):
    """从 ``FAnimLinkableElement`` 取时间值。

    UE 里 Notify/Section 的时间藏在 ``Link`` 子对象上，字段名在版本间
    有过变动（``LinkValue`` / ``CachedAbsoluteTime``），因此两个都试。
    """
    if element is None:
        return None
    for prop in ("LinkValue", "CachedAbsoluteTime"):
        value = rt.safe_get(element, prop, diag=None)
        if isinstance(value, (int, float)):
            return round(float(value), 4)
    link = rt.safe_get(element, "Link", diag=None)
    if link is not None:
        for prop in ("LinkValue", "CachedAbsoluteTime"):
            value = rt.safe_get(link, prop, diag=None)
            if isinstance(value, (int, float)):
                return round(float(value), 4)
    return None


def _obj_class_name(obj):
    if obj is None:
        return None
    try:
        return obj.get_class().get_name()
    except Exception:
        return None


def _obj_name(obj):
    if obj is None:
        return None
    try:
        return obj.get_name()
    except Exception:
        return None


def _dump_props(obj, diag=None, limit=40, skip=()):
    """枚举对象上所有可读的、非方法的 Python 属性。

    用于读取 Notify / 状态节点这些"用户自定义蓝图类"的属性，
    因为它们的属性名由用户定义，无法预先写死。
    """
    out = {}
    if obj is None:
        return out
    try:
        names = dir(obj)
    except Exception:
        return out

    for name in names:
        if name.startswith("_") or name in skip:
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value):
            continue
        if isinstance(value, (int, float, bool, str)):
            out[name] = value if not isinstance(value, float) else round(value, 4)
        elif value is None:
            continue
        else:
            text = rt.as_text(value)
            # 对象引用只保留短名，避免输出爆炸
            if text and len(text) <= 80:
                out[name] = text
        if len(out) >= limit:
            break
    return out


def load_any(asset_path):
    """装载任意资产，返回 ``(asset, error)``。"""
    path = asset_path
    if path.startswith("/Game/") and "." not in path.rsplit("/", 1)[-1]:
        path = path + "." + path.rsplit("/", 1)[-1]
    try:
        asset = unreal.load_asset(path)
    except Exception as exc:
        return None, "load_asset raised: %s" % exc
    if asset is None:
        return None, "asset not found: %s" % path
    return asset, None


# ====================================================================== Montage


def _read_slot_tracks(montage, diag):
    """读槽位轨道：哪个 Slot、放了哪些动画段、各自的时间区间。"""
    out = []
    tracks = rt.safe_get(montage, "SlotAnimTracks", diag=diag) or []
    for track in tracks:
        if track is None:
            continue
        slot_name = rt.as_text(rt.safe_get(track, "SlotName")) or "?"
        anim_track = rt.safe_get(track, "AnimTrack", diag=None)
        segments_out = []
        segments = rt.safe_get(anim_track, "AnimSegments", diag=None) if anim_track else None
        for seg in (segments or []):
            if seg is None:
                continue
            anim_ref = rt.safe_get(seg, "AnimReference", diag=None)
            entry = {
                "anim": _obj_name(anim_ref) or "?",
                "start_pos": rt.safe_get(seg, "StartPos", diag=None),
                "anim_start": rt.safe_get(seg, "AnimStartTime", diag=None),
                "anim_end": rt.safe_get(seg, "AnimEndTime", diag=None),
                "play_rate": rt.safe_get(seg, "AnimPlayRate", diag=None),
                "loops": rt.safe_get(seg, "LoopingCount", diag=None),
            }
            segments_out.append(dict(
                (k, round(float(v), 4) if isinstance(v, float) else v)
                for k, v in entry.items() if v is not None))
        out.append({"slot": slot_name, "segments": segments_out})
    return out


def _read_sections(montage, diag):
    """读段落：连招分段的骨架。NextSection 决定"接哪一段"。"""
    out = []
    for section in rt.safe_get(montage, "CompositeSections", diag=diag) or []:
        if section is None:
            continue
        entry = {
            "name": rt.as_text(rt.safe_get(section, "SectionName")) or "?",
            "start": _link_time(section),
        }
        nxt = rt.as_text(rt.safe_get(section, "NextSectionName"))
        if nxt and nxt not in ("None", "None "):
            entry["next"] = nxt
        out.append(entry)
    return out


def _notify_kind(evt):
    state = rt.safe_get(evt, "NotifyStateClass", diag=None)
    single = rt.safe_get(evt, "Notify", diag=None)
    if state is not None:
        return "state", state
    if single is not None:
        return "instant", single
    return "unknown", None


def _read_notifies(montage, diag, with_props=True):
    """读通知，并计算每个通知的绝对起止时间。

    这是连招诊断最需要的数据：输入禁用窗口、取消窗口、伤害判定帧都在这里。
    """
    out = []
    for evt in rt.safe_get(montage, "Notifies", diag=diag) or []:
        if evt is None:
            continue
        kind, obj = _notify_kind(evt)
        name = rt.as_text(rt.safe_get(evt, "NotifyName")) or _obj_name(obj) or "?"
        start = _link_time(evt)
        duration = rt.safe_get(evt, "Duration", diag=None)
        if not isinstance(duration, (int, float)):
            duration = 0.0

        entry = {
            "name": name,
            "kind": kind,
            "cls": _obj_class_name(obj),
        }
        if isinstance(start, (int, float)):
            entry["start"] = round(float(start), 4)
            if duration:
                entry["end"] = round(float(start) + float(duration), 4)
                entry["duration"] = round(float(duration), 4)
        track = rt.safe_get(evt, "TrackIndex", diag=None)
        if isinstance(track, int) and track >= 0:
            entry["track"] = track
        if rt.safe_get(evt, "bIsBranchingPoint", diag=None):
            entry["branching_point"] = True

        # 通知实例上的自定义属性（用户蓝图里定义的）
        if with_props and obj is not None:
            props = _dump_props(obj, diag)
            props = dict((k, v) for k, v in props.items()
                         if k not in ("notify_name", "duration", "frame_rate"))
            if props:
                entry["props"] = props

        out.append(entry)

    out.sort(key=lambda e: e.get("start", 0.0))
    return out


def montage_detail(asset_path, with_notify_props=True):
    """Montage 完整结构：槽位 / 段落 / 通知时间轴。"""
    diag = rt.Diagnostics()
    asset, err = load_any(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls = _obj_class_name(asset)
    if cls != "AnimMontage":
        return rt.fail("not an AnimMontage (class=%s)" % cls, diag)

    length = rt.safe_get(asset, "SequenceLength", diag=None)
    payload = {
        "asset": asset_path,
        "class": cls,
        "length": round(float(length), 4) if isinstance(length, (int, float)) else None,
        "rate_scale": rt.safe_get(asset, "RateScale", diag=None),
        "slots": _read_slot_tracks(asset, diag),
        "sections": _read_sections(asset, diag),
        "notifies": _read_notifies(asset, diag, with_props=with_notify_props),
    }

    blend_in = rt.safe_get(asset, "BlendIn", diag=None)
    if blend_in is not None:
        payload["blend_in"] = {
            "time": rt.safe_get(blend_in, "BlendTime", diag=None),
            "option": rt.as_text(rt.safe_get(blend_in, "BlendOption", diag=None)),
        }
    blend_out = rt.safe_get(asset, "BlendOut", diag=None)
    if blend_out is not None:
        payload["blend_out"] = {
            "time": rt.safe_get(blend_out, "BlendTime", diag=None),
            "option": rt.as_text(rt.safe_get(blend_out, "BlendOption", diag=None)),
        }
    if rt.safe_get(asset, "bEnableAutoBlendOut", diag=None) is False:
        payload["auto_blend_out"] = False

    notifies = rt.safe_get(asset, "Notifies", diag=None) or []
    tracks = rt.safe_get(asset, "AnimNotifyTracks", diag=None) or []
    if tracks:
        payload["notify_tracks"] = [
            {"name": rt.as_text(rt.safe_get(t, "TrackName"))}
            for t in tracks if t is not None
        ]
    payload["notify_count"] = len(notifies)

    # 时间轴一致性：Section 边界是否落在 Notify 之外
    payload["timeline_check"] = _timeline_consistency(payload)
    return rt.ok(payload, diag)


def _timeline_consistency(payload):
    """对 Montage 时间轴做基础一致性检查，把可疑之处直接标出来。

    只报告"结构上自洽性有问题"的事实，不下结论——结论由上层诊断器给。
    """
    findings = []
    length = payload.get("length")
    sections = payload.get("sections") or []
    notifies = payload.get("notifies") or []

    for section in sections:
        start = section.get("start")
        if isinstance(start, (int, float)) and isinstance(length, (int, float)):
            if start > length + 1e-4:
                findings.append("section '%s' starts at %.3f beyond montage length %.3f"
                                % (section["name"], start, length))

    names = set(s.get("name") for s in sections)
    for section in sections:
        nxt = section.get("next")
        if nxt and nxt not in names:
            findings.append("section '%s' NextSection '%s' does not exist"
                            % (section["name"], nxt))

    for n in notifies:
        end = n.get("end")
        if isinstance(end, (int, float)) and isinstance(length, (int, float)):
            if end > length + 1e-3:
                findings.append("notify '%s' ends at %.3f beyond montage length %.3f"
                                % (n["name"], end, length))

    if not sections:
        findings.append("montage has no sections: nothing can be linked by name")
    if not notifies:
        findings.append("montage has no notifies: no damage/cancel windows are driven here")

    return findings


# ====================================================================== Notify 蓝图


def notify_detail(asset_path):
    """读一个 AnimNotify / AnimNotifyState 蓝图：父类 + 变量 + 默认值 + 图清单。"""
    diag = rt.Diagnostics()
    asset, err = load_any(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls = _obj_class_name(asset)
    payload = {"asset": asset_path, "class": cls}

    if cls == "Blueprint":
        parent = rt.safe_get(asset, "ParentClass", diag=diag)
        payload["parent"] = _obj_name(parent)
        payload["variables"] = []
        for var in rt.safe_get(asset, "NewVariables", diag=None) or []:
            if var is None:
                continue
            payload["variables"].append({
                "name": rt.as_text(rt.safe_get(var, "VarName")),
                "type": rt.pin_type_str(rt.safe_get(var, "VarType")),
                "default": rt.as_text(rt.safe_get(var, "DefaultValue")) or None,
            })
        graphs = []
        for _, graph in rt.get_blueprint_graphs(asset, diag):
            graphs.append({
                "name": rt.graph_name(graph),
                "nodes": len(rt.safe_get(graph, "Nodes", diag=None) or []),
            })
        payload["graphs"] = graphs

        # CDO 上的默认值：这就是挂到 Montage 上时的实际参数
        cdo = None
        ok, cdo, _ = rt.safe_call(asset, "generated_class")
        gen = rt.safe_get(asset, "GeneratedClass", diag=None)
        if gen is not None:
            try:
                cdo = unreal.get_default_object(gen)
            except Exception:
                cdo = None
        if cdo is not None:
            payload["defaults"] = _dump_props(cdo, diag)
    else:
        payload["props"] = _dump_props(asset, diag)

    return rt.ok(payload, diag)


# ====================================================================== AnimBlueprint


_STATE_MACHINE_NODE = ("AnimGraphNode_StateMachine",
                       "AnimGraphNode_StateMachineBase")


def _read_state_graph(graph, diag, depth=0):
    """读一个状态机图：状态、转换、以及每个状态内部放的是什么节点。"""
    if graph is None or depth > 4:
        return None

    states, transitions = [], []
    nodes = rt.safe_get(graph, "Nodes", diag=None) or []
    index = {}
    for i, n in enumerate(nodes):
        if n is not None:
            index[id(n)] = i

    for node in nodes:
        if node is None:
            continue
        cls = rt.node_class_short(node)
        name = _obj_name(node) or cls

        if cls in ("AnimStateNode", "AnimStateNodeBase"):
            bound = rt.safe_get(node, "BoundGraph", diag=None)
            inner = []
            for inner_node in (rt.safe_get(bound, "Nodes", diag=None) or []) if bound else []:
                if inner_node is None:
                    continue
                icls = rt.node_class_short(inner_node)
                brief = {"cls": icls}
                label = _node_label(inner_node, icls)
                if label:
                    brief["label"] = label
                inner.append(brief)
            states.append({
                "name": name,
                "state_type": rt.as_text(rt.safe_get(node, "StateType", diag=None)),
                "nodes": inner,
            })

        elif cls == "AnimStateTransitionNode":
            shared = rt.safe_get(node, "SharedRules", diag=None)
            tr = {
                "from": _node_label_pin(node, "From"),
                "to": _node_label_pin(node, "To"),
                "crossfade": rt.safe_get(node, "CrossfadeDuration", diag=None),
                "priority": rt.safe_get(node, "PriorityOrder", diag=None),
                "bidirectional": rt.safe_get(node, "Bidirectional", diag=None),
                "logic": rt.as_text(rt.safe_get(node, "LogicType", diag=None)),
            }
            bound = rt.safe_get(node, "BoundGraph", diag=None)
            if bound is not None:
                conds = []
                for bn in rt.safe_get(bound, "Nodes", diag=None) or []:
                    if bn is None:
                        continue
                    bcls = rt.node_class_short(bn)
                    if bcls in ("AnimGraphNode_TransitionResult", "Tunnel", "Knot"):
                        continue
                    label = _node_label(bn, bcls)
                    if label:
                        conds.append(label)
                if conds:
                    tr["condition_nodes"] = conds
            transitions.append(dict((k, v) for k, v in tr.items() if v is not None))

    return {
        "states": states,
        "transitions": transitions,
        "entry": [s["name"] for s in states][:1] or [],
    }


def _node_label(node, cls):
    """给动画图节点取一个可读标签（播放的是什么资产）。"""
    for prop in ("Sequence", "AnimSequence", "BlendSpace", "Database",
                 "Chooser", "SequencePlayer"):
        value = rt.safe_get(node, prop, diag=None)
        if value is not None:
            name = _obj_name(value)
            if name:
                return name
    return rt.node_title(node) or None


def _node_label_pin(node, pin_name):
    pins = rt.safe_get(node, "Pins", diag=None) or []
    for pin in pins:
        if pin is None:
            continue
        if rt.as_text(rt.safe_get(pin, "PinName")) != pin_name:
            continue
        linked = rt.safe_get(pin, "LinkedTo", diag=None) or []
        for other in linked:
            if other is None:
                continue
            owner = rt.safe_call(other, "get_outer")[1]
            if owner is not None:
                return _obj_name(owner)
    return None


def animblueprint_detail(asset_path):
    """AnimBlueprint：目标骨架 + 状态机结构。"""
    diag = rt.Diagnostics()
    asset, err = load_any(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls = _obj_class_name(asset)
    if cls != "AnimBlueprint":
        return rt.fail("not an AnimBlueprint (class=%s)" % cls, diag)

    target = rt.safe_get(asset, "TargetSkeleton", diag=diag)
    payload = {
        "asset": asset_path,
        "class": cls,
        "skeleton": _obj_name(target),
        "state_machines": [],
        "graphs": [],
    }

    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        nodes = rt.safe_get(graph, "Nodes", diag=None) or []
        entry = {"name": rt.graph_name(graph), "kind": kind, "nodes": len(nodes)}
        payload["graphs"].append(entry)

        for node in nodes:
            if node is None:
                continue
            node_cls = rt.node_class_short(node)
            if node_cls not in _STATE_MACHINE_NODE:
                continue
            sm_graph = rt.safe_get(node, "EditorStateMachineGraph", diag=None)
            if sm_graph is None:
                sm_graph = rt.safe_get(node, "BoundGraph", diag=None)
            model = _read_state_graph(sm_graph, diag)
            if model:
                model["name"] = _obj_name(node) or "StateMachine"
                model["in_graph"] = rt.graph_name(graph)
                payload["state_machines"].append(model)

    # 只保留状态机，避免把整棵 AnimGraph 铺开
    payload["graph_count"] = len(payload.pop("graphs", []))
    return rt.ok(payload, diag)


# ====================================================================== 序列


def sequence_detail(asset_path):
    """AnimSequence / BlendSpace 摘要 + 通知。"""
    diag = rt.Diagnostics()
    asset, err = load_any(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls = _obj_class_name(asset)
    payload = {"asset": asset_path, "class": cls}

    length = rt.safe_get(asset, "SequenceLength", diag=None)
    if isinstance(length, (int, float)):
        payload["length"] = round(float(length), 4)
    rate = rt.safe_get(asset, "RateScale", diag=None)
    if isinstance(rate, (int, float)):
        payload["rate_scale"] = round(float(rate), 4)

    notifies = rt.safe_get(asset, "Notifies", diag=None) or []
    payload["notifies"] = _read_notifies(asset, diag, with_props=False)
    payload["notify_count"] = len(notifies)

    skeleton = rt.safe_get(asset, "Skeleton", diag=None)
    if skeleton is not None:
        payload["skeleton"] = _obj_name(skeleton)

    # BlendSpace 的样本点
    samples = rt.safe_get(asset, "SampleData", diag=None)
    if samples:
        pts = []
        for s in samples:
            if s is None:
                continue
            anim = rt.safe_get(s, "Animation", diag=None)
            pts.append({
                "anim": _obj_name(anim),
                "rate": rt.safe_get(s, "RateScale", diag=None),
            })
        if pts:
            payload["samples"] = pts

    return rt.ok(payload, diag)


# ====================================================================== 插件资产


def generic_asset_detail(asset_path):
    """Chooser / PoseSearch / 其它插件资产的通用读取。

    这些资产的结构随插件版本变化，因此这里做的是"反射式摘要"：
    顶层可读属性 + 数组长度，而不是假设固定结构。
    """
    diag = rt.Diagnostics()
    asset, err = load_any(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    cls = _obj_class_name(asset)
    payload = {"asset": asset_path, "class": cls, "props": {}}

    for name in dir(asset):
        if name.startswith("_"):
            continue
        try:
            value = getattr(asset, name)
        except Exception:
            continue
        if callable(value):
            continue
        if isinstance(value, (int, float, bool, str)):
            payload["props"][name] = value if not isinstance(value, float) else round(value, 4)
        elif value is None:
            continue
        else:
            try:
                n = len(value)
            except Exception:
                n = None
            if n is not None:
                payload["props"][name] = "<list len=%d>" % n
            else:
                text = rt.as_text(value)
                if text and len(text) <= 60:
                    payload["props"][name] = text
        if len(payload["props"]) >= 50:
            break

    return rt.ok(payload, diag)
