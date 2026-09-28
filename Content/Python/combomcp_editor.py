# -*- coding: utf-8 -*-
r"""
ComboMCP 编辑器菜单集成
=======================

由 ``init_unreal.py`` 在编辑器启动时调用 ``register()``，菜单挂在
``LevelEditor.MainMenu.Tools`` 下。

**为什么菜单项只做"轻量"的事**：它们全部只读文件、打印日志——不启动子进程、
不编译、不碰资产。重新采集缓存是重活（会把 ``UnrealEditor-Cmd`` 再拉起来一次），
放在编辑器主线程上做会把 UI 卡住；那件事交给 MCP 的 ``sync`` 工具或
``check.cmd`` 更合适。

菜单 API 的签名是实测出来的（见 ``tools/probe_plugin_load.py`` 与
``cache/plugin/probe_result.json``），不是猜的::

    init_entry(owner_name, menu, section, name, label="", tool_tip="") -> None
    register_menu_entry() -> None
    ToolMenus.add_menu_entry_object(menu_entry_object) -> bool

注意 ``section`` 是 ``Name`` 而不是 ``Text``——传错会报
``Cannot nativize 'Text' as 'Name'``。
"""

import json
import os
import sys

import unreal

_PYTHON_DIR = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.dirname(os.path.dirname(_PYTHON_DIR))

MENU_PATH = "LevelEditor.MainMenu.Tools"
OWNER = "ComboMCP"
SECTION = "ComboMCP"

# 必须保住 entry 的引用：被 GC 掉菜单项就会消失
_ENTRIES = []


# --------------------------------------------------------------------- 工具


def _cache_dir():
    return os.path.join(PLUGIN_DIR, "cache", "t3d")


def _cached_files():
    try:
        names = os.listdir(_cache_dir())
    except Exception:
        return []
    return sorted(n for n in names if n.endswith(".t3d"))


def _server_python():
    """定位引擎自带的 Python 解释器。

    编辑器里 ``sys.executable`` 是 ``<Engine>/Binaries/<Platform>/UnrealEditor``
    （实测确认），所以上溯三级就是引擎根目录。

    平台目录与解释器文件名按系统推导并逐个探测——不写死 Win64，
    否则在 macOS / Linux 上给出的配置会是错的。
    """
    exe = os.path.abspath(sys.executable)
    engine = os.path.dirname(os.path.dirname(os.path.dirname(exe)))
    base = os.path.join(engine, "Binaries", "ThirdParty", "Python3")

    if sys.platform.startswith("win"):
        candidates = [os.path.join(base, "Win64", "python.exe")]
    elif sys.platform == "darwin":
        candidates = [os.path.join(base, "Mac", "bin", "python3"),
                      os.path.join(base, "Mac", "python3")]
    else:
        candidates = [os.path.join(base, "Linux", "bin", "python3"),
                      os.path.join(base, "Linux", "python3")]

    for path in candidates:
        if os.path.isfile(path):
            return path
    # 都不存在也返回首选路径：让打印出的配置可读、便于用户手改
    return candidates[0]


def _server_script():
    return os.path.join(PLUGIN_DIR, "mcpserver", "server.py")


def _cfg_json():
    """生成可直接粘贴到 MCP 客户端的配置。"""
    return json.dumps({
        "mcpServers": {
            "combo": {
                "command": _server_python(),
                "args": [_server_script()],
            }
        }
    }, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------- 菜单动作


def _show_status():
    files = _cached_files()
    unreal.log("=" * 64)
    unreal.log("[ComboMCP] 插件目录 : %s" % PLUGIN_DIR)
    unreal.log("[ComboMCP] 缓存目录 : %s" % _cache_dir())
    unreal.log("[ComboMCP] 已缓存   : %d 个 T3D" % len(files))
    if files:
        total = 0
        for name in files:
            try:
                total += os.path.getsize(os.path.join(_cache_dir(), name))
            except Exception:
                pass
        unreal.log("[ComboMCP] 缓存体积 : %.1f MB" % (total / 1048576.0))
    else:
        unreal.log("[ComboMCP] 缓存为空——用 MCP 的 sync 工具或 check.cmd 采集一次")
    unreal.log("=" * 64)


def _list_assets():
    files = _cached_files()
    unreal.log("[ComboMCP] 已缓存 %d 个资产：" % len(files))
    for name in files:
        unreal.log("    %s" % name)


def _show_config():
    unreal.log("[ComboMCP] 把下面这段贴进 MCP 客户端配置即可接入：")
    unreal.log(_cfg_json())


def _safe(fn):
    """菜单回调的兜底：任何异常都只进日志，绝不让编辑器弹窗。"""
    try:
        fn()
    except Exception:
        try:
            import traceback
            unreal.log_error("[ComboMCP] 菜单动作失败:\n" + traceback.format_exc())
        except Exception:
            pass


# ------------------------------------------------------------- 菜单条目脚本
# 每个菜单项一个类：ToolMenuEntryScript 的 execute 是 ufunction override，
# 用工厂动态造类在 UE 的反射系统里不可靠，所以老老实实各写一个。


@unreal.uclass()
class _StatusEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _safe(_show_status)


@unreal.uclass()
class _ListEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _safe(_list_assets)


@unreal.uclass()
class _ConfigEntry(unreal.ToolMenuEntryScript):
    @unreal.ufunction(override=True)
    def execute(self, context):
        _safe(_show_config)


# ------------------------------------------------------------------- 注册


def register():
    """注册菜单项，返回是否真的注册成功。**绝不抛异常。**

    返回 False 最常见的情形是 commandlet：那里没有 LevelEditor，
    ``find_menu`` 取不到目标菜单（实测返回 None）。这也正是本模块的门控方式——
    比猜"是不是 commandlet"精确，因为 ToolMenus 对象在 commandlet 下也存在。
    """
    try:
        menus = unreal.ToolMenus.get()
        if menus is None:
            return False
        if menus.find_menu(MENU_PATH) is None:
            return False

        specs = (
            (_StatusEntry, "ShowStatus", "显示缓存状态",
             "统计 ComboMCP 已缓存的资产数量与体积"),
            (_ListEntry, "ListAssets", "列出已缓存资产",
             "在 Output Log 中列出已缓存的 T3D 资产"),
            (_ConfigEntry, "ShowConfig", "显示 MCP 客户端配置",
             "打印可直接粘贴到 MCP 客户端的 JSON 配置"),
        )

        for cls, name, label, tool_tip in specs:
            entry = cls()
            # 签名照抄：init_entry(owner_name, menu, section, name, label, tool_tip)
            entry.init_entry(OWNER, MENU_PATH, SECTION, name,
                             unreal.Text(label), unreal.Text(tool_tip))
            menus.add_menu_entry_object(entry)
            entry.register_menu_entry()
            _ENTRIES.append(entry)

        menus.refresh_all_widgets()
        unreal.log("[ComboMCP] 菜单已注册：Tools → ComboMCP")
        return True
    except Exception:
        return False
