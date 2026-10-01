"""Talk to the ComboMCP server directly and print the RAW response.

Why this exists
---------------
When a tool fails through the MCP client you get almost nothing:

    Error: MCP error -32603: internal error

The server actually builds a traceback and sends it in ``error.data``, but it never
reaches you through the client. This drives the stdio protocol by hand so you can
see it. It is what turned "interface_check is broken" into the real answer:
**the server was fine -- the process had inherited the system code page (GBK).**

Usage:
    python mcp_probe.py <tool> '<json args>'
    python mcp_probe.py <tool> @args.json          # args from a UTF-8 file
    python mcp_probe.py <tool> @args.json --raw-env

``@file`` exists because non-ASCII args get mangled by PowerShell/cmd command-line
encoding -- and "does a Chinese query work" is exactly the thing being tested, so
the args themselves have to arrive intact.

``--raw-env`` deliberately does NOT set PYTHONIOENCODING, reproducing the condition
under which the server used to fail. Use it as an A/B control: works with UTF-8
forced but fails with --raw-env means the bug is stdio encoding, not the tool.
"""
import json
import os
import subprocess
import sys
import threading
import time

PY = r"E:\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe"
SERVER = r"E:\Unreal Projects\Animation_Sample\Plugins\ComboMCP\mcpserver\server.py"
PROJECT = r"E:\Unreal Projects\Animation_Sample"


def main():
    tool = sys.argv[1] if len(sys.argv) > 1 else "interface_check"
    if len(sys.argv) > 2 and sys.argv[2].startswith("@"):
        # 从文件读参数：中文经 PowerShell/cmd 的命令行会被编码搞坏，
        # 而"中文查询能不能用"正是要测的东西——参数本身必须先保证是干净的。
        with open(sys.argv[2][1:], "r", encoding="utf-8") as fh:
            args = json.load(fh)
    else:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    env = dict(os.environ)
    # 对照实验：--raw-env 时不强制 UTF-8，复现"服务器继承系统代码页"的情形。
    # 这是为了验证一个假设——工具在我这条 MCP 通道上失败、在 --selftest 下成功，
    # 差别可能只是环境变量。
    raw_env = "--raw-env" in sys.argv
    if raw_env:
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        print("[probe] 不设 PYTHONIOENCODING（模拟继承系统代码页）")
    else:
        env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [PY, "-u", SERVER], cwd=PROJECT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stderr_lines = []

    def pump_err():
        for line in proc.stderr:
            stderr_lines.append(line.decode("utf-8", "replace").rstrip())

    threading.Thread(target=pump_err, daemon=True).start()

    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": tool, "arguments": args}},
    ]
    for r in reqs:
        proc.stdin.write((json.dumps(r) + "\n").encode("utf-8"))
        proc.stdin.flush()

    got = {}
    deadline = time.time() + 120
    while time.time() < deadline and len(got) < 2:
        line = proc.stdout.readline()
        if not line:
            break
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            print("NON-JSON stdout:", line[:200])
            continue
        got[msg.get("id")] = msg

    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    print("=" * 72)
    print("tool: %s" % tool)
    print("=" * 72)
    resp = got.get(2)
    if resp is None:
        print("!! 没有收到 tools/call 的响应")
    elif "error" in resp:
        err = resp["error"]
        print("JSON-RPC ERROR  code=%s  message=%s" % (err.get("code"), err.get("message")))
        if err.get("data"):
            print("--- error.data (服务器给的 traceback) ---")
            print(err["data"])
    else:
        content = (resp.get("result") or {}).get("content") or []
        text = content[0].get("text") if content else ""
        print("OK, %d 字符" % len(text or ""))
        print((text or "")[:1500])
    if stderr_lines:
        print("--- server stderr ---")
        for l in stderr_lines[:40]:
            print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
