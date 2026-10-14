# -*- coding: utf-8 -*-
r"""
ComboMCP / 编辑器动作（关卡 / PIE / Actor）
==========================================

这是**唯一会改动编辑器状态**的模块，因此它的边界写得比别处更硬。

实测得出的三条硬约束
--------------------
1. **绝不能在 commandlet 里碰 EditorSubsystem。** 实测：在
   ``UnrealEditor-Cmd -run=PythonScript`` 下构造 ``unreal.LevelEditorSubsystem()``
   会让整个进程以 ``EXCEPTION_ACCESS_VIOLATION``（UnrealEditor-LevelEditor.dll）
   崩掉——不是抛异常，是崩进程，Python 侧连 traceback 都拿不到。
   所以本模块的每个函数都先过 :func:`_editor_ready`，取编辑器世界失败就返回
   带 ``_need_editor`` 标记的错误，一个子系统都不实例化。
2. **子系统要用 ``get_editor_subsystem`` 取**，不要直接实例化：直接实例化从
   UE 5.2 起已废弃，且正是上面那个崩溃的来源。
3. **写操作必须显式授权。** 具体策略门在 MCP 侧（``engine/tools`` 与
   :mod:`ue.dispatch` 的双重检查），本模块只负责执行，并在返回值里如实说明
   改了哪个关卡、存了什么。

动作面（刻意只开这些）
----------------------
* ``capabilities`` —— 只读：这台编辑器现在能做什么
* ``pie``          —— start / stop / status
* ``level``        —— current / list / load / new / save / add
* ``actor``        —— list / selected / count

**没有** console 命令，**没有**任意资产保存。见 README 的安全边界一节。
"""

import os

import unreal  # noqa: F401

# 会改变编辑器状态的动作。MCP 侧用同一份名单做授权判断，
# 所以它必须在这里而不是在 MCP 侧硬编码的第二份列表里。
WRITE_ACTIONS = ("pie.start", "pie.stop", "pie.simulate",
                 "level.load", "level.new", "level.save", "level.add")


def is_write_action(action, sub=None):
    """``("pie", "start")`` -> True。"""
    if sub:
        return "%s.%s" % (action, sub) in WRITE_ACTIONS
    return action in WRITE_ACTIONS


# ====================================================================== 前置检查


def _subsystem(cls_name):
    """取编辑器子系统；取不到返回 None（绝不直接实例化）。"""
    cls = getattr(unreal, cls_name, None)
    if cls is None:
        return None
    getter = getattr(unreal, "get_editor_subsystem", None)
    if not callable(getter):
        return None
    try:
        return getter(cls)
    except Exception:
        return None


def _editor_ready():
    """判断"现在能不能碰编辑器子系统"。

    返回 ``(level_editor_subsystem, editor_world, error_dict)``。

    判据不是"猜是不是 commandlet"，而是**能不能真的拿到编辑器世界**——
    这正是崩进程的那一步所依赖的前提。取不到就一律拒绝，不冒险试。
    """
    world = None
    try:
        ues = _subsystem("UnrealEditorSubsystem")
        if ues is not None:
            world = ues.get_editor_world()
    except Exception:
        world = None

    les = _subsystem("LevelEditorSubsystem")
    if world is None or les is None:
        return None, None, {
            "error": "编辑器动作需要一个正在运行的 Unreal Editor。",
            "_need_editor": True,
            "why": ("关卡/PIE/Actor 这类动作只能在编辑器进程里执行。"
                    "它们不走 T3D 缓存那条离线路径——缓存里没有「当前打开了什么」。"),
            "how": [
                "1) 打开编辑器（不是 UnrealEditor-Cmd）。",
                "2) 勾选 Project Settings → Plugins → Python → "
                "Enable Remote Execution?（默认关闭，没有命令行开关）。",
                "3) 用 status 确认 bridge.connected 为 true。",
            ],
            "note": ("顺带一提：这些代码在 commandlet 里**会崩进程**"
                     "（实测 EXCEPTION_ACCESS_VIOLATION），所以这里宁可拒绝执行。"),
        }
    return les, world, None


def _guard_write(args):
    """写操作的二次确认（第一次在 MCP 侧的策略门）。

    两道门是刻意的：MCP 侧那道防止模型误调，这一道保证即使有人直接通过
    Remote Execution 送命令进来，也不会在没授权的情况下改状态。
    """
    if args.get("_actions_allowed"):
        return None
    return {
        "error": "写操作未授权。",
        "_actions_blocked": True,
        "how": ("设置环境变量 COMBO_ALLOW_ACTIONS=1 后重启 MCP 客户端；"
                "或在调用时传 confirm=true（单次授权）。"),
    }


# ====================================================================== 读取


def _level_info(level):
    if level is None:
        return None
    info = {"name": None, "path": None}
    try:
        info["name"] = level.get_name()
    except Exception:
        pass
    try:
        info["path"] = level.get_path_name()
    except Exception:
        pass
    return info


def _world_levels(world):
    """世界上挂着的所有关卡（含子关卡）。"""
    out = []
    try:
        levels = unreal.EditorLevelUtils.get_levels(world)
    except Exception as exc:
        return [], "get_levels failed: %s" % exc
    for level in levels or []:
        entry = _level_info(level)
        if entry is None:
            continue
        try:
            entry["is_persistent"] = bool(level.is_persistent_level())
        except Exception:
            pass
        try:
            entry["is_visible"] = bool(level.is_level_visible())
        except Exception:
            pass
        out.append(entry)
    return out, None


def _actor_brief(actor):
    entry = {"name": None, "class": None}
    try:
        entry["name"] = actor.get_name()
    except Exception:
        pass
    try:
        entry["class"] = actor.get_class().get_name()
    except Exception:
        pass
    try:
        loc = actor.get_actor_location()
        entry["location"] = {"x": round(loc.x, 1), "y": round(loc.y, 1),
                             "z": round(loc.z, 1)}
    except Exception:
        pass
    try:
        entry["level"] = actor.get_level().get_name()
    except Exception:
        pass
    try:
        entry["hidden"] = bool(actor.is_hidden())
    except Exception:
        pass
    return entry


def capabilities():
    """只读：这台编辑器现在能做什么。"""
    les, world, err = _editor_ready()
    if err:
        return err

    out = {
        "ok": True,
        "editor_world": None,
        "current_level": _level_info(les.get_current_level()),
        "in_pie": None,
        "subsystems": {},
        "actions": {
            "pie": ["start", "stop", "simulate", "status"],
            "level": ["current", "list", "load", "new", "save", "add"],
            "actor": ["list", "selected", "count"],
        },
    }
    try:
        out["editor_world"] = world.get_name()
    except Exception:
        pass
    try:
        out["in_pie"] = bool(les.is_in_play_in_editor())
    except Exception:
        pass
    for name in ("LevelEditorSubsystem", "UnrealEditorSubsystem",
                 "EditorActorSubsystem", "EditorLevelUtils",
                 "EditorLoadingAndSavingUtils"):
        out["subsystems"][name] = _subsystem(name) is not None
    # 相机：确认视口可读（帮用户判断"我看的是哪个位置"）
    try:
        cam = _subsystem("UnrealEditorSubsystem").get_level_viewport_camera_info()
        if cam:
            out["camera"] = {
                "location": [round(v, 1) for v in cam[0]],
                "rotation": [round(v, 1) for v in cam[1]],
            }
    except Exception:
        pass
    return out


# ====================================================================== PIE


def pie(args):
    """PIE 控制：start / stop / simulate / status。"""
    sub = (args.get("sub") or "status").lower()
    les, world, err = _editor_ready()
    if err:
        return err

    if sub == "status":
        state = {"ok": True, "action": "pie.status"}
        try:
            state["in_pie"] = bool(les.is_in_play_in_editor())
        except Exception as exc:
            state["in_pie"] = None
            state["error"] = "is_in_play_in_editor failed: %s" % exc
        try:
            game_world = _subsystem("UnrealEditorSubsystem").get_game_world()
            state["game_world"] = game_world.get_name() if game_world else None
        except Exception:
            state["game_world"] = None
        if not state.get("in_pie"):
            state["hint"] = ("要读运行时状态（武器挂哪个插槽、在播哪个 montage），"
                             "先 pie sub=start，再用 pie_state 工具。")
        return state

    blocked = _guard_write(args)
    if blocked:
        return blocked

    if sub == "start":
        try:
            already = bool(les.is_in_play_in_editor())
        except Exception:
            already = False
        if already:
            return {"ok": True, "action": "pie.start", "changed": False,
                    "in_pie": True, "note": "已经在 PIE 里了，没有重复启动。"}
        try:
            les.editor_play_simulate()
        except Exception as exc:
            return {"error": "editor_play_simulate failed: %s: %s"
                             % (type(exc).__name__, exc)}
        return {"ok": True, "action": "pie.start", "changed": True, "in_pie": True,
                "note": ("已以 **Simulate** 方式进入 Play：世界会跑起来，但不接管输入。"
                         "这正是读运行时状态（插槽/在播的 montage/组件变量）需要的。"
                         "要真正可操作的 PIE，请在编辑器里按 Play。")}

    if sub == "simulate":
        try:
            les.editor_request_begin_play()
        except Exception as exc:
            return {"error": "editor_request_begin_play failed: %s: %s"
                             % (type(exc).__name__, exc)}
        return {"ok": True, "action": "pie.simulate", "changed": True,
                "note": "已请求 BeginPlay 进入 Play。"}

    if sub == "stop":
        try:
            already = not bool(les.is_in_play_in_editor())
        except Exception:
            already = False
        if already:
            return {"ok": True, "action": "pie.stop", "changed": False,
                    "in_pie": False, "note": "当前不在 PIE 里。"}
        try:
            les.editor_request_end_play()
        except Exception as exc:
            return {"error": "editor_request_end_play failed: %s: %s"
                             % (type(exc).__name__, exc)}
        return {"ok": True, "action": "pie.stop", "changed": True, "in_pie": False}

    return {"error": "unknown pie sub: %s" % sub,
            "valid": ["start", "stop", "simulate", "status"]}


# ====================================================================== 关卡


def level(args):
    """关卡：current / list / load / new / save / add。"""
    sub = (args.get("sub") or "current").lower()
    les, world, err = _editor_ready()
    if err:
        return err

    if sub == "current":
        current = les.get_current_level()
        out = {"ok": True, "action": "level.current",
               "current_level": _level_info(current)}
        levels, level_err = _world_levels(world)
        out["loaded_levels"] = levels
        if level_err:
            out["level_error"] = level_err
        return out

    if sub == "list":
        levels, level_err = _world_levels(world)
        if level_err:
            return {"error": level_err}
        return {"ok": True, "action": "level.list", "count": len(levels),
                "levels": levels}

    if sub == "save":
        blocked = _guard_write(args)
        if blocked:
            return blocked
        try:
            ok = bool(les.save_current_level())
        except Exception as exc:
            return {"error": "save_current_level failed: %s: %s"
                             % (type(exc).__name__, exc)}
        return {"ok": True, "action": "level.save", "changed": bool(ok),
                "saved": ok,
                "level": _level_info(les.get_current_level()),
                "note": ("只保存当前关卡。地图从未存过盘时 UE 会失败——那属于正常限制。"
                         if not ok else "已保存当前关卡。")}

    if sub in ("load", "new", "add"):
        blocked = _guard_write(args)
        if blocked:
            return blocked
        path = args.get("asset_path") or args.get("level_path")
        if not path:
            return {"error": "level sub=%s 需要 asset_path，"
                             "例如 /Game/Maps/MyMap" % sub,
                    "example": {"asset_path": "/Game/Maps/MyMap"}}

        if sub == "load":
            try:
                ok = bool(les.load_level(path))
            except Exception as exc:
                return {"error": "load_level failed: %s: %s"
                                 % (type(exc).__name__, exc)}
            return {"ok": True, "action": "level.load", "changed": ok,
                    "loaded": ok, "asset_path": path,
                    "note": "加载关卡会关闭当前 PersistentLevel 且**不保存**它。"
                            if ok else "load_level 返回 False。"}

        if sub == "new":
            partitioned = bool(args.get("is_partitioned_world", False))
            if not path.startswith("/Game/"):
                return {"error": "new 需要 /Game/ 下的资产路径，例如 "
                                 "/Game/Maps/NewMap"}
            try:
                ok = bool(les.new_level(path, partitioned))
            except Exception as exc:
                return {"error": "new_level failed: %s: %s"
                                 % (type(exc).__name__, exc)}
            return {"ok": True, "action": "level.new", "changed": ok,
                    "created": ok, "asset_path": path,
                    "partitioned": partitioned,
                    "note": "新关卡会立刻存盘并加载（UE 的既有行为）。"}

        # add：把另一个关卡作为子关卡挂进来
        if sub == "add":
            try:
                streaming = unreal.EditorLevelUtils.add_level_to_world(
                    world, path, unreal.LevelStreamingDynamic)
            except Exception as exc:
                return {"error": "add_level_to_world failed: %s: %s"
                                 % (type(exc).__name__, exc)}
            return {"ok": True, "action": "level.add", "changed": streaming is not None,
                    "asset_path": path,
                    "streaming": getattr(streaming, "get_name", lambda: None)()}

    return {"error": "unknown level sub: %s" % sub,
            "valid": ["current", "list", "load", "new", "save", "add"]}


# ====================================================================== Actor


def actor(args):
    """关卡内 Actor：list / selected / count（全部只读）。"""
    sub = (args.get("sub") or "list").lower()
    _, _, err = _editor_ready()
    if err:
        return err

    eas = _subsystem("EditorActorSubsystem")
    if eas is None:
        return {"error": "EditorActorSubsystem 不可用"}

    try:
        if sub == "selected":
            actors = eas.get_selected_level_actors() or []
        else:
            actors = eas.get_all_level_actors() or []
    except Exception as exc:
        return {"error": "读取 Actor 失败: %s: %s" % (type(exc).__name__, exc)}

    pattern = (args.get("filter") or "").lower()
    briefs = []
    for item in actors:
        brief = _actor_brief(item)
        if pattern:
            hay = ("%s %s" % (brief.get("name") or "", brief.get("class") or "")).lower()
            if pattern not in hay:
                continue
        briefs.append(brief)

    limit = int(args.get("limit", 200))
    hist = {}
    for brief in briefs:
        hist[brief.get("class") or "?"] = hist.get(brief.get("class") or "?", 0) + 1

    return {
        "ok": True,
        "action": "actor.%s" % sub,
        "matched": len(briefs),
        "total": len(actors),
        "class_histogram": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "actors": briefs[:limit],
        "truncated": len(briefs) > limit,
    }


# ====================================================================== 分发


_ACTIONS = {
    "capabilities": lambda args: capabilities(),
    "pie": pie,
    "level": level,
    "actor": actor,
}


def editor_action(action, args=None):
    """统一入口。``action`` 是 ``capabilities`` / ``pie`` / ``level`` / ``actor``。"""
    args = dict(args or {})
    fn = _ACTIONS.get((action or "").lower())
    if fn is None:
        return {"error": "unknown editor action: %s" % action,
                "valid": sorted(_ACTIONS.keys())}
    return fn(args)
