# -*- coding: utf-8 -*-
r"""
ComboMCP / 在 MCP 进程里装载纯 Python 模块
==========================================

``ue/`` 下有几个模块**不依赖 unreal**（``graph_ir`` / ``t3d`` / ``montage_t3d``），
它们既能在编辑器里跑，也能在 MCP 进程里直接跑。

这一点很关键：既然 T3D 已经落到磁盘，那么"读图逻辑/读 Montage"就不再需要连编辑器——
只要把这三个模块装进 MCP 进程，解析与诊断全部在本地完成，毫秒级返回。

装载方式与 :mod:`engine.bridge` 送进编辑器时完全一致（把模块挂到一个名为
``combomcp`` 的包下），这样相对导入 ``from . import graph_ir`` 能正常解析，
两条路径共用同一份源码，不会出现"编辑器里能跑、本地跑不了"的分裂。
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC_DIR = os.path.join(ROOT, "ue")

# 依赖顺序：被依赖的在前
PURE_MODULES = ("graph_ir", "t3d", "montage_t3d", "anim_bp_t3d")

_loaded = None


def load():
    """装载并返回 ``(graph_ir, t3d, montage_t3d)`` 三个模块。"""
    global _loaded
    if _loaded is not None:
        return _loaded

    pkg = sys.modules.get("combomcp")
    if pkg is None:
        pkg = types.ModuleType("combomcp")
        pkg.__path__ = [UE_SRC_DIR]
        sys.modules["combomcp"] = pkg

    for name in PURE_MODULES:
        full = "combomcp." + name
        existing = sys.modules.get(full)
        if existing is not None and hasattr(pkg, name):
            continue
        path = os.path.join(UE_SRC_DIR, name + ".py")
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        mod = types.ModuleType(full)
        mod.__package__ = "combomcp"
        mod.__file__ = path
        sys.modules[full] = mod
        try:
            exec(compile(source, path, "exec"), mod.__dict__)
        except Exception:
            del sys.modules[full]
            raise
        setattr(pkg, name, mod)

    _loaded = (pkg.graph_ir, pkg.t3d, pkg.montage_t3d, pkg.anim_bp_t3d)
    return _loaded


def graph_ir():
    return load()[0]


def t3d():
    return load()[1]


def montage_t3d():
    return load()[2]


def anim_bp_t3d():
    return load()[3]
