# -*- coding: utf-8 -*-
r"""
ComboMCP / 战斗连招领域诊断器（只读）
=====================================

这是本项目与所有通用 Unreal MCP 的根本区别所在。通用工具能回答"这里有个
AnimMontage 变量"，但回答不了"为什么第 2 段接不上"。后者需要把**动画侧
的时序**和**蓝图侧的控制流**放在一起看。

诊断原则
--------
1. **只报告可验证的事实**，不臆断意图。例如"输入禁用窗口覆盖了整段
   Montage"是事实；"你的设计错了"不是。
2. **每条 finding 带 evidence**，让调用方能够回到原始数据复核。
3. **每条 finding 带 hint**，说明"如果要改，应该去看哪个资产的什么位置"，
   但**不代替人做决定**。
4. 有疑问就降级为 ``info``，宁可少报也不要制造假阳性——一个乱报的诊断器
   比没有诊断器更糟，因为它会让人开始怀疑正确的代码。

本模块的一切操作都是只读的。
"""

import unreal  # noqa: F401

from . import anim_read, bp_read, runtime as rt


# ====================================================================== 工具


def _finding(severity, category, subject, message, evidence=None, hint=None):
    out = {
        "severity": severity,
        "category": category,
        "subject": subject,
        "message": message,
    }
    if evidence:
        out["evidence"] = evidence
    if hint:
        out["hint"] = hint
    return out


_INPUT_GATE_HINTS = ("inputdisabled", "input_disabled", "disableinput",
                     "inputlock", "blockinput", "noinput")
_CANCEL_HINTS = ("cancel", "combo", "window", "buffer", "inputwindow",
                 "chainwindow", "link")
_DAMAGE_HINTS = ("damage", "hit", "attack", "trace", "collision")


def _classify_notify(name, cls_name):
    """把通知归类：输入门 / 取消窗口 / 伤害判定 / 其他。"""
    blob = ("%s %s" % (name or "", cls_name or "")).lower()
    if any(h in blob for h in _INPUT_GATE_HINTS):
        return "input_gate"
    if any(h in blob for h in _CANCEL_HINTS):
        return "cancel_window"
    if any(h in blob for h in _DAMAGE_HINTS):
        return "damage"
    return "other"


# ====================================================================== Montage 时序


def montage_timeline_analysis(asset_path):
    """对单个 Montage 做时序诊断。"""
    result = anim_read.montage_detail(asset_path, with_notify_props=False)
    if "error" in result:
        return result

    findings = []
    length = result.get("length") or 0.0
    sections = result.get("sections") or []
    notifies = result.get("notifies") or []
    subject = asset_path.rsplit("/", 1)[-1]

    # ---- 1. 通知分类与窗口
    windows = []
    for n in notifies:
        kind = _classify_notify(n.get("name"), n.get("cls"))
        start = n.get("start")
        end = n.get("end", start)
        if start is None:
            continue
        windows.append({
            "name": n.get("name"),
            "class": n.get("cls"),
            "kind": kind,
            "start": start,
            "end": end,
            "duration": n.get("duration", 0.0),
            "is_state": n.get("kind") == "state",
        })

    gates = [w for w in windows if w["kind"] == "input_gate"]
    cancels = [w for w in windows if w["kind"] == "cancel_window"]

    # ---- 2. 输入门是否把整段都锁死
    if gates:
        covered = 0.0
        cursor = 0.0
        for g in sorted(gates, key=lambda w: w["start"]):
            s, e = g["start"], max(g["end"], g["start"])
            if s > cursor:
                covered += max(0.0, min(e, length) - s)
            cursor = max(cursor, e)
        ratio = (covered / length) if length else 0.0
        if ratio > 0.95:
            findings.append(_finding(
                "error", "timeline", subject,
                "输入禁用窗口覆盖了整段 Montage 的 %.0f%%，连招没有可输入的缝隙" % (ratio * 100),
                evidence={"length": length, "gates": gates, "coverage": round(ratio, 3)},
                hint=("检查这些 AnimNotifyState 的时长或起止：%s。"
                      "通常需要在一段的收招阶段留出取消窗口。"
                      % ", ".join(g["name"] or "?" for g in gates)),
            ))
        elif ratio > 0.7:
            findings.append(_finding(
                "warning", "timeline", subject,
                "输入禁用窗口占整段的 %.0f%%，可输入时间偏少" % (ratio * 100),
                evidence={"length": length, "coverage": round(ratio, 3)},
                hint="确认这是有意为之（例如大招不可取消）；否则缩短末端窗口。",
            ))

    # ---- 3. 窗口重叠（同一时刻既是输入门又是取消窗口 = 自相矛盾）
    for g in gates:
        for c in cancels:
            overlap = min(g["end"], c["end"]) - max(g["start"], c["start"])
            if overlap > 1e-3:
                findings.append(_finding(
                    "error", "timeline", subject,
                    "输入门 '%s' 与取消窗口 '%s' 重叠 %.3fs：通知想禁输入，又开了取消窗口"
                    % (g["name"], c["name"], overlap),
                    evidence={"input_gate": g, "cancel_window": c,
                              "overlap": round(overlap, 4)},
                    hint="两者取其一，或把取消窗口挪到输入门结束之后。",
                ))

    # ---- 4. 尾段没有取消窗口
    if length and cancels:
        last_end = max(c["end"] for c in cancels)
        tail = length - last_end
        if tail > 0.25:
            findings.append(_finding(
                "info", "timeline", subject,
                "最后一个取消窗口在 %.3fs 结束，之后还有 %.3fs 无法衔接" % (last_end, tail),
                evidence={"length": length, "last_cancel_end": round(last_end, 4),
                          "tail": round(tail, 4)},
                hint="如果这是收招硬直，属正常；否则这一段是连招断点。",
            ))

    # ---- 5. Section 结构
    if sections:
        starts = [s["start"] for s in sections if s.get("start") is not None]
        if starts != sorted(starts):
            findings.append(_finding(
                "error", "timeline", subject,
                "Section 的起始时间不是递增的，Montage 的段落顺序与时间轴不一致",
                evidence={"sections": sections},
                hint="在 Montage 编辑器的 Sections 列表里按时间重排。",
            ))
        for s in sections:
            nxt = s.get("next")
            if nxt and nxt not in set(x["name"] for x in sections):
                findings.append(_finding(
                    "error", "timeline", subject,
                    "Section '%s' 的 NextSection 指向不存在的 '%s'" % (s["name"], nxt),
                    evidence={"section": s,
                              "available": [x["name"] for x in sections]},
                    hint="在 Montage 编辑器里重新指定该 Section 的 NextSection。",
                ))
        if not any(s.get("next") for s in sections):
            findings.append(_finding(
                "info", "timeline", subject,
                "所有 Section 都没有设置 NextSection，段落衔接完全依赖蓝图调用",
                evidence={"sections": [s["name"] for s in sections]},
                hint=("如果连招靠蓝图里 Montage_SetNextSection / JumpToSection 驱动，"
                      "这是正常的；确认蓝图侧确实这么做了。"),
            ))
    else:
        findings.append(_finding(
            "warning", "timeline", subject,
            "Montage 没有 Section：无法用段落名定位，连招只能整段重播",
            evidence={"length": length},
            hint="在 Montage 编辑器里至少切一个 Section，蓝图才能精确跳到某一段。",
        ))

    return rt.ok({
        "asset": asset_path,
        "length": length,
        "sections": sections,
        "windows": windows,
        "findings": findings,
        "finding_count": len(findings),
        "counts_by_severity": _severity_histogram(findings),
    }, None)


def _severity_histogram(findings):
    hist = {}
    for f in findings:
        hist[f["severity"]] = hist.get(f["severity"], 0) + 1
    return hist


# ====================================================================== 蓝图侧动画调用


PLAY_FNS = ("montage_play", "play_montage", "playanimmontage",
            "play_montage_and_wait", "montage_play_custom")
JUMP_FNS = ("montage_jump_to_section", "montage_set_next_section",
            "montage_set_position", "montage_set_play_rate")
STOP_FNS = ("montage_stop", "stop_anim_montage", "stop_all_montages")


def extract_animation_calls(asset_path):
    """从一个蓝图里提取所有动画相关调用及其目标资产。

    返回的每一条都能回答"这段连招是在哪个图的哪个节点触发的、播的是哪个
    Montage、跳到哪个 Section"。这是把动画时序接回控制流的关键一步。
    """
    diag = rt.Diagnostics()
    asset, err = bp_read.load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    calls = []
    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        model = bp_read.GraphModel(graph, diag)
        for idx, node in enumerate(model.nodes):
            if node["cls"] not in ("CallFunction", "CallParentFunction",
                                   "CallInterfaceFunction", "CallFunctionProxy"):
                continue
            fn = node["meta"].get("fn")
            if not fn:
                continue
            low = fn.lower()
            role = None
            if any(f in low for f in PLAY_FNS):
                role = "play"
            elif any(f in low for f in JUMP_FNS):
                role = "jump"
            elif any(f in low for f in STOP_FNS):
                role = "stop"
            if role is None:
                continue

            entry = {
                "graph": model.name,
                "graph_kind": kind,
                "node": idx,
                "fn": fn,
                "role": role,
            }
            on_class = node["meta"].get("fn_class")
            if on_class:
                entry["on"] = on_class

            # 取目标资产：DefaultObject 指向的 Montage / Section 名
            for pin in node["pins"]:
                if pin["dir"] != "in":
                    continue
                if pin.get("default_obj"):
                    entry.setdefault("assets", {})[pin["name"]] = pin["default_obj"]
                elif pin.get("default") and pin["type"] in ("Name", "String"):
                    entry.setdefault("literals", {})[pin["name"]] = pin["default"]

            calls.append(entry)

    return rt.ok({
        "asset": asset_path,
        "calls": calls,
        "count": len(calls),
        "by_role": _count_by(calls, "role"),
    }, diag)


def _count_by(items, key):
    out = {}
    for it in items:
        k = it.get(key)
        out[k] = out.get(k, 0) + 1
    return out


# ====================================================================== 蓝图逻辑审计


def _reachable_nodes(model):
    """从所有入口沿 exec 走一遍，返回可达节点集合与孤立节点列表。"""
    reachable = set()
    stack = list(model.entry_indices)
    while stack:
        idx = stack.pop()
        if idx in reachable:
            continue
        reachable.add(idx)
        for _, tgt, _ in model.exec_out_targets(idx):
            if tgt not in reachable:
                stack.append(tgt)
        # 纯函数节点（无 exec 引脚）通过数据连线被引用，同样视为可达
        for pin in model.nodes[idx]["pins"]:
            if pin["dir"] != "out":
                continue
            for target in pin.get("to", []):
                if target[0] not in reachable:
                    stack.append(target[0])

    orphans = [i for i in range(len(model.nodes))
               if i not in reachable and not model.exec_in_sources(i)
               and model.nodes[i]["cls"] not in ("Comment", "Knot")]
    return reachable, orphans


def audit_blueprint_logic(asset_path, graph_name=None):
    """蓝图逻辑审计：孤立节点、状态变量、复制一致性、动画调用风险。"""
    diag = rt.Diagnostics()
    asset, err = bp_read.load_asset(asset_path)
    if asset is None:
        return rt.fail(err, diag)

    findings = []
    subject = asset_path.rsplit("/", 1)[-1]
    graph_stats = []

    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        name = rt.graph_name(graph)
        if graph_name and name != graph_name:
            continue
        model = bp_read.GraphModel(graph, diag)
        reachable, orphans = _reachable_nodes(model)

        graph_stats.append({
            "graph": name,
            "nodes": len(model.nodes),
            "reachable": len(reachable),
            "orphans": len(orphans),
        })

        if orphans:
            findings.append(_finding(
                "warning", "deadcode", "%s :: %s" % (subject, name),
                "有 %d 个节点没有任何执行流到达" % len(orphans),
                evidence={"orphans": [
                    {"n": i, "cls": model.nodes[i]["cls"],
                     "title": model.nodes[i]["title"],
                     "meta": model.nodes[i]["meta"]}
                    for i in orphans[:12]]},
                hint=("这些节点多半是改逻辑时留下的残骸。注意：纯数据节点若被别的"
                      "可达节点引用，不应算孤立——上面的证据已排除有 exec 输入的"
                      "节点，仍列出的可以直接在图里核对。"),
            ))

        # 有多个 exec 输出但没连任何一个 = 流程断头
        for idx, node in enumerate(model.nodes):
            if idx not in reachable:
                continue
            exec_outs = [p for p in node["pins"]
                         if p["dir"] == "out" and p["type"] == "exec"]
            connected = [p for p in exec_outs if p.get("to")]
            if exec_outs and not connected and node["cls"] not in ("FunctionResult",):
                findings.append(_finding(
                    "warning", "flow", "%s :: %s" % (subject, name),
                    "节点 n%d(%s) 的执行输出没有接任何东西，流程在这里中断"
                    % (idx, node["cls"]),
                    evidence={"node": idx, "cls": node["cls"],
                              "title": node["title"], "meta": node["meta"],
                              "exec_pins": [p["name"] for p in exec_outs]},
                    hint="如果这里本该继续执行，把 exec 输出接上；否则它是多余的。",
                ))

    # ---- 状态变量：找出枚举型变量，检查转换完整性
    state_vars = []
    for var in rt.safe_get(asset, "NewVariables", diag=None) or []:
        if var is None:
            continue
        vtype = rt.pin_type_str(rt.safe_get(var, "VarType"))
        name = rt.as_text(rt.safe_get(var, "VarName"))
        if name and ("Enum" in vtype or "ECombat" in vtype or "E_" in vtype):
            state_vars.append({"name": name, "type": vtype,
                               "default": rt.as_text(rt.safe_get(var, "DefaultValue"))})
    if state_vars:
        findings.append(_finding(
            "info", "state", subject,
            "检测到 %d 个疑似状态变量：%s"
            % (len(state_vars), ", ".join(v["name"] for v in state_vars)),
            evidence={"state_vars": state_vars},
            hint=("用 flow 读包含这些变量的分支节点，核对是否存在某个状态没有出口"
                  "（进得去出不来），以及是否有状态没被任何分支处理。"),
        ))

    # ---- 复制一致性
    replicated, onreps = [], []
    for var in rt.safe_get(asset, "NewVariables", diag=None) or []:
        if var is None:
            continue
        name = rt.as_text(rt.safe_get(var, "VarName"))
        flags = rt.safe_get(var, "PropertyFlags", diag=None)
        rep_notify = rt.as_text(rt.safe_get(var, "RepNotifyFunc"))
        if isinstance(flags, int) and (flags & 0x8):   # CPF_Net
            replicated.append({"name": name, "rep_notify": rep_notify})
        if rep_notify and rep_notify != "None":
            onreps.append({"var": name, "func": rep_notify})

    for r in replicated:
        rn = r.get("rep_notify")
        if not rn or rn == "None":
            findings.append(_finding(
                "info", "replication", subject,
                "变量 '%s' 标记为 Replicated 但没有 RepNotify 函数" % r["name"],
                evidence=r,
                hint=("如果客户端需要在这个值变化时更新表现（动画、UI），需要 "
                      "RepNotify；如果只是服务器逻辑用，这是正常的。"),
            ))

    declared_funcs = set()
    for kind, graph in rt.get_blueprint_graphs(asset, diag):
        if kind == "function":
            declared_funcs.add(rt.graph_name(graph))
    for f in replicated:
        rn = f.get("rep_notify")
        if rn and rn != "None" and rn not in declared_funcs:
            findings.append(_finding(
                "error", "replication", subject,
                "变量 '%s' 指定的 RepNotify '%s' 在蓝图里不存在" % (f["name"], rn),
                evidence={"var": f["name"], "expected_function": rn,
                          "declared_functions": sorted(declared_funcs)},
                hint="在蓝图里创建该函数，或在变量详情里重新指定 OnRep 函数。",
            ))

    return rt.ok({
        "asset": asset_path,
        "graphs": graph_stats,
        "findings": findings,
        "finding_count": len(findings),
        "counts_by_severity": _severity_histogram(findings),
    }, diag)


# ====================================================================== 综合


def analyze_combo_system(component_path, montage_scope="/Game",
                         max_montages=24):
    """把蓝图侧与动画侧合起来看，给出整个连招系统的体检报告。

    这是回答"为什么第 N 段接不上"的主入口：它同时拿到
      * 组件蓝图里所有 Montage 调用点（谁在哪触发什么）
      * 每个 Montage 的 Section 结构与窗口时序
      * 两者对不上的地方
    """
    diag = rt.Diagnostics()
    report = {
        "component": component_path,
        "montage_scope": montage_scope,
        "findings": [],
        "montages": [],
        "animation_calls": None,
    }

    # ---- 蓝图侧
    calls_result = extract_animation_calls(component_path)
    if "error" in calls_result:
        report["findings"].append(_finding(
            "error", "setup", component_path,
            "无法读取组件蓝图：%s" % calls_result["error"], hint="确认资产路径正确。"))
    else:
        report["animation_calls"] = {
            "count": calls_result["count"],
            "by_role": calls_result["by_role"],
            "calls": calls_result["calls"][:60],
        }
        if calls_result["count"] == 0:
            report["findings"].append(_finding(
                "warning", "setup", component_path,
                "组件蓝图里没有任何 Montage 播放/跳转调用",
                hint=("如果连招由这个组件驱动，这里应该有 Montage_Play；"
                      "如果动画播放在别处（例如 AnimInstance 或 PlayerController），"
                      "把那个蓝图路径也交给我一起看。")))

    # ---- 动画侧：找出目录下所有 Montage
    try:
        paths = unreal.EditorAssetLibrary.list_assets(montage_scope, recursive=True)
    except Exception:
        paths = []

    montage_paths = []
    for path in paths or []:
        try:
            data = unreal.EditorAssetLibrary.find_asset_data(path)
            if data is not None and str(data.asset_class_path.asset_name) == "AnimMontage":
                montage_paths.append(path)
        except Exception:
            continue

    for path in montage_paths[:max_montages]:
        analysis = montage_timeline_analysis(path)
        if "error" in analysis:
            continue
        report["montages"].append({
            "asset": path,
            "length": analysis.get("length"),
            "section_count": len(analysis.get("sections") or []),
            "notify_count": len(analysis.get("windows") or []),
            "findings": analysis.get("findings") or [],
        })
        report["findings"].extend(analysis.get("findings") or [])

    # ---- 交叉核对：蓝图调用的目标是否真的存在
    called = set()
    if report["animation_calls"]:
        for c in report["animation_calls"]["calls"]:
            for _, asset_name in (c.get("assets") or {}).items():
                called.add(asset_name)

    available = set(p.rsplit("/", 1)[-1].split(".")[0] for p in montage_paths)
    missing = sorted(n for n in called if n and n not in available)
    if missing:
        report["findings"].append(_finding(
            "error", "setup", component_path,
            "蓝图里引用的 Montage 在 %s 下找不到：%s" % (montage_scope, ", ".join(missing)),
            evidence={"called": sorted(called), "available_sample": sorted(available)[:20]},
            hint="确认这些资产是否被移动/删除，或把 montage_scope 扩大到 /Game。",
        ))

    orphan_montages = sorted(a for a in available if a not in called)
    if called and orphan_montages:
        report["findings"].append(_finding(
            "info", "setup", montage_scope,
            "有 %d 个 Montage 没有被这个组件直接引用（可能由数据表或别处驱动）"
            % len(orphan_montages),
            evidence={"unreferenced": orphan_montages[:20]},
            hint="如果连招是数据表驱动的，这属正常；否则它们是未接入的素材。",
        ))

    report["finding_count"] = len(report["findings"])
    report["counts_by_severity"] = _severity_histogram(report["findings"])
    report["montage_total"] = len(montage_paths)
    return rt.ok(report, diag)
