# -*- coding: utf-8 -*-
r"""
ComboMCP 插件验收探针
=====================

用法（**故意不传 -EnablePlugins=PythonScriptPlugin**）::

    UnrealEditor-Cmd.exe <项目>.uproject -run=PythonScript ^
        -Script=<无空格路径>\probe_plugin_load.py -unattended -nosplash ^
        -nop4 -nullrhi -stdout

注意 UE 会在空格处截断 ``-Script`` 的值，所以脚本要放在不含空格的路径下运行。
插件目录由**引擎自己报告**（``PluginBlueprintLibrary``），所以从哪儿跑都不影响结论。

它验证四件事：

1. 插件是否真的被加载——看 ``cache/plugin/loaded.json``（由 init_unreal.py 写下）。
2. **引擎自己**是否把 ComboMCP 算作启用插件（PluginBlueprintLibrary）。
3. ``Content/Python`` 是否真的进了 ``sys.path``。
4. 菜单 API 的调用链是否可用——在 commandlet 下注册一个临时菜单实测，
   这样不必启动编辑器就能确认菜单代码写对了。

结果写入 ``cache/plugin/probe_result.json`` 而不是 stdout：UE 的 commandlet
标准输出在管道里并不可靠，落盘才是可复现的证据。
"""

import json
import os
import sys

import unreal

def _plugin_dir():
    """让引擎自己告诉我们这个插件装在哪。

    **不能从 ``__file__`` 推导**：UE 会在空格处截断 ``-Script`` 的值，所以本脚本
    必须先复制到无空格路径再运行，此时 ``__file__`` 指向的是那个临时位置而不是插件
    目录。改成问引擎，既准确，也天然支持插件被装在任意项目里。
    """
    try:
        import unreal
        path = unreal.PluginBlueprintLibrary.get_plugin_base_dir("ComboMCP")
        if path:
            return str(path).replace("/", os.sep)
    except Exception:
        pass
    # 退化路径：仅在原地运行时正确（例如直接 import 本模块做单测）
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


PLUGIN_DIR = _plugin_dir()
CACHE_PLUGIN = os.path.join(PLUGIN_DIR, "cache", "plugin")
RESULT = os.path.join(CACHE_PLUGIN, "probe_result.json")
CONTENT_PYTHON = os.path.join(PLUGIN_DIR, "Content", "Python")


@unreal.uclass()
class ComboMCPProbeEntry(unreal.ToolMenuEntryScript):
    """只为验证菜单 API 调用链而存在；commandlet 退出即消失，不留痕迹。"""

    @unreal.ufunction(override=True)
    def execute(self, context):
        pass


def _norm(path):
    """规范化以便比较：UE 的 sys.path 用正斜杠，os.path 给反斜杠，大小写也可能不同。"""
    try:
        return os.path.normcase(os.path.normpath(str(path)))
    except Exception:
        return str(path)


def _sig(obj, name, limit=600):
    fn = getattr(obj, name, None)
    return (getattr(fn, "__doc__", None) or "?")[:limit]


result = {
    "python_version": sys.version.split()[0],
    "sys_executable": sys.executable,
    "sys_argv": list(sys.argv),
    "uplugin_exists": os.path.isfile(os.path.join(PLUGIN_DIR, "ComboMCP.uplugin")),
    "loaded_json_exists": os.path.isfile(os.path.join(CACHE_PLUGIN, "loaded.json")),
    "content_python_in_sys_path_norm": any(
        _norm(p) == _norm(CONTENT_PYTHON) for p in sys.path),
    "sys_path_combo_entries": [p for p in sys.path if "ComboMCP" in str(p)],
}

# --- 1. 引擎自己怎么看待这个插件 --------------------------------------------
try:
    names = [str(n) for n in unreal.PluginBlueprintLibrary.get_enabled_plugin_names()]
    result["enabled_plugin_count"] = len(names)
    result["combo_in_enabled_plugins"] = "ComboMCP" in names
    result["combo_related_plugins"] = [n for n in names if "Combo" in n]
    for fn, key in (("get_plugin_base_dir", "plugin_base_dir"),
                    ("get_plugin_version_name", "plugin_version_name"),
                    ("get_plugin_description", "plugin_description"),
                    ("is_plugin_mounted", "plugin_mounted")):
        try:
            result[key] = str(getattr(unreal.PluginBlueprintLibrary, fn)("ComboMCP"))
        except Exception as exc:
            result[key] = "%s: %s" % (type(exc).__name__, exc)
except Exception as exc:
    result["combo_in_enabled_plugins"] = "err: %s" % exc

# --- 2. 同目录 import（等价于证明 Content/Python 在 sys.path 里）------------
try:
    import init_unreal
    result["same_dir_import"] = "ok: %s" % getattr(init_unreal, "__file__", "?")
except Exception as exc:
    result["same_dir_import"] = "%s: %s" % (type(exc).__name__, exc)

# --- 3. 菜单 API 签名：照抄，不猜 -------------------------------------------
for name in ("init_entry", "register_menu_entry", "unregister_menu_entry",
             "can_execute", "get_label"):
    result["sig_ToolMenuEntryScript." + name] = _sig(unreal.ToolMenuEntryScript, name, 400)
result["sig_ToolMenus.register_menu"] = _sig(unreal.ToolMenus, "register_menu", 500)
result["sig_ToolMenus.add_menu_entry_object"] = _sig(
    unreal.ToolMenus, "add_menu_entry_object", 300)

# --- 4. 端到端实测菜单调用链 ------------------------------------------------
# commandlet 下 LevelEditor.MainMenu.Tools 不存在（find_menu 返回 None），
# 所以这里自己 register_menu 一个临时菜单，专门验证 API 调用链是否正确。
try:
    menus = unreal.ToolMenus.get()
    result["toolmenus_get_here"] = "ok: %r" % (menus,)
    result["find_editor_menu_here"] = "%r" % (
        menus.find_menu("LevelEditor.MainMenu.Tools"),)

    probe_menu = menus.register_menu("ComboMCPProbeMenu", "None",
                                     unreal.MultiBoxType.MENU, False)
    result["register_menu_ok"] = probe_menu is not None

    entry = ComboMCPProbeEntry()
    # 签名照抄：init_entry(owner_name, menu, section, name, label="", tool_tip="")
    # 第 3 个参数 section 是 Name 类型，传 Text 会报 Cannot nativize 'Text' as 'Name'
    entry.init_entry("ComboMCP", "ComboMCPProbeMenu", "ComboMCP", "ProbeEntry",
                     unreal.Text("Probe"), unreal.Text("probe tooltip"))
    result["init_entry_ok"] = True

    result["add_menu_entry_object_ok"] = bool(menus.add_menu_entry_object(entry))
    # register_menu_entry() 不带参数：menu / section 已在 init_entry 里指定
    entry.register_menu_entry()
    result["register_menu_entry_ok"] = True
    result["probe_menu_found_after"] = "%r" % (
        menus.find_menu("ComboMCPProbeMenu"),)
except Exception as exc:
    result["menu_e2e_error"] = "%s: %s" % (type(exc).__name__, exc)

# --- 5. 真实菜单模块：import 与在 commandlet 下的安全性 ---------------------
# import 会执行模块级的 @unreal.uclass() / @unreal.ufunction 定义，能提前暴露
# 反射装饰器的问题，不必等编辑器；register() 在 commandlet 下必须安全地返回
# False（find_menu 取不到 LevelEditor 菜单），而不是抛异常。
try:
    import combomcp_editor
    result["editor_module_import"] = "ok"
    result["editor_register_in_commandlet"] = combomcp_editor.register()
    result["editor_entries_after"] = len(combomcp_editor._ENTRIES)
except Exception as exc:
    result["editor_module_import"] = "%s: %s" % (type(exc).__name__, exc)

try:
    os.makedirs(CACHE_PLUGIN, exist_ok=True)
    with open(RESULT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
except Exception:
    pass
