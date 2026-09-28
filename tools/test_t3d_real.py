# -*- coding: utf-8 -*-
r"""
ComboMCP / 真实 T3D 回归测试
============================

夹具 ``fixtures/real_bp_ue57.t3d`` 是 **UE 5.7 真实导出**的蓝图 T3D，由
``UnrealEditor-Cmd -run=PythonScript`` 在一个一次性测试工程里生成：

    建一个 Actor 蓝图 → 加一个函数图 ProbeFunc → 加成员变量 ProbeInt /
    ProbeMontage → 保存 → 用 ObjectExporterT3D 导出

用真数据而不是合成样本，是因为真实导出暴露了合成样本里根本看不出来的两个坑：

1. **导出是两遍的**：第一遍写 ``Class=`` + ``ExportPath=`` 但没有属性；
   第二遍用 ``Begin Object Name="..." ExportPath="..."``（**没有 Class=**）
   重新打开同一对象写属性与引脚。解析器必须按 ``ExportPath`` 合并两遍，
   否则第二遍的对象会因为缺 Class 而变成类名未知的野对象。
2. **对象引用路径的真实写法**：``MemberParent="/Script/CoreUObject.Class'/Script/Engine.Actor'"``
   —— 外层带包名后缀、内层才是真正的对象路径。

运行：
  & "...\python.exe" tools\test_t3d_real.py
"""

import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC = os.path.join(ROOT, "ue")
FIXTURE = os.path.join(HERE, "fixtures", "real_bp_ue57.t3d")

pkg = types.ModuleType("combomcp")
pkg.__path__ = [UE_SRC]
sys.modules["combomcp"] = pkg
for name in ("graph_ir", "t3d"):
    mod = types.ModuleType("combomcp." + name)
    mod.__package__ = "combomcp"
    sys.modules["combomcp." + name] = mod
    with open(os.path.join(UE_SRC, name + ".py"), "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), "<combomcp/%s>" % name, "exec"), mod.__dict__)
    setattr(pkg, name, mod)

from combomcp import graph_ir, t3d  # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


def main():
    print("=" * 70)
    print("真实 T3D 回归测试（夹具来自 UE 5.7 实机导出）")
    print("=" * 70)

    if not os.path.isfile(FIXTURE):
        print("[FAIL] 找不到夹具：%s" % FIXTURE)
        return 1

    with open(FIXTURE, "rb") as handle:
        raw_bytes = handle.read()
    # 刻意用普通 utf-8 读：这样能同时验证解析器对 BOM 的容错
    with open(FIXTURE, "r", encoding="utf-8", errors="replace") as handle:
        REAL = handle.read()
    print("夹具: %s (%d 字节 / %d 字符)" % (
        os.path.basename(FIXTURE), len(raw_bytes), len(REAL)))

    # ---------------------------------------------------------- 0. BOM
    print("\n[0] BOM 处理（UE 把 T3D 写成 UTF-8 with BOM）")
    check("夹具确实带 UTF-8 BOM",
          raw_bytes[:3] == b"\xef\xbb\xbf", raw_bytes[:3].hex(" "))
    check("用普通 utf-8 读进来时首字符是 BOM",
          REAL[0] == "\ufeff", repr(REAL[:12]))
    check("Python 的 \\s 不匹配 BOM（这正是坑所在）",
          re.match(r"^\s*Begin Object", REAL) is None,
          "若这里匹配上了，说明前提变了")

    # ---------------------------------------------------------- 1. 两遍合并
    print("\n[1] 两遍导出结构与合并")
    check("夹具里确实有两种 Begin Object 写法",
          "Begin Object Class=" in REAL and re.search(r'Begin Object Name="', REAL) is not None)
    # 计数要在剥掉 BOM 之后做 —— 首行带 \ufeff 时 ^\s* 匹配不上，
    # 那正是解析器必须处理的坑，不该让测试的计数也踩进去。
    CLEAN = REAL[1:] if REAL[:1] == "\ufeff" else REAL
    without_class = len(re.findall(r'^\s*Begin Object Name="', CLEAN, re.M))
    with_class = len(re.findall(r'^\s*Begin Object Class=', CLEAN, re.M))
    check("第一遍对象都带 Class=（10 个）", with_class == 10,
          "got %d" % with_class)
    check("第二遍对象都不带 Class=（9 个）", without_class == 9,
          "got %d" % without_class)
    check("两遍合计 19 个 Begin Object", with_class + without_class == 19,
          "got %d" % (with_class + without_class))

    stats = {}
    roots = t3d.parse(REAL, stats=stats)
    check("BOM 被剥掉后解析出唯一的顶层对象", len(roots) == 1,
          "got %d" % len(roots))
    check("顶层是 Blueprint", roots and roots[0].cls == "Blueprint",
          roots[0].cls if roots else "<none>")
    check("合并后对象数 = 不同对象数（10）",
          stats.get("_objects") == 10,
          "objects=%s" % stats.get("_objects"))
    check("统计记录了第二遍合并次数（9）",
          stats.get("_merged_second_pass") == 9,
          "merged=%s" % stats.get("_merged_second_pass"))
    check("没有对象缺 Class（第二遍不该产生新对象）",
          stats.get("_objects_without_class", 0) == 0,
          "without_class=%s" % stats.get("_objects_without_class"))

    # ---------------------------------------------------------- 2. 类名解析
    print("\n[2] 类名解析（合并后不该有 '?'）")
    bp = roots[0]
    check("顶层名 BP_T3DProbe", bp.name == "BP_T3DProbe", bp.name)

    def walk(obj, out):
        out.append(obj)
        for child in obj.children:
            walk(child, out)

    all_objs = []
    walk(bp, all_objs)
    unknown = [o.name for o in all_objs if o.cls in ("?", "", None)]
    check("所有对象类名已知", not unknown, "unknown=%s" % unknown)

    graphs = [o for o in all_objs if o.cls == "EdGraph"]
    graph_names = sorted(g.name for g in graphs)
    check("解析出 4 个 EdGraph", len(graphs) == 4, "got %s" % graph_names)
    check("图名正确",
          graph_names == sorted(["UserConstructionScript", "EventGraph",
                                 "ExecuteUbergraph_BP_T3DProbe", "ProbeFunc"]),
          str(graph_names))

    # ---------------------------------------------------------- 3. 节点属性
    print("\n[3] 节点属性与语义")
    event_graph = next(g for g in graphs if g.name == "EventGraph")
    check("EventGraph 有 3 个节点", len(event_graph.children) == 3,
          "got %d" % len(event_graph.children))
    check("节点类名去 K2Node_ 前缀",
          all(n.cls == "Event" for n in event_graph.children),
          str([n.cls for n in event_graph.children]))

    e0 = event_graph.children[0]
    check("读到 NodeGuid", e0.props.get("NodeGuid") ==
          "45A7ADFA4C0E519C18F2D39E0EF2BC92",
          repr(e0.props.get("NodeGuid")))
    check("读到 NodeComment（含转义与中文）",
          "被禁用" in str(e0.props.get("NodeComment")),
          repr(e0.props.get("NodeComment"))[:80])

    sem = t3d.t3d_object_semantics(e0)
    check("EventReference 解析出事件名 ReceiveBeginPlay",
          sem.get("event") == "ReceiveBeginPlay", repr(sem))

    e2 = event_graph.children[2]
    check("第二个事件节点解析出 ReceiveTick",
          t3d.t3d_object_semantics(e2).get("event") == "ReceiveTick",
          repr(t3d.t3d_object_semantics(e2)))

    # ---------------------------------------------------------- 4. 引脚
    print("\n[4] 引脚解析")
    check("EventBeginPlay 有 2 个引脚", len(e0.pins) == 2,
          "got %d" % len(e0.pins))
    names = [p["attrs"].get("PinName") for p in e0.pins]
    check("引脚名正确", names == ["OutputDelegate", "then"], str(names))
    check("引脚方向都是 EGPD_Output",
          all(p["attrs"].get("Direction") == "EGPD_Output" for p in e0.pins))

    # 真实夹具里节点之间没有连线（Python 造不出连接），所以 LinkedTo 为空
    check("夹具本身无连线（预期）",
          all(not p["linked"] for p in e0.pins),
          "unexpected links")

    # ---------------------------------------------------------- 5. 引脚类型
    print("\n[5] 引脚类型渲染")
    ir_graphs = t3d.build_graphs(REAL, stats={})
    check("构建出 4 个 IR 图", len(ir_graphs) == 4, "got %d" % len(ir_graphs))
    ir_event = next(g for g in ir_graphs if g.name == "EventGraph")
    check("IR 事件图有 3 个节点", len(ir_event.nodes) == 3,
          "got %d" % len(ir_event.nodes))
    check("IR 识别出 3 个入口", len(ir_event.entries) == 3,
          "got %d" % len(ir_event.entries))

    type_map = {}
    for node in ir_event.nodes:
        for pin in node["pins"]:
            type_map[pin["name"]] = (pin["type"], pin.get("default"))

    check("exec 引脚类型为 exec", type_map.get("then", ("?",))[0] == "exec",
          repr(type_map.get("then")))
    check("delegate 引脚类型为 Delegate",
          type_map.get("OutputDelegate", ("?",))[0] == "Delegate",
          repr(type_map.get("OutputDelegate")))
    check("对象引脚用真实类名 Actor",
          type_map.get("OtherActor", ("?",))[0] == "Actor",
          repr(type_map.get("OtherActor")))
    check("float 引脚渲染为 float 且带默认值",
          type_map.get("DeltaSeconds", ("?",))[0] == "float"
          and type_map.get("DeltaSeconds", ("?", None))[1] == "0.0",
          repr(type_map.get("DeltaSeconds")))

    # ---------------------------------------------------------- 6. 函数图
    print("\n[6] 函数图与 FunctionEntry")
    ir_func = next(g for g in ir_graphs if g.name == "ProbeFunc")
    check("ProbeFunc 图被解析", ir_func is not None)
    check("ProbeFunc 有 1 个节点", len(ir_func.nodes) == 1,
          "got %d" % len(ir_func.nodes))
    if ir_func.nodes:
        check("该节点是 FunctionEntry",
              ir_func.nodes[0]["cls"] == "FunctionEntry",
              ir_func.nodes[0]["cls"])
        check("FunctionEntry 有 then 引脚",
              any(p["name"] == "then" for p in ir_func.nodes[0]["pins"]),
              str([p["name"] for p in ir_func.nodes[0]["pins"]]))

    # ---------------------------------------------------------- 7. UserConstructionScript
    print("\n[7] UserConstructionScript 的函数引用")
    ir_ucs = next(g for g in ir_graphs if g.name == "UserConstructionScript")
    if ir_ucs.nodes:
        sem_ucs = ir_ucs.nodes[0]["meta"]
        check("FunctionReference 解析出函数名 UserConstructionScript",
              sem_ucs.get("fn") == "UserConstructionScript", repr(sem_ucs))
        check("MemberParent 解析出真实类名 Actor（不是 Class）",
              sem_ucs.get("fn_class") == "Actor", repr(sem_ucs))

    # ---------------------------------------------------------- 8. 连线
    print("\n[8] 连线还原（真实格式 + 注入 LinkedTo）")
    # LinkedTo 的格式由引擎的 UEdGraphPin::ExportText_PinArray 决定：
    #   LinkedTo=(<拥有者节点名> <引脚GUID>,)
    # 注意是**引脚**的 GUID（PinId），不是节点的 NodeGuid —— 这里必须用真实的 PinId。
    target_pin_id = "CFDA6573472CEB6A1E35209BAC943E66"   # 夹具里 K2Node_Event_1 的 then 引脚
    check("目标 PinId 确实存在于夹具中", target_pin_id in CLEAN,
          "PinId not found in fixture")
    check("夹具里的 NodeGuid 与 PinId 是不同的东西",
          "174031434354210ADEAC6E8017A5B449" in CLEAN
          and target_pin_id != "174031434354210ADEAC6E8017A5B449")

    pattern = re.compile(r'(PinName="then",Direction="EGPD_Output"[^\n]*?),bOrphanedPin=False,\)')

    def inject(match):
        return "%s,bOrphanedPin=False,LinkedTo=(K2Node_Event_1 %s,),)" % (
            match.group(1), target_pin_id)

    # 注入必须限定在 EventGraph 之内：连线是**图内**关系（引擎按
    # pin->GetOwningNode()->GetOuter() 解析节点名），跨图注入是无效连线，
    # 解析器正确地判为无法还原。
    #
    # 另外注意：由于导出分两遍，Name="EventGraph" 在文件里出现两次——第一遍
    # 只有结构没有引脚，必须定位到**第二遍**那个带属性的块（它以
    # `Begin Object Name=`（无 Class=）开头）才不会注错地方。
    eg_marker = 'Begin Object Name="EventGraph"'
    eg_pos = CLEAN.find(eg_marker)
    check("能定位到第二遍的 EventGraph 块", eg_pos > 0, "pos=%d" % eg_pos)
    head, tail = CLEAN[:eg_pos], CLEAN[eg_pos:]
    tail, count = pattern.subn(inject, tail, count=1)
    linked_text = head + tail
    check("成功在 EventGraph 内注入一条 LinkedTo", count == 1, "count=%d" % count)

    linked_graphs = t3d.build_graphs(linked_text, stats={})
    linked_event = next(g for g in linked_graphs if g.name == "EventGraph")
    found_link = None
    for node in linked_event.nodes:
        for pin in node["pins"]:
            if pin.get("to"):
                found_link = pin
    check("注入的连线被解析出来", found_link is not None,
          "no link resolved")
    if found_link:
        check("连线指向正确的目标节点（K2Node_Event_1 = 下标 1）",
              found_link["to"][0][0] == 1,
              "to=%s" % found_link["to"])
        check("连线还原到正确的引脚名 then",
              found_link["to"][0][1] == "then",
              "to=%s" % found_link["to"])

    # 另外验证：跨图注入确实还原不出来（解析器按图隔离，这是正确行为）
    cross = pattern.sub(inject, CLEAN, count=1)
    cross_graphs = t3d.build_graphs(cross, stats={})
    cross_event = next(g for g in cross_graphs if g.name == "EventGraph")
    cross_links = [p for n in cross_event.nodes for p in n["pins"] if p.get("to")]
    check("跨图注入不会被误判成 EventGraph 内的连线",
          not cross_links, "unexpected cross-graph link")

    # ---------------------------------------------------------- 9. 端到端 DSL
    print("\n[9] 端到端：真实夹具渲染 DSL")
    dsl = graph_ir.render_graph_dsl(ir_event, max_flows=5)
    print("\n--- 真实夹具渲染出的 DSL ---")
    print(dsl)
    print("--- end ---")
    check("DSL 非空", bool(dsl.strip()))
    check("DSL 含三个事件入口",
          dsl.count("(event ") == 3, "count=%d" % dsl.count("(event "))
    check("DSL 含 ReceiveBeginPlay",
          "ReceiveBeginPlay" in dsl, dsl[:200])
    check("DSL 含 ReceiveTick", "ReceiveTick" in dsl, dsl[:400])

    # ---------------------------------------------------------- 10. 无引脚容忍
    print("\n[10] 退化输入容错")
    check("空文本不崩", t3d.parse("") == [])
    check("乱文本不崩", isinstance(t3d.parse("garbage\nmore garbage"), list))
    half = REAL[:len(REAL) // 2]
    check("被截断的 T3D 不崩", isinstance(t3d.parse(half), list))

    print()
    print("=" * 70)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for item in FAIL:
            print("  - %s" % item)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
