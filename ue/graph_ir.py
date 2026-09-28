# -*- coding: utf-8 -*-
r"""
ComboMCP / 图中间表示（IR）与执行流渲染
=======================================

这个模块**不依赖 unreal**，也不依赖任何数据来源。它定义一套图中立表示，
并提供两类操作：

* **执行流遍历**：从入口出发沿 exec 连线走，还原 if / seq / 循环的结构
* **DSL 渲染**：把执行流渲染成 S 表达式伪代码

为什么要有这一层：蓝图数据有两条来源，各自的能力边界不同——

  ``bp_read.GraphModel``  直接走引擎反射（快，但某些成员没有 UPROPERTY，读不到）
  ``t3d``                从 T3D 文本导出重建（能拿到连线和引脚，但要多一步解析）

两条路产出的原始形状不一样。如果 DSL 渲染直接依赖某一条路的原始结构，那么
换数据源就得重写渲染逻辑。所以这里定义 IR，两条路都翻译成它，下游只有一份实现。

IR 形状::

    Graph = {
        "name": str,
        "kind": "event" | "function" | "macro" | "unknown",
        "nodes": [Node, ...],
        "entries": [int, ...],       # 入口节点下标
    }

    Node = {
        "id": int,                   # 图内下标，稳定且可引用
        "cls": str,                  # 去掉 K2Node_ 前缀的类名
        "title": str | None,
        "pos": [int, int],
        "comment": str | None,
        "meta": {"fn": str, "var": str, "event": str, "macro": str,
                 "cast_to": str, "fn_class": str, "pure": bool},
        "pins": [Pin, ...],
    }

    Pin = {
        "name": str,
        "dir": "in" | "out",
        "type": str,                 # "exec" / "bool" / "TArray<AActor*>"
        "default": str | None,
        "default_obj": str | None,
        "to": [[node_id, pin_name], ...],
    }
"""

# ====================================================================== 常量


EXEC = "exec"

# 哪些节点类属于"函数调用"，哪些属于"取/存变量"
FN_CLASSES = frozenset((
    "CallFunction", "CallParentFunction", "CallInterfaceFunction",
    "CallFunctionProxy", "CommutativeAssociativeBinaryOperator",
    "PromotableOperator", "CallArrayFunction", "CreateDelegate",
    "CallDelegate", "AddDelegate", "RemoveDelegate", "ClearDelegate",
    "CallFunctionProxy",
))

VAR_CLASSES = frozenset((
    "VariableGet", "VariableSet", "Self", "ClassVariable", "LocalVariable",
    "GetVariable", "SetVariable", "PropertyAccess", "StructMemberSet",
    "StructMemberGet", "InstancedStructGet", "InstancedStructMake",
))

ENTRY_CLASSES = frozenset((
    # 函数/事件图入口
    "Event", "CustomEvent", "FunctionEntry", "FunctionResult",
    "Tunnel", "Composite",
    # 输入事件——连招系统的触发链全都从这里开始，漏一个整条链就看不见了。
    # 名字是去掉 K2Node_ 前缀后的形式。
    "InputKey", "InputAction", "InputTouch", "InputAxis", "InputAxisEvent",
    "InputVectorAxisEvent", "EnhancedInputAction", "EnhancedInputActionEvent",
    # 其它事件源
    "ActorBoundEvent", "ComponentBoundEvent", "Timeline",
))

# 判定"事件源"的动态规则：有 exec 输出、没有 exec 输入。
# 固定清单永远会漏掉没见过的输入节点类型（InputKey 就是这么被漏掉的，
# 结果整条连招触发链对 DSL 不可见），所以再加一条结构性判据兜底。
def looks_like_entry(node):
    if node.get("cls") in ENTRY_CLASSES:
        return True
    has_exec_out = False
    for pin in node.get("pins") or []:
        if pin.get("type") != EXEC:
            continue
        if pin.get("dir") == "in":
            return False
        if pin.get("dir") == "out":
            has_exec_out = True
    return has_exec_out


# ====================================================================== 图封装


class IRGraph(object):
    """IR 图的查询封装。"""

    def __init__(self, data):
        self.data = data
        self.nodes = data.get("nodes") or []
        self.name = data.get("name") or "<unnamed>"
        self.kind = data.get("kind") or "unknown"
        self.entries = list(data.get("entries") or [])

    # ---------------------------------------------------------------- 查询

    def exec_out(self, node_id):
        """该节点所有 exec 输出的连线目标 ``[(引脚名, 目标节点, 目标引脚)]``。"""
        out = []
        for pin in self.nodes[node_id].get("pins") or []:
            if pin.get("dir") != "out" or pin.get("type") != EXEC:
                continue
            for target in pin.get("to") or []:
                out.append((pin.get("name"), target[0], target[1]))
        return out

    def exec_in(self, node_id):
        out = []
        for pin in self.nodes[node_id].get("pins") or []:
            if pin.get("dir") != "in" or pin.get("type") != EXEC:
                continue
            for source in pin.get("to") or []:
                out.append((pin.get("name"), source[0], source[1]))
        return out

    def data_source(self, node_id, pin_name):
        """某个数据输入引脚的来源，返回 ``(节点, 引脚)`` 或 None。"""
        for pin in self.nodes[node_id].get("pins") or []:
            if pin.get("name") == pin_name and pin.get("dir") == "in":
                to = pin.get("to") or []
                if to:
                    return to[0][0], to[0][1]
                return None
        return None

    def input_pin(self, node_id, pin_name):
        for pin in self.nodes[node_id].get("pins") or []:
            if pin.get("name") == pin_name and pin.get("dir") == "in":
                return pin
        return None


# ====================================================================== 表达式


def _literal(value):
    if isinstance(value, str):
        return '"%s"' % value.replace('"', '\\"')
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return expr_to_dsl(value)
    return str(value)


def _inline(ir, node_id, pin_name, depth, budget):
    """把一个数据输入引脚解析成紧凑表达式。"""
    if budget[0] <= 0:
        return "<budget>"
    source = ir.data_source(node_id, pin_name)
    if source is None:
        pin = ir.input_pin(node_id, pin_name)
        if pin and pin.get("default"):
            return pin["default"]
        if pin and pin.get("default_obj"):
            return pin["default_obj"]
        return None
    if depth > 4:
        return "<deep>"
    budget[0] -= 1
    return node_expr(ir, source[0], depth + 1, budget)


def node_expr(ir, node_id, depth=0, budget=None):
    """把一个节点渲染成表达式 dict（含其数据来源内联）。"""
    if budget is None:
        budget = [400]
    if budget[0] <= 0:
        return "<budget>"
    budget[0] -= 1

    node = ir.nodes[node_id]
    cls = node.get("cls")
    meta = node.get("meta") or {}
    expr = {"n": node_id}

    if cls in VAR_CLASSES:
        expr["op"] = "self" if cls == "Self" else "get"
        expr["var"] = meta.get("var") or node.get("title") or cls
        return expr

    if cls in FN_CLASSES or meta.get("fn"):
        expr["op"] = "call"
        expr["fn"] = meta.get("fn") or node.get("title") or cls
        if meta.get("fn_class"):
            expr["on"] = meta["fn_class"]
        args = {}
        for pin in node.get("pins") or []:
            if pin.get("dir") != "in":
                continue
            if pin.get("type") in (EXEC, "delegate", "MulticastDelegate"):
                continue
            value = _inline(ir, node_id, pin.get("name"), depth, budget)
            if value is not None:
                args[pin["name"]] = value
        if args:
            expr["args"] = args
        return expr

    if cls == "DynamicCast":
        expr["op"] = "cast"
        expr["to"] = meta.get("cast_to") or "?"
        for pin in node.get("pins") or []:
            if pin.get("dir") == "in" and pin.get("type") not in (EXEC, "delegate"):
                value = _inline(ir, node_id, pin.get("name"), depth, budget)
                if value is not None:
                    expr["of"] = value
                    break
        return expr

    if cls == "IfThenElse":
        expr["op"] = "branch"
        cond = _inline(ir, node_id, "Condition", depth, budget)
        if cond is not None:
            expr["cond"] = cond
        return expr

    if cls == "MacroInstance":
        expr["op"] = "macro"
        expr["macro"] = meta.get("macro") or node.get("title") or "?"
        return expr

    expr["op"] = cls
    if node.get("title"):
        expr["title"] = node["title"]
    if meta.get("event"):
        expr["op"] = "event"
        expr["event"] = meta["event"]
    return expr


def expr_to_dsl(expr):
    """把表达式 dict 渲染成 S 表达式片段。"""
    if not isinstance(expr, dict):
        return _literal(expr)
    op = expr.get("op", "?")

    if op == "get":
        return str(expr.get("var", "?"))
    if op == "self":
        return "self"
    if op == "event":
        return "(event %s)" % expr.get("event", "?")
    if op == "call":
        head = "(call %s" % expr.get("fn", "?")
        if expr.get("on"):
            head += " :on %s" % expr["on"]
        for key, value in (expr.get("args") or {}).items():
            head += " :%s %s" % (key, _literal(value))
        return head + ")"
    if op == "cast":
        return "(cast %s %s)" % (_literal(expr.get("of")), expr.get("to", "?"))
    if op == "branch":
        return "(if %s)" % _literal(expr.get("cond"))
    if op == "macro":
        return "(macro %s)" % expr.get("macro", "?")
    if op == "goto":
        return "(goto n%s)" % expr.get("n")
    if op == "truncated":
        return "..."
    return "(%s)" % (expr.get("title") or op)


# ====================================================================== 执行流


MAX_FLOW_STEPS = 400


def walk_exec(ir, start_id, budget, visited, depth=0):
    """沿 exec 流走出一条语句序列。

    返回 ``[{n, expr, then?, else?, seq?}, ...]``。分支节点带 then/else，
    序列节点带 seq。遇到已访问节点时停止展开（防循环图无限递归）。
    """
    stmts = []
    node_id = start_id
    guard = 0

    while node_id is not None and budget[0] > 0 and guard < MAX_FLOW_STEPS:
        guard += 1
        if node_id in visited:
            stmts.append({"n": node_id, "expr": {"op": "goto", "n": node_id}})
            return stmts
        visited.add(node_id)
        if node_id >= len(ir.nodes):
            return stmts

        node = ir.nodes[node_id]
        cls = node.get("cls")
        stmt = {"n": node_id, "expr": node_expr(ir, node_id, depth, budget)}

        if cls == "IfThenElse":
            outs = {}
            for pin_name, target, _ in ir.exec_out(node_id):
                outs[pin_name] = target
            then_id = outs.get("then")
            else_id = outs.get("else")
            if then_id is not None:
                stmt["then"] = walk_exec(ir, then_id, budget, visited, depth + 1)
            if else_id is not None:
                stmt["else"] = walk_exec(ir, else_id, budget, visited, depth + 1)
            stmts.append(stmt)
            return stmts

        if cls == "ExecutionSequence":
            outs = sorted(ir.exec_out(node_id), key=lambda t: t[0])
            stmt["seq"] = [walk_exec(ir, t[1], budget, visited, depth + 1)
                           for t in outs]
            stmts.append(stmt)
            return stmts

        stmts.append(stmt)

        outs = ir.exec_out(node_id)
        if not outs:
            return stmts
        # 优先走常规的 then / execute；潜行节点则走它唯一的输出
        preferred = None
        for pin_name, target, _ in outs:
            if pin_name in ("then", "execute", "Out", ""):
                preferred = target
                break
        if preferred is None:
            preferred = outs[0][1]
        node_id = preferred

    if budget[0] <= 0:
        stmts.append({"expr": {"op": "truncated"}})
    return stmts


def stmt_to_dsl(stmt, indent=0):
    """把一条语句渲染成 S 表达式文本。"""
    pad = "  " * indent
    if not isinstance(stmt, dict):
        return pad + _literal(stmt)

    expr = stmt.get("expr") or {}
    if expr.get("op") == "truncated":
        return pad + "... (truncated)"
    if expr.get("op") == "goto":
        return "%s(goto n%s)" % (pad, stmt.get("n"))

    if "then" in stmt or "else" in stmt:
        lines = ["%s(if %s" % (pad, _literal(expr.get("cond")))]
        if stmt.get("then"):
            lines.append("%s  (:then" % pad)
            for s in stmt["then"]:
                lines.append(stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        if stmt.get("else"):
            lines.append("%s  (:else" % pad)
            for s in stmt["else"]:
                lines.append(stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        lines.append("%s)" % pad)
        return "\n".join(lines)

    if "seq" in stmt:
        lines = ["%s(seq" % pad]
        for i, branch in enumerate(stmt["seq"]):
            lines.append("%s  (:then_%d" % (pad, i))
            for s in branch:
                lines.append(stmt_to_dsl(s, indent + 2))
            lines.append("%s   )" % pad)
        lines.append("%s)" % pad)
        return "\n".join(lines)

    return "%s%s" % (pad, expr_to_dsl(expr))


# ====================================================================== 汇总

ENTRY_LABELS = ("event", "fn")


def entry_label(ir, node_id):
    """给入口节点取一个可读标签。"""
    meta = ir.nodes[node_id].get("meta") or {}
    for key in ENTRY_LABELS:
        if meta.get(key):
            return meta[key]
    return ir.nodes[node_id].get("title") or ir.nodes[node_id].get("cls")


def render_graph_dsl(ir, max_flows=12, budget=None):
    """把一个 IR 图渲染成 S 表达式文本。"""
    budget = budget or [1200]
    visited = set()
    chunks = []
    flows = 0

    for entry_id in ir.entries:
        node = ir.nodes[entry_id]
        if node.get("cls") == "FunctionResult":
            continue
        if flows >= max_flows:
            remaining = len(ir.entries) - flows
            chunks.append("; ... %d more entries omitted" % max(remaining, 0))
            break
        flows += 1

        body = []
        for _, target, _ in ir.exec_out(entry_id):
            body.extend(walk_exec(ir, target, budget, visited))

        chunks.append("(event %s" % entry_label(ir, entry_id))
        for stmt in body:
            chunks.append(stmt_to_dsl(stmt, 1))
        chunks.append(")")

    if not chunks:
        chunks.append("; (empty graph)")

    return "\n".join(chunks)


def reachable_nodes(ir):
    """从入口出发做活跃性分析，返回 ``(可达集合, 孤立节点列表)``。

    走法有两段，缺一不可：

    * **沿 exec 正向**：执行流到达的节点自然是活的。
    * **沿数据连线反向**：纯数据节点（VariableGet / 纯函数）自己没有任何 exec
      引脚，只有"被下游用到"这一条线索。正向走永远到不了它们——必须从已判定
      为活的节点出发，把它**输入**引脚的上游来源也标记为活。

    整体刻意偏向"判活"：把活节点误报成孤立，比漏报一个孤立节点有害得多，
    因为前者会让人开始怀疑正确的代码。
    """
    reachable = set()
    stack = [n for n in ir.entries if n < len(ir.nodes)]

    while stack:
        node_id = stack.pop()
        if node_id in reachable or node_id >= len(ir.nodes):
            continue
        reachable.add(node_id)

        # 正向：exec 流
        for _, target, _ in ir.exec_out(node_id):
            if target not in reachable:
                stack.append(target)

        # 反向：本节点用到的数据来源也是活的
        for pin in ir.nodes[node_id].get("pins") or []:
            if pin.get("dir") != "in":
                continue
            for source in pin.get("to") or []:
                if source[0] not in reachable:
                    stack.append(source[0])

    orphans = []
    for node_id, node in enumerate(ir.nodes):
        if node_id in reachable:
            continue
        if node.get("cls") in ("Comment", "Knot"):
            continue
        if ir.exec_in(node_id):
            continue
        orphans.append(node_id)
    return reachable, orphans
