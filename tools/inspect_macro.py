# -*- coding: utf-8 -*-
r"""
读引擎标准宏的内部实现
======================

排查"这个宏到底有没有副作用"必须看**宏自己的图**，不能猜，也不能只看调用点。

例：``IncrementInt`` 的 ``Value`` 引脚是 ``bIsReference=True``（引用传递），
那么它究竟会不会写回调用者的变量，完全取决于宏内部有没有对 ``Value`` 赋值。
调用点的 T3D 只能告诉你"传的是引用"，告诉不了你"宏里写了什么"。

用法：
  <引擎 python> tools\inspect_macro.py [宏名]

默认 IncrementInt。宏所在的蓝图是引擎自带的 StandardMacros。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import collector, offline  # noqa: E402

MACRO_BP = "/Engine/EditorBlueprintResources/StandardMacros"


def _as_text(payload):
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("dsl", "text", "render", "code"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return json.dumps(payload, ensure_ascii=False, indent=1)
    return str(payload)


def main():
    macro = sys.argv[1] if len(sys.argv) > 1 else "IncrementInt"

    print("宏所在蓝图 : %s" % MACRO_BP)
    print("宏名       : %s" % macro)
    print("=" * 78)

    if not offline.has_cache(MACRO_BP):
        print("首次读取，先采集一次（引擎资产，约几秒）...")
        result = collector.collect([MACRO_BP], force=False)
        print(json.dumps(result, ensure_ascii=False)[:500])
    else:
        print("(已有缓存)")

    overview = offline.graph_overview(MACRO_BP)
    names = [g.get("graph") for g in overview.get("graphs", [])]
    print()
    print("宏蓝图里共 %d 个图，含 '%s' 的：" % (len(names), macro))
    for name in names:
        if macro.lower() in str(name).lower():
            print("   ", name)

    dsl = _as_text(offline.graph_dsl(MACRO_BP, macro))
    print()
    print("=" * 78)
    print("DSL: %s" % macro)
    print("=" * 78)
    print(dsl)

    # 直接回答那个关键问题：宏内部有没有对输入引脚赋值
    print()
    print("=" * 78)
    body = dsl
    wrote_back = any(
        line.strip() in ("Value", "Value ")
        for line in body.splitlines())
    print("宏内部是否出现对 Value 的赋值（DSL 里变量名单独成行 = Set）：%s"
          % ("是 —— 会写回调用者的变量" if wrote_back else "否"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
