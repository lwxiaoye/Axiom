"""交付验收裁定（执行团队一期数据层，2026-07-27 产品定义 §验收环节）。

职责三分：子智能体只交付、主对话只验收、主对话最后总结——本模块是"验收"的机器质检步：
按委派时的结构化验收标准（acceptance_criteria）逐条核对交付文本与产物清单，产出验收单
[{criterion, passed, evidence}]。**无打回闭环**：未过项如实进入验收单，由最终总结承接。

护栏：
- fail-open——裁定服务异常/超时/输出不可解析一律返回 None（未验收 ≠ 验收失败），
  绝不因验收环节故障拦下交付；
- 逐条裁定数量与输入标准数强对齐（模型漏答的条目补 passed=None 的 unknown 行）；
- evidence 截断 120 字；整体 JSON 强校验。
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.core.model_endpoint import get_model_base_url
from app.core.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30.0
_SYSTEM = (
    "你是交付验收员。根据「验收标准」逐条核对「交付内容」，输出严格的 JSON："
    '{"verdicts":[{"criterion":"<原样复述标准>","passed":true|false,"evidence":"<≤50字的依据，'
    "引用交付内容里的具体事实>\"}]}。规则：每条标准恰好一个裁定，顺序与输入一致；"
    "只依据给出的交付内容判断，不臆测未展示的内容；无法核验的条目 passed=false 并在"
    " evidence 里写明「交付内容中未体现」。只输出 JSON，不要任何解释或代码围栏。"
)


def _parse_verdicts(content: str, criteria: List[str]) -> Optional[List[Dict[str, Any]]]:
    """严格解析 + 与标准清单强对齐；解析不出返回 None（fail-open）。"""
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    raw = data.get("verdicts")
    if not isinstance(raw, list):
        return None
    verdicts: List[Dict[str, Any]] = []
    for i, criterion in enumerate(criteria):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        passed = item.get("passed")
        verdicts.append({
            "criterion": criterion,
            "passed": bool(passed) if isinstance(passed, bool) else None,
            "evidence": str(item.get("evidence") or "")[:120],
        })
    return verdicts


async def review(
    *,
    task: str,
    criteria: List[str],
    result_text: str,
    model: str,
    api_key: str,
    run_id: str = "",
    thread_id: str = "",
    root_run_id: str = "",
    parent_logical_call_id: str = "",
) -> Optional[Dict[str, Any]]:
    """逐条验收裁定。返回 {"verdicts":[...], "passed_count", "total"}；异常一律 None。

    2026-07-28：原有的 `artifacts=` 入参（产物清单）已删除——子智能体侧从来产不出
    结构化产物（见 sse_protocol.subagent_completed 注释），这个参数恒为 None，
    "产物清单" 那段提示词从未出现过。裁定只依据交付正文。
    """
    crits = [str(c).strip() for c in (criteria or []) if str(c).strip()][:5]
    if not crits or not (result_text or "").strip():
        return None
    user = (
        f"委派任务：{str(task or '')[:800]}\n\n"
        f"验收标准（逐条裁定，顺序一致）：\n"
        + "\n".join(f"{i + 1}. {c}" for i, c in enumerate(crits))
        + f"\n\n交付内容：\n{str(result_text or '')[:6000]}"
    )
    wire_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "temperature": 0,
    }
    from app.services.agent_harness import model_usage_audit

    logical = attempt = None
    if run_id:
        logical = await model_usage_audit.begin_logical_call(
            run_id=run_id,
            thread_id=thread_id,
            root_run_id=root_run_id,
            parent_logical_call_id=parent_logical_call_id,
            model=model,
            transport="chat_completions",
            purpose="acceptance",
            purpose_detail="subagent_delivery_review",
            scope_key="acceptance",
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
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{get_model_base_url().rstrip('/')}/chat/completions",
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
                logger.warning("验收裁定调用失败: %s %s", resp.status_code, resp.text[:200])
                return None
            content = (response_payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
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
    except Exception:  # noqa: BLE001
        response_seen = resp is not None
        await model_usage_audit.finish_attempt(
            attempt,
            terminal_status="failed",
            usage=model_usage_audit.provider_usage_from_response(response_payload),
            provider_event_seen=response_seen,
            terminal_seen=response_seen,
            http_status=getattr(resp, "status_code", None),
            committed=False,
        )
        await model_usage_audit.finish_logical_call(
            logical, terminal_status="failed", committed=False,
        )
        logger.warning("验收裁定异常（fail-open，跳过验收）", exc_info=True)
        return None
    verdicts = _parse_verdicts(content, crits)
    terminal_status = "completed" if verdicts is not None else "incomplete"
    await model_usage_audit.finish_attempt(
        attempt,
        terminal_status=terminal_status,
        usage=model_usage_audit.provider_usage_from_response(response_payload),
        response_id=model_usage_audit.provider_response_id(response_payload),
        provider_event_seen=True,
        terminal_seen=True,
        http_status=getattr(resp, "status_code", None),
        committed=verdicts is not None,
    )
    await model_usage_audit.finish_logical_call(
        logical,
        terminal_status=terminal_status,
        selected_attempt_id=(attempt.attempt_id if attempt else ""),
        committed=verdicts is not None,
    )
    if verdicts is None:
        logger.warning("验收裁定输出不可解析（fail-open，跳过验收）：%s", content[:200])
        return None
    return {
        "verdicts": verdicts,
        "passed_count": sum(1 for v in verdicts if v.get("passed") is True),
        "total": len(verdicts),
    }
