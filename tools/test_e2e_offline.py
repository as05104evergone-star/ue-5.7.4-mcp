# -*- coding: utf-8 -*-
r"""
ComboMCP / 端到端集成测试：收集 -> 离线解析
==========================================

验证整条链路：headless commandlet 把用户的资产导成 T3D 落到缓存，
然后 MCP 进程**不依赖编辑器**地把图逻辑与 Montage 时序读出来。

这一份跑通，就意味着 ComboMCP 可以在"编辑器没开 Remote Execution"的情况下工作。
"""

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import collector, offline, pure  # noqa: E402

MONTAGES = [
    "/Game/Combo_Demo/Animation/Montage/AS_Combo01_01.AS_Combo01_01",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo02_01.AS_Combo02_01",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo02_02.AS_Combo02_02",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo02_03.AS_Combo02_03",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo02_04.AS_Combo02_04",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo03_01.AS_Combo03_01",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo03_02.AS_Combo03_02",
    "/Game/Combo_Demo/Animation/Montage/AS_Combo03_04.AS_Combo03_04",
]

OTHERS = [
    "/Game/Combo_Demo/Component/AC_Combat.AC_Combat",
    "/Game/Combo_Demo/Component/BP_MasterWeapon.BP_MasterWeapon",
    "/Game/Combo_Demo/Component/BPI_CombatComponent.BPI_CombatComponent",
    "/Game/Combo_Demo/Component/BPI_WeaponActor.BPI_WeaponActor",
    "/Game/Combo_Demo/Animation/Notifies/ANS_InputDisabled.ANS_InputDisabled",
]

ANIM = [
    "/Game/Blueprints/SandboxCharacter_Mover_ABP.SandboxCharacter_Mover_ABP",
    "/Game/Characters/UEFN_Mannequin/Animations/ExperimentalStateMachineData/PSD_SM_Mover_Loops.PSD_SM_Mover_Loops",
]

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


def main():
    print("=" * 72)
    print("ComboMCP 端到端：收集 -> 离线解析")
    print("=" * 72)

    # ---------------------------------------------------------------- 1
    print("\n[1] 收集（headless commandlet）")
    before = len(offline.list_cached())
    print("  收集前已缓存 %d 个资产" % before)

    t0 = time.time()
    result = collector.collect(MONTAGES + OTHERS + ANIM, timeout=1800)
    elapsed = time.time() - t0

    if "error" in result:
        check("收集成功", False, result.get("error"))
        print(json.dumps(result, ensure_ascii=False, indent=1)[:3000])
        return 1

    print("  用时 %.1fs  导出 %d  跳过 %d  失败 %d" % (
        elapsed, len(result.get("exported") or []),
        len(result.get("skipped") or []), len(result.get("failed") or [])))
    check("收集过程无失败", not result.get("failed"),
          json.dumps(result.get("failed"), ensure_ascii=False)[:400])
    after = len(offline.list_cached())
    print("  收集后已缓存 %d 个资产" % after)
    check("缓存里有 >= 5 个文件", after >= 5, "cached=%d" % after)
    check("索引已写入", len(offline.load_index()) >= 5,
          "index=%d" % len(offline.load_index()))

    # ---------------------------------------------------------------- 2
    print("\n[2] 离线读取蓝图（不需要编辑器）")
    bp = "/Game/Combo_Demo/Component/AC_Combat.AC_Combat"
    ov = offline.graph_overview(bp)
    check("AC_Combat 图概览可读", "error" not in ov,
          str(ov.get("error"))[:200])
    if "error" not in ov:
        graphs = ov.get("graphs") or []
        total = sum(g.get("node_count", 0) for g in graphs)
        print("  图数 %d  节点合计 %d" % (len(graphs), total))
        for g in sorted(graphs, key=lambda x: -x.get("node_count", 0))[:5]:
            print("    %-40s %-9s nodes=%-5d entries=%d" % (
                g["graph"][:40], g["kind"], g["node_count"], len(g.get("entries") or [])))
        check("读到多个图", len(graphs) >= 5, "got %d" % len(graphs))
        check("节点总数合理(>100)", total > 100, "got %d" % total)
        check("引脚形态为 custom_properties",
              ov.get("pin_form") == "custom_properties", str(ov.get("pin_form")))

    dsl = offline.graph_dsl(bp, graph_name="EventGraph", max_flows=4, budget=2000)
    check("EventGraph DSL 可渲染", "error" not in dsl, str(dsl.get("error"))[:200])
    if "error" not in dsl:
        text = dsl.get("dsl") or ""
        print("\n--- AC_Combat / EventGraph DSL（前 1800 字符）---")
        print(text[:1800])
        print("--- end ---")
        check("DSL 含函数调用", "(call " in text)
        check("DSL 含连招关键调用 PlayAnimMontage 或 GetAttackMontages",
              "PlayAnimMontage" in text or "GetAttackMontages" in text,
              text[:300])

    calls = offline.find_animation_calls(bp)
    check("动画调用提取可读", "error" not in calls, str(calls.get("error"))[:200])
    if "error" not in calls:
        print("\n  动画相关调用 %d 条，按角色：%s" % (
            calls["count"], json.dumps(calls["by_role"], ensure_ascii=False)))
        for c in calls["calls"][:10]:
            print("    %-8s %-28s in %-28s %s" % (
                c["role"], c["fn"][:28], c["graph"][:28],
                json.dumps({k: v for k, v in c.items()
                            if k in ("assets", "literals")}, ensure_ascii=False)[:90]))

    au = offline.audit(bp)
    check("逻辑审计可跑", "error" not in au, str(au.get("error"))[:200])
    if "error" not in au:
        print("\n  审计：%d 条 finding" % au.get("finding_count", 0))
        for f in (au.get("findings") or [])[:6]:
            print("    [%s/%s] %s" % (f["severity"], f["category"], f["message"][:110]))

    # ---------------------------------------------------------------- 3
    print("\n[3] 离线读取 Montage（反射读不到 Section，只能走 T3D）")
    ok_montages = 0
    for path in MONTAGES:
        md = offline.montage_detail(path, analyze=True)
        if "error" in md:
            print("    %-22s 失败: %s" % (path.rsplit("/", 1)[-1][:22],
                                          str(md.get("error"))[:70]))
            continue
        ok_montages += 1
        ana = md.get("analysis") or {}
        print("    %-22s 长度=%-8s 段落=%d 槽位=%d 通知=%d findings=%d" % (
            path.rsplit("/", 1)[-1][:22], md.get("length"),
            md.get("section_count", 0), len(md.get("slots") or []),
            md.get("notify_count", 0), len(ana.get("findings") or [])))
        for w in (ana.get("windows") or [])[:3]:
            print("        window %-24s %-12s [%.3f, %.3f]" % (
                str(w.get("name"))[:24], w.get("kind"),
                w.get("start", 0.0), w.get("end", 0.0)))
    check("全部 8 个 Montage 可读", ok_montages == len(MONTAGES),
          "ok=%d/%d" % (ok_montages, len(MONTAGES)))

    # ---------------------------------------------------------------- 4
    print("\n[4] 汇集：整个连招系统的窗口一览")
    rows = []
    m3 = pure.load()[2]      # montage_t3d 模块（pure.montage_t3d 是取模块的函数）
    for path in MONTAGES:
        md = offline.montage_detail(path)
        if "error" in md:
            continue
        gates = []
        for n in md.get("notifies") or []:
            kind = m3.classify_notify(n.get("name"), n.get("cls"))
            if kind == "input_gate":
                gates.append((n.get("start", 0.0), n.get("end", n.get("start", 0.0))))
        rows.append((path.rsplit("/", 1)[-1].split(".")[0],
                     md.get("length"), len(md.get("sections") or []),
                     md.get("notify_count", 0), gates))
    for name, length, secs, notifies, gates in rows:
        covered = sum(e - s for s, e in gates)
        ratio = (covered / length * 100) if length else 0.0
        print("    %-18s 长度=%-8s 段落=%d 通知=%d  输入禁用=%.0f%%" % (
            name, length, secs, notifies, ratio))
    check("窗口一览产出 %d 行" % len(rows), len(rows) == len(MONTAGES),
          "rows=%d" % len(rows))

    # ---------------------------------------------------------------- 5
    print("\n[5] 动画面板：AnimBP 状态机 / PoseSearch（反射读不到，只能走 T3D）")
    abp = offline.anim_blueprint(ANIM[0])
    check("AnimBP 可读", "error" not in abp, str(abp.get("error"))[:200])
    if "error" not in abp:
        sms = abp.get("state_machines") or []
        check("读到状态机", len(sms) >= 1, "got %d" % len(sms))
        if sms:
            sm = sms[0]
            states = [s for s in (sm.get("states") or []) if s.get("kind") == "state"]
            aliases = [s for s in (sm.get("states") or []) if s.get("kind") == "alias"]
            trans = sm.get("transitions") or []
            print("    状态机 '%s' 入口=%s  状态=%d 别名=%d 转换=%d" % (
                sm.get("name"), sm.get("entry"), len(states), len(aliases), len(trans)))
            print("    状态: %s" % ", ".join(s["name"] for s in states)[:150])
            named = [t for t in trans if t.get("from") and t.get("to")]
            print("    可解析端点的转换: %d/%d" % (len(named), len(trans)))
            for t in named[:5]:
                print("        %-24s -> %-22s cond=%s" % (
                    str(t["from"])[:24], str(t["to"])[:22],
                    ",".join(t.get("conditions") or [])[:50]))
            check("状态名可读（不是 AnimStateNode_N）",
                  all(not s["name"].startswith("AnimStateNode_") for s in states),
                  str([s["name"] for s in states])[:160])
            check("转换端点可解析（In/Out 引脚）", len(named) >= len(trans) // 2,
                  "named=%d total=%d" % (len(named), len(trans)))
            check("别名可读（StateAliasName）",
                  all(not a["name"].startswith("AnimStateAliasNode_") for a in aliases),
                  str([a["name"] for a in aliases])[:160])
        check("读到 Montage Slot", bool(abp.get("slots")),
              str(abp.get("slots"))[:120])

    psd = offline.generic_asset(ANIM[1])
    check("PoseSearch 数据库可读", "error" not in psd, str(psd.get("error"))[:200])
    if "error" not in psd:
        print("    PoseSearch class=%s indexed=%s" % (
            psd.get("class"), psd.get("indexed_props")))
        check("是 PoseSearchDatabase", psd.get("class") == "PoseSearchDatabase",
              str(psd.get("class")))
        check("读到数据库里的动画清单",
              (psd.get("indexed_props") or {}).get("DatabaseAnimationAssets", 0) > 0,
              str(psd.get("indexed_props")))

    print()
    print("=" * 72)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for item in FAIL:
            print("  - %s" % item)
    print("=" * 72)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
