# -*- coding: utf-8 -*-
r"""
ComboMCP / T3D 收集器（headless commandlet）
===========================================

把资产的 T3D 导出到本地缓存。走的是 ::

    UnrealEditor-Cmd.exe <project>.uproject -run=PythonScript -Script=<临时脚本>
        -unattended -nosplash -nop4 -nullrhi -EnablePlugins=PythonScriptPlugin

而不是 Remote Execution。这个选择是被实测逼出来的：Remote Execution 需要
``bRemoteExecution`` 打开（默认 false，且没有命令行开关），而 commandlet
**不需要编辑器开着、也不需要任何通道**，实测可直接对已打开的项目运行。

代价是每次要加载一次项目（用户项目约 40 秒）。所以：

* 已经缓存过的资产默认跳过（``force=True`` 才重导）
* 一次调用可以批量导出多个资产，把加载成本摊薄
* 导出之后所有解析与诊断都在本地毫秒级完成（见 :mod:`engine.offline`）

本模块只**导出**，不修改任何资产。
"""

import json
import os
import subprocess
import sys
import time

from engine import bridge

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE_T3D = os.path.join(ROOT, "cache", "t3d")
INDEX_PATH = os.path.join(CACHE_T3D, "_index.json")
WORK_DIR = os.path.join(ROOT, "cache", "collector")

# 项目根：Plugins/ComboMCP -> 上两级
PROJECT_ROOT = os.path.dirname(os.path.dirname(ROOT))


# 在编辑器里执行的导出脚本。用占位符而不是 str.format，避免和代码里的花括号打架。
_EXPORT_SCRIPT = r'''# -*- coding: utf-8 -*-
"""ComboMCP 收集器脚本（在编辑器内执行，只读）。由 engine/collector.py 生成。"""
import json
import os

import unreal

CACHE_DIR = __CACHE_DIR__
TARGETS = __TARGETS__
FORCE = __FORCE__

os.makedirs(CACHE_DIR, exist_ok=True)
index = {}
if not FORCE:
    try:
        with open(__INDEX_PATH__, "r", encoding="utf-8") as fh:
            index = json.load(fh)
    except Exception:
        index = {}

report = {"engine": unreal.SystemLibrary.get_engine_version(),
          "exported": [], "skipped": [], "failed": []}


def safe_name(asset_path):
    return asset_path.replace("/", "_").replace(":", "_").replace(".", "_") + ".t3d"


def export_one(asset_path):
    out_path = os.path.join(CACHE_DIR, safe_name(asset_path))
    if (not FORCE) and os.path.isfile(out_path):
        report["skipped"].append(asset_path)
        return

    try:
        asset = unreal.load_asset(asset_path)
    except Exception as exc:
        report["failed"].append({"asset": asset_path, "error": str(exc)[:200]})
        return
    if asset is None:
        report["failed"].append({"asset": asset_path, "error": "asset not found"})
        return

    try:
        if os.path.isfile(out_path):
            os.remove(out_path)
    except Exception:
        pass

    try:
        task = unreal.AssetExportTask()
        task.set_editor_property("object", asset)
        task.set_editor_property("filename", out_path)
        task.set_editor_property("automated", True)
        task.set_editor_property("replace_identical", True)
        task.set_editor_property("prompt", False)
        task.set_editor_property("use_file_archive", True)
        if hasattr(unreal, "ObjectExporterT3D"):
            task.set_editor_property("exporter", unreal.ObjectExporterT3D())
        result = unreal.Exporter.run_asset_export_task(task)
    except Exception as exc:
        report["failed"].append({"asset": asset_path,
                                 "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        return

    if not os.path.isfile(out_path):
        report["failed"].append({"asset": asset_path, "error": "no file produced"})
        return

    size = os.path.getsize(out_path)
    cls = asset.get_class().get_name()
    index[asset_path] = {"file": os.path.basename(out_path), "class": cls,
                         "bytes": size,
                         "exported_at": __NOW__}
    report["exported"].append({"asset": asset_path, "class": cls, "bytes": size})


for target in TARGETS:
    export_one(target)

with open(__INDEX_PATH__, "w", encoding="utf-8") as fh:
    json.dump(index, fh, ensure_ascii=False, indent=1)

with open(__REPORT_PATH__, "w", encoding="utf-8") as fh:
    json.dump(report, fh, ensure_ascii=False, indent=1)

print("__COLLECT_DONE__")
'''


def _editor_cmd_path(engine):
    """按平台推导 UnrealEditor-Cmd 的位置。

    写死 ``Win64/UnrealEditor-Cmd.exe`` 会让插件在 Linux / macOS 上直接找不到
    引擎——这两处的目录名和可执行文件后缀都不一样。
    """
    if sys.platform.startswith("win"):
        platform_dir, exe_name = "Win64", "UnrealEditor-Cmd.exe"
    elif sys.platform == "darwin":
        platform_dir, exe_name = "Mac", "UnrealEditor-Cmd"
    else:
        platform_dir, exe_name = "Linux", "UnrealEditor-Cmd"
    return os.path.join(engine, "Engine", "Binaries", platform_dir, exe_name)


def _find_editor_cmd(engine_root=None):
    engine = bridge.find_engine(engine_root)
    exe = _editor_cmd_path(engine)
    if not os.path.isfile(exe):
        raise RuntimeError("找不到 UnrealEditor-Cmd: %s" % exe)
    return engine, exe


def project_file():
    """定位 .uproject。"""
    for name in sorted(os.listdir(PROJECT_ROOT)):
        if name.endswith(".uproject"):
            return os.path.join(PROJECT_ROOT, name)
    raise RuntimeError("在 %s 里找不到 .uproject" % PROJECT_ROOT)


def collect(asset_paths, engine_root=None, force=False, timeout=1800, verbose=False):
    """把若干资产导出到 T3D 缓存。返回结果字典。"""
    asset_paths = [p for p in (asset_paths or []) if p]
    if not asset_paths:
        return {"error": "no asset paths given"}

    os.makedirs(CACHE_T3D, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    try:
        engine, exe = _find_editor_cmd(engine_root)
        project = project_file()
    except Exception as exc:
        return {"error": str(exc), "_collector": "setup_failed"}

    report_path = os.path.join(WORK_DIR, "last_report.json")
    script_path = os.path.join(WORK_DIR, "collect_script.py")

    source = (_EXPORT_SCRIPT
              .replace("__CACHE_DIR__", json.dumps(CACHE_T3D))
              .replace("__INDEX_PATH__", json.dumps(INDEX_PATH))
              .replace("__REPORT_PATH__", json.dumps(report_path))
              .replace("__TARGETS__", json.dumps(asset_paths))
              .replace("__FORCE__", "True" if force else "False")
              .replace("__NOW__", json.dumps(time.strftime("%Y-%m-%d %H:%M:%S"))))

    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(source)

    if os.path.isfile(report_path):
        try:
            os.remove(report_path)
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
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
        returncode = proc.returncode
        tail = (proc.stdout or "")[-4000:]
    except subprocess.TimeoutExpired:
        return {"error": "收集超时（%ds）" % timeout, "_collector": "timeout",
                "assets": asset_paths}
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc),
                "_collector": "spawn_failed"}

    elapsed = time.time() - started
    report = {}
    if os.path.isfile(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as handle:
                report = json.load(handle)
        except Exception as exc:
            report = {"report_read_error": str(exc)}

    ok = bool(report.get("exported")) or bool(report.get("skipped"))
    if not ok and not report:
        return {
            "error": "收集未产出结果（返回码 %s）。可能未启用 PythonScriptPlugin，"
                     "或项目加载失败。" % returncode,
            "_collector": "no_result",
            "stdout_tail": tail,
        }

    return {
        "ok": True,
        "elapsed_seconds": round(elapsed, 1),
        "engine": report.get("engine"),
        "exported": report.get("exported") or [],
        "skipped": report.get("skipped") or [],
        "failed": report.get("failed") or [],
        "cache_dir": CACHE_T3D,
        "index_size": len(_read_index()),
        "_collector": "commandlet",
    }


def _read_index():
    if not os.path.isfile(INDEX_PATH):
        return {}
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {}


def discover_assets(scope="/Game", class_names=None, engine_root=None,
                    timeout=1800, limit=400):
    """用一次 commandlet 列出范围内指定类的资产路径。

    ``class_names=None`` 表示只要蓝图与动画类（连招系统关心的都在这里）。
    """
    class_names = class_names or ["Blueprint", "AnimMontage", "AnimSequence",
                                  "AnimBlueprint", "DataTable", "UserDefinedStruct"]
    os.makedirs(WORK_DIR, exist_ok=True)
    try:
        engine, exe = _find_editor_cmd(engine_root)
        project = project_file()
    except Exception as exc:
        return {"error": str(exc)}

    out_path = os.path.join(WORK_DIR, "discovered.json")
    script_path = os.path.join(WORK_DIR, "discover.py")
    source = (
        "import json, unreal\n"
        "wanted = set(%r)\n"
        "out = []\n"
        "for p in unreal.EditorAssetLibrary.list_assets(%r, recursive=True) or []:\n"
        "    try:\n"
        "        d = unreal.EditorAssetLibrary.find_asset_data(p)\n"
        "        cls = str(d.asset_class_path.asset_name) if d is not None else ''\n"
        "    except Exception:\n"
        "        cls = ''\n"
        "    if cls in wanted:\n"
        "        out.append({'path': p, 'class': cls})\n"
        "open(%r, 'w', encoding='utf-8').write(json.dumps(out))\n"
        "print('__DISCOVER_DONE__')\n"
    ) % (class_names, scope, out_path)

    with open(script_path, "w", encoding="utf-8") as handle:
        handle.write(source)

    try:
        subprocess.run([exe, project, "-run=PythonScript", "-Script=%s" % script_path,
                        "-unattended", "-nosplash", "-nop4", "-nullrhi",
                        "-EnablePlugins=PythonScriptPlugin"],
                       cwd=PROJECT_ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:
        return {"error": "%s: %s" % (type(exc).__name__, exc)}

    if not os.path.isfile(out_path):
        return {"error": "发现步骤未产出清单"}
    try:
        with open(out_path, "r", encoding="utf-8") as handle:
            found = json.load(handle)
    except Exception as exc:
        return {"error": str(exc)}
    return {"scope": scope, "assets": found[:limit], "total": len(found)}


def discover_montages(scope="/Game", engine_root=None, timeout=1800):
    """列出范围内的所有 AnimMontage 路径。"""
    result = discover_assets(scope, ["AnimMontage"], engine_root=engine_root,
                             timeout=timeout)
    if "error" in result:
        return result
    return {"scope": scope,
            "montages": [a["path"] for a in result.get("assets") or []]}
