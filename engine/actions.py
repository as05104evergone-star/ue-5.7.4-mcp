# -*- coding: utf-8 -*-
r"""
ComboMCP / 写操作授权门
=======================

这是"默认只读、要写就显式打开"这条边界在 MCP 侧的实现。

为什么要单独一个模块
--------------------
授权判断必须**只有一份**。写死在工具函数里的话，很容易出现"A 工具检查了、
新加的 B 工具忘了检查"这种漏洞——而漏掉的那次就是一个能改用户项目、
却没有任何提示的入口。所以这里把"什么算写操作"和"允不允许"都收在一处，
工具层只调用 :func:`check`。

两道门
------
1. **这里**（MCP 侧）—— 防止模型在没被授权时误调；
2. ``ue/editor_actions.py`` 里的 ``_guard_write`` —— 防止有人绕过 MCP，
   直接通过 Remote Execution 通道把命令送进编辑器。

两道都要有，因为通道本身是开放的，只靠外部那层不算数。

怎么授权
--------
* 环境变量 ``COMBO_ALLOW_ACTIONS=1`` —— 长期生效，MCP 服务启动时读取；
* 调用参数 ``confirm=true`` —— 只对这一次调用生效。

刻意不做的事：不提供"记住上次的 confirm"这类状态。授权要么是持久的
（环境变量），要么是单次的（confirm），没有中间态——中间态最容易在某个
不相关的调用里意外生效。
"""

import os

# 视为"真"的环境变量取值
_TRUTHY = ("1", "true", "yes", "on", "y")

# 允许的环境变量名。``COMBO_ALLOW_WRITE`` 作为别名一并接受——
# 名字里带 write 更直观，两条都认不会造成歧义。
_ENV_KEYS = ("COMBO_ALLOW_ACTIONS", "COMBO_ALLOW_WRITE")


def actions_allowed():
    """环境变量是否放行写操作。"""
    for key in _ENV_KEYS:
        value = os.environ.get(key)
        if value and str(value).strip().lower() in _TRUTHY:
            return True
    return False


def check(is_write, confirm=False):
    """判断能否执行。

    返回 ``(allowed, blocked_payload)``：允许时 payload 为 None；
    不允许时是一份**可直接返回给模型**的说明（含下一步怎么做）。
    """
    if not is_write:
        return True, None
    if actions_allowed():
        return True, None
    if confirm:
        return True, None
    return False, {
        "error": "写操作未授权，已拒绝执行。这是刻意的默认。",
        "_actions_blocked": True,
        "what_counts_as_write": [
            "pie sub=start / stop / simulate",
            "level sub=load / new / save / add",
        ],
        "how_to_allow": [
            "长期生效：设环境变量 COMBO_ALLOW_ACTIONS=1，然后重启 MCP 客户端"
            "（MCP 服务是子进程，环境变量在它启动时读取）。",
            "单次生效：这次调用传 confirm=true。",
        ],
        "not_allowed_at_all": [
            "执行任意 console 命令",
            "保存任意资产（只有显式的 level sub=save 保存当前关卡）",
            "创建 / 修改 / 删除资产",
        ],
        "note": "读取类工具不受影响，任何时候都能用。",
    }


def describe():
    """给 status 用的一句话状态。"""
    if actions_allowed():
        return {
            "write_actions": "enabled",
            "via": [k for k in _ENV_KEYS if os.environ.get(k)],
            "note": "写操作已放行（环境变量）。仍可用 confirm=false 之外的方式逐次确认。",
        }
    return {
        "write_actions": "disabled",
        "note": ("写操作默认关闭。要打开：设 COMBO_ALLOW_ACTIONS=1 并重启 MCP 客户端，"
                 "或在单次调用里传 confirm=true。"),
        "affected": ["editor action=pie sub=start|stop|simulate",
                     "editor action=level sub=load|new|save|add"],
    }
