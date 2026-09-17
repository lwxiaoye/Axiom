# -*- coding: utf-8 -*-
"""发给 OpenAI 兼容网关的 messages 序列协议断言（跨用例复用）。

网关的硬校验：`role:"tool"` 的**紧邻前一条**必须是带 tool_calls 的 assistant，或另一条
tool；tool_call_id 也必须出自最近那批 tool_calls。违反即整轮 400。

主循环有一串「往 messages 里插消息」的注入点——预算提示、叙述提示、停滞提示、截断回执、
强制收敛提示、运行中引导——它们全都必须落在「上一轮 tool 结果已全部配对回填」之后的那个
窗口里。2026-07-27 的真实故障：停滞提示插在了 assistant(tool_calls) 与 tool 结果之间，
把「模型原地打转」这个可恢复状态变成了整轮硬失败；而当时的用例只断言行为、不校验协议，
所以一路绿灯。凡是驱动 drive_model 的假网关都应该把 payload 交给这里过一遍。
"""


def assert_tool_message_protocol(messages, label: str = "") -> None:
    """校验单次请求的 messages 序列符合网关的 tool 消息配对规则。"""
    where = f"（{label}）" if label else ""
    prev = None
    open_ids: set = set()
    for idx, msg in enumerate(messages or []):
        role = str((msg or {}).get("role") or "")
        if role == "tool":
            assert prev is not None, f"messages{where} 第 {idx} 条 tool 前面没有任何消息"
            prev_role = str(prev.get("role") or "")
            assert (
                (prev_role == "assistant" and prev.get("tool_calls")) or prev_role == "tool"
            ), (
                f"messages{where} 第 {idx} 条 role=tool 的紧邻前一条是 "
                f"{prev_role}（content={str(prev.get('content'))[:60]!r}），"
                "网关只接受带 tool_calls 的 assistant 或另一条 tool——注入点插错位置了"
            )
            call_id = str(msg.get("tool_call_id") or "")
            assert call_id in open_ids, (
                f"messages{where} 第 {idx} 条 tool 的 tool_call_id={call_id!r} "
                f"不在最近一批 tool_calls {sorted(open_ids)} 里（悬空或重复回填）"
            )
            open_ids.discard(call_id)
        elif role == "assistant" and msg.get("tool_calls"):
            open_ids = {str(c.get("id") or "") for c in (msg.get("tool_calls") or [])}
        prev = msg


def assert_all_requests_valid(payloads, label: str = "") -> None:
    """校验一整轮里发给网关的每一次请求（注入点错在第几轮都跑不掉）。"""
    for i, payload in enumerate(payloads or []):
        assert_tool_message_protocol(
            (payload or {}).get("messages") or [], label=f"{label}第 {i + 1} 次请求")
