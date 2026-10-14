# -*- coding: utf-8 -*-
r"""
ComboMCP / 编辑器动作与授权门测试
================================

这三件事**必须在没有编辑器的情况下测**，因为它们全都是"出问题时才生效"的路径：

1. **commandlet 硬隔离。** 实测：在 ``UnrealEditorCmd -run=PythonScript`` 下构造
   ``unreal.LevelEditorSubsystem()`` 会让进程以 ``EXCEPTION_ACCESS_VIOLATION``
   崩掉。所以这里用一个**假的 unreal 模块**（没有任何编辑器子系统）来验证：
   这种情况下每个动作都必须返回 ``_need_editor``，而且**一个子系统实例都不能创建**。
2. **写操作授权门。** 默认必须是关的。一个"默认开着"的写通道是最危险的回归——
   它会静默地让模型改用户的项目。
3. **日志解析。** 5.6 没有 ``unreal.log_capture_get``，日志走本地文件，
   所以这段逻辑可以不依赖引擎直接测。

运行：
  & "...\python.exe" tools\test_editor_actions.py
"""

import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC = os.path.join(ROOT, "ue")

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


# ====================================================================== 假 unreal


class _FakeSubsystem(object):
    """占位子系统：任何方法调用都记账，方便断言"有没有被碰过"。"""

    def __init__(self, name, log):
        self._name = name
        self._log = log

    def __getattr__(self, item):
        def _call(*args, **kwargs):
            self._log.append("%s.%s" % (self._name, item))
            return None
        return _call


def install_fake_unreal(with_subsystems=False):
    """装一个假的 ``unreal`` 模块。

    ``with_subsystems=False`` 模拟 **commandlet**：类存在但 ``get_editor_subsystem``
    取不到实例。这正是必须拒绝执行的场景。
    """
    calls = []
    fake = types.ModuleType("unreal")

    for name in ("LevelEditorSubsystem", "UnrealEditorSubsystem",
                 "EditorActorSubsystem", "EditorLevelUtils",
                 "EditorLoadingAndSavingUtils", "LevelStreamingDynamic"):
        setattr(fake, name, type(name, (object,), {}))

    if with_subsystems:
        def _get(cls):
            return _FakeSubsystem(cls.__name__, calls)
        fake.get_editor_subsystem = _get
    else:
        def _get(cls):
            return None
        fake.get_editor_subsystem = _get

    sys.modules["unreal"] = fake
    return fake, calls


def load_editor_actions():
    """在假的 unreal 下装载 ue/editor_actions.py。"""
    pkg = sys.modules.get("combomcp")
    if pkg is None:
        pkg = types.ModuleType("combomcp")
        pkg.__path__ = [UE_SRC]
        sys.modules["combomcp"] = pkg
    name = "combomcp.editor_actions"
    sys.modules.pop(name, None)
    mod = types.ModuleType(name)
    mod.__package__ = "combomcp"
    sys.modules[name] = mod
    with open(os.path.join(UE_SRC, "editor_actions.py"), "r",
              encoding="utf-8") as handle:
        exec(compile(handle.read(), "<combomcp/editor_actions>", "exec"),
             mod.__dict__)
    setattr(pkg, "editor_actions", mod)
    return mod


def load_actions_gate():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from engine import actions as gate
    return gate


# ====================================================================== 测试


def test_commandlet_isolation():
    """没有编辑器子系统时必须拒绝，且绝不创建子系统实例。"""
    print("\n[1] commandlet 隔离（不能崩进程）")
    install_fake_unreal(with_subsystems=False)
    ea = load_editor_actions()

    for action, args in (("capabilities", {}),
                         ("pie", {"sub": "status"}),
                         ("pie", {"sub": "start", "_actions_allowed": True}),
                         ("pie", {"sub": "stop", "_actions_allowed": True}),
                         ("level", {"sub": "current"}),
                         ("level", {"sub": "list"}),
                         ("level", {"sub": "save", "_actions_allowed": True}),
                         ("actor", {"sub": "list"})):
        out = ea.editor_action(action, args)
        ok = isinstance(out, dict) and out.get("_need_editor") is True
        check("%s/%s 返回 _need_editor" % (action, args.get("sub") or "-"), ok,
              "got=%s" % str(out)[:110])

    out = ea.editor_action("capabilities", {})
    check("拒绝时给出可照做的 how 列表",
          isinstance(out.get("how"), list) and len(out["how"]) >= 3,
          "how=%s" % out.get("how"))
    check("拒绝时说明为什么不硬试（会崩）",
          "崩" in str(out.get("note") or ""), "note=%s" % out.get("note"))

    check("未知 action 被拒绝",
          "error" in ea.editor_action("nonsense", {}))


def test_write_action_list():
    """写操作名单必须与 MCP 侧的判断一致。"""
    print("\n[2] 写操作名单")
    install_fake_unreal(with_subsystems=False)
    ea = load_editor_actions()

    expect_write = [("pie", "start"), ("pie", "stop"), ("pie", "simulate"),
                    ("level", "load"), ("level", "new"), ("level", "save"),
                    ("level", "add")]
    expect_read = [("pie", "status"), ("level", "current"), ("level", "list"),
                   ("actor", "list"), ("actor", "selected"),
                   ("capabilities", None), ("logs", None)]

    for action, sub in expect_write:
        check("%s.%s 被标为写" % (action, sub), ea.is_write_action(action, sub))
    for action, sub in expect_read:
        check("%s.%s 不是写" % (action, sub), not ea.is_write_action(action, sub))

    check("WRITE_ACTIONS 与 is_write_action 自洽",
          all(ea.is_write_action(a) for a in ea.WRITE_ACTIONS),
          "list=%s" % list(ea.WRITE_ACTIONS))
    check("console 不在动作面里",
          "console" not in [k for k in ea._ACTIONS], "actions=%s" % list(ea._ACTIONS))


def test_write_gate():
    """授权门：默认关，环境变量或 confirm 才能开。"""
    print("\n[3] 写操作授权门（默认必须关闭）")
    gate = load_actions_gate()

    saved = dict((k, os.environ.get(k))
                 for k in ("COMBO_ALLOW_ACTIONS", "COMBO_ALLOW_WRITE"))
    for key in saved:
        os.environ.pop(key, None)
    try:
        check("默认不放行写操作", gate.actions_allowed() is False)

        allowed, payload = gate.check(is_write=True)
        check("写操作默认被拒", allowed is False)
        check("拒绝时带 _actions_blocked 标记",
              isinstance(payload, dict) and payload.get("_actions_blocked") is True)
        check("拒绝时说明写入范围",
              isinstance(payload.get("what_counts_as_write"), list)
              and len(payload["what_counts_as_write"]) >= 2)
        check("拒绝时给出两种开启方式",
              isinstance(payload.get("how_to_allow"), list)
              and len(payload["how_to_allow"]) == 2)
        check("拒绝时明确列出永久不做的事",
              isinstance(payload.get("not_allowed_at_all"), list)
              and any("console" in str(x) for x in payload["not_allowed_at_all"]))

        allowed, payload = gate.check(is_write=False)
        check("只读操作永远放行", allowed is True and payload is None)

        allowed, payload = gate.check(is_write=True, confirm=True)
        check("confirm=true 单次放行", allowed is True and payload is None)

        for value in ("1", "true", "YES", "on"):
            os.environ["COMBO_ALLOW_ACTIONS"] = value
            check("环境变量 %r 放行写操作" % value, gate.actions_allowed() is True)
        os.environ.pop("COMBO_ALLOW_ACTIONS")

        os.environ["COMBO_ALLOW_WRITE"] = "1"
        check("COMBO_ALLOW_WRITE 也认", gate.actions_allowed() is True)
        os.environ.pop("COMBO_ALLOW_WRITE")

        os.environ["COMBO_ALLOW_ACTIONS"] = "0"
        check("值 '0' 不放行", gate.actions_allowed() is False)
        os.environ.pop("COMBO_ALLOW_ACTIONS")

        described = gate.describe()
        check("describe 报 disabled", described.get("write_actions") == "disabled",
              "got=%s" % described.get("write_actions"))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_editor_side_guard():
    """编辑器侧的第二道门：没有 _actions_allowed 时必须拒绝写。"""
    print("\n[4] 编辑器侧第二道门")
    install_fake_unreal(with_subsystems=True)
    ea = load_editor_actions()

    blocked = ea._guard_write({})
    check("未授权时 _guard_write 拒绝",
          isinstance(blocked, dict) and blocked.get("_actions_blocked") is True,
          "got=%s" % str(blocked)[:110])
    check("授权后 _guard_write 放行", ea._guard_write({"_actions_allowed": True}) is None)


def test_logs():
    """日志解析：不依赖编辑器、不依赖引擎。"""
    print("\n[5] 日志解析（本地文件）")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from engine import logs as logs_mod

    tmp = tempfile.mkdtemp(prefix="combomcp_logs_")
    try:
        log_dir = os.path.join(tmp, "Saved", "Logs")
        os.makedirs(log_dir)
        sample = [
            "[2026.10.14-01.00.00:001][  0]LogInit: Display: Starting",
            "[2026.10.14-01.00.01:002][  0]LogPython: Display: hello",
            "[2026.10.14-01.00.02:003][  0]LogBlueprint: Warning: node is orphaned",
            "[2026.10.14-01.00.03:004][  0]LogPython: Error: script failed",
            "",
            "[2026.10.14-01.00.04:005][  0]LogWindows: Error: === Critical error: ===",
            "Fatal error!",
        ]
        with open(os.path.join(log_dir, "Proj.log"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(sample))

        saved_root = logs_mod.PROJECT_ROOT
        logs_mod.PROJECT_ROOT = tmp
        try:
            files = logs_mod.available()
            check("列出了日志文件", len(files) == 1 and files[0]["file"] == "Proj.log",
                  "files=%s" % files)

            out = logs_mod.read(tail=100)
            check("能读到日志", out.get("ok") is True, "out=%s" % str(out)[:120])
            check("总行数正确", out.get("total_lines") == 7,
                  "total=%s" % out.get("total_lines"))
            check("空行被跳过",
                  all(line["text"].strip() for line in out.get("lines") or []))

            counts = out.get("severity_counts") or {}
            check("识别出 fatal", counts.get("fatal", 0) >= 1, "counts=%s" % counts)
            check("识别出 error", counts.get("error", 0) >= 1, "counts=%s" % counts)
            check("识别出 warning", counts.get("warning", 0) >= 1, "counts=%s" % counts)
            check("有 fatal 时给出 verdict",
                  "FATAL" in str(out.get("verdict") or "").upper()
                  or "崩" in str(out.get("verdict") or ""),
                  "verdict=%s" % out.get("verdict"))

            errs = logs_mod.read(severity="error")
            levels = set(l["level"] for l in errs.get("lines") or [])
            check("severity=error 只给 fatal/error",
                  levels <= {"fatal", "error"} and levels,
                  "levels=%s" % levels)

            py = logs_mod.read(pattern="LogPython")
            check("pattern 生效",
                  all("LogPython" in l["text"] for l in py.get("lines") or [])
                  and len(py.get("lines") or []) == 2,
                  "n=%s" % len(py.get("lines") or []))

            cat = logs_mod.read(grep_category="LogPython")
            check("category 过滤生效",
                  all((l.get("category") or "") == "LogPython"
                      for l in cat.get("lines") or []),
                  "n=%s" % len(cat.get("lines") or []))

            tailed = logs_mod.read(tail=2)
            check("tail 生效", tailed.get("returned") == 2,
                  "returned=%s" % tailed.get("returned"))
            check("tail 时标记 truncated", tailed.get("truncated") is True)

            bad = logs_mod.read(pattern="([unclosed")
            check("非法正则返回 error 而不是抛异常", "error" in bad,
                  "got=%s" % str(bad)[:120])

            missing = logs_mod.read(file="nope.log")
            check("指定不存在的日志返回 error 与可选清单",
                  "error" in missing and isinstance(missing.get("available"), list))
        finally:
            logs_mod.PROJECT_ROOT = saved_root

        empty = tempfile.mkdtemp(prefix="combomcp_nologs_")
        try:
            logs_mod.PROJECT_ROOT = empty
            out = logs_mod.read()
            check("没有日志目录时返回可读错误",
                  "error" in out and "hint" in out, "got=%s" % str(out)[:140])
        finally:
            logs_mod.PROJECT_ROOT = saved_root
            shutil.rmtree(empty, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_classify():
    print("\n[6] 行级别判定")
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from engine import logs as logs_mod

    cases = (
        ("[x][ 0]LogWindows: Error: === Critical error: ===", "fatal"),
        ("Fatal error!", "fatal"),
        ("[x][ 0]LogPython: Error: boom", "error"),
        ("[x][ 0]LogBlueprint: Warning: hmm", "warning"),
        ("[x][ 0]LogInit: Display: fine", "info"),
        ("no prefix at all", "info"),
    )
    for line, expected in cases:
        check("%r -> %s" % (line[:38], expected),
              logs_mod.classify(line) == expected,
              "got=%s" % logs_mod.classify(line))


def main():
    print("=" * 70)
    print("编辑器动作 / 授权门 / 日志测试（不需要编辑器）")
    print("=" * 70)
    test_commandlet_isolation()
    test_write_action_list()
    test_write_gate()
    test_editor_side_guard()
    test_logs()
    test_classify()

    print("\n" + "=" * 70)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print("  失败: %s" % label)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
