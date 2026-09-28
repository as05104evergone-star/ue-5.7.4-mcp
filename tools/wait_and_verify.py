# -*- coding: utf-8 -*-
r"""
ComboMCP / 等待编辑器就绪并做首次真实读取
=========================================

后台运行：轮询 Remote Execution 通道直到编辑器加载完成，然后依次执行
若干真实查询，并把每个查询的 ``_diag.misses`` 汇总出来。

这个脚本同时承担"读取器健康检查"的职责：如果某个字段在引擎这一版里
读不到，misses 会明确指出来，我再据此修正 ``ue/`` 下的读取器。

运行：
  python tools\wait_and_verify.py [超时秒数]
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.bridge import UEBridge, BridgeError  # noqa: E402


def banner(text):
    print()
    print("=" * 70)
    print(text)
    print("=" * 70)
    sys.stdout.flush()


def show(label, payload, limit=3500):
    print("\n--- %s ---" % label)
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    except Exception as exc:
        text = "<not serializable: %s>" % exc
    if len(text) > limit:
        text = text[:limit] + "\n... [截断，原长 %d]" % len(text)
    print(text)
    sys.stdout.flush()


def diag_summary(label, payload):
    """把一个结果里的 _diag.misses 汇总出来。"""
    diag = (payload or {}).get("_diag") or {}
    misses = diag.get("misses") or []
    if not misses:
        return None
    print("\n[diag] %s: %d 个属性读取失败" % (label, diag.get("miss_count", len(misses))))
    for miss in misses[:25]:
        print("       %-42s .%s  -> %s"
              % (miss.get("owner", "?")[:42], miss.get("prop", "?"),
                 miss.get("error", "")[:70]))
    sys.stdout.flush()
    return misses


def main():
    timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 1800

    banner("ComboMCP 首次验证（等待编辑器就绪）")
    bridge = UEBridge(discover_timeout=4.0, verbose=True)

    started = time.time()
    attempt = 0
    ready = False
    while time.time() - started < timeout:
        attempt += 1
        try:
            bridge.connect(force=True)
            ready = True
            break
        except BridgeError:
            elapsed = int(time.time() - started)
            print("[wait] %4ds  第 %d 次探测：通道未就绪" % (elapsed, attempt))
            sys.stdout.flush()
            time.sleep(12)
        except Exception as exc:
            print("[wait] 探测异常：%s" % exc)
            sys.stdout.flush()
            time.sleep(12)

    if not ready:
        print("\n[FAIL] 等待 %d 秒后通道仍未就绪。" % timeout)
        print("       编辑器可能还在编译 shader，或 -EnablePlugins 未生效。")
        return 1

    banner("通道已就绪（用时 %d 秒，%d 次探测）"
           % (int(time.time() - started), attempt))

    all_misses = {}

    # ---- 1. 环境
    ping = bridge.call("ping")
    show("ping / 环境", ping)
    diag_summary("ping", ping)

    # ---- 2. Combo_Demo 目录资产统计
    overview = bridge.call("project_overview", {"scope": "/Game/Combo_Demo"})
    if "error" not in overview:
        classes = overview.get("classes") or {}
        print("\n--- /Game/Combo_Demo 资产分布 ---")
        for name, info in list(classes.items())[:20]:
            print("  %-28s %4d   e.g. %s"
                  % (name, info.get("count", 0),
                     (info.get("examples") or ["-"])[0]))
        print("  蓝图数: %s" % overview.get("blueprint_count"))
    else:
        show("project_overview 失败", overview)
    sys.stdout.flush()

    # ---- 3. 战斗组件类摘要
    cls = bridge.call("class_summary",
                      {"asset_path": "/Game/Combo_Demo/Component/AC_Combat"})
    if "error" in cls:
        show("class_summary(AC_Combat) 失败", cls)
    else:
        brief = {
            "parent": cls.get("parent"),
            "interfaces": cls.get("interfaces"),
            "blueprint_type": cls.get("blueprint_type"),
            "variable_count": len(cls.get("variables") or []),
            "variables_sample": (cls.get("variables") or [])[:18],
            "function_count": len(cls.get("functions") or []),
            "functions": cls.get("functions"),
            "events": cls.get("events"),
            "components": cls.get("components"),
            "graphs": cls.get("graphs"),
        }
        show("class_summary(AC_Combat)", brief, limit=6000)
    all_misses["class_summary"] = diag_summary("class_summary", cls)

    # ---- 4. 图概览
    graphs = cls.get("graphs") or []
    target_graph = None
    for g in graphs:
        if g.get("kind") == "event":
            target_graph = g.get("name")
            break
    if target_graph:
        ov = bridge.call("graph_overview", {
            "asset_path": "/Game/Combo_Demo/Component/AC_Combat",
            "graph_name": target_graph,
        })
        show("graph_overview(%s)" % target_graph,
             (ov.get("graphs") or [{}])[0] if "error" not in ov else ov,
             limit=2500)
        all_misses["graph_overview"] = diag_summary("graph_overview", ov)

    # ---- 5. Montage
    montage_path = None
    try:
        listing = bridge.call("list_assets", {
            "scope": "/Game/Combo_Demo/Animation/Montage",
            "class_filter": "AnimMontage",
            "limit": 20,
        })
        assets = listing.get("assets") or []
        if assets:
            montage_path = assets[0]["path"]
    except Exception:
        pass

    if montage_path:
        mnt = bridge.call("montage_detail", {"asset_path": montage_path})
        if "error" in mnt:
            show("montage_detail 失败 (%s)" % montage_path, mnt)
        else:
            show("montage_detail(%s)" % montage_path.rsplit("/", 1)[-1], {
                "length": mnt.get("length"),
                "slots": mnt.get("slots"),
                "sections": mnt.get("sections"),
                "notify_count": mnt.get("notify_count"),
                "notifies": (mnt.get("notifies") or [])[:12],
                "timeline_check": mnt.get("timeline_check"),
            }, limit=5000)
        all_misses["montage_detail"] = diag_summary("montage_detail", mnt)

    # ---- 6. 执行流 DSL（只取一个入口）
    if target_graph:
        dsl = bridge.call("graph_dsl", {
            "asset_path": "/Game/Combo_Demo/Component/AC_Combat",
            "graph_name": target_graph,
            "max_flows": 2,
        })
        if "error" in dsl:
            show("graph_dsl 失败", dsl, limit=1500)
        else:
            print("\n--- graph_dsl(%s) 前 2200 字符 ---" % target_graph)
            print((dsl.get("dsl") or "")[:2200])
            sys.stdout.flush()

    # ---- 汇总
    banner("验证结论")
    total = sum(len(v or []) for v in all_misses.values())
    print("属性读取失败总计：%d" % total)
    for key, value in all_misses.items():
        print("  %-16s %d" % (key, len(value or [])))
    if total == 0:
        print("\n所有读取器在本次采样的资产上都没有出现属性缺失。")
    else:
        print("\n上面列出的 (owner, prop, error) 就是需要修正的读取点。")
    print("\nbridge 统计：")
    print(json.dumps(bridge.stats(), ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
