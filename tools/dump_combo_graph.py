# -*- coding: utf-8 -*-
r"""
渲染连招图，检查 ComboIndex 的写入路径
=====================================

**为什么不能手工解 T3D**：本会话里我在这上面栽了两次——

1. 用 ``Begin Object Class=... Name=...`` 做正则，漏掉了第二遍导出（那一遍**没有
   ``Class=``**，而 ``LinkedTo`` 连线恰好只出现在那一遍），于是对 ComboIndex 报了 0 个引用；
2. 改用 ``End Object`` 当块边界，仍然切错。

``ue/t3d.py`` 已经正确处理了两遍合并（按 ``ExportPath``），``graph_ir.py`` 也已经把
宏与连线还原成执行流。**该用它们，而不是重新发明。**

运行：
  <引擎 python> tools\dump_combo_graph.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine import offline  # noqa: E402

ASSET = "/Game/Combo_Demo/Component/AC_Combat"
KEYWORDS = ("ComboIndex", "IncrementInt", "VariableSet", "Add_IntInt", "SetRef")


def _as_text(payload):
    """graph_dsl 返回的是 dict 而不是字符串，这里把正文取出来。"""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in ("dsl", "text", "render", "code", "lines"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return "\n".join(str(item) for item in value)
        return json.dumps(payload, ensure_ascii=False, indent=1)
    return str(payload)


def main():
    print("=" * 78)
    print("图清单：%s" % ASSET)
    print("=" * 78)
    overview = offline.graph_overview(ASSET)
    print(json.dumps(overview, ensure_ascii=False, indent=1)[:1500])

    for graph in ("EventGraph", "ExecuteUbergraph_AC_Combat"):
        try:
            raw = offline.graph_dsl(ASSET, graph)
        except Exception as exc:
            print("\n!! %s 渲染失败: %s: %s" % (graph, type(exc).__name__, exc))
            continue
        if isinstance(raw, dict):
            print("\n[%s] 返回字段: %s" % (graph, sorted(raw.keys())))
        dsl = _as_text(raw)
        if not dsl.strip():
            print("\n!! %s 渲染结果为空" % graph)
            continue

        print()
        print("=" * 78)
        print("DSL: %s  (%d 字符)" % (graph, len(dsl)))
        print("=" * 78)
        print(dsl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
