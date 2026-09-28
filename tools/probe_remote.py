r"""
ComboMCP - 通道探测脚本 (只读，无任何副作用)

用途：检测当前正在运行的 Unreal Editor 是否已经开启 Python Remote Execution 通道。
原理：UE 的 PythonScriptPlugin 启用后，会在 UDP 239.0.0.1:6766 上响应 "ping" 发现包，
      并开放 TCP 命令连接。本脚本使用引擎自带的 remote_execution.py 客户端库来发现它。

运行方式（必须使用 UE 自带的 Python 解释器）：
  & "E:\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" probe_remote.py

退出码：
  0 = 发现可用节点
  2 = 未发现节点（Python 插件很可能未启用）
"""

import importlib.util
import json
import os
import socket
import sys
import time

# ---------------------------------------------------------------- 引擎定位

CANDIDATE_ENGINES = [
    r"E:\UE_5.7",
    r"E:\UE_5.6\UE_5.6",
]

REMOTE_EXEC_REL = os.path.join(
    "Engine", "Plugins", "Experimental", "PythonScriptPlugin",
    "Content", "Python", "remote_execution.py",
)


def find_remote_execution_module():
    """定位引擎自带的 remote_execution.py。"""
    for engine in CANDIDATE_ENGINES:
        path = os.path.join(engine, REMOTE_EXEC_REL)
        if os.path.isfile(path):
            return engine, path
    return None, None


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- 端口探测

def probe_ports():
    """检查 TCP 6776 (命令通道) 是否在监听。"""
    results = {}
    for port in (6776, 30010, 8000):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.6)
        try:
            s.connect(("127.0.0.1", port))
            results[port] = "LISTENING"
        except Exception:
            results[port] = "closed"
        finally:
            s.close()
    return results


# ---------------------------------------------------------------- 主流程

def main():
    print("=" * 68)
    print("ComboMCP 通道探测")
    print("=" * 68)

    print("\n[1] Python 解释器")
    print("    版本   :", sys.version.split()[0])
    print("    可执行 :", sys.executable)

    print("\n[2] 端口探测")
    for port, state in probe_ports().items():
        label = {6776: "Python Remote Execution TCP",
                 30010: "Remote Control API HTTP",
                 8000: "Epic 官方 MCP (5.8+)"}.get(port, "")
        print(f"    {port:<6} {state:<10} {label}")

    print("\n[3] 定位引擎 remote_execution.py")
    engine, re_path = find_remote_execution_module()
    if not re_path:
        print("    [FAIL] 未找到 remote_execution.py，请确认引擎路径")
        return 3
    print("    引擎   :", engine)
    print("    模块   :", re_path)

    re_mod = load_module("ue_remote_execution", re_path)

    print("\n[4] UDP 组播发现 (239.0.0.1:6766, 最多等待 10 秒)")
    config = re_mod.RemoteExecutionConfig()
    session = re_mod.RemoteExecution(config)
    nodes = []
    try:
        session.start()
        deadline = time.time() + 10.0
        while time.time() < deadline:
            nodes = session.remote_nodes
            if nodes:
                break
            time.sleep(0.4)

        if not nodes:
            print("    [FAIL] 未发现任何 Remote Execution 节点")
            print("           → PythonScriptPlugin 很可能未在编辑器中启用")
            return 2

        print(f"    [ OK ] 发现 {len(nodes)} 个节点")
        for node in nodes:
            print("           node_id  :", node.get("node_id"))
            print("           项目     :", node.get("project_name"))
            print("           引擎版本 :", node.get("engine_version"))
            print("           执行模式 :", node.get("exec_modes"))

        # -------------------------------------------------- 实际执行验证
        print("\n[5] 命令通道验证 (只读: 读取引擎版本与项目名)")
        node_id = nodes[0]["node_id"]
        session.open_command_connection(node_id)

        probe_code = (
            "import unreal, json\n"
            "print(json.dumps({\n"
            "    'engine_version': unreal.SystemLibrary.get_engine_version(),\n"
            "    'project_dir': unreal.Paths.project_dir(),\n"
            "    'content_dir': unreal.Paths.project_content_dir(),\n"
            "}))\n"
        )
        result = session.run_command(
            probe_code,
            unattended=True,
            exec_mode=re_mod.MODE_EXEC_FILE,
        )
        # 注意：UE 5.7 起命令返回里的 output / result 可能是「日志行列表」而不是字符串。
        # 直接 .strip() 会抛 AttributeError，把「通道其实可用」误报成探测失败——
        # 这个坑真实踩过：通道返回 success=True 之后，脚本却以异常退出。
        def _as_text(value):
            if isinstance(value, (list, tuple)):
                return "\n".join(str(item) for item in value)
            return "" if value is None else str(value)

        print("    success :", result.get("success"))
        print("    output  :", _as_text(result.get("output")).strip()[:600])
        if result.get("result"):
            print("    result  :", _as_text(result.get("result")).strip()[:600])

        if not result.get("success"):
            print("\n    [WARN] 通道打通但命令执行失败，请检查上方输出")
            return 4

        print("\n" + "=" * 68)
        print("结论：Python Remote Execution 通道【可用】")
        print("=" * 68)
        return 0

    finally:
        try:
            session.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
