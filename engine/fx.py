# -*- coding: utf-8 -*-
r"""
ComboMCP / 特效挂点求解（方案 B：运行时姿态采样）
================================================

问题：把动画通知里的 Niagara 特效对齐到「刀尖 / 掌心 / 拳头」这类位置，只能
在蒙太奇里反复试 ``LocationOffset`` 与 ``RotationOffset``。这两个量是**挂点
局部空间**里的数，而人眼看到的是**组件空间**里的位置——中间隔着一整条骨骼链，
靠试基本试不中。

本模块把这件事变成一次计算：

1. 用 T3D 解析蒙太奇，拿到通知时间、它落在哪个动画段、对应哪条 AnimSequence、
   序列内时间是多少（:mod:`ue.montage_t3d` 已经具备）；
2. 用 commandlet 对那条 AnimSequence 求该时刻的姿态，取挂点与目标骨骼在
   **组件空间**的变换（:mod:`engine.pose`）；
3. 用 ``FTransform`` 的相对变换把「目标相对挂点」直接算成
   ``LocationOffset`` / ``RotationOffset``——这正是通知里要填的两个字段。

三条必须记住的引擎事实（都在源码里核过）：

* ``USkinnedMeshComponent::GetSocketTransform`` 对**不存在的名字静默回落到
  组件变换**（也就是角色根）。于是「名字写错」与「没写名字」结果完全一样：
  特效出现在原点，而且不报任何错。本模块会把这种情况显式报出来。
* ``UAnimNotify_PlayNiagaraEffect::SpawnEffect`` 里 ``LocationOffset`` 在
  两个分支（``Attached`` 与不 ``Attached``）都是**挂点局部空间**的量，
  所以算法只有一套。
* 同函数里有 ``if (Template->IsLooping()) return nullptr;``——循环的 Niagara
  系统**永远不生成**，这时再怎么调偏移都没用。看到「调了没反应」先查这条。

本模块只读：解析缓存、跑 commandlet 采样，不写任何资产。
"""

from engine import offline, pose
from ue import montage_t3d


# ====================================================================== 小工具


def _object_path(package_path):
    """``/Game/A/B.B`` -> ``/Game/A/B``（``unreal.load_asset`` 两种都吃，但短的好读）。"""
    if not package_path:
        return package_path
    if "/" not in package_path:
        return package_path
    head, tail = package_path.rsplit("/", 1)
    if "." in tail:
        tail = tail.split(".", 1)[0]
    return head + "/" + tail


def _quat_rotate(quaternion, vector):
    """按四元数旋转一个向量（纯 Python，不为一次乘法再起一次引擎）。"""
    if not quaternion or len(quaternion) != 4 or not vector or len(vector) != 3:
        return list(vector or [0.0, 0.0, 0.0])
    x, y, z, w = quaternion
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return [vx + w * tx + (y * tz - z * ty),
            vy + w * ty + (z * tx - x * tz),
            vz + w * tz + (x * ty - y * tx)]


def _round_vec(value, digits=4):
    try:
        out = [round(float(v), digits) for v in value]
    except (TypeError, ValueError):
        return None
    return [0.0 if v == 0 else v for v in out]


def _distance(a, b):
    if not a or not b:
        return None
    return round(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5, 2)


def _locate_sequence(short_name):
    """把 T3D 里的短名（``AS_Combo_Attack_01_01_Seq``）还原成包路径。"""
    if not short_name:
        return None
    index = offline.load_index()
    wanted = "/%s" % short_name.lower()
    matches = []
    for key in index:
        try:
            norm = offline.normalize_asset_path(key).lower()
        except Exception:  # noqa: BLE001
            norm = key.lower()
        if norm == wanted or norm.endswith(wanted):
            matches.append(key)
    if not matches:
        return None
    matches.sort(key=len)
    return _object_path(matches[0])


def _resolve_sequence(notify):
    """通知对应的 AnimSequence 包路径。

    优先用 T3D 里 ``LinkedSequence`` / ``AnimReference`` 自带的完整路径——
    那是资产里写死的真值，不依赖缓存索引是否收录了这个序列。
    索引只作兜底（老缓存里没有 ``sequence_path`` 字段时才用到）。
    """
    full = notify.get("sequence_path")
    if full:
        return full
    return _locate_sequence(notify.get("sequence"))


def _read_montage(asset_path):
    """读 T3D 缓存并解析。返回 ``(parsed, resolved, error_dict)``。"""
    text, resolved = offline.read_cached(asset_path)
    if text is None:
        return None, None, {
            "error": "没有 %s 的 T3D 缓存。先调用 sync 收集该资产。" % asset_path,
            "_cache_miss": True,
            "asset": asset_path,
        }
    parsed = montage_t3d.parse_montage(text)
    if parsed is None:
        return None, None, {
            "error": "%s 不是 AnimMontage（T3D 顶层类不是 AnimMontage）" % asset_path,
            "asset": asset_path,
        }
    return parsed, resolved, None


def _pick_notify(parsed, notify_index=None):
    """按 ``notify_index`` 取一条通知；不给序号则取第一条带动画段的。"""
    notifies = parsed.get("notifies") or []
    if not notifies:
        return None, {"error": "这个蒙太奇里没有任何通知。",
                      "notify_count": 0,
                      "hint": "先用 montage 工具确认通知是否真的存在"}
    if notify_index is None:
        for entry in notifies:
            if entry.get("sequence") and entry.get("sequence_time") is not None:
                return entry, None
        return notifies[0], None
    try:
        index = int(notify_index)
    except (TypeError, ValueError):
        return None, {"error": "notify_index 必须是整数",
                      "notify_count": len(notifies)}
    if index < 0 or index >= len(notifies):
        return None, {"error": "notify_index=%s 超出范围（0..%d）"
                               % (notify_index, len(notifies) - 1),
                      "notify_count": len(notifies)}
    return notifies[index], None


def _notify_summary(index, notify):
    """一条通知的稳定摘要（含「它现在挂在哪」）。"""
    props = notify.get("props") or {}
    item = {
        "index": index,
        "name": notify.get("name"),
        "class": notify.get("cls"),
        "kind": notify.get("kind"),
        "time": notify.get("start"),
        "duration": notify.get("duration"),
        "sequence": notify.get("sequence"),
        "sequence_path": notify.get("sequence_path"),
        "sequence_time": notify.get("sequence_time"),
        "socket_name": props.get("socket_name") or "",
        "location_offset": props.get("location_offset") or [0.0, 0.0, 0.0],
        "rotation_offset": props.get("rotation_offset") or [0.0, 0.0, 0.0],
    }
    if notify.get("props", {}).get("template"):
        item["niagara_system"] = props["template"]
    if props.get("attached") is False:
        item["attached"] = False
    if props.get("scale"):
        item["scale"] = props["scale"]
    return item


# ====================================================================== 命令


def notify_map(asset_path):
    """列出蒙太奇里所有通知，以及每条通知当前的挂点与偏移（纯 T3D，不启引擎）。"""
    parsed, resolved, error = _read_montage(asset_path)
    if error:
        return error

    entries = []
    for index, notify in enumerate(parsed.get("notifies") or []):
        entries.append(_notify_summary(index, notify))

    fx_candidates = [e for e in entries
                     if "niagara" in str(e.get("class") or "").lower()
                     or "niagara" in str(e.get("name") or "").lower()]

    return {
        "asset": resolved,
        "length": parsed.get("length"),
        "notify_count": len(entries),
        "notifies": entries,
        "niagara_notifies": len(fx_candidates),
        "note": ("LocationOffset / RotationOffset 是**挂点局部空间**的偏移；"
                 "socket_name 为空表示基准是组件（角色根），特效会出现在原点。"),
        "source": "t3d",
    }


def mounts(asset_path, notify_index=None, engine_root=None):
    """列出某条通知所在那一帧可用的骨骼与 socket（需要起一次 commandlet）。"""
    parsed, resolved, error = _read_montage(asset_path)
    if error:
        return error

    notify, error = _pick_notify(parsed, notify_index)
    if error:
        return error
    index = (parsed.get("notifies") or []).index(notify)

    if not notify.get("sequence"):
        return {"error": "这条通知没有绑定动画段，定位不到 AnimSequence。",
                "notify": _notify_summary(index, notify)}
    sequence = _resolve_sequence(notify)
    if not sequence:
        return {"error": "找不到序列 %s 的包路径（T3D 里没有可用的 AnimReference）。"
                         % notify.get("sequence"),
                "notify": _notify_summary(index, notify)}

    sample_time = notify.get("sequence_time")
    if sample_time is None:
        sample_time = notify.get("start") or 0.0

    report = pose.sample([{
        "id": "mounts",
        "sequence": sequence,
        "time": sample_time,
        "list_bones": True,
    }], engine_root=engine_root)
    if report.get("error"):
        return {"error": report["error"], "_pose": report.get("_pose")}

    entry = (report.get("samples") or [{}])[0]
    if entry.get("error"):
        return {"error": entry["error"], "notify": _notify_summary(index, notify)}

    return {
        "asset": resolved,
        "notify": _notify_summary(index, notify),
        "sample": {"sequence": sequence, "time": sample_time,
                   "elapsed_seconds": report.get("elapsed_seconds")},
        "bone_count": entry.get("bone_count"),
        "bones": entry.get("bone_names") or [],
        "sockets": entry.get("socket_names") or [],
    }


def solve_offset(asset_path, notify_index=None, target_bone=None,
                 target_offset=None, basis_name=None, engine_root=None):
    """算出「特效出现在 target_bone」时通知该填的挂点与偏移。

    返回的 ``suggested`` 就是可直接抄进通知的三个字段：
    ``socket_name`` / ``location_offset`` / ``rotation_offset``。
    """
    if not target_bone:
        return {"error": "缺少 target_bone（想让特效出现在哪根骨骼）。"
                         "可以先用 mounts 看有哪些骨骼名。"}

    parsed, resolved, error = _read_montage(asset_path)
    if error:
        return error

    notify, error = _pick_notify(parsed, notify_index)
    if error:
        return error
    index = (parsed.get("notifies") or []).index(notify)
    summary = _notify_summary(index, notify)

    if not notify.get("sequence"):
        return {"error": "这条通知没有绑定动画段，定位不到 AnimSequence。"
                         "只有落在槽位动画段上的通知才有逐帧姿态可采样。",
                "notify": summary}
    sequence = _resolve_sequence(notify)
    if not sequence:
        return {"error": "找不到序列 %s 的包路径（T3D 里没有可用的 AnimReference）。"
                         % notify.get("sequence"),
                "notify": summary}

    sample_time = notify.get("sequence_time")
    time_warning = None
    if sample_time is None:
        sample_time = notify.get("start") or 0.0
        time_warning = ("通知没有 LinkedSequence，用蒙太奇绝对时间近似采样；"
                        "结果可能偏离真实姿态。")

    offset = _round_vec(target_offset) if target_offset else [0.0, 0.0, 0.0]
    if offset is None or len(offset) != 3:
        return {"error": "target_offset 必须是 3 个数的数组，如 [0, 0, 0]"}

    current_basis = summary.get("socket_name") or ""
    requested_basis = basis_name if basis_name is not None else current_basis

    # 一次命令起两个采样：一个量「保持现有挂点」时的偏移，一个量「改挂到目标骨骼」
    # 时的偏移。都在同一条序列、同一时刻，代价等于一次采样。
    samples = [
        {"id": "keep", "sequence": sequence, "time": sample_time,
         "basis_name": requested_basis, "target_name": target_bone,
         "target_offset": offset, "list_bones": True},
        {"id": "rebase", "sequence": sequence, "time": sample_time,
         "basis_name": target_bone, "target_name": target_bone,
         "target_offset": offset},
    ]
    if requested_basis == target_bone:
        samples.pop()

    report = pose.sample(samples, engine_root=engine_root)
    if report.get("error"):
        return {"error": report["error"], "_pose": report.get("_pose")}

    by_id = {}
    for entry in report.get("samples") or []:
        by_id[entry.get("id")] = entry

    keep = by_id.get("keep") or {}
    rebase = by_id.get("rebase") or keep

    if keep.get("error"):
        failure = {"error": keep["error"], "notify": summary}
        if "suggestions" in keep:
            failure["suggestions"] = keep["suggestions"]
        if keep.get("bone_sample"):
            failure["bone_sample"] = keep["bone_sample"]
        return failure

    basis_kind = keep.get("basis_kind")
    basis_warning = keep.get("basis_warning")
    bone_names = keep.get("bone_names") or []

    # ---- 现在特效落在哪（组件空间）与想去哪
    basis_transform = keep.get("basis") or {}
    basis_location = basis_transform.get("location") or [0.0, 0.0, 0.0]
    basis_rotation = basis_transform.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    current_offset = summary.get("location_offset") or [0.0, 0.0, 0.0]
    rotated = _quat_rotate(basis_rotation, current_offset)
    current_position = [round(basis_location[i] + rotated[i], 4) for i in range(3)]

    target_transform = keep.get("target") or {}
    target_position = target_transform.get("location") or [0.0, 0.0, 0.0]

    gap = _distance(current_position, target_position)

    # ---- 建议值：挂点是有效 socket/骨骼就保持不变，否则改挂到目标骨骼
    keep_current = basis_kind in ("socket", "bone") and bool(requested_basis)
    if keep_current:
        suggested = {
            "socket_name": requested_basis,
            "location_offset": keep.get("location_offset") or [0.0, 0.0, 0.0],
            "rotation_offset": keep.get("rotation_offset") or [0.0, 0.0, 0.0],
            "keep_current_socket": True,
        }
        why = ("现有挂点 %s 是有效的，所以保持不动，只改偏移。"
               % requested_basis)
    else:
        suggested = {
            "socket_name": target_bone,
            "location_offset": rebase.get("location_offset") or offset,
            "rotation_offset": rebase.get("rotation_offset") or [0.0, 0.0, 0.0],
            "keep_current_socket": False,
        }
        why = ("现有挂点写错了或为空，引擎会静默回落到角色根；"
               "直接把 SocketName 设为目标骨骼最省事。")

    notes = []
    if time_warning:
        notes.append(time_warning)
    if basis_warning:
        notes.append(basis_warning)
    if not summary.get("niagara_system"):
        notes.append("这条通知没有 Template（Niagara 系统）引用；请确认它真的是特效通知。")
    if keep_current and abs(gap or 0.0) < 0.5:
        notes.append("当前偏移已经把特效放在目标位置附近（相差 < 0.5 cm），可能不需要改。")

    return {
        "asset": resolved,
        "notify": summary,
        "sample": {"sequence": sequence, "time": sample_time,
                   "elapsed_seconds": report.get("elapsed_seconds")},
        "target": {
            "bone": target_bone,
            "offset": offset,
            "position_cm": _round_vec(target_position),
        },
        "current": {
            "socket_name": requested_basis,
            "basis_kind": basis_kind,
            "location_offset": current_offset,
            "rotation_offset": summary.get("rotation_offset") or [0.0, 0.0, 0.0],
            "effect_position_cm": current_position,
            "distance_to_target_cm": gap,
        },
        "suggested": suggested,
        "notes": notes,
        "why": why,
        "how_to_apply": (
            "打开蒙太奇 %s，选中第 %d 条通知（%s），把下面三个字段改成 suggested 的值："
            " SocketName=%s, LocationOffset=%s, RotationOffset=%s"
            % (resolved, index, summary.get("name"),
               suggested["socket_name"],
               suggested["location_offset"], suggested["rotation_offset"])),
        "units": "cm（UE 单位；1 m = 100 cm）",
        "_pose": report.get("_pose"),
    }
