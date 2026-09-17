"""最小版自动意图路由（R3+R4 降配，§8.1/ADR-045）。

R3 召回：候选来自 `subagent_service.list_subagents(user)`——已按 ACL 过滤、
`source_system=agent_workbench`、已发布的工作台智能体；广场第三方不在此列。
R4 精选：轻量 LLM 在候选中做 `matched(single) / direct_answer` 二分判定。
暂不做 R5 消歧多选与 R6 外部兜底（`ambiguous` 归并为 `direct_answer`）。

不变量：
- 只在无 R0 活动挂起、无 R1 显式 @、未选知识库、未开联网时调用（问答意图跳过路由）。
- 返回结构化决策，绝不让模型自由文本决定执行；未命中一律 direct_answer（主模型直答）。
- 候选描述属不可信展示文本，截断后进 prompt，模型只能返回候选 id 或 direct_answer。
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_DESC_LIMIT = 200
_HISTORY_TURNS = 4  # 送入路由器的最近消息条数（用于分辨跟进询问 vs 新请求）
_SYSTEM_PROMPT = (
    "你是校园 AXIOM 校园智能体的意图路由器。结合最近对话，判断用户【当前这条消息】是否是一个"
    "【新的、明确要办理的业务请求】，且与某个可用智能体的职责匹配。匹配才返回该智能体，"
    "否则一律 direct_answer（由主助手直接回答）。\n"
    "关键判据（务必区分）：\n"
    "1) 只有『现在要发起并办理某项业务』才 matched，例如『我要请假』『帮我提交报修』。\n"
    "2) 以下一律 direct_answer，即使句子里含业务关键词：\n"
    "   - 询问已办业务的状态/结果：『请假好了吗』『刚才提交成功没』『单号是多少』；\n"
    "   - 对刚完成任务的追问、评价、感谢、闲聊；\n"
    "   - 知识/概念问题：『怎么请假』『请假流程是什么』；\n"
    "   - 意图模糊、信息不足、或没有合适智能体。\n"
    "3) 如果最近对话显示该业务【刚刚已经办理/提交过】，用户的后续消息几乎都是跟进询问，"
    "应 direct_answer，不要再次发起同一业务。\n"
    "4) 不确定就 direct_answer，宁可不匹配也不要误发起业务。\n"
    "5) 若【多个】智能体都可能承接当前请求、无法确定唯一，返回 ambiguous 并列出 2-4 个候选 id，"
    "交由用户选择（不要臆断挑一个）。只有确实唯一匹配才 matched。\n"
    "示例：\n"
    '  用户「我想请假」→ {"decision":"matched","subagent_id":"<请假智能体id>"}\n'
    '  用户「我要办个事」（多个办事智能体都可能）→ {"decision":"ambiguous","subagent_ids":["<id1>","<id2>"]}\n'
    '  用户「请假好了吗」（上文刚提交过请假）→ {"decision":"direct_answer"}\n'
    '  用户「怎么请假」→ {"decision":"direct_answer"}\n'
    '只输出 JSON：{"decision":"matched","subagent_id":"<id>"} 或 '
    '{"decision":"ambiguous","subagent_ids":["<id>",...]} 或 {"decision":"direct_answer"}。'
)


def _candidate_block(candidates: List[Dict[str, Any]]) -> str:
    lines = []
    for c in candidates[: settings.AUTO_ROUTE_MAX_CANDIDATES]:
        desc = str(c.get("description") or "")[:_DESC_LIMIT]
        lines.append(f'- id={c.get("id")} 名称={c.get("name")} 职责={desc}')
    return "\n".join(lines)


def _history_block(history: Optional[List[Dict[str, str]]]) -> str:
    if not history:
        return "（无历史，本条为会话开头）"
    lines = []
    for m in history[-_HISTORY_TURNS:]:
        role = "用户" if m.get("role") == "user" else "助手"
        content = str(m.get("content") or "")[:300]
        if content:
            lines.append(f"{role}：{content}")
    return "\n".join(lines) or "（无有效历史）"


async def route(
    *,
    message: str,
    candidates: List[Dict[str, Any]],
    model: str,
    api_key: str,
    history: Optional[List[Dict[str, str]]] = None,
    source: str = "internal",
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
    parent_logical_call_id: str = "",
) -> Dict[str, Any]:
    """R4 单选 + R5 消歧判定。返回 {decision: matched|ambiguous|direct_answer, ...}。

    - matched：{subagent_id, name, source}
    - ambiguous（R5）：{candidates: [{id, name}], source}——交前端呈现多选澄清卡
    - direct_answer：主模型直答
    source：候选来源（internal=agent_workbench / external=external_app，供 R6 兜底标记）。
    history：最近对话（分辨"跟进询问"vs"新业务请求"）。任何异常/超时/解析失败一律降级 direct_answer。
    """
    if not candidates or not message or not message.strip() or not model or not api_key:
        return {"decision": "direct_answer", "reason_code": "NO_CANDIDATE_OR_INPUT"}

    valid_ids = {str(c.get("id")) for c in candidates}
    name_by_id = {str(c.get("id")): c.get("name") for c in candidates}
    user_prompt = (
        f"最近对话：\n{_history_block(history)}\n\n"
        f"用户当前消息：{message[:1000]}\n\n可用智能体：\n{_candidate_block(candidates)}\n\n"
        "请结合最近对话判定【当前消息】是否新业务请求，只输出 JSON。"
    )
    base_url = settings.NEWAPI_BASE_URL.rstrip("/")
    wire_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0,
    }
    from app.services.agent_harness import model_usage_audit

    logical = attempt = None
    if run_id:
        logical = await model_usage_audit.begin_logical_call(
            run_id=run_id,
            root_run_id=root_run_id,
            thread_id=thread_id,
            parent_logical_call_id=parent_logical_call_id,
            model=model,
            transport="chat_completions",
            purpose="router",
            purpose_detail=f"auto_route:{source}"[:200],
            scope_key=f"router:{source}"[:255],
            provider_api_key=api_key,
        )
        attempt = await model_usage_audit.begin_attempt(
            logical,
            wire_payload=wire_payload,
            attempt_kind="http_chat",
        )
    response_payload: Dict[str, Any] = {}
    resp = None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json=wire_payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        try:
            response_payload = resp.json()
        except Exception:  # noqa: BLE001
            response_payload = {}
        if resp.status_code >= 400:
            await model_usage_audit.finish_attempt(
                attempt,
                terminal_status="failed",
                usage=model_usage_audit.provider_usage_from_response(response_payload),
                response_id=model_usage_audit.provider_response_id(response_payload),
                provider_event_seen=True,
                terminal_seen=True,
                http_status=resp.status_code,
                committed=False,
            )
            await model_usage_audit.finish_logical_call(
                logical, terminal_status="failed", committed=False,
            )
            logger.info("自动路由 LLM 调用失败 %s，降级直答", resp.status_code)
            return {"decision": "direct_answer", "reason_code": "ROUTER_LLM_ERROR"}
        content = str(
            ((response_payload.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="completed" if content.strip() else "incomplete",
            usage=model_usage_audit.provider_usage_from_response(response_payload),
            response_id=model_usage_audit.provider_response_id(response_payload),
            provider_event_seen=True,
            terminal_seen=True,
            http_status=resp.status_code,
            committed=bool(content.strip()),
        )
        await model_usage_audit.finish_logical_call(
            logical,
            terminal_status="completed" if content.strip() else "incomplete",
            selected_attempt_id=(attempt.attempt_id if attempt else ""),
            committed=bool(content.strip()),
        )
    except asyncio.CancelledError:
        response_seen = resp is not None
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="cancelled",
            usage=model_usage_audit.provider_usage_from_response(response_payload),
            provider_event_seen=response_seen,
            terminal_seen=response_seen,
            http_status=getattr(resp, "status_code", None),
            committed=False,
        )
        await model_usage_audit.finish_logical_call(
            logical, terminal_status="cancelled", committed=False,
        )
        raise
    except Exception as e:  # noqa: BLE001
        response_seen = resp is not None
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="failed",
            usage=model_usage_audit.provider_usage_from_response(response_payload),
            provider_event_seen=response_seen,
            terminal_seen=response_seen,
            http_status=getattr(resp, "status_code", None),
            error_code=type(e).__name__,
            committed=False,
        )
        await model_usage_audit.finish_logical_call(
            logical, terminal_status="failed", committed=False,
        )
        logger.info("自动路由异常，降级直答: %s", e)
        return {"decision": "direct_answer", "reason_code": "ROUTER_EXCEPTION"}

    decision = _parse_decision(content)
    kind = decision.get("decision")
    if kind == "matched":
        sid = str(decision.get("subagent_id") or "")
        # 校验模型只能选真实候选（防幻觉 id / 越权）
        if sid in valid_ids:
            return {"decision": "matched", "subagent_id": sid, "name": name_by_id.get(sid),
                    "source": source, "reason_code": "LLM_MATCHED"}
        logger.info("自动路由模型返回非法候选 id=%s，降级直答", sid)
    elif kind == "ambiguous":
        raw_ids = decision.get("subagent_ids") or []
        picked = [str(i) for i in raw_ids if str(i) in valid_ids][:4]
        # 去重保序
        seen: set = set()
        picked = [i for i in picked if not (i in seen or seen.add(i))]
        if len(picked) >= 2:
            return {
                "decision": "ambiguous",
                "candidates": [{"id": i, "name": name_by_id.get(i)} for i in picked],
                "source": source,
                "reason_code": "LLM_AMBIGUOUS",
            }
        if len(picked) == 1:  # 只剩一个合法候选 → 退化为单选命中
            sid = picked[0]
            return {"decision": "matched", "subagent_id": sid, "name": name_by_id.get(sid),
                    "source": source, "reason_code": "LLM_AMBIGUOUS_TO_SINGLE"}
    return {"decision": "direct_answer", "reason_code": "LLM_DIRECT_ANSWER"}


def _parse_decision(content: str) -> Dict[str, Any]:
    """从模型输出抽取 JSON 决策；容忍 ```json 包裹与前后噪声。"""
    text = content.strip()
    if "```" in text:
        # 取第一个代码块内容
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                text = p
                break
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict) and data.get("decision") in ("matched", "ambiguous", "direct_answer"):
                return data
        except json.JSONDecodeError:
            pass
    return {"decision": "direct_answer"}
