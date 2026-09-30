# -*- coding: utf-8 -*-
"""
ComboMCP / PIE 运行时状态读取（在编辑器内执行，只读）
======================================================

为什么需要这个模块
------------------
``ue/`` 下其余模块读的都是**磁盘上的资产**（T3D 缓存）或**编辑器世界**。但有一类
问题它们永远答不了：**图连对了、资产也没错，运行起来却不对**。

真实案例（本模块的由来）：某角色的"装备武器"链路——查表、生成、AttachToComponent
全都接对了，蓝图编译无错，`audit` 也查不出问题；但按装备键武器就是不动。
根因只能在运行时看出来（组件的私有变量值、Actor 当前挂在哪个插槽、montage 是否
真的在播）。静态读取无法区分"连对了但没执行"和"连错了"。

设计边界
--------
* **只读**。不改属性、不调函数、不 spawn。与 ``ue/`` 其余模块同一条安全边界。
* **不假设 PIE 在跑**。没有 PIE 世界时返回 ``{"pie_running": false}`` 并给出提示，
  而不是报错——这是正常状态，不是失败。
* **不猜 API**。本模块用到的每个引擎函数都在 UE 5.7.4 上实测存在（见文件末尾
  ``VERIFIED_API``）。踩过的坑：``Actor.get_attach_parent`` / ``SceneComponent.get_socket_name``
  / ``SkeletalMeshComponent.get_active_montages`` 在 Python 里**都不存在**，别用。

对外接口
--------
``pie_state(component_path=None, actor_name=None, include_sockets=True)``
"""

import json

import unreal

# 组件/挂点相关的变量名。按顺序尝试，读到第一个存在的就用。
_COMBAT_VARS = (
    "CurrentWeapon",
    "EquippedWeaponRow",
    "PendingWeaponRow",
    "bWeaponBusy",
    "bWeaponInHand",
    "OwningCharacter",
)

# 常见的"武器插槽"候选名：用于在没有 PIE 武器时也能验证骨架侧配置
_DEFAULT_SOCKET_CANDIDATES = ("Weapon", "Weapon_Back", "weapon_r", "hand_r",
                              "palm_l_Socket")


# ====================================================================== 工具


def _err(bag, label, exc):
    """把异常记进 _diag，而不是让它中断整个采集。"""
    bag.setdefault("_errors", {})[label] = "%s: %s" % (type(exc).__name__, exc)


def _get(obj, prop, bag=None, label=None):
    """读一个属性；不存在/读不到时返回哨兵字符串而不是抛异常。"""
    try:
        return getattr(obj, prop) if False else obj.get_editor_property(prop)
    except Exception as exc:
        if bag is not None:
            _err(bag, label or prop, exc)
        return None


def _describe_value(value):
    """把引擎返回值转成能直接进 JSON 的紧凑形式。"""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    name = getattr(value, "get_name", None)
    if callable(name):
        try:
            return {"name": value.get_name(), "class": value.get_class().get_name()}
        except Exception:
            return str(value)
    return str(value)


def _find_pie_world(bag):
    """返回 (world, how)。找不到时 world 为 None。

    ``UnrealEditorSubsystem.get_game_world()`` 是 UE5 官方路径
    （``EditorLevelLibrary.get_editor_world`` 已废弃，且给的是编辑器世界，不是 PIE）。
    """
    try:
        subsystem = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = subsystem.get_game_world()
        if world:
            return world, "UnrealEditorSubsystem.get_game_world"
    except Exception as exc:
        _err(bag, "get_game_world", exc)
    return None, None


def _find_combat_component(pawn, component_path, bag):
    """在 pawn 上找战斗组件。

    ``component_path`` 省略时按类名模糊匹配（``AC_Combat`` / ``Combat``），
    因为不同项目的命名不同；给全路径时按全路径精确匹配。
    """
    try:
        comps = pawn.get_components_by_class(unreal.ActorComponent) or []
    except Exception as exc:
        _err(bag, "get_components_by_class", exc)
        return None, []

    classes = sorted({c.get_class().get_name() for c in comps})
    if component_path:
        want = component_path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]
        for c in comps:
            if c.get_class().get_name() == want or c.get_class().get_name() == want + "_C":
                return c, classes
        return None, classes

    for c in comps:
        n = c.get_class().get_name().lower()
        if "combat" in n or n.startswith("ac_"):
            return c, classes
    return None, classes


def _weapon_report(weapon, bag):
    """武器的挂载事实：这是判断"在手上还是背上"的唯一可靠依据。"""
    if not weapon:
        return {"present": False,
                "why": "组件的武器变量为空 —— 武器从未生成，或已被销毁"}

    rep = {
        "present": True,
        "name": weapon.get_name(),
        "class": weapon.get_class().get_name(),
        "is_valid": None,
    }
    try:
        rep["is_valid"] = bool(unreal.SystemLibrary.is_valid(weapon))
    except Exception as exc:
        _err(bag, "is_valid", exc)

    # 关键字段：当前挂载的插槽名。Python 里只有 get_attach_parent_socket_name，
    # 没有 get_attach_parent / SceneComponent.get_socket_name。
    try:
        rep["attached_to_socket"] = str(weapon.get_attach_parent_socket_name())
    except Exception as exc:
        _err(bag, "get_attach_parent_socket_name", exc)
        rep["attached_to_socket"] = None

    try:
        root = weapon.get_editor_property("root_component")
        rep["root_component"] = root.get_name() if root else None
        parent = root.get_attach_parent() if root else None
        rep["parent_component"] = parent.get_name() if parent else None
        rep["parent_class"] = parent.get_class().get_name() if parent else None
    except Exception as exc:
        _err(bag, "weapon_root", exc)

    try:
        loc = weapon.get_actor_location()
        rep["world_location"] = {"x": round(loc.x, 2), "y": round(loc.y, 2), "z": round(loc.z, 2)}
    except Exception as exc:
        _err(bag, "weapon_location", exc)

    return rep


def _mesh_report(mesh, sockets, bag):
    """Mesh 侧：骨架、插槽是否存在、当前 montage。"""
    if not mesh:
        return {"present": False}

    rep = {"present": True, "class": mesh.get_class().get_name()}

    sk_asset = None
    for prop in ("skeletal_mesh_asset", "skeletal_mesh"):
        try:
            sk_asset = mesh.get_editor_property(prop)
            if sk_asset:
                rep["mesh_property_used"] = prop
                break
        except Exception:
            continue
    if sk_asset:
        rep["skeletal_mesh"] = sk_asset.get_path_name()
        try:
            skel = sk_asset.get_editor_property("skeleton")
            rep["skeleton"] = skel.get_path_name() if skel else None
        except Exception as exc:
            _err(bag, "skeleton", exc)

    # 插槽存在性是 attach 静默失败的头号原因，必须报出来
    if sockets:
        rep["sockets"] = {}
        for s in sockets:
            try:
                rep["sockets"][s] = bool(mesh.does_socket_exist(s))
            except Exception as exc:
                _err(bag, "does_socket_exist." + s, exc)

    # 当前播放的 montage。
    #
    # 实测踩坑（两个都踩过）：
    #   1. ``SkeletalMeshComponent.anim_montage`` 在 UE 5.7.4 上**不存在**；
    #      ``get_active_montages`` / ``get_current_active_montage`` 也没有。
    #   2. ``hasattr(anim_instance, "montage_is_playing")`` 为真，但
    #      ``anim_instance.get_editor_property("montage_is_playing")`` 抛异常——
    #      因为 ``UAnimInstance::Montage_IsPlaying`` 的反射参数名是
    #      ``K2Node_`` 前缀的那个，**属性名与参数名不同**，必须走
    #      ``get_editor_property(obj, "参数名")`` 这个重载。
    # 所以 montage 检测做成"候选逐个试 + 报出命中的名字"，不写死任何一条。
    rep["current_montage"] = None
    rep["montage_playing"] = None
    anim = None
    try:
        anim = mesh.get_anim_instance()
    except Exception as exc:
        _err(bag, "get_anim_instance", exc)

    if anim:
        rep["anim_instance"] = anim.get_class().get_name()
        probe = {}
        for prop in ("active_montage", "active_montage_asset", "current_montage",
                     "anim_montage", "next_montage", "montage_is_playing"):
            value = _reflect_property(anim, prop)
            if value is not _ABSENT:
                probe[prop] = value
        if probe:
            rep["montage_probe"] = probe
        if isinstance(probe.get("montage_is_playing"), bool):
            rep["montage_playing"] = probe["montage_is_playing"]
        for key in ("active_montage", "active_montage_asset", "current_montage"):
            if probe.get(key):
                rep["current_montage"] = probe[key]
                rep["current_montage_property"] = "anim_instance." + key
                break

    for prop in ("animation_mode", "play_rate", "global_anim_rate_scale"):
        try:
            rep[prop] = str(mesh.get_editor_property(prop))
        except Exception:
            pass

    return rep


_ABSENT = object()


def _reflect_property(obj, name):
    """读一个属性；属性名不存在时**退化到把同名方法调用一次**。

    实测踩坑（费了两轮才定位）：``UAnimInstance::Montage_IsPlaying`` 在 Python 里
    暴露成**方法**而不是属性——
    ``hasattr(anim, "montage_is_playing")`` 为真、``getattr`` 拿到
    ``builtin_function_or_method``，但 ``get_editor_property(name)`` 抛异常。
    所以正确的读法是 ``anim.montage_is_playing()``。

    返回值：读到的值；读不到返回 ``_ABSENT`` 哨兵（调用方据此跳过），
    不抛异常也不返回 None——None 是合法值，混在一起会让"没有这个属性"
    和"属性是空"无法区分。
    """
    try:
        return obj.get_editor_property(name)
    except Exception:
        pass
    if hasattr(obj, name):
        try:
            attr = getattr(obj, name)
            return attr() if callable(attr) else attr
        except Exception:
            pass
    return _ABSENT


def _component_vars(component, bag):
    """读战斗组件的状态变量。缺字段不报错——不同项目变量名不同。"""
    out = {}
    missing = []
    for var in _COMBAT_VARS:
        try:
            out[var] = _describe_value(component.get_editor_property(var))
        except Exception:
            out[var] = None
            missing.append(var)
    if missing:
        # 变量不存在 ≠ 读取出错，分开报，免得调用方以为工具坏了
        out["_missing_vars"] = missing
    return out


# ====================================================================== 主入口


def pie_state(component_path=None, actor_name=None, include_sockets=True):
    """读取正在运行的 PIE 世界的战斗/装备运行时状态。

    :param component_path: 战斗组件的类路径（如
        ``/Game/Combo_Demo/Component/AC_Combat``）。省略则按类名模糊匹配。
    :param actor_name: 指定要看的 Pawn 名。省略则用玩家 0 的 Pawn。
    :param include_sockets: 是否检查骨架插槽存在性（默认 True）。
    :returns: dict，永远不抛异常。``pie_running`` 为 False 时表示没有 PIE。
    """
    out = {"pie_running": False}
    bag = out

    world, how = _find_pie_world(bag)
    if not world:
        out["hint"] = ("没有正在运行的 PIE 世界。请在编辑器里 Play（或 Simulate）后重试。"
                       "编辑器世界不算——本工具要的是 game world。")
        return out

    out["pie_running"] = True
    out["world"] = {"name": world.get_name(), "discovered_via": how}
    try:
        out["world"]["is_pie"] = bool(world.get_editor_property("is_play_in_editor"))
    except Exception:
        pass

    # ---------------------------------------------------------------- Pawn
    pawn = None
    try:
        if actor_name:
            for a in unreal.GameplayStatics.get_all_actors_of_class(world, unreal.Pawn) or []:
                if a.get_name() == actor_name:
                    pawn = a
                    break
        else:
            pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
    except Exception as exc:
        _err(bag, "find_pawn", exc)

    if not pawn:
        out["error"] = ("没有找到 Pawn。actor_name 过滤没命中，或这个世界里没有玩家 Pawn。"
                        + ("（已指定 actor_name=%s）" % actor_name if actor_name else ""))
        return out

    out["pawn"] = {"name": pawn.get_name(), "class": pawn.get_class().get_name()}

    # ---------------------------------------------------------------- 组件
    combat, comp_classes = _find_combat_component(pawn, component_path, bag)
    out["pawn"]["component_classes"] = comp_classes

    if not combat:
        out["error"] = ("在这个 Pawn 上没找到战斗组件。"
                        + ("已按 component_path=%s 精确匹配。" % component_path
                           if component_path else "按类名模糊匹配（含 combat 或以 ac_ 开头）。"))
        return out

    out["combat_component"] = {
        "name": combat.get_name(),
        "class": combat.get_class().get_name(),
    }
    out["combat_vars"] = _component_vars(combat, bag)

    # ---------------------------------------------------------------- 武器
    weapon = None
    try:
        weapon = combat.get_editor_property("CurrentWeapon")
    except Exception as exc:
        _err(bag, "CurrentWeapon", exc)
    out["weapon"] = _weapon_report(weapon, bag)

    # ---------------------------------------------------------------- Mesh
    mesh = None
    for prop in ("mesh", "Mesh"):
        try:
            mesh = pawn.get_editor_property(prop)
            if mesh:
                break
        except Exception:
            continue
    if mesh is None:
        try:
            mesh = pawn.get_editor_property("mesh")
        except Exception as exc:
            _err(bag, "pawn.mesh", exc)

    sockets = list(_DEFAULT_SOCKET_CANDIDATES) if include_sockets else []
    out["mesh"] = _mesh_report(mesh, sockets, bag)

    # ---------------------------------------------------------------- 交叉结论
    out["verdict"] = _verdict(out)
    return out


def _verdict(state):
    """把上面的事实压成一两句"最可能是什么问题"，省得调用方自己推。"""
    notes = []
    weapon = state.get("weapon") or {}
    mesh = state.get("mesh") or {}
    cvars = state.get("combat_vars") or {}

    if not weapon.get("present"):
        notes.append("武器不存在（变量为空）→ 检查 BeginPlay 里生成武器的那条链是否执行、"
                     "以及数据表行名是否查得到。")
        return notes

    sock = weapon.get("attached_to_socket")
    if sock in (None, "", "None"):
        notes.append("武器存在但没有挂到任何插槽（attach 失败或挂在组件根上）→ "
                     "检查 AttachToComponent 的 Parent 是否有值、SocketName 是否为 None。")
    else:
        sockets = mesh.get("sockets") or {}
        if sock in sockets and sockets[sock] is False:
            notes.append("武器挂在插槽 '%s' 上，但该插槽在骨架里**不存在** → "
                         "attach 会静默退化到 mesh 根节点，武器会跑到不合理的位置。" % sock)
        else:
            notes.append("武器当前挂在插槽 '%s'。" % sock)

    if sock and "back" in sock.lower():
        notes.append("→ 武器在**背上**。若此时刚按过装备键，说明装备分支没有被执行到"
                     "（回调没触发 / 守卫条件把它拦掉了）。")
    elif sock and "back" not in sock.lower():
        notes.append("→ 武器不在背上，看起来已经在手侧插槽。")

    if cvars.get("bWeaponBusy") is True:
        notes.append("bWeaponBusy 仍为 true → 上一次装备动作的回调没有复位它，"
                     "后续按键会被防连点直接吃掉。这是「回调没触发」的典型指纹。")

    if cvars.get("bWeaponInHand") is False and sock and "back" not in sock.lower():
        notes.append("bWeaponInHand 为 false 但武器已在手侧插槽 → 状态标记与实际位置不一致，"
                     "下次装备判断会走进错误分支。")

    cur = mesh.get("current_montage")
    playing = mesh.get("montage_playing")
    if cur:
        notes.append("Mesh 当前在播 montage：%s。" % cur)
    elif playing is True:
        notes.append("Mesh 报告 montage 正在播放，但拿不到具体是哪一个"
                     "（见 mesh.montage_probe 里实际命中的属性名）。")
    elif playing is False:
        notes.append("Mesh 当前没有在播 montage。")
    else:
        notes.append("montage 状态读不到（见 mesh.montage_probe）；"
                     "这不代表没在播，别据此下结论。")

    return notes


# ======================================================================
# 实测记录（UE 5.7.4 / Windows）
#
# 可用（本模块依赖）：
#   UnrealEditorSubsystem.get_game_world()          —— 拿到 PIE 世界（官方路径）
#   GameplayStatics.get_player_pawn / get_all_actors_of_class
#   Actor.get_components_by_class
#   Actor.get_attach_parent_socket_name()           —— 读武器当前挂的插槽
#   SkeletalMeshComponent.does_socket_exist / get_socket_location / get_socket_rotation
#   Mesh 属性 anim_montage                          —— 当前 montage
#
# 不存在（踩过，别用）：
#   Actor.get_attach_parent()                       —— 只能通过 root_component 间接拿
#   SceneComponent.get_socket_name()
#   SkeletalMeshComponent.get_active_montages() / get_current_active_montage()
#   unreal.find_all_objects() / unreal.find_all_objects_of_class()
#
# 需要避开的废弃路径：
#   EditorLevelLibrary.get_editor_world()           —— 已废弃，且给的是编辑器世界
# ======================================================================
