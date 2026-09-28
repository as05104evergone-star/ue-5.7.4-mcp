# -*- coding: utf-8 -*-
r"""
ComboMCP / MCP 侧的蓝图索引缓存与查询
=====================================

索引由编辑器内的 ``ue/index.py`` 产生（只有引擎知道自己有哪些蓝图），
产生后落盘缓存；查询则完全在本地做，不需要再连编辑器——"谁调用了
Montage_Play"这种问题一天可能问几十遍，每次都让编辑器重新扫一遍全项目
是不可接受的。
"""

import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE_DIR = os.path.join(ROOT, "cache")
INDEX_PATH = os.path.join(CACHE_DIR, "blueprint_index.json")


def _ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def save_index(payload, scope="/Game"):
    """把编辑器返回的索引落盘。"""
    _ensure_cache_dir()
    record = {
        "scope": scope,
        "built_at": time.time(),
        "built_at_text": time.strftime("%Y-%m-%d %H:%M:%S"),
        "blueprint_total": payload.get("blueprint_total"),
        "scanned": payload.get("scanned"),
        "truncated": payload.get("truncated"),
        "index": payload.get("index") or [],
    }
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
    os.replace(tmp, INDEX_PATH)
    return record


def load_index():
    """读取索引。返回 ``(record, error)``。"""
    if not os.path.isfile(INDEX_PATH):
        return None, ("尚无蓝图索引。先调用 combo_index(action='build')，"
                      "它会驱动编辑器扫描项目并落盘缓存。")
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle), None
    except Exception as exc:
        return None, "索引文件损坏：%s" % exc


def index_status():
    record, err = load_index()
    if record is None:
        return {"built": False, "hint": err}
    age = time.time() - record.get("built_at", 0)
    return {
        "built": True,
        "built_at": record.get("built_at_text"),
        "age_seconds": int(age),
        "stale": age > 3600,
        "scope": record.get("scope"),
        "blueprint_total": record.get("blueprint_total"),
        "scanned": record.get("scanned"),
        "truncated": record.get("truncated"),
    }


def query(record, query_text, mode="any", limit=40):
    """在索引里检索。

    ``mode``:
      * ``any``      路径 / 父类 / 变量 / 函数 / 调用 任一命中
      * ``call``     谁调用了匹配的函数
      * ``var``      谁读写 / 声明了匹配的变量
      * ``function`` 谁的函数图匹配
      * ``class``    谁的路径或父类匹配
    """
    q = (query_text or "").lower()
    if not q:
        return {"error": "empty query"}

    index = record.get("index") or []
    hits = []

    for bp in index:
        reasons = []

        if mode in ("any", "class"):
            if q in bp.get("path", "").lower():
                reasons.append("path")
            parent = str(bp.get("parent") or "")
            if q in parent.lower():
                reasons.append("parent:%s" % parent)

        if mode in ("any", "call"):
            for fn, count in (bp.get("calls") or {}).items():
                if q in fn.lower():
                    reasons.append("calls %s x%d" % (fn, count))

        if mode in ("any", "var"):
            for var, count in (bp.get("uses_vars") or {}).items():
                if q in var.lower():
                    reasons.append("uses %s x%d" % (var, count))
            for var in (bp.get("variables") or []):
                name = str(var.get("name", ""))
                if q in name.lower():
                    reasons.append("declares %s : %s" % (name, var.get("type")))

        if mode in ("any", "function"):
            for fn in (bp.get("functions") or []):
                if q in fn.lower():
                    reasons.append("function %s" % fn)

        if reasons:
            hits.append({
                "path": bp.get("path"),
                "parent": bp.get("parent"),
                "why": reasons[:8],
                "graphs": bp.get("graphs"),
            })
            if len(hits) >= limit:
                break

    return {
        "query": query_text,
        "mode": mode,
        "count": len(hits),
        "index_age": record.get("built_at_text"),
        "hits": hits,
    }


def callers_of(record, function_name, limit=60):
    """谁调用了某函数——连招调试里最常用的一问。"""
    q = (function_name or "").lower()
    hits = []
    for bp in record.get("index") or []:
        matched, total = [], 0
        for fn, count in (bp.get("calls") or {}).items():
            if q in fn.lower():
                matched.append(fn)
                total += count
        if total:
            hits.append({
                "path": bp.get("path"),
                "call_count": total,
                "functions": matched[:8],
            })
    hits.sort(key=lambda h: -h["call_count"])
    return {
        "function": function_name,
        "blueprints": len(hits),
        "index_age": record.get("built_at_text"),
        "hits": hits[:limit],
    }


def variables_of(record, variable_name, limit=60):
    """谁读写某变量。"""
    q = (variable_name or "").lower()
    hits = []
    for bp in record.get("index") or []:
        matched, total = [], 0
        for var, count in (bp.get("uses_vars") or {}).items():
            if q in var.lower():
                matched.append(var)
                total += count
        declares = [v for v in (bp.get("variables") or [])
                    if q in str(v.get("name", "")).lower()]
        if total or declares:
            hits.append({
                "path": bp.get("path"),
                "use_count": total,
                "variables": matched[:8],
                "declares": [v.get("name") for v in declares][:8],
            })
    hits.sort(key=lambda h: -h["use_count"])
    return {
        "variable": variable_name,
        "blueprints": len(hits),
        "index_age": record.get("built_at_text"),
        "hits": hits[:limit],
    }
