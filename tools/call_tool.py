# -*- coding: utf-8 -*-
"""直接调 ComboMCP 工具层，绕过命令行引号解析（Windows PowerShell 会吃掉 JSON 引号）。

用法（在 Plugins/ComboMCP 下执行）：
    python tools/call_tool.py pie_state component_path=/Game/.../AC_Combat
    python tools/call_tool.py status
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mcpserver import tools as tool_layer  # noqa: E402


def _coerce(text):
    """把 k=v 的 v 转成合适的 Python 类型。"""
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def main(argv):
    if not argv:
        names = [t["name"] for t in tool_layer.list_tools()]
        sys.stdout.write("tools (%d):\n  %s\n" % (len(names), "\n  ".join(names)))
        return 0

    name = argv[0]
    arguments = {}
    for item in argv[1:]:
        if "=" not in item:
            sys.stderr.write("bad argument (want k=v): %s\n" % item)
            return 2
        key, value = item.split("=", 1)
        arguments[key] = _coerce(value)

    payload, is_error = tool_layer.call_tool(name, arguments)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=1, default=str) + "\n")
    return 1 if is_error else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
