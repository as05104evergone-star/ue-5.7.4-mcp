# -*- coding: utf-8 -*-
r"""
ComboMCP / AnimBlueprint 读取（纯数据，不依赖 unreal）
====================================================

读动画蓝图的状态机、状态内部节点、转换条件，以及 Motion Matching 相关的
关键节点（Slot / MotionMatching / BlendStack / Chooser / Warping）。

为什么走 T3D：``UAnimBlueprint`` 的图结构与所有 ``UEdGraph::Nodes`` 一样是
裸 ``UPROPERTY()``，Python 反射会被 ``CanGetPropertyValue`` 拒绝。
T3D 里则是完整的——实测 ``SandboxCharacter_Mover_ABP`` 导出 18.7 MB，
含 18 个状态、60 个转换、440 个图。

T3D 里的真实层级::

    AnimGraph
      └ AnimGraphNode_StateMachine   (EditorStateMachineGraph -> "State Controller")
           └ AnimationStateMachineGraph "State Controller"
                ├ AnimStateNode "Idle Loop"       (BoundGraph -> AnimationStateGraph)
                │    └ AnimationStateGraph "Idle Loop"   ← 该状态的内部动画图
                ├ AnimStateTransitionNode         (BoundGraph -> AnimationTransitionGraph)
                ├ AnimStateConduitNode
                └ AnimStateEntryNode
"""

from . import t3d as t3d_parser

# 被视为"图"的类（与 t3d.GRAPH_CLASSES 一致，这里按需再列一次便于阅读）
STATE_MACHINE_GRAPH = "AnimationStateMachineGraph"
STATE_GRAPH = "AnimationStateGraph"
TRANSITION_GRAPH = "AnimationTransitionGraph"
CONDUIT_GRAPH = "AnimationConduitGraph"
ANIM_GRAPH = "AnimationGraph"


def _short(value):
    return t3d_parser._object_short_name(value)


def walk(obj):
    """深度优先遍历对象树。"""
    stack = [obj]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def bound_graph_of(node):
    """取节点绑定的图。

    **不能用名字查**：所有转换图都叫 "Transition"，按名字会命中别人的图。
    绑定图是节点自己的**子对象**，这才是精确定位。
    """
    for child in node.children:
        if child.cls in (STATE_GRAPH, TRANSITION_GRAPH, CONDUIT_GRAPH,
                         STATE_MACHINE_GRAPH, ANIM_GRAPH, "EdGraph"):
            return child
    return None


def _find_graph_by_name(root, name):
    if not name:
        return None
    for obj in walk(root):
        if obj.name == name and obj.cls in (
                STATE_MACHINE_GRAPH, STATE_GRAPH, TRANSITION_GRAPH,
                CONDUIT_GRAPH, ANIM_GRAPH, "EdGraph"):
            return obj
    return None


def _state_display_name(bp, object_name):
    """把 ``AnimStateNode_4`` 这类对象名换成可读的状态名（如 "Locomotion Loop"）。"""
    if not object_name:
        return None
    for obj in walk(bp):
        if obj.name != object_name:
            continue
        if obj.cls in ("AnimStateNode", "AnimStateAliasNode"):
            return (obj.props.get("StateAliasName")
                    or _short(obj.props.get("BoundGraph")) or obj.name)
        if obj.cls == "AnimStateEntryNode":
            return "(entry)"
        return obj.name
    return object_name


# 这些节点类说明"这个状态在做什么"，对 Motion Matching 项目尤其有信息量
_INTERESTING_NODES = (
    ("AnimGraphNode_MotionMatching", "action", "Motion Matching"),
    ("AnimGraphNode_BlendStack", "action", "Blend Stack"),
    ("AnimationBlendStackGraph", "action", "Blend Stack Graph"),
    ("AnimGraphNode_BlendStackInput", "action", "Blend Stack Input"),
    ("AnimGraphNode_BlendStackResult", "action", "Blend Stack Result"),
    ("AnimGraphNode_Slot", "slot", "Montage Slot"),
    ("AnimGraphNode_LinkedAnimLayer", "layer", "Linked Anim Layer"),
    ("AnimGraphNode_LinkedInputPose", "layer", "Linked Input Pose"),
    ("AnimGraphNode_BlendSpacePlayer", "sample", "Blend Space"),
    ("AnimGraphNode_SequencePlayer", "sample", "Sequence Player"),
    ("AnimGraphNode_StateMachine", "nested", "State Machine"),
    ("AnimGraphNode_OrientationWarping", "warp", "Orientation Warping"),
    ("AnimGraphNode_StrideWarping", "warp", "Stride Warping"),
    ("AnimGraphNode_OffsetRootBone", "warp", "Offset Root Bone"),
    ("AnimGraphNode_Inertialization", "blend", "Inertialization"),
    ("AnimGraphNode_DeadBlending", "blend", "Dead Blending"),
    ("AnimGraphNode_PoseSearchHistoryCollector", "pose", "Pose Search History"),
    ("MotionMatching", "action", "Motion Matching"),
    ("EvaluateChooser2", "chooser", "Evaluate Chooser"),
    ("EvaluateChooser", "chooser", "Evaluate Chooser"),
    ("AnimGraphNode_ControlRig", "rig", "Control Rig"),
    ("AnimGraphNode_LegIK", "rig", "Leg IK"),
    ("AnimGraphNode_ApplyMeshSpaceAdditive", "blend", "Mesh Space Additive"),
)


def _state_summary(state_graph):
    """一个状态内部放了什么——对连招系统来说，关键是"这个状态里有没有 Slot"。"""
    inventory = []
    if state_graph is None:
        return inventory
    for obj in walk(state_graph):
        for cls, kind, label in _INTERESTING_NODES:
            if obj.cls == cls:
                entry = {"cls": obj.cls, "kind": kind, "label": label}
                # 取一些能说明配置的属性
                for prop in ("SlotName", "Layer", "Database", "Chooser",
                             "BlendSpace", "Sequence", "Player"):
                    value = obj.props.get(prop)
                    if value:
                        entry[prop.lower()] = _short(value) or value
                inventory.append(entry)
                break
    return inventory


_CONDITION_SKIP = (
    "AnimGraphNode_TransitionResult", "AnimGraphNode_TransitionPoseEvaluator",
    "AnimGraphNode_CustomTransitionResult", "AnimationTransitionGraph",
    "EdGraph", "EdGraphSchema",
)


def _transition_info(node, root):
    """转换：从哪个状态到哪个状态、条件是怎样的。"""
    info = {"name": node.name}
    bound = bound_graph_of(node)
    if bound is not None:
        info["graph"] = bound.name
        conditions = []
        for obj in walk(bound):
            cls = obj.cls
            if cls in _CONDITION_SKIP:
                continue
            # 只有动画图节点才是真正的条件；通知/绑定这些只是恰好挂在同一张图上
            if not cls.startswith("AnimGraphNode") and cls != "AnimGetter":
                continue
            ref = _short(obj.props.get("FunctionReference"))
            var = _short(obj.props.get("VariableReference"))
            if ref:
                conditions.append({"node": cls, "role": "call", "ref": ref})
            elif var:
                conditions.append({"node": cls, "role": "var", "ref": var})
            elif cls == "AnimGetter":
                conditions.append({"node": cls, "role": "anim_getter",
                                   "ref": obj.props.get("GetterName") or obj.name})
        if conditions:
            info["conditions"] = conditions[:20]
    for prop, out in (("CrossfadeDuration", "crossfade"),
                      ("PriorityOrder", "priority"),
                      ("Bidirectional", "bidirectional"),
                      ("LogicType", "logic"),
                      ("bDisabled", "disabled"),
                      ("TransitionInterrupt", "interrupt")):
        value = node.props.get(prop)
        if value not in (None, "", "False", "0"):
            info[out] = value
    return info


def _link_endpoints(node):
    """从转换节点的 ``In`` / ``Out`` 引脚找出它连接的两个状态。

    字段名是从真实 T3D 里确认的：不是 ``From``/``To``，而是 ``In``（指向前驱
    状态，缺省方向即输入）与 ``Out``（指向后继状态，``EGPD_Output``）。
    """
    result = {}
    for pin in node.pins:
        name = (pin.get("attrs") or {}).get("PinName")
        if name == "In":
            for target, _pin_id in pin.get("linked") or []:
                result["from"] = target
                break
            if "from" not in result:
                result["from_disconnected"] = True
        elif name == "Out":
            for target, _pin_id in pin.get("linked") or []:
                result["to"] = target
                break
            if "to" not in result:
                result["to_disconnected"] = True
    return result


def _compiler_message(node):
    """取出节点上存留的编译器消息（``ErrorMsg`` / ``ErrorType``）。

    这是上一次编译留下的记录，属于"事实"而非猜测；但它是存留信息，
    不代表此刻一定仍在报错，所以调用方要如实呈现、不要断言。
    """
    msg = node.props.get("ErrorMsg")
    if not msg:
        return None
    return {
        "node": node.name,
        "cls": node.cls,
        "error_type": node.props.get("ErrorType"),
        "message": msg,
    }


def parse_anim_blueprint(text):
    """T3D 文本 -> 动画蓝图结构。非 AnimBlueprint 返回 ``None``。"""
    roots = t3d_parser.parse(text)
    if not roots:
        return None
    bp = roots[0]
    if bp.cls not in ("AnimBlueprint", "AnimationBlueprint"):
        return None

    state_machines = []
    slots = []
    counts = {}

    for obj in walk(bp):
        counts[obj.cls] = counts.get(obj.cls, 0) + 1

    for obj in walk(bp):
        if obj.cls != "AnimGraphNode_StateMachine":
            continue
        sm_name = _short(obj.props.get("EditorStateMachineGraph"))
        sm_graph = _find_graph_by_name(bp, sm_name)
        if sm_graph is None:
            continue

        # 同一台状态机在图里可能出现两次（AnimGraph 与 Ubergraph 各一份），去重
        if any(sm["name"] == sm_graph.name for sm in state_machines):
            continue

        states, transitions, conduits, entry = [], [], [], None
        messages = []
        for child in sm_graph.children:
            msg = _compiler_message(child)
            if msg:
                messages.append(msg)
            if child.cls in ("AnimStateEntryNode",):
                entry = child.name
                for pin in child.pins:
                    for target, _pid in pin.get("linked") or []:
                        entry = target
                        break
            elif child.cls in ("AnimStateNode", "AnimStateAliasNode"):
                bound_name = _short(child.props.get("BoundGraph"))
                state_graph = bound_graph_of(child)
                # 可读名就是它的绑定图名（如 "Idle Loop" / "Locomotion Loop"），
                # 而不是 AnimStateNode_4 这种对象名。
                display = bound_name or child.name
                alias = child.props.get("StateAliasName")
                if alias:
                    display = alias
                state = {
                    "name": display,
                    "object": child.name,
                    "bound_graph": bound_name,
                    "kind": ("alias" if child.cls == "AnimStateAliasNode" else "state"),
                    "inventory": _state_summary(state_graph),
                }
                if child.props.get("bAlwaysResetOnEntry") == "True":
                    state["always_reset_on_entry"] = True
                aliased = child.props.get("AliasedStateNodes")
                if aliased:
                    state["aliased_count"] = str(aliased).count("/Script/AnimGraph.AnimStateNode'")
                states.append(state)
            elif child.cls == "AnimStateConduitNode":
                conduits.append({"name": child.name,
                                 "bound_graph": _short(child.props.get("BoundGraph"))})
            elif child.cls == "AnimStateTransitionNode":
                info = _transition_info(child, bp)
                info.update(_link_endpoints(child))
                # 用可读名替换对象名，方便人看
                for key in ("from", "to"):
                    target = info.get(key)
                    if target:
                        info[key] = _state_display_name(bp, target)
                transitions.append(info)

        state_machines.append({
            "name": sm_graph.name,
            "node": obj.name,
            "states": states,
            "state_count": len(states),
            "transitions": transitions,
            "transition_count": len(transitions),
            "conduits": conduits,
            "entry": _state_display_name(bp, entry),
            "compiler_messages": messages,
        })

    # ---- 全图里的关键节点（Slot / MotionMatching / Chooser / Warping …）
    inventory = []
    for obj in walk(bp):
        for cls, kind, label in _INTERESTING_NODES:
            if obj.cls != cls:
                continue
            entry = {"cls": obj.cls, "kind": kind, "label": label,
                     "owner": obj.parent.name if obj.parent else None}
            for prop in ("SlotName", "Layer", "Database", "Chooser",
                         "BlendSpace", "Sequence"):
                value = obj.props.get(prop)
                if value:
                    entry[prop.lower()] = _short(value) or value
            inventory.append(entry)
            if cls == "AnimGraphNode_Slot":
                # 属性缺席不等于没有值：UE 的 T3D 只写"与默认不同"的属性，
                # 而 UAnimGraphNode_Slot::SlotName 的默认就是 "DefaultSlot"。
                slot_name = (obj.props.get("SlotName")
                             or obj.props.get("SlotNodeName")
                             or "DefaultSlot")
                slots.append({"slot": slot_name,
                              "in_graph": obj.parent.name if obj.parent else None,
                              "slot_name_is_default": "SlotName" not in obj.props})
            break

    return {
        "asset": bp.name,
        "class": bp.cls,
        "parent": _short(bp.props.get("ParentClass")),
        "skeleton": _short(bp.props.get("TargetSkeleton")) or _short(bp.props.get("Skeleton")),
        "state_machines": state_machines,
        "state_machine_count": len(state_machines),
        "slots": slots,
        "inventory": inventory,
        "node_type_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:40]),
        "source": "t3d-cache",
    }


def summarize_anim_blueprint(text):
    """给模型看的紧凑摘要：状态机 + 状态 + 转换，去掉噪音。"""
    full = parse_anim_blueprint(text)
    if full is None:
        return None
    machines = []
    for sm in full.get("state_machines") or []:
        states = []
        for st in sm.get("states") or []:
            entry = {"name": st["name"], "kind": st["kind"]}
            inv = st.get("inventory") or []
            labels = []
            for item in inv:
                if item["label"] not in labels:
                    labels.append(item["label"])
            if labels:
                entry["contains"] = labels
            if st.get("bound_graph"):
                entry["graph"] = st["bound_graph"]
            states.append(entry)
        transitions = []
        for tr in sm.get("transitions") or []:
            item = {}
            if tr.get("from"):
                item["from"] = tr["from"]
            if tr.get("to"):
                item["to"] = tr["to"]
            if tr.get("crossfade"):
                item["crossfade"] = tr["crossfade"]
            if tr.get("logic"):
                item["logic"] = tr["logic"]
            conds = [c.get("ref") for c in (tr.get("conditions") or [])
                     if c.get("ref")]
            if conds:
                item["conditions"] = conds[:6]
            transitions.append(item)
        machines.append({
            "name": sm["name"],
            "entry": sm.get("entry"),
            "states": states,
            "transitions": transitions,
            "conduits": sm.get("conduits") or [],
            # 存留的编译器消息是事实，但它是上次编译留下的记录，
            # 呈现时要说明这一点，不要断言"此刻仍在报错"。
            "compiler_messages": sm.get("compiler_messages") or [],
        })
    return {
        "asset": full["asset"],
        "class": full["class"],
        "parent": full.get("parent"),
        "skeleton": full.get("skeleton"),
        "state_machines": machines,
        "slots": full.get("slots"),
        "inventory_summary": _inventory_summary(full.get("inventory") or []),
        "source": "t3d-cache",
    }


def _inventory_summary(inventory):
    """按用途聚类，避免把上百个同类节点摊平。"""
    by_kind = {}
    for item in inventory:
        kind = item.get("kind") or "other"
        bucket = by_kind.setdefault(kind, {"count": 0, "labels": [], "examples": []})
        bucket["count"] += 1
        if item.get("label") not in bucket["labels"]:
            bucket["labels"].append(item["label"])
        if len(bucket["examples"]) < 4:
            example = {"label": item["label"]}
            for key in ("slotname", "layer", "database", "chooser"):
                if item.get(key):
                    example[key] = item[key]
            if item.get("owner"):
                example["in"] = item["owner"]
            bucket["examples"].append(example)
    return by_kind
