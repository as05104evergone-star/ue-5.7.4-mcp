# -*- coding: utf-8 -*-
r"""
ComboMCP / UE Python API 能力自省探针
=====================================

这是一次**决定性的实验**，不是猜测。

背景：UE 官方 Python 文档显示 ``unreal.EdGraphNode`` 只有构造函数、没有任何属性；
引擎源码里 ``UEdGraphNode::Pins`` 也确实没有 ``UPROPERTY()`` 宏（而 ``UEdGraph::Nodes`` 有）。
同时有一份公开的一手记录称 ``UBlueprint::NewVariables`` 虽然能调用
``get_editor_property`` 但"returns nothing, the property is protected"。

如果这些都是真的，那么"读蓝图图逻辑"这条路必须改走 T3D 文本导出，
而不是直接反射。这份探针把两种路线一次性测清楚：

  A. 直接反射：UBlueprint / UEdGraph / UEdGraphNode / UEdGraphPin 各能读到什么
  B. T3D 导出：能否导出、导出文本里是否含引脚与连线
  C. 其它候选 API：JsonObjectGraphFunctionLibrary / SubobjectDataSubsystem 是否存在

产出直接决定 ``ue/`` 下的读取器怎么写。

运行：
  & "...\python.exe" tools\probe_api.py [等待秒数]
"""

import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

ENGINE_CANDIDATES = [r"E:\UE_5.7", r"E:\UE_5.6\UE_5.6", r"D:\UE_5.7"]
REMOTE_EXEC_REL = os.path.join(
    "Engine", "Plugins", "Experimental", "PythonScriptPlugin",
    "Content", "Python", "remote_execution.py")

BP_PATH = "/Game/Combo_Demo/Component/AC_Combat"
MONTAGE_PATH = "/Game/Combo_Demo/Animation/Montage/AS_Combo01_01"


# ============================================================== UE 内探针代码

PROBE = r'''
import unreal, json

R = {"probe_version": 1}

# ---------------------------------------------------------------- A. 直接反射

bp = unreal.load_asset("__BP__")
R["A.load"] = {"ok": bp is not None, "class": bp.get_class().get_name() if bp else None}

BP_PROPS = ["UbergraphPages", "FunctionGraphs", "MacroGraphs", "NewVariables",
            "SimpleConstructionScript", "ImplementedInterfaces", "ParentClass",
            "GeneratedClass", "BlueprintType", "Status"]
R["A.bp_properties"] = {}
for prop in BP_PROPS:
    entry = {}
    try:
        value = bp.get_editor_property(prop)
        entry["ok"] = True
        entry["py_type"] = type(value).__name__
        try:
            entry["len"] = len(value)
        except Exception:
            entry["repr"] = str(value)[:120]
    except Exception as exc:
        entry["ok"] = False
        entry["err"] = str(exc)[:160]
    R["A.bp_properties"][prop] = entry

# 属性访问（snake_case）是否也通
R["A.bp_attr_access"] = {}
for name in ["ubergraph_pages", "function_graphs", "new_variables"]:
    try:
        v = getattr(bp, name)
        R["A.bp_attr_access"][name] = {"ok": True, "len": len(v) if hasattr(v, "__len__") else None}
    except Exception as exc:
        R["A.bp_attr_access"][name] = {"ok": False, "err": str(exc)[:120]}

R["A.dir_bp"] = sorted([a for a in dir(bp) if not a.startswith("_")])[:120]

# 图与节点
try:
    graphs = bp.get_editor_property("UbergraphPages")
    g = graphs[0]
    R["A.graph_class"] = g.get_class().get_name()
    nodes = g.get_editor_property("Nodes")
    R["A.nodes_len"] = len(nodes)

    # 找一个有执行引脚的节点
    sample = None
    for n in nodes:
        if n.get_class().get_name() in ("K2Node_CallFunction", "K2Node_Event",
                                        "K2Node_VariableGet", "K2Node_IfThenElse"):
            sample = n
            break
    if sample is None and nodes:
        sample = nodes[0]

    R["A.node_class"] = sample.get_class().get_name()
    R["A.node_properties"] = {}
    for prop in ["Pins", "NodePosX", "NodePosY", "NodeComment", "NodeGuid",
                 "FunctionReference", "VariableReference", "EventReference"]:
        entry = {}
        try:
            v = sample.get_editor_property(prop)
            entry["ok"] = True
            entry["py_type"] = type(v).__name__
            try:
                entry["len"] = len(v)
            except Exception:
                entry["repr"] = str(v)[:110]
        except Exception as exc:
            entry["ok"] = False
            entry["err"] = str(exc)[:140]
        R["A.node_properties"][prop] = entry

    # 属性访问方式
    R["A.node_attr_pins"] = {}
    for name in ["pins", "get_pins", "get_all_pins"]:
        try:
            v = getattr(sample, name)
            if callable(v):
                try:
                    res = v()
                    R["A.node_attr_pins"][name] = {"ok": True, "callable": True,
                                                   "len": len(res) if hasattr(res, "__len__") else None}
                except Exception as exc:
                    R["A.node_attr_pins"][name] = {"ok": False, "callable": True, "err": str(exc)[:120]}
            else:
                R["A.node_attr_pins"][name] = {"ok": True, "callable": False,
                                               "len": len(v) if hasattr(v, "__len__") else None}
        except Exception as exc:
            R["A.node_attr_pins"][name] = {"ok": False, "err": str(exc)[:120]}

    R["A.dir_node"] = sorted([a for a in dir(sample) if not a.startswith("_")])[:140]
except Exception as exc:
    R["A.graph_error"] = str(exc)[:300]

# 引脚对象本身能读到什么（如果拿得到）
try:
    pins = None
    try:
        pins = sample.get_editor_property("Pins")
    except Exception:
        pass
    if pins is None:
        try:
            pins = sample.pins
        except Exception:
            pass
    if pins:
        p = pins[0]
        R["A.pin_class"] = p.get_class().get_name()
        R["A.pin_properties"] = {}
        for prop in ["PinName", "Direction", "PinType", "DefaultValue",
                     "DefaultObject", "LinkedTo", "bHidden"]:
            entry = {}
            try:
                v = p.get_editor_property(prop)
                entry["ok"] = True
                entry["py_type"] = type(v).__name__
                try:
                    entry["len"] = len(v)
                except Exception:
                    entry["repr"] = str(v)[:110]
            except Exception as exc:
                entry["ok"] = False
                entry["err"] = str(exc)[:140]
            R["A.pin_properties"][prop] = entry
    else:
        R["A.pin_properties"] = "no pins obtainable"
except Exception as exc:
    R["A.pin_error"] = str(exc)[:300]

# ---------------------------------------------------------------- B. T3D 导出

R["B.t3d"] = {}
exporters = ["ObjectExporterT3D", "TextBufferExporterTXT", "ObjectExporter",
             "Exporter", "AnimSequenceExporterT3D", "SequenceExporterT3D"]
R["B.exporter_classes_available"] = {}
for name in exporters:
    R["B.exporter_classes_available"][name] = hasattr(unreal, name)

out_file = "E:/Unreal Projects/Animation_Sample/Plugins/ComboMCP/cache/probe_ac_combat.t3d"
try:
    task = unreal.AssetExportTask()
    task.set_editor_property("object", bp)
    task.set_editor_property("filename", out_file)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_identical", True)
    task.set_editor_property("prompt", False)
    task.set_editor_property("use_file_archive", True)
    if hasattr(unreal, "ObjectExporterT3D"):
        task.set_editor_property("exporter", unreal.ObjectExporterT3D())
        R["B.t3d"]["exporter_used"] = "ObjectExporterT3D"
    elif hasattr(unreal, "TextBufferExporterTXT"):
        task.set_editor_property("exporter", unreal.TextBufferExporterTXT())
        R["B.t3d"]["exporter_used"] = "TextBufferExporterTXT"
    ok = unreal.Exporter.run_asset_export_task(task)
    R["B.t3d"]["run_asset_export_task"] = bool(ok)
    errs = None
    for prop in ("errors", "Errors"):
        try:
            errs = task.get_editor_property(prop)
            break
        except Exception:
            continue
    if errs:
        R["B.t3d"]["task_errors"] = [str(e)[:200] for e in errs][:8]
except Exception as exc:
    R["B.t3d"]["run_asset_export_task"] = "ERR: %s" % str(exc)[:220]

try:
    if unreal.Paths.file_exists(out_file):
        with open(out_file, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        R["B.t3d"]["file_bytes"] = len(text)
        R["B.t3d"]["has_pin_marker"] = "CustomProperties Pin" in text
        R["B.t3d"]["pin_marker_count"] = text.count("CustomProperties Pin")
        R["B.t3d"]["has_begin_object"] = "Begin Object" in text
        R["B.t3d"]["begin_object_count"] = text.count("Begin Object")
        R["B.t3d"]["has_linkedto"] = "LinkedTo=" in text
        R["B.t3d"]["head_1200"] = text[:1200]
        # 抓一段含 Pin 的样本
        idx = text.find("CustomProperties Pin")
        if idx >= 0:
            R["B.t3d"]["pin_sample"] = text[idx:idx + 700]
    else:
        R["B.t3d"]["file_exists"] = False
except Exception as exc:
    R["B.t3d"]["read_error"] = str(exc)[:220]

# ---------------------------------------------------------------- C. 其它候选 API

R["C.candidates"] = {}
for name in ["JsonObjectGraphFunctionLibrary", "JsonBlueprintFunctionLibrary",
             "SubobjectDataSubsystem", "BlueprintEditorLibrary",
             "KismetEditorUtilities", "EditorAssetLibrary", "AnimationLibrary",
             "AnimMontageLibrary", "EditorUtilityLibrary", "AssetTools",
             "AssetToolsHelpers"]:
    R["C.candidates"][name] = hasattr(unreal, name)

# BPVariableMetaDataEntry 是否存在（变量元数据路径）
R["C.has_BPVariableMetaDataEntry"] = hasattr(unreal, "BPVariableMetaDataEntry")

# SubobjectDataSubsystem 能否读组件
try:
    if hasattr(unreal, "SubobjectDataSubsystem"):
        sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        if sub:
            handles = sub.k2_gather_subobject_data_for_blueprint(bp)
            R["C.subobject_count"] = len(handles) if handles else 0
except Exception as exc:
    R["C.subobject_error"] = str(exc)[:200]

# ---------------------------------------------------------------- D. Montage

try:
    m = unreal.load_asset("__MONTAGE__")
    R["D.montage_loaded"] = m is not None
    R["D.montage_class"] = m.get_class().get_name() if m else None
    if m:
        R["D.montage_properties"] = {}
        for prop in ["SequenceLength", "CompositeSections", "SlotAnimTracks",
                     "Notifies", "AnimNotifyTracks", "RateScale"]:
            entry = {}
            try:
                v = m.get_editor_property(prop)
                entry["ok"] = True
                try:
                    entry["len"] = len(v)
                except Exception:
                    entry["repr"] = str(v)[:110]
            except Exception as exc:
                entry["ok"] = False
                entry["err"] = str(exc)[:140]
            R["D.montage_properties"][prop] = entry
except Exception as exc:
    R["D.montage_error"] = str(exc)[:250]

print("__PROBE_BEGIN__")
print(json.dumps(R, ensure_ascii=False, default=str))
print("__PROBE_END__")
'''


def find_engine():
    for candidate in ENGINE_CANDIDATES:
        if os.path.isfile(os.path.join(candidate, REMOTE_EXEC_REL)):
            return candidate
    raise RuntimeError("找不到引擎")


def load_remote_execution(engine):
    path = os.path.join(engine, REMOTE_EXEC_REL)
    spec = importlib.util.spec_from_file_location("probe_remote_execution", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_remote_execution"] = module
    spec.loader.exec_module(module)
    return module


def main():
    wait_seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 1800

    engine = find_engine()
    mod = load_remote_execution(engine)
    print("引擎: %s" % engine)
    print("等待编辑器（最多 %d 秒）..." % wait_seconds)

    session = mod.RemoteExecution(mod.RemoteExecutionConfig())
    session.start()

    started = time.time()
    node = None
    while time.time() - started < wait_seconds:
        nodes = session.remote_nodes
        if nodes:
            node = nodes[0]
            break
        elapsed = int(time.time() - started)
        if elapsed % 60 < 13:
            print("  [%4ds] 等待中..." % elapsed)
            sys.stdout.flush()
        time.sleep(13)

    if node is None:
        print("[FAIL] 编辑器通道未就绪")
        session.stop()
        return 1

    print("\n发现节点: project=%s engine=%s" % (node.get("project_name"),
                                                node.get("engine_version")))
    session.open_command_connection(node.get("node_id"))

    code = PROBE.replace("__BP__", BP_PATH).replace("__MONTAGE__", MONTAGE_PATH)
    result = session.run_command(code, unattended=True, exec_mode=mod.MODE_EXEC_FILE)

    print("\nsuccess=%s" % result.get("success"))
    output = result.get("output") or ""
    begin, end = "__PROBE_BEGIN__", "__PROBE_END__"
    i, j = output.find(begin), output.find(end)
    if i < 0 or j < 0:
        print("未找到探针标记。原始输出尾部：")
        print(output[-4000:])
        session.stop()
        return 2

    payload = json.loads(output[i + len(begin):j].strip())
    out_path = os.path.join(ROOT, "cache", "probe_result.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("完整结果已写入: %s" % out_path)
    print()
    print(json.dumps(payload, ensure_ascii=False, indent=1)[:12000])

    session.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
