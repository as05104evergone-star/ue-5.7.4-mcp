# -*- coding: utf-8 -*-
r"""
ComboMCP / 与 Unreal Editor 的桥接
==================================

职责：把 ``ue/`` 下的只读脚本送进正在运行的编辑器执行，并把结果取回来。

为什么走 UE 自带的 Remote Execution
-----------------------------------
编辑器里已经有完整的 Unreal 反射，引擎自己知道每个节点是什么。外部进程
重新解析 ``.uasset`` 二进制是另一条路，但那条路要对着引擎源码硬啃格式，
且每个小版本都可能崩——为了"读懂逻辑"这个目标不值得。

UE 的 PythonScriptPlugin 自带 ``remote_execution.py``（UDP 组播发现 + TCP 命令），
它**只依赖标准库**，因此可以被外部 Python 进程直接加载使用。于是我们不需要
C++ 插件、不需要编译、不需要安装任何运行时。

通信协议
--------
``ue/`` 下的模块被拼成一个自包含的 bootstrap 脚本，在编辑器里注册成
``combomcp`` 包（首次执行时装载，之后按源码指纹复用），然后调用
``combomcp.dispatch.dispatch(command, args)``，结果用标记包裹后从 stdout 取回。

大结果不走 stdout，改为落盘，避免把 MB 级 JSON 塞进 TCP 命令通道。
"""

import hashlib
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC_DIR = os.path.join(ROOT, "ue")
CACHE_DIR = os.path.join(ROOT, "cache")

# 结果超过此字节数就落盘，只回传路径
INLINE_LIMIT = 300_000

# 引擎定位候选（顺序即优先级）
ENGINE_CANDIDATES = [
    r"E:\UE_5.7",
    r"D:\UE_5.7",
    r"C:\Program Files\Epic Games\UE_5.7",
    r"E:\UE_5.6\UE_5.6",
    r"D:\UE_5.6\UE_5.6",
    r"C:\Program Files\Epic Games\UE_5.6",
]

REMOTE_EXEC_REL = os.path.join(
    "Engine", "Plugins", "Experimental", "PythonScriptPlugin",
    "Content", "Python", "remote_execution.py",
)

# 模块装载顺序（被依赖的在前）
MODULE_ORDER = (
    "graph_ir",     # 纯数据：IR 定义 + 执行流遍历 + DSL 渲染（无依赖）
    "t3d",          # 纯数据：T3D 文本 -> IR（依赖 graph_ir）
    "runtime",      # UE 反射的防御性包装
    "bp_read",      # 反射路径的蓝图读取
    "anim_read",    # 动画资产读取
    "index",        # 资产枚举与蓝图索引
    "t3d_read",     # T3D 路径的蓝图读取（依赖 runtime/graph_ir/t3d）
    "domain",       # 连招领域诊断
    "pie_state",    # PIE 运行时状态读取（唯一需要 game world 的模块）
    "dispatch",     # 命令分发
)


# ====================================================================== 引擎定位


def find_engine(explicit=None):
    """定位引擎根目录。"""
    if explicit:
        if os.path.isfile(os.path.join(explicit, REMOTE_EXEC_REL)):
            return explicit
        raise RuntimeError("engine root has no remote_execution.py: %s" % explicit)

    env = os.environ.get("COMBO_UE_ENGINE")
    if env and os.path.isfile(os.path.join(env, REMOTE_EXEC_REL)):
        return env

    for candidate in ENGINE_CANDIDATES:
        if os.path.isfile(os.path.join(candidate, REMOTE_EXEC_REL)):
            return candidate
    raise RuntimeError(
        "找不到 Unreal Engine。请设置环境变量 COMBO_UE_ENGINE 指向引擎根目录，"
        "例如 E:\\UE_5.7")


def _project_stem():
    """从 ``<project>/Plugins/ComboMCP`` 反推 ``.uproject`` 的名字（不含扩展名）。

    目录结构固定是 ``<project>/Plugins/ComboMCP``，所以上溯两级就是项目根；
    这里刻意不写死项目名，插件拷到别的项目也能正确选中编辑器实例。
    """
    try:
        project_root = os.path.dirname(os.path.dirname(ROOT))
        for name in sorted(os.listdir(project_root)):
            if name.endswith(".uproject"):
                return os.path.splitext(name)[0]
    except Exception:
        pass
    return None


# ====================================================================== 源码打包


def _load_ue_sources():
    """读取 ``ue/`` 下的源码，返回 ``({name: source}, fingerprint)``。"""
    sources = {}
    hasher = hashlib.sha256()
    for name in MODULE_ORDER:
        path = os.path.join(UE_SRC_DIR, name + ".py")
        if not os.path.isfile(path):
            raise RuntimeError("missing ue module: %s" % path)
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
        sources[name] = source
        hasher.update(name.encode("utf-8"))
        hasher.update(source.encode("utf-8"))
    return sources, hasher.hexdigest()[:16]


_BOOTSTRAP = '''\
import sys, types, json, os

_SOURCES = {sources!r}
_VER = {version!r}
_SAVE_TO = {save_to!r}
_CACHE_DIR = {cache_dir!r}

_pkg = sys.modules.get("combomcp")
if _pkg is None or getattr(_pkg, "__combomcp_ver__", None) != _VER:
    for _k in [k for k in list(sys.modules) if k == "combomcp" or k.startswith("combomcp.")]:
        del sys.modules[_k]
    _pkg = types.ModuleType("combomcp")
    _pkg.__path__ = []
    _pkg.__combomcp_ver__ = _VER
    # 这些模块是被 exec 送进来的，__file__ 只能是假路径；真实缓存目录由此注入，
    # 否则任何按 __file__ 推断路径的代码都会落到编辑器的工作目录下。
    _pkg.__cache_dir__ = _CACHE_DIR
    sys.modules["combomcp"] = _pkg
    for _mod_name in {order!r}:
        _src = _SOURCES[_mod_name]
        _mod = types.ModuleType("combomcp." + _mod_name)
        _mod.__package__ = "combomcp"
        _mod.__file__ = "<combomcp/%s>" % _mod_name
        sys.modules["combomcp." + _mod_name] = _mod
        exec(compile(_src, "<combomcp/%s>" % _mod_name, "exec"), _mod.__dict__)
        setattr(_pkg, _mod_name, _mod)
else:
    _pkg.__cache_dir__ = _CACHE_DIR

from combomcp import dispatch as _dispatch
_result = _dispatch.dispatch({command!r}, {args!r})

try:
    _text = json.dumps(_result, ensure_ascii=False, default=str)
except Exception as _exc:
    _text = json.dumps({{"error": "result not serializable: %s" % _exc}})

if _SAVE_TO and len(_text) > {inline_limit}:
    try:
        os.makedirs(os.path.dirname(_SAVE_TO), exist_ok=True)
        with open(_SAVE_TO, "w", encoding="utf-8") as _fh:
            _fh.write(_text)
        _text = json.dumps({{"__saved_to__": _SAVE_TO, "bytes": len(_text)}})
    except Exception as _exc:
        _text = json.dumps({{"error": "failed to persist large result: %s" % _exc}})

print("{begin}")
print(_text)
print("{end}")
'''


def build_bootstrap(sources, version, command, args, save_to=None, cache_dir=None):
    return _BOOTSTRAP.format(
        sources=sources,
        version=version,
        order=list(MODULE_ORDER),
        command=command,
        args=args,
        save_to=save_to,
        cache_dir=cache_dir or CACHE_DIR,
        inline_limit=INLINE_LIMIT,
        begin="__COMBOMCP_BEGIN__",
        end="__COMBOMCP_END__",
    )


# ====================================================================== 桥接


class BridgeError(RuntimeError):
    pass


def _as_text(value):
    """把命令返回里的 output/result 归一化成字符串。

    UE 5.7 的 ``run_command`` 返回里 ``output`` 是**列表**，且元素是
    ``{'type': 'Info'|'Error'|'Command', 'output': '行文本'}`` 这样的 dict，
    而不是字符串。桥接层所有对返回文本的处理都必须先过这里，否则会出现
    「命令其实执行成功了，桥却抛 AttributeError / 找不到结果标记」的假故障。
    （``tools/probe_remote.py`` 踩的是同一个坑的轻量版。）
    """
    if isinstance(value, dict):
        for key in ("output", "text", "line", "message"):
            if key in value:
                return _as_text(value[key])
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            text = _as_text(item)
            if text and not text.endswith(("\n", "\r")):
                text += "\n"
            parts.append(text)
        return "".join(parts)
    return "" if value is None else str(value)


# 连不上编辑器时的统一提示。
#
# 引擎自带的 remote_execution 库在"编辑器完全没开"时**不会**让 remote_nodes
# 返回空列表，而是直接抛 RuntimeError（例如 "Remote party failed to attempt the
# command socket connection!"）。那种原始异常对用户毫无指导意义，一律换成这段。
_NO_EDITOR_HINT = (
    "没有发现正在运行的 Unreal Editor（Python Remote Execution 无响应）。\n"
    "这条通道是**可选**的：读蓝图的主力路径是 T3D 缓存（见 engine/offline.py），\n"
    "它不需要编辑器在运行。只有缓存里没有该资产时才会走到这里。\n"
    "若确实需要实时读取，请确认：\n"
    "  1) 编辑器已启动并完成加载（不是还在编译 shader）\n"
    "  2) 编辑器里勾选了 Project Settings → Plugins → Python →\n"
    "     Enable Remote Execution?（该项默认关闭）\n"
    "  3) 防火墙没有拦截 UDP 239.0.0.1:6766\n"
    "可用 tools/probe_remote.py 单独验证通道。"
)


class UEBridge(object):
    """与一个 Unreal Editor 实例的只读命令通道。"""

    def __init__(self, engine_root=None, discover_timeout=8.0, verbose=False):
        self.engine_root = find_engine(engine_root)
        self.discover_timeout = discover_timeout
        self.verbose = verbose

        self._re_mod = None
        self._session = None
        self._node_id = None
        self._node_info = None
        self._sources = None
        self._fingerprint = None
        self._calls = 0
        self._failures = 0

    # ---------------------------------------------------------------- 内部

    def _log(self, message):
        if self.verbose:
            sys.stderr.write("[combomcp] %s\n" % message)
            sys.stderr.flush()

    def _load_remote_execution(self):
        if self._re_mod is not None:
            return self._re_mod
        path = os.path.join(self.engine_root, REMOTE_EXEC_REL)
        spec = importlib.util.spec_from_file_location("combo_remote_execution", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["combo_remote_execution"] = module
        spec.loader.exec_module(module)
        self._re_mod = module
        return module

    def _load_sources(self):
        if self._sources is None:
            self._sources, self._fingerprint = _load_ue_sources()
        return self._sources, self._fingerprint

    # ---------------------------------------------------------------- 连接

    @property
    def connected(self):
        return self._session is not None and self._session.has_command_connection()

    def node_info(self):
        return self._node_info

    def connect(self, force=False):
        """发现编辑器并打开命令连接。已连接时直接返回。"""
        if self.connected and not force:
            return True

        mod = self._load_remote_execution()
        self.close()

        session = mod.RemoteExecution(mod.RemoteExecutionConfig())
        try:
            session.start()

            deadline = time.time() + self.discover_timeout
            nodes = []
            while time.time() < deadline:
                nodes = session.remote_nodes
                if nodes:
                    break
                time.sleep(0.25)

            if not nodes:
                raise BridgeError(_NO_EDITOR_HINT)

            # 优先选择打开了本项目的那一个。项目名从磁盘上的 .uproject 推导而不是
            # 写死——这样整个插件目录可以原样拷到别的项目里继续用。
            chosen = nodes[0]
            wanted = _project_stem()
            if wanted:
                for node in nodes:
                    if wanted.lower() in str(node.get("project_name", "")).lower():
                        chosen = node
                        break

            self._node_info = dict(chosen)
            self._node_id = chosen.get("node_id")
            session.open_command_connection(self._node_id)
        except BridgeError:
            try:
                session.stop()
            except Exception:
                pass
            raise
        except Exception as exc:
            # 见 _NO_EDITOR_HINT 的说明：库会直接抛 RuntimeError，必须包装。
            try:
                session.stop()
            except Exception:
                pass
            raise BridgeError(
                _NO_EDITOR_HINT + "\n\n底层错误：%s: %s"
                % (type(exc).__name__, exc))

        self._session = session
        self._log("connected to %s (%s)" % (
            chosen.get("project_name"), chosen.get("engine_version")))
        return True

    def close(self):
        if self._session is not None:
            try:
                self._session.stop()
            except Exception:
                pass
            self._session = None

    def ensure(self):
        if not self.connected:
            self.connect(force=True)

    # ---------------------------------------------------------------- 调用

    def call(self, command, args=None, retry=True):
        """执行一个 UE 侧命令并返回解析后的 dict。"""
        try:
            self.ensure()
        except BridgeError as exc:
            return {"error": str(exc), "_bridge": "disconnected"}

        sources, fingerprint = self._load_sources()
        args = args or {}

        save_to = None
        if command in ("build_index", "project_overview", "list_assets"):
            os.makedirs(CACHE_DIR, exist_ok=True)
            save_to = os.path.join(CACHE_DIR, "last_%s.json" % command)

        code = build_bootstrap(sources, fingerprint, command, args,
                               save_to=save_to, cache_dir=CACHE_DIR)

        mod = self._re_mod
        started = time.time()
        try:
            raw = self._session.run_command(
                code, unattended=True, exec_mode=mod.MODE_EXEC_FILE)
        except Exception as exc:
            self._failures += 1
            if retry:
                self._log("command transport failed (%s), reconnecting" % exc)
                self.close()
                return self.call(command, args, retry=False)
            return {"error": "remote execution failed: %s" % exc,
                    "_bridge": "transport_error"}

        elapsed = time.time() - started
        self._calls += 1

        # UE 5.7 起 run_command 的 output / result 可能是「日志行列表」而不是字符串。
        # 直接 .find() / .strip() 会抛 AttributeError，把「通道可用、命令已执行」
        # 误报成桥接失败——`tools/probe_remote.py` 在探测脚本里踩过同一个坑。
        output_text = _as_text(raw.get("output"))

        if not raw.get("success"):
            output = output_text.strip()
            self._log("command failed: %s" % output[-500:])
            return {
                "error": "remote command reported failure",
                "output": output[-4000:],
                "command": command,
                "_bridge": "command_error",
            }

        payload = self._extract(output_text)
        if payload is None:
            return {
                "error": "could not find result markers in remote output",
                "output": output_text[-4000:],
                "command": command,
                "_bridge": "protocol_error",
            }

        if isinstance(payload, dict):
            payload.setdefault("_elapsed_ms", int(elapsed * 1000))
            if payload.get("__saved_to__"):
                try:
                    with open(payload["__saved_to__"], "r", encoding="utf-8") as fh:
                        loaded = json.load(fh)
                    if isinstance(loaded, dict):
                        loaded["_from_cache_file"] = payload["__saved_to__"]
                        return loaded
                except Exception as exc:
                    return {"error": "failed to read persisted result: %s" % exc}

        return payload

    @staticmethod
    def _extract(output):
        begin = "__COMBOMCP_BEGIN__"
        end = "__COMBOMCP_END__"
        output = _as_text(output)
        start = output.find(begin)
        if start < 0:
            return None
        start += len(begin)
        stop = output.find(end, start)
        if stop < 0:
            return None
        text = output[start:stop].strip()
        try:
            return json.loads(text)
        except Exception:
            return {"error": "result was not valid JSON", "raw": text[:2000]}

    # ---------------------------------------------------------------- 统计

    def stats(self):
        return {
            "engine_root": self.engine_root,
            "connected": self.connected,
            "node": self._node_info,
            "calls": self._calls,
            "failures": self._failures,
        }
