# -*- coding: utf-8 -*-
r"""
ComboMCP / 姿态采样（headless commandlet）
==========================================

T3D 只存**静态结构**，没有逐帧姿态。要回答"这个特效该偏多少"，就必须知道
通知触发那一帧目标骨骼（或挂点）在**组件空间**的位置与朝向——这个只能求值。

为什么求值必须在引擎里做：

* :func:`unreal.AnimPoseExtensions.get_anim_pose_at_time` 属于
  ``AnimationBlueprintLibrary``（Editor 模块），编辑器外的 Python 没有它；
* 但它**不能**直接喂 AnimMontage——``UAnimMontage::GetAnimationPose`` 在
  ``Engine/Classes/Animation/AnimMontage.h:770`` 里是 ``check(false)``，
  传进蒙太奇会直接把进程杀掉。必须先由 T3D 把
  "蒙太奇时间 -> 序列 + 序列内时间" 解出来（见 :mod:`ue.montage_t3d`），
  再对 **AnimSequence** 求值。

实测代价：项目预热后约 10 秒，冷启动约 40 秒。所以一次请求允许多个采样点，
把启动成本摊薄；本模块不缓存结果（姿态随时长变化，缓存反而是陷阱）。

本模块只**读取**，不修改任何资产。
"""

import json
import os
import subprocess
import time

from engine import collector

WORK_DIR = os.path.join(collector.ROOT, "cache", "pose")


# 在编辑器里执行的采样脚本。用占位符替换，避免和代码里的花括号打架。
_SAMPLE_SCRIPT = r'''# -*- coding: utf-8 -*-
"""ComboMCP 姿态采样（在引擎内执行，只读）。由 engine/pose.py 生成。"""
import difflib
import json
import os

import unreal

REQ = __REQUEST__
OUT = __OUTPUT__

WORLD = unreal.AnimPoseSpaces.WORLD      # World = 组件空间，正是通知偏移所在的空间

report = {"engine": unreal.SystemLibrary.get_engine_version(),
          "samples": [], "errors": []}


def _n(value, digits):
    """四舍五入，并把 -0.0 归一成 0.0（否则输出里会出现刺眼的 ``-0.0``）。"""
    out = round(float(value), digits)
    return 0.0 if out == 0 else out


def _vec(v):
    return [_n(v.x, 4), _n(v.y, 4), _n(v.z, 4)]


def _quat(q):
    return [_n(q.x, 6), _n(q.y, 6), _n(q.z, 6), _n(q.w, 6)]


def _rot(r):
    return [_n(r.pitch, 4), _n(r.yaw, 4), _n(r.roll, 4)]


def _describe(transform):
    return {"location": _vec(transform.translation),
            "rotation": _quat(transform.rotation),
            "rotator": _rot(transform.rotation.rotator()),
            "scale": _vec(transform.scale3d)}


def _pose_of(pose, kind, name):
    """按 ``kind`` 取一个变换；``kind`` 只能是 socket / bone。"""
    if kind == "socket":
        return unreal.AnimPoseExtensions.get_socket_pose(pose, name, WORLD)
    return unreal.AnimPoseExtensions.get_bone_pose(pose, name, WORLD)


def _resolve_mount(pose, name, bone_names, socket_names):
    """复刻 ``USkinnedMeshComponent::GetSocketTransform`` 的解析顺序。

    引擎先查 socket、再查骨骼、**都查不到就静默回落到组件变换**——也就是
    角色根。这正是"名字写错 -> 特效出现在原点"而且不报任何错的根因，
    所以这里必须把它显式报出来，不能跟着引擎一起沉默。
    """
    if not name:
        return "component", "未指定挂点：引擎用组件变换（角色根）作基准"
    if name in socket_names:
        return "socket", None
    if name in bone_names:
        return "bone", None
    return "component", ("挂点名 '%s' 既不是 socket 也不是骨骼；引擎会静默回落到"
                         "组件变换（角色根），特效因此出现在原点" % name)


with open(REQ, "r", encoding="utf-8") as fh:
    request = json.load(fh)

options = unreal.AnimPoseEvaluationOptions()
options.set_editor_property("evaluation_type", unreal.AnimDataEvalType.RAW)
options.set_editor_property("should_retarget", False)
options.set_editor_property("extract_root_motion", False)
options.set_editor_property("evaluate_curves", False)

for item in request.get("samples") or []:
    entry = {"id": item.get("id"), "sequence": item.get("sequence"),
             "time": item.get("time")}
    sequence_path = item.get("sequence")
    sample_time = float(item.get("time") or 0.0)

    sequence = unreal.load_asset(sequence_path) if sequence_path else None
    if sequence is None:
        entry["error"] = "找不到动画序列：%s" % sequence_path
        report["samples"].append(entry)
        continue

    try:
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(sequence, sample_time, options)
    except Exception as exc:  # noqa: BLE001
        entry["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:300])
        report["samples"].append(entry)
        continue

    try:
        valid = unreal.AnimPoseExtensions.is_valid(pose)
    except Exception:  # noqa: BLE001
        valid = True
    if not valid:
        entry["error"] = "姿态无效（时间超出序列长度？）"
        report["samples"].append(entry)
        continue

    bone_names = [str(b) for b in unreal.AnimPoseExtensions.get_bone_names(pose)]
    socket_names = [str(s) for s in unreal.AnimPoseExtensions.get_socket_names(pose)]
    entry["bone_count"] = len(bone_names)
    entry["socket_names"] = socket_names

    # 只要清单（没有目标骨骼）时到此为止：挂点清点不需要算相对变换
    if item.get("list_bones"):
        entry["bone_names"] = bone_names
    if not item.get("target_name"):
        report["samples"].append(entry)
        continue

    # ---- 基准：通知最终会以它为父
    basis_kind, basis_warning = _resolve_mount(
        pose, item.get("basis_name"), bone_names, socket_names)
    if basis_warning:
        entry["basis_warning"] = basis_warning
    entry["basis_kind"] = basis_kind
    if basis_kind == "component":
        basis = unreal.Transform()
    else:
        basis = _pose_of(pose, basis_kind, item.get("basis_name"))
    entry["basis"] = _describe(basis)

    # ---- 目标：想让特效出现在哪根骨骼（或哪根骨骼上的偏移）
    target_name = item.get("target_name")
    if target_name in bone_names:
        target_kind = "bone"
    elif target_name in socket_names:
        target_kind = "socket"
    else:
        entry["error"] = "目标 '%s' 既不是骨骼也不是 socket" % target_name
        pool = list(bone_names) + list(socket_names)
        suggestions = difflib.get_close_matches(str(target_name), pool, n=8, cutoff=0.5)
        if not suggestions:
            needle = str(target_name).lower()
            suggestions = [b for b in pool if needle and needle in b.lower()][:8]
        entry["suggestions"] = suggestions
        entry["bone_sample"] = bone_names[:20]
        report["samples"].append(entry)
        continue
    target = _pose_of(pose, target_kind, target_name)

    # ---- 目标骨骼上的微调偏移（局部空间 -> 组件空间）
    offset = item.get("target_offset") or [0.0, 0.0, 0.0]
    try:
        offset = [float(offset[0]), float(offset[1]), float(offset[2])]
    except Exception:  # noqa: BLE001
        offset = [0.0, 0.0, 0.0]
    if any(abs(v) > 1e-6 for v in offset):
        vector = unreal.Vector(offset[0], offset[1], offset[2])
        try:
            world_offset = unreal.MathLibrary.quat_rotate_vector(target.rotation, vector)
        except Exception:  # noqa: BLE001
            world_offset = target.transform_location(vector) - target.translation
        shifted = target.copy()
        shifted.set_editor_property("translation", target.translation + world_offset)
        target = shifted

    entry["target_kind"] = target_kind
    entry["target"] = _describe(target)

    # ---- 相对变换 = 目标相对基准。这正是通知里要填的两个字段。
    relative = unreal.MathLibrary.make_relative_transform(target, basis)
    entry["location_offset"] = _vec(relative.translation)
    entry["rotation_offset"] = _rot(relative.rotation.rotator())
    entry["scale"] = _vec(relative.scale3d)
    entry["distance"] = round(float((target.translation - basis.translation).length()), 4)
    report["samples"].append(entry)

with open(OUT, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)

print("__POSE_DONE__")
'''


def sample(samples, engine_root=None, timeout=900):
    """对一个或多个采样点求姿态。

    ``samples`` 是列表，每项::

        {"id": "s0",                  # 可选，回传给调用方
         "sequence": "/Game/.../AS_X", # AnimSequence 的包路径（不能是 Montage）
         "time": 0.21,                # 序列内时间（秒）
         "basis_name": "weapon_r",    # 通知的 SocketName；空 = 组件
         "target_name": "weapon_r",   # 想让特效出现在哪
         "target_offset": [0, 0, 0]}  # 相对目标骨骼的微调（骨骼局部空间）

    返回 ``{"ok": True, "elapsed_seconds": .., "samples": [...], "engine": ..}``；
    失败返回 ``{"error": ...}``。
    """
    samples = [s for s in (samples or []) if s]
    if not samples:
        return {"error": "没有采样点"}

    try:
        os.makedirs(WORK_DIR, exist_ok=True)
    except OSError:
        pass

    try:
        _engine, exe = collector._find_editor_cmd(engine_root)
        project = collector.project_file()
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "_pose": "setup_failed"}

    request_path = os.path.join(WORK_DIR, "request.json")
    output_path = os.path.join(WORK_DIR, "result.json")
    script_path = os.path.join(WORK_DIR, "sample_script.py")

    with open(request_path, "w", encoding="utf-8") as handle:
        json.dump({"samples": samples}, handle, ensure_ascii=False)

    source = (_SAMPLE_SCRIPT
              .replace("__REQUEST__", json.dumps(request_path))
              .replace("__OUTPUT__", json.dumps(output_path)))
    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(source)

    if os.path.isfile(output_path):
        try:
            os.remove(output_path)
        except OSError:
            pass

    cmd = [
        exe, project,
        "-run=PythonScript", "-Script=%s" % script_path,
        "-unattended", "-nosplash", "-nop4", "-nullrhi",
        "-EnablePlugins=PythonScriptPlugin",
        "-stdout",
    ]

    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=collector.PROJECT_ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        returncode = proc.returncode
        tail = (proc.stdout or "")[-2000:]
    except subprocess.TimeoutExpired:
        return {"error": "姿态采样超时（%ds）" % timeout, "_pose": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"error": "%s: %s" % (type(exc).__name__, exc), "_pose": "spawn_failed"}

    elapsed = time.time() - started
    if not os.path.isfile(output_path):
        return {"error": "采样步骤没有产出结果（返回码 %s）" % returncode,
                "_pose": "no_result", "stdout_tail": tail}

    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
    except Exception as exc:  # noqa: BLE001
        return {"error": "结果文件读不出来：%s" % exc, "_pose": "bad_result"}

    report["ok"] = True
    report["elapsed_seconds"] = round(elapsed, 1)
    report["_pose"] = "commandlet"
    return report
