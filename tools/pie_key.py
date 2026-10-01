# -*- coding: utf-8 -*-
"""只打印 pie_state 的几个关键字段，便于快速轮询。

用法：
    python tools/pie_key.py
    python tools/pie_key.py --watch 5        # 每 0.5s 打一次，共 5 次
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcpserver import tools as tool_layer  # noqa: E402

KEYS = ("attached_to_socket", "bWeaponInHand", "bWeaponBusy",
        "EquippedWeaponRow", "PendingWeaponRow")


def one():
    payload, _ = tool_layer.call_tool("pie_state", {
        "component_path": "/Game/Combo_Demo/Component/AC_Combat"})
    if not isinstance(payload, dict):
        return "payload not a dict: %r" % (payload,)
    if not payload.get("pie_running"):
        return "PIE 未运行"
    if payload.get("error"):
        return "error: %s" % payload["error"]
    weapon = payload.get("weapon") or {}
    cvars = payload.get("combat_vars") or {}
    parts = ["socket=%-12s" % weapon.get("attached_to_socket")]
    for k in KEYS[1:]:
        parts.append("%s=%s" % (k, cvars.get(k)))
    return "  ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=1, help="采样次数（默认 1）")
    ap.add_argument("--interval", type=float, default=0.5)
    args = ap.parse_args()
    for i in range(max(1, args.watch)):
        if args.watch > 1:
            sys.stdout.write("[%d] " % (i + 1))
        sys.stdout.write(one() + "\n")
        sys.stdout.flush()
        if i + 1 < args.watch:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
