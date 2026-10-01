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
    def __init__(self, drop_env=()):
        # drop_env：**故意**去掉某些环境变量来复现真实故障环境。
        # 编码那一条就是靠它复现的——服务器在真实会话里不会有人替它设
        # PYTHONIOENCODING，而测试如果总是替它设好，就永远测不出问题。
        env = dict(os.environ)
        for key in drop_env:
            env.pop(key, None)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
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

        # 工具清单是**契约**：增删工具必须在这里显式改一次，而不是让它悄悄漂移。
        # 数量断言直接引用集合大小——两处各写一个数，迟早有一处忘了改
        # （实际就发生过：这里一直写着 17，知识库的 4 个工具加进来后没人更新，
        #   于是这个测试长期处于"失败但没人看"的状态）。
        core = {"status", "sync", "project_map", "search", "find_refs", "index",
                "class_summary", "graph_overview", "flow", "node_detail",
                "montage", "anim_asset", "anim_calls", "diagnose",
                "audit", "reflect", "pie_state"}
        # 知识库四件套依赖**可选的** Plugins/KBaseUE（兄弟目录，不在本仓库里）。
        #
        # 但注意：**它们是静态注册的**——`_TOOLS` 里永远有这 4 个，KBaseUE 在不在
        # 都一样。KBaseUE 只决定调用时返回结果还是 `{"error": "KBaseUE module
        # not found"}`。第一版这里按"KBaseUE 是否存在"增减期望集合，结果在**新克隆
        # 的仓库里必然失败**（工具数 21 ≠ 期望 17）——靠克隆到临时目录跑一遍才发现。
        # 缺组件时的行为由第 9 节单独验证。
        kb_tools = {"engine_facts", "engine_source", "ue_docs", "interface_check"}
        kbase_dir = os.path.join(os.path.dirname(ROOT), "KBaseUE", "Content", "Python")
        kbase_present = os.path.isfile(os.path.join(kbase_dir, "engine_query.py"))
        expected = core | kb_tools
        print("      KBaseUE %s（影响这 4 个工具是否可用，不影响它们是否注册）"
              % ("已安装" if kbase_present else "未安装"))

        names = [t.get("name") for t in tools]
        check("工具数量 == %d" % len(expected), len(tools) == len(expected),
              "count=%d" % len(tools))
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

    # ---- 8. stdio 必须是 UTF-8，且不依赖环境变量
    #
    # 真实故障：服务器进程继承系统代码页（中文机器是 GBK）时——
    #   * 进来的中文查询被按 GBK 解码 → 乱码 → 知识库 0 命中；
    #   * 出去的中文被按 GBK 编码 → 客户端按 UTF-8 读 → 一片乱码；
    #   * 结果里只要有一个 GBK 编不出的字符，sys.stdout.write 就抛异常，
    #     **整个响应变成客户端看到的 "-32603 internal error"**，
    #     而真正的 traceback 只留在服务器的 error.data 里，客户端看不到。
    #
    # 所以这里故意**不设** PYTHONIOENCODING，复现最容易出问题的环境。
    # 修法在 server.py：进程启动时自己把三个标准流 reconfigure 成 UTF-8。
    #
    # 断言方式刻意不依赖 KBaseUE：`initialize` 的 instructions 本身就是中文，
    # 它一定能验证 stdout；中文**输入**的回程则用知识库工具（装了才测）。
    # 依赖可选组件的测试，等于给克隆者埋一个必然失败项。
    print("\n[8] stdio 编码（不依赖环境变量）")
    c2 = Client(drop_env=("PYTHONIOENCODING", "PYTHONUTF8"))
    try:
        c2.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        init = c2.recv("initialize")
        instructions = (init.get("result") or {}).get("instructions") or ""
        check("中文 instructions 原样返回（验证 stdout 编码）",
              "请结合证据" in instructions,
              "(len=%d, 中文是否完好=%s)"
              % (len(instructions), any("\u4e00" <= c <= "\u9fff" for c in instructions)))

        if not kbase_present:
            print("      （未安装 KBaseUE，跳过中文输入回程）")
        else:
            c2.send("tools/call",
                    {"name": "engine_facts",
                     "arguments": {"query": "接口函数 为什么不是事件"}})
            response = c2.recv("engine_facts cn")
            check("中文查询不触发协议错误", "error" not in response,
                  repr(response.get("error"))[:200])
            text = ((response.get("result") or {}).get("content")
                    or [{}])[0].get("text", "")
            try:
                payload = json.loads(text)
            except ValueError as exc:
                payload = {}
                check("中文查询返回合法 JSON", False, str(exc))
            ids = [h.get("id") for h in (payload.get("hits") or [])]
            check("中文查询能命中规则（验证 stdin 编码）",
                  "iface.event-vs-function" in ids, "(ids=%s)" % ids)
            check("中文查询词没有被传输搞坏",
                  payload.get("query") == "接口函数 为什么不是事件",
                  "(query=%r)" % payload.get("query"))
    finally:
        c2.close()

    # ---- 9. 可选依赖缺失时必须**降级**，不能崩溃
    #
    # 知识库四件套依赖 Plugins/KBaseUE，它不在本仓库里。克隆者没有它时，
    # 这 4 个工具必须返回**结构化的错误**，而不是让协议层崩掉或返回半个结果。
    # 这一节在"装了"和"没装"两种环境下都成立——这正是它值得存在的原因。
    print("\n[9] 可选依赖缺失时的降级行为")
    c3 = Client()
    try:
        c3.send("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})
        c3.recv("initialize")
        for tool, args in (("engine_facts", {"query": "event"}),
                           ("engine_source", {"action": "status"}),
                           ("ue_docs", {"action": "status"}),
                           ("interface_check", {"asset_path": "/Game/X"})):
            if tool not in names:
                check("%s 未注册（KBaseUE 缺失）" % tool, True)
                continue
            c3.send("tools/call", {"name": tool, "arguments": args})
            resp = c3.recv(tool)
            check("%s 不产生协议错误" % tool, "error" not in resp,
                  repr(resp.get("error"))[:150])
            text = ((resp.get("result") or {}).get("content") or [{}])[0].get("text", "")
            try:
                payload = json.loads(text)
            except ValueError as exc:
                payload = {}
                check("%s 返回合法 JSON" % tool, False, str(exc))
            has_result = bool(payload) and "error" not in payload
            has_clean_error = "error" in payload
            check("%s 要么给结果、要么给结构化错误" % tool,
                  has_result or has_clean_error, repr(payload)[:160])
            if kbase_present:
                # 要验的性质是"**不报缺模块**"，不是"必须有结果"。
                # 例如用假路径调 interface_check，返回 "no cached T3D" 是**正确**的
                # ——那是资产的问题，不是组件缺失。第一版这里断言 has_result，
                # 结果是测试错了、代码没错。
                blob = json.dumps(payload, ensure_ascii=False)
                check("%s 不报缺模块" % tool,
                      "KBaseUE module not found" not in blob, repr(payload)[:160])
    finally:
        c3.close()

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
