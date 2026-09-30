# -*- coding: utf-8 -*-
r"""
ComboMCP / 接线测试
===================

不做真实查询，只验证"送进编辑器的那个包"本身是好的：

* ``ue/`` 下全部模块能按 :mod:`engine.bridge` 的顺序装载（相对导入能解析）
* ``dispatch`` 的命令表覆盖了工具层会调用的每一个命令
* bootstrap 源码能被编译成合法 Python（送进去之前就发现语法问题）
* 工具层能正常列出 15 个工具、且每个 handler 都有对应命令或本地实现

这些错误如果留到编辑器里才暴露，每轮排查都要等一次编辑器往返，代价很高。

运行：
  & "...\python.exe" tools\test_wiring.py
"""

import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import bridge as bridge_mod  # noqa: E402
from mcpserver import tools as tool_layer  # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


def load_ue_package():
    """按 bridge 的方式把 ue/ 装载成 combomcp 包。

    用假的 ``unreal`` 模块顶替，好让依赖 unreal 的模块（bp_read / anim_read /
    index / domain / t3d_read）也能被导入——它们的模块级代码只做 import，
    不调用任何引擎 API。
    """
    fake = types.ModuleType("unreal")

    class _Any(object):
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Any()

        def __call__(self, *a, **k):
            return _Any()

    def _missing(name):
        return _Any()

    fake.__getattr__ = _missing
    sys.modules["unreal"] = fake

    sources, fingerprint = bridge_mod._load_ue_sources()
    pkg = types.ModuleType("combomcp")
    pkg.__path__ = [bridge_mod.UE_SRC_DIR]
    pkg.__combomcp_ver__ = fingerprint
    sys.modules["combomcp"] = pkg

    for name in bridge_mod.MODULE_ORDER:
        mod = types.ModuleType("combomcp." + name)
        mod.__package__ = "combomcp"
        mod.__file__ = "<combomcp/%s>" % name
        sys.modules["combomcp." + name] = mod
        try:
            exec(compile(sources[name], "<combomcp/%s>" % name, "exec"), mod.__dict__)
        except Exception as exc:
            return None, "module %s failed to load: %s: %s" % (
                name, type(exc).__name__, exc), fingerprint
        setattr(pkg, name, mod)
    return pkg, None, fingerprint


# 工具层会调用的 UE 侧命令；由 mcpserver/tools.py 里的 _call(...) 产生
EXPECTED_COMMANDS = {
    "ping", "env_info",
    "project_overview", "list_assets", "search_assets",
    "find_references", "find_dependencies", "build_index",
    "class_summary", "graph_overview", "graph_dsl", "exec_flow",
    "node_detail", "node_reflect",
    "montage_detail", "montage_timeline_analysis",
    "notify_detail", "sequence_detail", "animblueprint_detail",
    "generic_asset_detail", "extract_animation_calls",
    "audit_blueprint_logic", "analyze_combo_system",
    # T3D 直通命令
    "t3d_graphs", "t3d_overview", "t3d_dsl", "t3d_node", "t3d_audit",
}


def main():
    print("=" * 66)
    print("ComboMCP 接线测试")
    print("=" * 66)

    # ---------------------------------------------------------- 1. 模块装载
    print("\n[1] ue/ 包按 bridge 的顺序装载")
    sources, fingerprint = bridge_mod._load_ue_sources()
    check("源码全部读取成功", len(sources) == len(bridge_mod.MODULE_ORDER),
          "got %d" % len(sources))
    check("模块顺序含 graph_ir 与 t3d",
          "graph_ir" in bridge_mod.MODULE_ORDER and "t3d" in bridge_mod.MODULE_ORDER,
          str(bridge_mod.MODULE_ORDER))
    check("graph_ir 排在 t3d 之前",
          bridge_mod.MODULE_ORDER.index("graph_ir") < bridge_mod.MODULE_ORDER.index("t3d"))
    check("t3d_read 排在 graph_ir/t3d/runtime 之后",
          all(bridge_mod.MODULE_ORDER.index("t3d_read") > bridge_mod.MODULE_ORDER.index(d)
              for d in ("graph_ir", "t3d", "runtime")))

    pkg, error, fingerprint = load_ue_package()
    check("ue/ 包整体装载成功", pkg is not None, error or "")

    if pkg is None:
        print("\n装载失败，后续检查跳过。")
        return 1

    # ---------------------------------------------------------- 2. 命令表
    print("\n[2] dispatch 命令表")
    commands = set(pkg.dispatch.COMMANDS.keys())
    missing = sorted(EXPECTED_COMMANDS - commands)
    check("覆盖工具层需要的全部命令", not missing, "missing=%s" % missing)

    extra = sorted(commands - EXPECTED_COMMANDS)
    if extra:
        print("       (额外注册的命令: %s)" % ", ".join(extra))

    # ---------------------------------------------------------- 3. 自动择路
    print("\n[3] 自动择路逻辑存在且签名正确")
    import inspect
    for fn_name in ("flow_auto", "overview_auto", "node_auto", "audit_auto"):
        fn = getattr(pkg.dispatch, fn_name, None)
        check("dispatch.%s 存在" % fn_name, callable(fn))
        if callable(fn):
            params = list(inspect.signature(fn).parameters)
            check("dispatch.%s 接受 source 参数" % fn_name, "source" in params,
                  str(params))

    # `graph_dsl` 命令要接受 format/source 两个关键参数
    fn = pkg.dispatch.COMMANDS.get("graph_dsl")
    if fn is not None:
        params = list(inspect.signature(fn).parameters)
        check("graph_dsl 接受 source 与 format",
              "source" in params and "format" in params, str(params))

    # T3D 导出器候选名：UE 5.7 里实际叫 UObjectExporterT3D（Python 名去掉 U 前缀）
    candidates = getattr(pkg.t3d_read, "_EXPORTER_CANDIDATES", ())
    check("T3D 导出器候选首个是 ObjectExporterT3D",
          candidates and candidates[0] == "ObjectExporterT3D", str(candidates))
    check("T3D 导出器候选非空", len(candidates) >= 1, str(candidates))

    # 缓存目录必须优先取包上注入的真实路径。
    # 这里查源码文本而不是 inspect.getsource——模块是从字符串 exec 装载的，没有真实文件。
    t3d_read_src = sources.get("t3d_read", "")
    check("t3d_read 优先使用注入的缓存目录",
          "__cache_dir__" in t3d_read_src and "sys.modules.get" in t3d_read_src,
          "缺少 __cache_dir__ 优先逻辑")

    # ---------------------------------------------------------- 4. bootstrap
    print("\n[4] bootstrap 源码可编译")
    code = bridge_mod.build_bootstrap(
        sources, fingerprint, "ping", {"asset_path": "/Game/X"}, save_to=None)
    try:
        compile(code, "<bootstrap>", "exec")
        check("bootstrap 是合法 Python", True)
    except Exception as exc:
        check("bootstrap 是合法 Python", False, str(exc))

    for token in ("combomcp", "__COMBOMCP_BEGIN__", "__COMBOMCP_END__"):
        check("bootstrap 含 %s" % token, token in code)
    check("bootstrap 内嵌了模块顺序",
          repr(list(bridge_mod.MODULE_ORDER)) in code,
          "模块顺序未内嵌")
    for name in bridge_mod.MODULE_ORDER:
        check("bootstrap 内嵌 %s 源码" % name, name in sources)

    # 缓存目录必须由 bootstrap 注入：模块是被 exec 送进去的，__file__ 是假路径，
    # 任何按 __file__ 推断目录的代码都会落到编辑器工作目录下。
    check("bootstrap 注入真实缓存目录", "__cache_dir__" in code and
          repr(bridge_mod.CACHE_DIR) in code,
          "缺少 __cache_dir__ 注入")
    try:
        ns = {}
        exec(compile("_CACHE_DIR = %r" % bridge_mod.CACHE_DIR, "<t>", "exec"), ns)
        check("注入的缓存目录是绝对路径",
              os.path.isabs(ns["_CACHE_DIR"]), ns["_CACHE_DIR"])
    except Exception as exc:
        check("注入的缓存目录是绝对路径", False, str(exc))

    # ---------------------------------------------------------- 5. 工具层
    print("\n[5] 工具层")
    listed = tool_layer.list_tools()
    # 工具定义会进入每一次模型请求，所以数量本身是被刻意约束的——加工具时必须改这里，
    # 强制作者重新想一遍"这个工具值得占用每次请求的 token 吗"。
    check("列出 17 个工具", len(listed) == 17, "got %d" % len(listed))
    names = [t["name"] for t in listed]
    check("工具名无重复", len(names) == len(set(names)), str(names))
    # 运行时读取工具必须存在：它是 ue/ 下唯一需要 game world 的能力
    check("含运行时读取工具 pie_state", "pie_state" in names, str(names))
    for tool in listed:
        if "_handler" in tool:
            check("工具定义不含内部 handler", False, tool["name"])
            break
    else:
        check("工具定义不含内部 handler", True)

    # 每个工具的 inputSchema 都必须是合法 object schema
    for tool in listed:
        schema = tool.get("inputSchema") or {}
        if schema.get("type") != "object" or "properties" not in schema:
            check("工具 %s 的 inputSchema 合法" % tool["name"], False, json.dumps(schema))
            break
    else:
        check("所有工具 inputSchema 合法", True)

    # 需要 source 的工具都应声明它
    for name in ("flow", "graph_overview", "node_detail", "audit"):
        tool = next((t for t in listed if t["name"] == name), None)
        if tool is None:
            check("工具 %s 存在" % name, False)
            continue
        props = (tool["inputSchema"].get("properties") or {})
        check("工具 %s 声明了 source" % name, "source" in props, str(sorted(props)))

    # ---------------------------------------------------------- 6. 未知工具
    print("\n[6] 错误路径")
    payload, is_error = tool_layer.call_tool("no_such_tool", {})
    check("未知工具返回 isError", is_error and "error" in payload, repr(payload)[:160])

    payload, is_error = tool_layer.call_tool("flow", {})
    check("缺参数返回结构化错误", is_error and "asset_path" in str(payload), repr(payload)[:160])

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
