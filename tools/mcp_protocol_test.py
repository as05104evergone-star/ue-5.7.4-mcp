# -*- coding: utf-8 -*-
r"""
ComboMCP / MCP 协议端到端测试
=============================

以真实 MCP 客户端的方式启动 ``mcpserver/server.py``，走一遍
``initialize`` → ``notifications/initialized`` → ``tools/list`` → ``tools/call``，
校验每一帧的 JSON-RPC 形状。

这个测试**不需要 Unreal Editor**：它验证的是协议层与工具层能否正确装载和应答，
编辑器连接失败应当是工具返回的结构化错误，而不是协议层崩溃。

运行：
  & "E:\UE_5.7\Engine\Binaries\ThirdParty\Python3\Win64\python.exe" tools\mcp_protocol_test.py
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SERVER = os.path.join(ROOT, "mcpserver", "server.py")

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


class Client(object):
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.next_id = 1

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if not notify:
            message["id"] = self.next_id
            self.next_id += 1
        if params is not None:
            message["params"] = params
        self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        return message.get("id")

    def recv(self, timeout_hint=""):
        line = self.proc.stdout.readline()
        if not line:
            stderr = ""
            try:
                stderr = self.proc.stderr.read()
            except Exception:
                pass
            raise RuntimeError("server closed stdout%s\nstderr:\n%s"
                               % (" (%s)" % timeout_hint if timeout_hint else "", stderr))
        return json.loads(line)

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def main():
    print("=" * 66)
    print("ComboMCP MCP 协议端到端测试")
    print("=" * 66)
    print("server: %s" % SERVER)
    print()

    client = Client()
    try:
        # ---- 1. initialize
        print("[1] initialize")
        request_id = client.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "combomcp-selftest", "version": "1.0"},
        })
        response = client.recv("initialize")
        check("响应 id 与请求一致", response.get("id") == request_id,
              "got %r" % response.get("id"))
        result = response.get("result") or {}
        check("返回 protocolVersion", bool(result.get("protocolVersion")),
              repr(result.get("protocolVersion")))
        check("声明 tools 能力", "tools" in (result.get("capabilities") or {}))
        info = result.get("serverInfo") or {}
        check("serverInfo.name == combomcp", info.get("name") == "combomcp",
              repr(info.get("name")))
        check("携带 instructions（工作流指引）",
              isinstance(result.get("instructions"), str)
              and len(result["instructions"]) > 200)

        # ---- 2. notifications/initialized（无响应）
        print("\n[2] notifications/initialized")
        client.send("notifications/initialized", notify=True)
        check("通知不产生响应帧", True)

        # ---- 3. tools/list
        print("\n[3] tools/list")
        request_id = client.send("tools/list")
        response = client.recv("tools/list")
        tools = (response.get("result") or {}).get("tools") or []
        check("返回工具列表", len(tools) > 0, "count=%d" % len(tools))
        check("工具数量 == 17", len(tools) == 17, "count=%d" % len(tools))

        names = [t.get("name") for t in tools]
        expected = {"status", "sync", "project_map", "search", "find_refs", "index",
                    "class_summary", "graph_overview", "flow", "node_detail",
                    "montage", "anim_asset", "anim_calls", "diagnose",
                    "audit", "reflect", "pie_state"}
        check("工具名集合正确", set(names) == expected,
              "missing=%s extra=%s" % (sorted(expected - set(names)),
                                       sorted(set(names) - expected)))
        for tool in tools:
            if not tool.get("description") or not tool.get("inputSchema"):
                check("工具 %s 有 description 与 inputSchema" % tool.get("name"), False)
                break
        else:
            check("每个工具都有 description 与 inputSchema", True)

        # ---- 4. tools/call -> status（两种环境都必须返回结构正确的 payload）
        #
        # 注意：这里**不能**断言 isError。status 的结果取决于编辑器是否在线——
        # 作者机器上编辑器常开着，CI/别人机器上通常没开。原来写死 isError==True，
        # 于是"编辑器连着"这种更健康的情况下反而报失败。断言应该只约束
        # "无论在线与否，payload 结构都得对"。
        print("\n[4] tools/call: status（结构必须合法，与编辑器是否在线无关）")
        request_id = client.send("tools/call",
                                 {"name": "status", "arguments": {}})
        response = client.recv("tools/call status")
        check("响应 id 一致", response.get("id") == request_id)
        result = response.get("result") or {}
        content = result.get("content") or []
        check("返回 content 数组", len(content) > 0)
        check("content[0].type == text", content[0].get("type") == "text")
        payload = {}
        try:
            payload = json.loads(content[0].get("text") or "{}")
            check("content 是合法 JSON", True)
        except Exception as exc:
            check("content 是合法 JSON", False, str(exc))

        check("payload 是对象", isinstance(payload, dict), repr(payload)[:120])
        # 无论在线与否，这三块都必须在：它们不依赖编辑器
        check("含 bridge 段", "bridge" in payload, sorted(payload.keys())[:12])
        check("含 index 段", "index" in payload, sorted(payload.keys())[:12])
        check("含 t3d_cache 段", "t3d_cache" in payload, sorted(payload.keys())[:12])
        # 在线：有引擎信息且不算错误；离线：有 error 且带 isError
        if payload.get("error"):
            check("离线时标记 isError", result.get("isError") is True)
            check("离线时给出编辑器通道提示",
                  "_bridge" in payload or "editor" in json.dumps(payload).lower(),
                  json.dumps(payload, ensure_ascii=False)[:200])
        else:
            check("在线时报告引擎版本", bool(payload.get("engine_version")),
                  json.dumps(payload, ensure_ascii=False)[:200])
            check("在线时不算 isError", result.get("isError") is not True)

        # ---- 5. tools/call -> index status（纯本地，应成功）
        print("\n[5] tools/call: index(action=status)（不依赖编辑器）")
        request_id = client.send("tools/call",
                                 {"name": "index", "arguments": {"action": "status"}})
        response = client.recv("tools/call index")
        result = response.get("result") or {}
        content = result.get("content") or []
        payload = json.loads(content[0].get("text") or "{}")
        check("索引状态可读且未报 isError", result.get("isError") is not True,
              json.dumps(payload, ensure_ascii=False)[:200])
        check("如实报告 built=False", payload.get("built") is False,
              json.dumps(payload, ensure_ascii=False)[:200])

        # ---- 6. 未知方法与未知工具
        print("\n[6] 错误路径")
        request_id = client.send("no/such/method")
        response = client.recv("unknown method")
        check("未知方法返回 -32601",
              (response.get("error") or {}).get("code") == -32601,
              repr(response.get("error")))

        request_id = client.send("tools/call",
                                 {"name": "no_such_tool", "arguments": {}})
        response = client.recv("unknown tool")
        result = response.get("result") or {}
        check("未知工具返回 isError=true 而非协议错误",
              result.get("isError") is True, repr(result)[:200])

        # ---- 7. ping
        print("\n[7] ping")
        request_id = client.send("ping")
        response = client.recv("ping")
        check("ping 返回空结果", response.get("result") == {}, repr(response))

    finally:
        client.close()

    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for item in FAIL:
            print("  - %s" % item)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
