# -*- coding: utf-8 -*-
r"""
ComboMCP 编辑器内启动钩子
=========================

PythonScriptPlugin 初始化时会扫描 ``sys.path`` 的每一个条目，只要该条目下存在
``init_unreal.py`` 就把它当脚本执行（``PythonScriptPlugin.cpp`` 中
``FPythonScriptPlugin::RunStartupScripts``）。而插件的 ``Content/Python`` 目录由
``FPythonScriptPlugin::OnContentPathMounted`` 自动注入 ``sys.path``，所以本文件
**不需要任何配置就会被调用**——引擎里 11 个官方插件（ControlRig / IKRig /
USDImporter / MovieRenderPipeline 等）用的就是这个机制。

因为它是被"当脚本运行"而非被 import 的，所以用 ``if __name__ == "__main__"``
守卫，这与官方 ``HairCardGenerator`` 的写法一致。

这里做两件事，且全部包在 try/except 里：

1. 在 ``cache/plugin/loaded.json`` 留一份"插件确实被加载"的证据。启动一次
   commandlet 后检查该文件，就能在不打开编辑器的情况下确认插件生效；
   它同时记录了 Python 版本与 ``Content/Python`` 是否成功进入 ``sys.path``。
2. Slate 菜单系统可用时注册编辑器菜单（实现见 ``combomcp_editor.py``）。

**绝不向调用方抛异常。** 启动脚本报错会让编辑器弹错误对话框，而本插件对用户
应该完全无感；所以这里连 import 失败都要吞掉。
"""

import os
import sys

# __file__ = <plugin>/Content/Python/init_unreal.py  →  三次 dirname 得到插件根
_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))
_CONTENT_DIR = os.path.dirname(_PYTHON_DIR)
PLUGIN_DIR = os.path.dirname(_CONTENT_DIR)

# 让 `<plugin>/engine`、`<plugin>/ue` 等包可以被直接 import（编辑器内调试用）。
# MCP 服务器是独立进程，有自己的 sys.path 管理，不依赖这一步。
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def _same_dir_in_sys_path(path):
    """判断某目录是否在 ``sys.path`` 里，比较前统一规范化。

    UE 写入 ``sys.path`` 的是正斜杠绝对路径（``E:/...``），而 Windows 上
    ``os.path`` 给的是反斜杠（``E:\\...``）；直接 ``in`` 会把同一个目录判成两个
    不同字符串——这正是第一版误报 ``content_python_on_sys_path: false`` 的原因。
    """
    target = os.path.normcase(os.path.normpath(str(path)))
    for entry in sys.path:
        try:
            if os.path.normcase(os.path.normpath(str(entry))) == target:
                return True
        except Exception:
            continue
    return False


def _tool_menus_available():
    """判断 Slate 的菜单系统此刻是否可用。

    这里**不去猜"是不是 commandlet"**。实测（见 cache/plugin/probe_result.json）
    UE 5.7 的 Python 里既没有 ``unreal.IsRunningCommandlet``，``SystemLibrary``
    下也没有对应函数，那条路是死的。

    改为直接检测菜单注册真正需要的前提：``ToolMenus`` 能不能取到。这比间接推断
    更精确——commandlet 下 Slate 未初始化，取不到；编辑器里能取到。
    """
    try:
        import unreal
    except Exception:
        return False

    getter = getattr(unreal.ToolMenus, "get", None)
    if not callable(getter):
        return False
    try:
        return getter() is not None
    except Exception:
        return False


def _record_loaded(menus_available):
    """写一份加载证据到 cache/plugin/loaded.json。失败无所谓，绝不上抛。"""
    try:
        import json
        import platform
        import time

        out_dir = os.path.join(PLUGIN_DIR, "cache", "plugin")
        os.makedirs(out_dir, exist_ok=True)

        info = {
            "plugin": "ComboMCP",
            "plugin_dir": PLUGIN_DIR,
            "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            # 决定性字段：Content/Python 有没有被注入 sys.path。
            # 它证明 .uplugin 的 CanContainContent 与 PythonScriptPlugin 都生效了。
            "content_python_on_sys_path": _same_dir_in_sys_path(_PYTHON_DIR),
            "tool_menus_available": bool(menus_available),
            "combo_sys_path_entries": [p for p in sys.path if "ComboMCP" in str(p)],
            # 注意：UE 里 sys.argv 并不是进程命令行（启动脚本阶段实测为 [""]），
            # 留在这里只为诊断，不要拿它做判断。
            "argv": list(sys.argv)[:24],
        }

        with open(os.path.join(out_dir, "loaded.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _register_menu():
    """注册编辑器菜单。整体失败只记日志，绝不影响编辑器。"""
    try:
        import combomcp_editor  # 与本文件同目录，靠 Content/Python 的 sys.path 注入
    except Exception:
        _warn("导入 combomcp_editor 失败", include_traceback=True)
        return

    try:
        combomcp_editor.register()
    except Exception:
        _warn("菜单注册失败（插件其余功能不受影响）", include_traceback=True)


def _warn(message, include_traceback=False):
    """尽力把警告写进 UE 日志；连 unreal 都不可用就彻底放弃。"""
    try:
        import traceback
        import unreal

        text = "[ComboMCP] " + message
        if include_traceback:
            text += "\n" + traceback.format_exc()
        unreal.log_warning(text)
    except Exception:
        pass


def main():
    menus_available = _tool_menus_available()
    _record_loaded(menus_available)
    if menus_available:
        _register_menu()


if __name__ == "__main__":
    main()
