# -*- coding: utf-8 -*-
r"""
ComboMCP / T3D 解析器单元测试
=============================

T3D 解析与 IR 渲染都是**纯 Python、不依赖 unreal**的，所以可以脱离编辑器直接测。
这份测试用一段仿真的蓝图 T3D（形状照 UE 的 ExportText 写法）验证：

* 对象树的嵌套关系
* 引脚的方向、类型、默认值
* ``LinkedTo`` 连线解析（含跨行引脚）
* IR 转换、执行流遍历、DSL 渲染

运行：
  & "E:\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" tools\test_t3d.py
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC = os.path.join(ROOT, "ue")

# 以包的形式装载 ue/ 下的模块（与 bridge 送入编辑器的装载方式一致）
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


def pin_forms(stats):
    """只取引脚形态计数。

    ``parse`` 往同一个 dict 里还写了以 ``_`` 开头的记账键（对象数、合并次数等），
    这里过滤掉，避免把记账当成引脚形态。
    """
    return dict((k, v) for k, v in stats.items() if not k.startswith("_"))


# 一段仿真 T3D：一个事件图，含 Event -> Branch -> 两个 CallFunction，
# 覆盖 exec 连线、数据连线、字面量默认值、以及一个跨行引脚。
SAMPLE = r'''
Begin Object Class=/Script/Engine.Blueprint Name="AC_Combat" ExportPath="/Game/Combo_Demo/Component/AC_Combat.AC_Combat"
   Begin Object Class=/Script/Engine.EdGraph Name="EventGraph" ExportPath="/Game/Combo_Demo/Component/AC_Combat.AC_Combat:EventGraph"
      Begin Object Class=/Script/BlueprintGraph.K2Node_Event Name="K2Node_Event_0" ExportPath="/Game/x.K2Node_Event_0"
         EventReference=(MemberParent=Class'"/Script/Engine.Actor"',MemberName="ReceiveBeginPlay")
         NodePosX=-48
         NodePosY=-32
         CustomProperties Pin (PinId=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,PinName="OutputDelegate",Direction="EGPD_Output",PinType.PinCategory="delegate",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(),)
         CustomProperties Pin (PinId=BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_IfThenElse_1 CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC,),)
      End Object
      Begin Object Class=/Script/BlueprintGraph.K2Node_IfThenElse Name="K2Node_IfThenElse_1" ExportPath="/Game/x.K2Node_IfThenElse_1"
         NodePosX=224
         NodePosY=0
         NodeComment="连招入口判断"
         CustomProperties Pin (PinId=CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC,PinName="execute",Direction="EGPD_Input",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_Event_0 BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB,),)
         CustomProperties Pin (PinId=DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD,PinName="Condition",Direction="EGPD_Input",PinType.PinCategory="bool",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="true",LinkedTo=(K2Node_VariableGet_2 EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE,),)
         CustomProperties Pin (PinId=FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_CallFunction_3 11111111111111111111111111111111,),)
         CustomProperties Pin (PinId=22222222222222222222222222222222,PinName="else",Direction="EGPD_Output",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_CallFunction_4 33333333333333333333333333333333,),)
      End Object
      Begin Object Class=/Script/BlueprintGraph.K2Node_VariableGet Name="K2Node_VariableGet_2" ExportPath="/Game/x.K2Node_VariableGet_2"
         VariableReference=(MemberName="bIsAttacking",MemberGuid=44444444444444444444444444444444,bSelfContext=True)
         NodePosX=32
         NodePosY=96
         CustomProperties Pin (PinId=EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE,PinName="bIsAttacking",Direction="EGPD_Output",PinType.PinCategory="bool",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_IfThenElse_1 DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD,),)
      End Object
      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_3" ExportPath="/Game/x.K2Node_CallFunction_3"
         FunctionReference=(MemberParent=Class'"/Script/Engine.AnimInstance"',MemberName="Montage_JumpToSection")
         NodePosX=448
         NodePosY=-64
         CustomProperties Pin (PinId=11111111111111111111111111111111,PinName="execute",Direction="EGPD_Input",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_IfThenElse_1 FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF,),)
         CustomProperties Pin (PinId=55555555555555555555555555555555,PinName="self",Direction="EGPD_Input",PinType.PinCategory="object",PinType.PinSubCategory="self",PinType.PinSubCategoryObject=Class'"/Script/Engine.AnimInstance"',PinType.ContainerType=None,DefaultValue="",LinkedTo=(),)
         CustomProperties Pin (PinId=66666666666666666666666666666666,PinName="SectionName",Direction="EGPD_Input",PinType.PinCategory="name",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="Combo02",LinkedTo=(),)
         CustomProperties Pin (PinId=77777777777777777777777777777777,PinName="then",Direction="EGPD_Output",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(),)
      End Object
      Begin Object Class=/Script/BlueprintGraph.K2Node_CallFunction Name="K2Node_CallFunction_4" ExportPath="/Game/x.K2Node_CallFunction_4"
         FunctionReference=(MemberParent=Class'"/Script/Engine.AnimInstance"',MemberName="Montage_Play")
         NodePosX=448
         NodePosY=96
         CustomProperties Pin (PinId=33333333333333333333333333333333,PinName="execute",Direction="EGPD_Input",PinType.PinCategory="exec",PinType.PinSubCategory="",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="",LinkedTo=(K2Node_IfThenElse_1 22222222222222222222222222222222,),)
         CustomProperties Pin (PinId=88888888888888888888888888888888,PinName="MontageToPlay",Direction="EGPD_Input",PinType.PinCategory="object",PinType.PinSubCategory="",PinType.PinSubCategoryObject=Class'"/Script/Engine.AnimMontage"',PinType.ContainerType=None,DefaultValue="",DefaultObject=AnimMontage'"/Game/Combo_Demo/Animation/Montage/AS_Combo01_01.AS_Combo01_01"',LinkedTo=(),)
         CustomProperties Pin (PinId=99999999999999999999999999999999,PinName="InPlayRate",Direction="EGPD_Input",PinType.PinCategory="real",PinType.PinSubCategory="float",PinType.PinSubCategoryObject=None,PinType.ContainerType=None,DefaultValue="1.000000",LinkedTo=(),)
      End Object
   End Object
End Object
'''


def main():
    print("=" * 66)
    print("T3D 解析器单元测试")
    print("=" * 66)

    # ---------------------------------------------------------- 1. 对象树
    print("\n[1] 对象树解析")
    roots = t3d.parse(SAMPLE)
    check("解析出顶层对象", len(roots) == 1, "got %d" % len(roots))
    bp = roots[0]
    check("顶层是 Blueprint", bp.cls == "Blueprint", bp.cls)
    check("蓝图名正确", bp.name == "AC_Combat", bp.name)
    check("蓝图有一个子对象(EventGraph)", len(bp.children) == 1,
          "got %d" % len(bp.children))
    graph = bp.children[0]
    check("子对象是 EdGraph", graph.cls == "EdGraph", graph.cls)
    check("图名 EventGraph", graph.name == "EventGraph", graph.name)
    check("图含 5 个节点", len(graph.children) == 5,
          "got %d" % len(graph.children))

    # ---------------------------------------------------------- 2. 属性
    print("\n[2] 节点属性")
    event = graph.children[0]
    check("事件节点类名去前缀", event.cls == "Event", event.cls)
    check("NodePosX 解析", event.props.get("NodePosX") == "-48",
          repr(event.props.get("NodePosX")))
    branch = graph.children[1]
    check("分支节点 NodeComment 解析",
          branch.props.get("NodeComment") == "连招入口判断",
          repr(branch.props.get("NodeComment")))
    call4 = graph.children[4]
    check("函数引用 MemberName 解析",
          t3d.t3d_object_semantics(call4).get("fn") == "Montage_Play",
          repr(t3d.t3d_object_semantics(call4)))
    check("函数引用 MemberParent 解析出类名",
          t3d.t3d_object_semantics(call4).get("fn_class") == "AnimInstance",
          repr(t3d.t3d_object_semantics(call4)))

    # ---------------------------------------------------------- 3. 引脚
    print("\n[3] 引脚解析")
    check("事件节点有 2 个引脚", len(event.pins) == 2, "got %d" % len(event.pins))
    then_pin = None
    for pin in event.pins:
        if pin["attrs"].get("PinName") == "then":
            then_pin = pin
    check("找到 then 引脚", then_pin is not None)
    if then_pin:
        check("then 方向为 EGPD_Output",
              then_pin["attrs"].get("Direction") == "EGPD_Output",
              then_pin["attrs"].get("Direction"))
        check("then 的 LinkedTo 解析出 1 条",
              len(then_pin["linked"]) == 1, repr(then_pin["linked"]))
        check("then 连到 K2Node_IfThenElse_1",
              then_pin["linked"][0][0] == "K2Node_IfThenElse_1",
              repr(then_pin["linked"]))
    cond_pin = None
    for pin in branch.pins:
        if pin["attrs"].get("PinName") == "Condition":
            cond_pin = pin
    check("bool 引脚默认值 true",
          cond_pin and cond_pin["attrs"].get("DefaultValue") == "true",
          repr(cond_pin and cond_pin["attrs"].get("DefaultValue")))

    # ---------------------------------------------------------- 4. IR
    print("\n[4] IR 构建与连线还原")
    graphs = t3d.build_graphs(SAMPLE)
    check("构建出 1 个 IR 图", len(graphs) == 1, "got %d" % len(graphs))
    ir = graphs[0]
    check("IR 图名", ir.name == "EventGraph", ir.name)
    check("IR 识别为事件图", ir.kind == "event", ir.kind)
    check("IR 含 5 个节点", len(ir.nodes) == 5, "got %d" % len(ir.nodes))
    check("入口节点唯一且是 Event", len(ir.entries) == 1 and
          ir.nodes[ir.entries[0]]["cls"] == "Event",
          repr(ir.entries))

    event_id = ir.entries[0]
    outs = ir.exec_out(event_id)
    check("Event 的 exec 输出有 1 条", len(outs) == 1, repr(outs))
    if outs:
        check("exec 输出连到分支节点",
              ir.nodes[outs[0][1]]["cls"] == "IfThenElse",
              ir.nodes[outs[0][1]]["cls"])

    branch_id = outs[0][1] if outs else None
    if branch_id is not None:
        src = ir.data_source(branch_id, "Condition")
        check("Condition 的数据来源被还原",
              src is not None and ir.nodes[src[0]]["meta"].get("var") == "bIsAttacking",
              repr(src))
        bout = dict((n, t) for n, t, _ in ir.exec_out(branch_id))
        check("分支有 then 与 else 两条出口",
              "then" in bout and "else" in bout, repr(bout))
        if "then" in bout:
            check("then 出口是 Montage_JumpToSection",
                  ir.nodes[bout["then"]]["meta"].get("fn") == "Montage_JumpToSection",
                  repr(ir.nodes[bout["then"]]["meta"]))
        if "else" in bout:
            check("else 出口是 Montage_Play",
                  ir.nodes[bout["else"]]["meta"].get("fn") == "Montage_Play",
                  repr(ir.nodes[bout["else"]]["meta"]))

    # 引脚类型渲染
    print("\n[5] 引脚类型渲染")
    for node in ir.nodes:
        for pin in node["pins"]:
            if pin["name"] == "Condition":
                check("bool 引脚类型渲染为 bool", pin["type"] == "bool", pin["type"])
            if pin["name"] == "SectionName":
                check("Name 引脚类型渲染为 Name", pin["type"] == "Name", pin["type"])
            if pin["name"] == "InPlayRate":
                check("float 引脚渲染", pin["type"] == "float", pin["type"])
            if pin["name"] == "MontageToPlay":
                check("对象引脚用真实类名",
                      pin["type"] == "AnimMontage", pin["type"])
                check("DefaultObject 被解析成资产名",
                      pin.get("default_obj") == "AS_Combo01_01",
                      repr(pin.get("default_obj")))

    # ---------------------------------------------------------- 6. 执行流
    print("\n[6] 执行流遍历与 DSL 渲染")
    budget = [400]
    visited = set()
    body = []
    for _, target, _ in ir.exec_out(event_id):
        body.extend(graph_ir.walk_exec(ir, target, budget, visited))
    check("执行流走出一条语句", len(body) >= 1, "got %d" % len(body))
    if body:
        first = body[0]
        check("第一条是分支语句", "then" in first or "else" in first, repr(first))
        if "then" in first and "else" in first:
            check("then 分支有内容", len(first["then"]) >= 1)
            check("else 分支有内容", len(first["else"]) >= 1)

    dsl = graph_ir.render_graph_dsl(ir)
    print("\n--- 渲染出的 DSL ---")
    print(dsl)
    print("--- end ---")
    check("DSL 含 event 头", "(event ReceiveBeginPlay" in dsl, dsl[:200])
    check("DSL 含 if 结构", "(if " in dsl, dsl[:400])
    check("DSL 含 :then 与 :else", ":then" in dsl and ":else" in dsl, dsl[:400])
    check("DSL 含函数名", "Montage_Play" in dsl, dsl[:600])
    check("DSL 含字面量 Section 名", '"Combo02"' in dsl, dsl[:600])
    check("DSL 含变量读取", "bIsAttacking" in dsl, dsl[:400])

    # ---------------------------------------------------------- 7. 可达性
    print("\n[7] 可达性分析")
    reachable, orphans = graph_ir.reachable_nodes(ir)
    check("所有节点可达", len(orphans) == 0, "orphans=%s" % orphans)

    # ---------------------------------------------------------- 8. 引脚形态
    # 引擎把引脚当作 tagged property 写出来时用的是 CustomProperties Pin (...)，
    # 但不同版本可能写成普通属性数组。解析器要能兼容并如实报告命中的形态。
    print("\n[8] 引脚形态兼容性与形态上报")

    stats = {}
    t3d.build_graphs(SAMPLE, stats=stats)
    forms = pin_forms(stats)
    check("主形态被识别为 custom_properties",
          forms.get("custom_properties", 0) > 0, repr(stats))
    # 样本里的引脚数：Event 2 + Branch 4 + VariableGet 1 + Call 4 + Call 3 = 14
    total_pins = sum(forms.values())
    check("统计到的引脚数与样本一致 (14)", total_pins == 14, repr(stats))

    # 变体一：裸 Pin (...) 前缀
    bare = SAMPLE.replace("CustomProperties Pin (", "Pin (")
    stats_bare = {}
    graphs_bare = t3d.build_graphs(bare, stats=stats_bare)
    check("裸 Pin 形态也能解析出图", len(graphs_bare) == 1, "got %d" % len(graphs_bare))
    check("裸 Pin 形态被识别为 bare_pin",
          pin_forms(stats_bare).get("bare_pin", 0) > 0, repr(stats_bare))
    if graphs_bare:
        ir_bare = graphs_bare[0]
        check("裸 Pin 形态节点数一致", len(ir_bare.nodes) == 5,
              "got %d" % len(ir_bare.nodes))
        check("裸 Pin 形态入口一致",
              len(ir_bare.entries) == 1, repr(ir_bare.entries))
        if ir_bare.entries:
            outs_bare = ir_bare.exec_out(ir_bare.entries[0])
            check("裸 Pin 形态连线也能还原", len(outs_bare) == 1, repr(outs_bare))

    # 变体二：属性数组形式 Pins(0)=(...)
    indexed = SAMPLE.replace("CustomProperties Pin (", "Pins(0)=(")
    stats_idx = {}
    graphs_idx = t3d.build_graphs(indexed, stats=stats_idx)
    check("Pins(N)= 形态也能解析出图", len(graphs_idx) == 1,
          "got %d" % len(graphs_idx))
    check("Pins(N)= 形态被识别为 pins_indexed",
          pin_forms(stats_idx).get("pins_indexed", 0) > 0, repr(stats_idx))

    # 变体三：完全不含引脚形态时，必须如实报告而不是假装成功
    stats_none = {}
    no_pins = "\n".join(l for l in SAMPLE.splitlines()
                        if "CustomProperties Pin" not in l)
    graphs_none = t3d.build_graphs(no_pins, stats=stats_none)
    check("无引脚时形态统计为空", len(pin_forms(stats_none)) == 0, repr(stats_none))
    if graphs_none:
        check("无引脚时节点仍在（只是没有引脚）",
              len(graphs_none[0].nodes) == 5,
              "got %d" % len(graphs_none[0].nodes))
        check("无引脚时所有节点引脚为空",
              all(not n["pins"] for n in graphs_none[0].nodes))

    # ---------------------------------------------------------- 9. 跨行引脚
    print("\n[9] 跨行引脚")
    wrapped = SAMPLE.replace(
        'CustomProperties Pin (PinId=66666666666666666666666666666666,'
        'PinName="SectionName",',
        'CustomProperties Pin (PinId=66666666666666666666666666666666,\n'
        '            PinName="SectionName",\n            ')
    stats_wrap = {}
    graphs_wrap = t3d.build_graphs(wrapped, stats=stats_wrap)
    check("跨行引脚仍能解析", len(graphs_wrap) == 1, "got %d" % len(graphs_wrap))
    if graphs_wrap:
        found = None
        for node in graphs_wrap[0].nodes:
            for pin in node["pins"]:
                if pin["name"] == "SectionName":
                    found = pin
        check("跨行后仍解析出 SectionName 引脚", found is not None)
        if found:
            check("跨行后默认值仍正确", found.get("default") == "Combo02",
                  repr(found.get("default")))

    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for item in FAIL:
            print("  - %s" % item)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
