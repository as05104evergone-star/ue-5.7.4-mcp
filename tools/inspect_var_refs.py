# -*- coding: utf-8 -*-
r"""
查看某个变量在蓝图各图里的引用点
================================

用法：
  <引擎 python> tools\inspect_var_refs.py [资产路径] [变量名]

默认：/Game/Combo_Demo/Component/AC_Combat  ComboIndex

**这个工具的历史教训（留在这里免得后人重犯）**：前两版自己写正则去切 T3D，
结果对有 21 处引用的变量报了 0。原因有两个，都很隐蔽：

1. 第一版要求 ``Begin Object Class=...``，而 **T3D 是两遍导出**，第二遍**没有
   ``Class=``**——偏偏 ``LinkedTo`` 连线只出现在第二遍，于是连线全丢；
2. 第二版改用 ``End Object`` 当块边界，仍然切错。

正确做法是用 ``ue/t3d.py``（已按 ``ExportPath`` 合并两遍）与 ``graph_ir.py``
（已还原宏与执行流）——它们有 201 项测试覆盖。本工具现在就走这条路。

**注意**：DSL 里"变量名单独成行"代表对它 Set。但宏**内部**的写回（例如
``IncrementInt`` 通过 by-ref 参数递增调用者的变量）不会体现在这里——那种情况
要用 ``tools/inspect_macro.py`` 去读宏自己的图。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import offline  # noqa: E402

DEFAULT_ASSET = "/Game/Combo_Demo/Component/AC_Combat"
DEFAULT_VAR = "ComboIndex"


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
    asset = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ASSET
    var = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_VAR

    if not offline.has_cache(asset):
        print("没有缓存：%s" % asset)
        print("先采集一次（MCP 的 sync 工具，或 tools/resync_changed.py）")
        return 1

    overview = offline.graph_overview(asset)
    graphs = overview.get("graphs", []) or []
    print("资产 : %s" % asset)
    print("变量 : %s" % var)
    print("图数 : %d" % len(graphs))
    print("=" * 78)

    total_sets = 0
    for info in graphs:
        name = info.get("graph")
        if not name:
            continue
        dsl = _as_text(offline.graph_dsl(asset, name))
        hits = [ln for ln in dsl.splitlines() if var in ln]
        if not hits:
            continue

        sets = [ln for ln in hits if ln.strip() == var]
        total_sets += len(sets)
        print()
        print("--- %s （%d 处引用，其中 Set %d 处）---"
              % (name, len(hits), len(sets)))
        for line in hits:
            tag = "SET " if line.strip() == var else "ref "
            text = line.strip()
            if len(text) > 150:
                text = text[:150] + "..."
            print("  [%s] %s" % (tag, text))

    print()
    print("=" * 78)
    print("合计 Set 点（DSL 层面）：%d" % total_sets)
    print()
    print("提醒：宏内部的写回不在此列。若该变量被传给了引用参数的宏")
    print("（如 IncrementInt），要用 tools/inspect_macro.py 读宏的图来确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
