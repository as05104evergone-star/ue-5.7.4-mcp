# -*- coding: utf-8 -*-
r"""
ComboMCP / 端到端集成验证
=========================

通道一开就自动跑这一份。它**走生产代码路径**——通过 :class:`engine.bridge.UEBridge`
调用真实的工具命令，而不是另发一段临时探针代码。所以它验证的是实际会用的东西。

检查顺序刻意从"基础能力"到"能力边界"：

  1. 通道与环境
  2. 资产枚举（AssetRegistry 是否工作）
  3. 类摘要（反射能读到什么、读不到什么）
  4. 图结构：反射路径到底能不能拿到引脚
  5. **T3D 导出与解析**（这是关键——反射读不到时的主力）
  6. 执行流 DSL
  7. 动画资产（Montage 的 Section 与 Notify 时序）
  8. 连招诊断

每一节都会把 ``_diag.misses`` 汇总出来，那些就是需要修正的读取点。

运行：
  & "...\python.exe" tools\verify_all.py [等待秒数]
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.bridge import UEBridge, BridgeError  # noqa: E402

BP = "/Game/Combo_Demo/Component/AC_Combat"
MONTAGE_DIR = "/Game/Combo_Demo/Animation/Montage"
NOTIFY_BP = "/Game/Combo_Demo/Animation/Notifies/ANS_InputDisabled"

REPORT = {
    "sections": [],
    "problems": [],
    "diag_misses": {},
}


def banner(text):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)
    sys.stdout.flush()


def note(text):
    print(text)
    sys.stdout.flush()


def show(label, payload, limit=2600):
    print("\n--- %s ---" % label)
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    except Exception as exc:
        text = "<not serializable: %s>" % exc
    if len(text) > limit:
        text = text[:limit] + "\n... [截断，原长 %d]" % len(text)
    print(text)
    sys.stdout.flush()


def collect_diag(section, payload):
    diag = (payload or {}).get("_diag") or {}
    misses = diag.get("misses") or []
    if misses:
        REPORT["diag_misses"][section] = misses
        note("\n[diag] %s: %d 个属性读取失败" % (section, diag.get("miss_count", len(misses))))
        for miss in misses[:20]:
            note("       %-40s .%s -> %s" % (
                str(miss.get("owner"))[:40], miss.get("prop"),
                str(miss.get("error"))[:60]))
    return misses


def section(name, ok, detail=""):
    REPORT["sections"].append({"name": name, "ok": bool(ok), "detail": detail})
    note("\n[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def problem(text):
    REPORT["problems"].append(text)
    note("       !! %s" % text)


def main():
    wait_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 1800

    banner("ComboMCP 端到端集成验证")
    bridge = UEBridge(discover_timeout=4.0, verbose=False)

    started = time.time()
    attempt = 0
    ready = False
    while time.time() - started < wait_seconds:
        attempt += 1
        try:
            bridge.connect(force=True)
            ready = True
            break
        except BridgeError:
            elapsed = int(time.time() - started)
            if attempt == 1 or elapsed % 60 < 14:
                note("[wait] %4ds  第 %d 次探测：通道未就绪" % (elapsed, attempt))
                note("       （需要你在编辑器里勾选 Project Settings → Plugins → Python")
                note("         → Enable Remote Execution?）")
            time.sleep(12)
        except Exception as exc:
            note("[wait] 探测异常：%s" % exc)
            time.sleep(12)

    if not ready:
        banner("通道未就绪")
        note("等待 %d 秒后仍未发现编辑器。" % wait_seconds)
        note("请确认已勾选 Enable Remote Execution?，并确认编辑器已完成加载。")
        return 2

    banner("通道已就绪（用时 %d 秒，%d 次探测）" % (int(time.time() - started), attempt))

    # ---------------------------------------------------------------- 1
    banner("1. 通道与环境")
    ping = bridge.call("ping")
    show("ping", ping)
    if "error" in ping:
        problem("ping 失败：%s" % ping["error"])
        return 3
    section("通道连通", True, "engine=%s" % ping.get("engine_version"))
    section("Python 版本", bool(ping.get("python")), ping.get("python"))

    # ---------------------------------------------------------------- 2
    banner("2. 资产枚举")
    listing = bridge.call("list_assets", {
        "scope": MONTAGE_DIR, "class_filter": "AnimMontage", "limit": 30})
    montages = [a["path"] for a in (listing.get("assets") or [])]
    section("AssetRegistry 可枚举 Montage", len(montages) > 0,
            "找到 %d 个" % len(montages))
    if montages:
        note("       例：%s" % ", ".join(m.rsplit("/", 1)[-1] for m in montages[:5]))

    # ---------------------------------------------------------------- 3
    banner("3. 类摘要（反射路径能读到什么）")
    cls = bridge.call("class_summary", {"asset_path": BP})
    if "error" in cls:
        problem("class_summary 失败：%s" % cls["error"])
        show("错误详情", cls)
    else:
        summary = {
            "parent": cls.get("parent"),
            "interfaces": cls.get("interfaces"),
            "variable_count": len(cls.get("variables") or []),
            "variables_sample": (cls.get("variables") or [])[:12],
            "function_count": len(cls.get("functions") or []),
            "functions": (cls.get("functions") or [])[:8],
            "event_count": len(cls.get("events") or []),
            "events": (cls.get("events") or [])[:8],
            "components": cls.get("components"),
            "graphs": cls.get("graphs"),
        }
        show("AC_Combat 类摘要", summary, limit=4200)
        section("读到父类", bool(cls.get("parent")), str(cls.get("parent")))
        section("读到图清单", bool(cls.get("graphs")),
                "%d 个图" % len(cls.get("graphs") or []))
        section("读到变量（反射对 NewVariables 的可见性）",
                bool(cls.get("variables")),
                "%d 个变量" % len(cls.get("variables") or []))
        if not cls.get("variables"):
            problem("反射读不到变量表 —— 变量需要走 T3D 或 CDO 路径补齐")
        if not cls.get("functions"):
            problem("反射读不到函数签名 —— 需要走 T3D 路径补齐")
    collect_diag("class_summary", cls)

    # ---------------------------------------------------------------- 4
    banner("4. 图结构：反射路径到底能不能拿到引脚")
    graphs = (cls.get("graphs") or []) if "error" not in cls else []
    event_graph = None
    for g in graphs:
        if g.get("kind") == "event":
            event_graph = g.get("name")
            break
    if not event_graph and graphs:
        event_graph = graphs[0].get("name")
    note("目标图：%s" % event_graph)

    if event_graph:
        ov_reflect = bridge.call("graph_overview", {
            "asset_path": BP, "graph_name": event_graph, "source": "reflect"})
        if "error" not in ov_reflect:
            g0 = (ov_reflect.get("graphs") or [{}])[0]
            show("graph_overview (reflect)", {
                "node_count": g0.get("node_count"),
                "node_types_top": dict(list((g0.get("node_types") or {}).items())[:10]),
                "entries": (g0.get("entries") or [])[:6],
            }, limit=2000)
            section("反射能列出节点", bool(g0.get("node_count")),
                    "%s 个节点" % g0.get("node_count"))

        flow_reflect = bridge.call("graph_dsl", {
            "asset_path": BP, "graph_name": event_graph,
            "source": "reflect", "format": "dsl", "max_flows": 1})
        dsl_reflect = (flow_reflect or {}).get("dsl") or ""
        reflect_usable = bool(dsl_reflect.strip()) and "call " in dsl_reflect
        section("反射路径能读出执行流（引脚可读性）", reflect_usable,
                "DSL %d 字符" % len(dsl_reflect))
        if not reflect_usable:
            problem("反射路径读不出执行流 —— 这正是 UEdGraphNode.Pins 没有 UPROPERTY "
                    "的后果，图逻辑必须走 T3D")
        else:
            note("\n反射路径 DSL 片段：")
            note(dsl_reflect[:1200])

    # ---------------------------------------------------------------- 5
    banner("5. T3D 导出与解析（关键路径）")
    t3d_graphs = bridge.call("t3d_graphs", {"asset_path": BP})
    if "error" in t3d_graphs:
        problem("T3D 导出/解析失败：%s" % t3d_graphs["error"])
        show("T3D 错误详情", t3d_graphs, limit=3000)
    else:
        show("T3D 图清单", {
            "graph_count": t3d_graphs.get("graph_count"),
            "graphs": (t3d_graphs.get("graphs") or [])[:12],
            "export_info": t3d_graphs.get("export_info"),
        }, limit=3200)
        section("T3D 导出成功", True,
                "%d 个图" % (t3d_graphs.get("graph_count") or 0))

        graphs_list = t3d_graphs.get("graphs") or []
        with_entries = [g for g in graphs_list if g.get("entries")]
        section("T3D 解析出图入口", bool(with_entries),
                "%d/%d 个图有入口" % (len(with_entries), len(graphs_list)))

        total_nodes = sum(g.get("nodes") or 0 for g in graphs_list)
        section("T3D 解析出节点", total_nodes > 0, "共 %d 个节点" % total_nodes)

        # DSL
        t3d_dsl = bridge.call("t3d_dsl", {
            "asset_path": BP, "graph_name": event_graph, "max_flows": 2})
        if "error" in t3d_dsl:
            problem("T3D DSL 渲染失败：%s" % t3d_dsl["error"])
            show("详情", t3d_dsl, limit=2000)
        else:
            dsl = t3d_dsl.get("dsl") or ""
            section("T3D 渲染出 DSL", bool(dsl.strip()), "%d 字符" % len(dsl))
            note("\nT3D 路径 DSL（前 2400 字符）：")
            note(dsl[:2400])
            has_call = "(call " in dsl
            has_if = "(if " in dsl
            section("DSL 含函数调用", has_call)
            section("DSL 含分支结构", has_if)
            if not has_call:
                problem("T3D DSL 里没有函数调用 —— 解析可能没抓到 FunctionReference")

        # 节点详情
        if with_entries:
            g = with_entries[0]
            entry_node = (g.get("entries") or [{}])[0].get("n")
            nd = bridge.call("t3d_node", {
                "asset_path": BP, "graph_name": g.get("name"),
                "node_index": entry_node})
            if "error" not in nd:
                show("入口节点详情 (%s n%s)" % (g.get("name"), entry_node), {
                    "cls": nd.get("cls"),
                    "pins": (nd.get("pins") or [])[:10],
                }, limit=2400)
                section("T3D 节点详情含引脚", bool(nd.get("pins")),
                        "%d 个引脚" % len(nd.get("pins") or []))

    # ---------------------------------------------------------------- 6
    banner("6. 自动择路是否生效")
    auto = bridge.call("graph_dsl", {
        "asset_path": BP, "graph_name": event_graph,
        "source": "auto", "format": "dsl", "max_flows": 1})
    src = (auto or {}).get("source")
    section("auto 择路返回 source 字段", src in ("reflect", "t3d"), str(src))
    if (auto or {}).get("_fallback_note"):
        note("       回退说明：%s" % auto["_fallback_note"])
    note("       实际使用路径：%s" % src)

    # ---------------------------------------------------------------- 7
    banner("7. 动画资产：Montage 的 Section 与 Notify 时序")
    if montages:
        target = None
        for m in montages:
            if "Combo" in m:
                target = m
                break
        target = target or montages[0]
        note("目标 Montage：%s" % target)
        mnt = bridge.call("montage_detail", {"asset_path": target})
        if "error" in mnt:
            problem("montage_detail 失败：%s" % mnt["error"])
            show("详情", mnt, limit=2000)
        else:
            show("Montage 结构", {
                "length": mnt.get("length"),
                "slots": mnt.get("slots"),
                "sections": mnt.get("sections"),
                "notify_count": mnt.get("notify_count"),
                "notifies": (mnt.get("notifies") or [])[:14],
                "timeline_check": mnt.get("timeline_check"),
            }, limit=5000)
            section("读到 Montage 长度", mnt.get("length") is not None,
                    str(mnt.get("length")))
            section("读到 Section", bool(mnt.get("sections")),
                    "%d 个" % len(mnt.get("sections") or []))
            section("读到 Notify 时序", bool(mnt.get("notifies")),
                    "%d 个" % len(mnt.get("notifies") or []))
            if not mnt.get("notifies"):
                problem("读不到 Notify —— 时间轴诊断会失效")

        ana = bridge.call("montage_timeline_analysis", {"asset_path": target})
        if "error" not in ana:
            show("时序诊断", {
                "windows": (ana.get("windows") or [])[:10],
                "findings": ana.get("findings"),
            }, limit=3200)
            section("时序诊断产出结论", True,
                    "%d 条" % len(ana.get("findings") or []))
        collect_diag("montage", mnt)

    # Notify 蓝图
    notify = bridge.call("notify_detail", {"asset_path": NOTIFY_BP})
    if "error" not in notify:
        show("ANS_InputDisabled", {
            "class": notify.get("class"),
            "parent": notify.get("parent"),
            "variables": notify.get("variables"),
            "defaults": notify.get("defaults"),
            "graphs": notify.get("graphs"),
        }, limit=3000)
        section("读到 Notify 蓝图", True, str(notify.get("parent")))
    collect_diag("notify_detail", notify)

    # ---------------------------------------------------------------- 8
    banner("8. 连招综合诊断")
    diag = bridge.call("analyze_combo_system", {
        "component_path": BP,
        "montage_scope": "/Game/Combo_Demo",
        "max_montages": 8})
    if "error" in diag:
        problem("analyze_combo_system 失败：%s" % diag["error"])
        show("详情", diag, limit=2000)
    else:
        show("连招诊断摘要", {
            "montage_total": diag.get("montage_total"),
            "animation_calls": {
                "count": (diag.get("animation_calls") or {}).get("count"),
                "by_role": (diag.get("animation_calls") or {}).get("by_role"),
            },
            "findings": (diag.get("findings") or [])[:12],
            "counts_by_severity": diag.get("counts_by_severity"),
        }, limit=5000)
        section("诊断能产出结论", True,
                "%d 条 finding" % len(diag.get("findings") or []))

    # ---------------------------------------------------------------- 汇总
    banner("验证汇总")
    ok_count = sum(1 for s in REPORT["sections"] if s["ok"])
    note("检查项：%d 通过 / %d 总计" % (ok_count, len(REPORT["sections"])))
    for s in REPORT["sections"]:
        note("  %-4s %s %s" % ("PASS" if s["ok"] else "FAIL", s["name"], s["detail"]))

    if REPORT["diag_misses"]:
        note("\n属性读取失败汇总（这些就是需要修正的读取点）：")
        for sec, misses in REPORT["diag_misses"].items():
            note("  [%s] %d 条" % (sec, len(misses)))
            for miss in misses[:10]:
                note("      %-36s .%s -> %s" % (
                    str(miss.get("owner"))[:36], miss.get("prop"),
                    str(miss.get("error"))[:56]))
    else:
        note("\n本次采样中没有出现属性读取失败。")

    if REPORT["problems"]:
        note("\n需要处理的问题：")
        for p in REPORT["problems"]:
            note("  - %s" % p)
    else:
        note("\n没有发现问题。")

    out_path = os.path.join(ROOT, "cache", "verify_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, ensure_ascii=False, indent=1)
    note("\n报告已写入：%s" % out_path)

    return 0 if not REPORT["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
