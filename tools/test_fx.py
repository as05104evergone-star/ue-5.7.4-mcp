# -*- coding: utf-8 -*-
r"""engine/fx.py 的离线回归测试（不启动引擎）。

只覆盖**不需要 commandlet** 的路径：T3D 解析、通知选择、参数校验、纯数学。
真正需要采样姿态的 ``mounts`` / ``solve_offset`` 成功路径依赖 UE 进程，属于
tools/test_pose_smoke.py 的手工验收范围，这里不跑。

跑法：
    python tools/test_fx.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import fx  # noqa: E402

PASS = 0
FAIL = 0
FAILED = []


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  [ OK ] %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  [FAIL] %s%s" % (name, ("  -> " + str(detail)) if detail else ""))


def section(title):
    print("\n%s" % title)


# 缓存里存在、且路径与缓存文件名对得上的蒙太奇
COUNTER = "/Game/Combo_Demo/Animation/Montage/Parry/AM_Counter"
# 缓存里存在，但不是蒙太奇
NOT_MONTAGE = "/Game/Blueprints/SmartObjects/TasksAndConditions/STT_PlayAnimMontage"
MISSING = "/Game/No/Such/Montage"


def main():
    section("[1] notify_map：正常路径")
    result = fx.notify_map(COUNTER)
    if "error" in result:
        check("AM_Counter 能读出通知", False, result.get("error"))
        return
    check("AM_Counter 有 2 条通知", result["notify_count"] == 2,
          result["notify_count"])
    check("length 与 T3D 一致（1.5）", abs(result["length"] - 1.5) < 1e-6,
          result["length"])
    check("本项目没有 Niagara 通知", result["niagara_notifies"] == 0,
          result["niagara_notifies"])
    check("notifies 是列表", isinstance(result["notifies"], list))
    first = result["notifies"][0]
    for key in ("index", "name", "class", "time", "sequence",
                "socket_name", "location_offset", "rotation_offset"):
        check("通知含字段 %s" % key, key in first, sorted(first))

    section("[2] notify_map：错误路径")
    missing = fx.notify_map(MISSING)
    check("缺失缓存报错", "error" in missing, missing)
    check("缺失缓存带 _cache_miss 标记", missing.get("_cache_miss") is True)
    check("错误里带资产路径", MISSING in (missing.get("asset") or ""))
    not_montage = fx.notify_map(NOT_MONTAGE)
    check("非蒙太奇报错", "error" in not_montage, not_montage)
    check("非蒙太奇错误说明不是 AnimMontage",
          "AnimMontage" in (not_montage.get("error") or ""))

    section("[3] _pick_notify：默认选择与越界")
    parsed = fx._read_montage(COUNTER)[0]
    picked, err = fx._pick_notify(parsed)
    check("不给序号能选中一条", err is None and picked is not None)
    check("默认优先选带动画段的通知",
          picked.get("sequence_time") is not None or not any(
              n.get("sequence_time") is not None for n in parsed["notifies"]))
    none_notify, err = fx._pick_notify(parsed, 99)
    check("序号越界报错", err is not None and "超出范围" in err["error"])
    check("越界错误带上界", err.get("notify_count") == 2, err)
    _, err = fx._pick_notify(parsed, "abc")
    check("序号非整数报错", err is not None and "整数" in err["error"])
    _, err = fx._pick_notify({"notifies": []})
    check("空通知列表报错", err is not None and err.get("notify_count") == 0)

    section("[4] solve_offset：参数校验（不启引擎）")
    no_target = fx.solve_offset(COUNTER)
    check("缺 target_bone 报错", "error" in no_target, no_target)
    check("报错提示可用 mounts 查骨骼名",
          "mounts" in (no_target.get("error") or ""))
    bad_offset = fx.solve_offset(COUNTER, target_bone="hand_r",
                                 target_offset=[1, 2])
    check("target_offset 长度错误报错", "error" in bad_offset, bad_offset)
    check("报错说明了正确形状", "3 个数" in (bad_offset.get("error") or ""))
    bad_asset = fx.solve_offset(MISSING, target_bone="hand_r")
    check("资产缺失时不启引擎直接报错", "error" in bad_asset, bad_asset)

    section("[5] 纯数学：四元数旋转与距离")
    check("单位四元数不改变向量",
          fx._quat_rotate([0, 0, 0, 1], [1, 2, 3]) == [1.0, 2.0, 3.0])
    import math
    s = math.sin(math.pi / 4)
    rotated = fx._quat_rotate([0, 0, s, s], [1, 0, 0])
    check("绕 Z 轴 90° 把 +X 转到 +Y",
          abs(rotated[0]) < 1e-9 and abs(rotated[1] - 1) < 1e-9, rotated)
    check("坏输入回落成零向量", fx._quat_rotate(None, [1, 2, 3]) == [1.0, 2.0, 3.0])
    check("距离计算", fx._distance([0, 0, 0], [3, 4, 0]) == 5.0)
    check("距离缺参返回 None", fx._distance(None, [1, 2, 3]) is None)
    check("-0.0 被归一成 0.0", fx._round_vec([-0.0, -0.00001]) == [0.0, 0.0])
    check("非法向量返回 None", fx._round_vec("nope") is None)

    section("[6] _object_path：包路径归一")
    check("/Game/A/B.B -> /Game/A/B",
          fx._object_path("/Game/A/B.B") == "/Game/A/B")
    check("已归一的不变", fx._object_path("/Game/A/B") == "/Game/A/B")
    check("无斜杠原样返回", fx._object_path("B.B") == "B.B")
    check("空值原样返回", fx._object_path("") == "")

    section("[7] 所有返回值都可 JSON 序列化")
    payloads = [fx.notify_map(COUNTER), fx.notify_map(MISSING),
                fx.notify_map(NOT_MONTAGE), fx.solve_offset(COUNTER),
                fx.solve_offset(COUNTER, target_bone="hand_r", target_offset=[1, 2])]
    try:
        json.dumps(payloads, ensure_ascii=False)
        check("全部可序列化", True)
    except (TypeError, ValueError) as exc:  # noqa: BLE001
        check("全部可序列化", False, exc)

    print("\n" + "=" * 66)
    print("通过 %d 项，失败 %d 项" % (PASS, FAIL))
    if FAILED:
        print("失败项：")
        for name in FAILED:
            print("  - %s" % name)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
