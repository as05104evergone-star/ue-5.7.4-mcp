# -*- coding: utf-8 -*-
r"""
ComboMCP / 日志读取（纯本地，不需要编辑器）
==========================================

直接读 ``<项目>/Saved/Logs/`` 下的日志文件，而不是去问编辑器的 Output Log。

为什么这么选
------------
* **5.6 根本没有这个 API。** 实测 UE 5.6 的 ``unreal`` 模块里没有
  ``log_capture_get``（5.7 才引入），所以"从编辑器取日志"这条路本身就不通。
* **要求编辑器开着不划算。** 排查问题最常见的情形恰恰是"编辑器刚崩过"——
  那时候能读的只有磁盘上的文件。
* **文件路径在 5.6 / 5.7 上一致**，跨版本没有差异。

所以这条路是本地文件解析：不需要编辑器、不需要 Remote Execution、不需要引擎。
"""

import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Plugins/ComboMCP -> 上两级是项目根
PROJECT_ROOT = os.path.dirname(os.path.dirname(ROOT))

# UE 日志行形如：
#   [2026.10.14-01.38.02:497][  0]LogPython: Warning: ...
#   [2026.10.14-01.38.02:530][  0]LogStreaming: Display: ...
# 前缀里的时间戳与帧号不一定存在（早期的行没有），所以两段都可选。
_RE_LOG_LINE = re.compile(
    r'^(?:\[(?P<time>[\d.\-:]+)\]\[\s*(?P<frame>\d+)\])?'
    r'(?P<category>[A-Za-z_][\w.]*)?:\s*(?P<rest>.*)$')

_LEVELS = (
    ("fatal", ("fatal error", "critical error", "=== critical error")),
    ("error", ("error:", "error ")),
    ("warning", ("warning:", "warning ")),
)

# 这些类别本身就带诊断价值，即使行本身是 Display 也值得单独归一类
_NOTABLE_CATEGORIES = ("LogPython", "LogBlueprint", "LogAnimation", "LogScript",
                       "LogLoad", "LogAssetRegistry", "LogSlate", "LogCrash")


def logs_dir():
    return os.path.join(PROJECT_ROOT, "Saved", "Logs")


def available():
    """列出可用日志文件（最新的在前）。"""
    directory = logs_dir()
    if not os.path.isdir(directory):
        return []
    out = []
    for name in os.listdir(directory):
        if not name.endswith(".log"):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append({
            "file": name,
            "bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(stat.st_mtime)),
            "age_seconds": int(time.time() - stat.st_mtime),
            "is_backup": "-backup-" in name,
        })
    out.sort(key=lambda item: item["modified"], reverse=True)
    return out


def newest_file(prefer=None):
    """挑一个日志文件。

    ``prefer`` 给名字片段时优先它；否则挑**非 backup** 的最新文件——
    编辑器正在写的就是那一个，而 backup 是上一次启动的。
    没有任何非 backup 文件时退回最新的 backup。
    """
    files = available()
    if not files:
        return None, None

    if prefer:
        low = prefer.lower()
        for entry in files:
            if low in entry["file"].lower():
                return entry, os.path.join(logs_dir(), entry["file"])
        return None, None

    for entry in files:
        if not entry["is_backup"]:
            return entry, os.path.join(logs_dir(), entry["file"])
    return files[0], os.path.join(logs_dir(), files[0]["file"])


def classify(line):
    """给一行日志定级别。返回 ``"fatal" | "error" | "warning" | "info"``。"""
    low = line.lower()
    for level, needles in _LEVELS:
        for needle in needles:
            if needle in low:
                return level
    return "info"


def parse_line(line):
    """拆出 ``{time, frame, category, level, text}``。"""
    match = _RE_LOG_LINE.match(line)
    entry = {
        "time": (match.group("time") if match else None),
        "frame": (match.group("frame") if match else None),
        "category": (match.group("category") if match else None),
        "level": classify(line),
        "text": line,
    }
    if match and match.group("rest"):
        entry["message"] = match.group("rest")
    return entry


def read(tail=200, pattern=None, severity="all", file=None, grep_category=None):
    """读日志。

    ``severity``：``all`` / ``error`` / ``warning``。
    ``error`` 只给 fatal+error；``warning`` 给 fatal+error+warning
    （因为查"有什么不对劲"时通常也想看到 error）。
    """
    directory = logs_dir()
    if not os.path.isdir(directory):
        return {
            "error": "找不到日志目录：%s" % directory,
            "hint": ("日志只有编辑器/commandlet 启动过才会生成。"
                     "确认插件装在 <项目>/Plugins/ComboMCP 下（否则反推不到项目根）。"),
            "project_root": PROJECT_ROOT,
        }

    entry, path = newest_file(prefer=file)
    if path is None:
        if file:
            return {"error": "没有匹配的日志文件", "want": file,
                    "available": [e["file"] for e in available()[:20]]}
        return {"error": "日志目录里没有 .log 文件", "dir": directory}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except Exception as exc:
        return {"error": "读日志失败: %s: %s" % (type(exc).__name__, exc),
                "file": path}

    want_levels = {"all": ("fatal", "error", "warning", "info"),
                   "error": ("fatal", "error"),
                   "warning": ("fatal", "error", "warning")}.get(
                       (severity or "all").lower(), ("fatal", "error", "warning", "info"))

    matcher = None
    if pattern:
        try:
            matcher = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return {"error": "pattern 不是合法正则: %s" % exc, "pattern": pattern}

    selected = []
    for line in lines:
        if not line.strip():
            continue
        parsed = parse_line(line)
        if parsed["level"] not in want_levels:
            continue
        if matcher is not None and not matcher.search(line):
            continue
        if grep_category and (parsed["category"] or "").lower() != \
                grep_category.lower():
            continue
        selected.append(parsed)

    counts = {}
    for parsed in selected:
        counts[parsed["level"]] = counts.get(parsed["level"], 0) + 1

    tail = max(1, int(tail))
    shown = selected[-tail:]

    out = {
        "ok": True,
        "file": entry["file"],
        "path": path,
        "file_modified": entry["modified"],
        "total_lines": len(lines),
        "matched": len(selected),
        "returned": len(shown),
        "truncated": len(selected) > len(shown),
        "severity_counts": counts,
        "lines": shown,
    }
    if counts.get("fatal"):
        out["verdict"] = ("日志里有 FATAL/CRITICAL —— 进程崩过。"
                          "崩溃行之后的日志通常不完整。")
    elif counts.get("error"):
        out["verdict"] = "有 error 级日志，见上面的 lines。"
    elif not selected:
        out["verdict"] = "按当前过滤条件没有命中；试试 severity=all 或去掉 pattern。"
    return out
