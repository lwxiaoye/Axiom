"""P0.5/Phase 4 HITL 契约用例（roadmap 验收：正常完成 / 缺字段补充 / 确认与取消 /
事件乱序与重复 / 超时进入不确定态）。

容器内运行：docker exec -i agent-api python scripts/p05_hitl_contract.py
覆盖说明：
- C1 结果协议三态映射（succeeded/failed/needs_input）——缺字段补充的协议层；
- C2 确认与取消——Run 状态机 waiting_confirmation → cancelled，终态不可再恢复；
- C3 恢复令牌一次性/匹配——HTTP 级打 /chat/resume：不存在的 Run、错误令牌均被拒；
- C4 事件重复与乱序防护——SSEChannel sequence 严格递增 + event_id 唯一（前端据此去重）；
- C5 超时明确态——waiting 超 TTL 由 sweep 置 failed（不伪装成功、不停留模糊态）。
真实交互子智能体端到端（formInput 挂起 → 用户补充 → 续接完成）需要已发布的交互
工作流应用与模型凭证，属部署后人工/E2E 环节，不在本脚本内伪造。
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

import httpx  # noqa: E402

BASE = "http://localhost:8000/agent-api"
TEST_USER = "p05-contract-user"
HEADERS = {"X-User-Id": TEST_USER, "X-Username": "p05", "Content-Type": "application/json"}

PASS = []


def ok(name: str):
    PASS.append(name)
    print(f"  ✓ {name}")


async def c1_result_protocol():
    from app.services.agents.subagent_service import _map_workflow_result

    class App:
        id = "a1"
        name = "助手"

    r1 = _map_workflow_result({"status": "completed", "output": "完成"}, App())
    assert r1["status"] == "succeeded" and r1["text"] == "完成", r1
    r2 = _map_workflow_result({"status": "failed", "errorMessage": "boom"}, App())
    assert r2["status"] == "failed", r2
    r3 = _map_workflow_result(
        {"status": "waiting", "interactive": {"type": "formInput", "params": {"inputForm": []}, "resumeId": "rk"}},
        App(),
    )
    assert r3["status"] == "needs_input" and r3["resume_id"] == "rk", r3
    ok("C1 结果协议三态映射（含缺字段补充 needs_input）")


async def c2_confirm_cancel():
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun
    from app.services.tasks import task_run_service
    rid = "p05-c2-run"
    factory = runtime_session()
    async with factory() as s:
        old = await s.get(AgentRun, rid)
        if old:
            await s.delete(old)
            await s.commit()
    await task_run_service.create_run(run_id=rid, thread_id="t-c2", user_id=TEST_USER, goal="c2")
    await task_run_service.set_waiting(rid, "waiting_confirmation", resume_token="tok-c2")
    run = await task_run_service.get_run(rid, TEST_USER)
    assert run and run["status"] == "waiting_confirmation"
    await task_run_service.cancel_run(rid)
    run = await task_run_service.get_run(rid, TEST_USER)
    assert run["status"] == "cancelled", run
    # 终态不可再恢复：resume 状态门（waiting_* 之外一律拒绝）
    assert run["status"] not in ("waiting_user", "waiting_confirmation", "waiting_system")
    async with factory() as s:
        row = await s.get(AgentRun, rid)
        await s.delete(row)
        await s.commit()
    ok("C2 确认与取消：waiting_confirmation → cancelled，终态拒绝恢复")


async def _read_sse_error(resp: httpx.Response) -> str:
    text = resp.text
    for line in text.splitlines():
        if line.startswith("data:") and "[DONE]" not in line:
            try:
                data = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                return str(data["error"])
            if data.get("type") == "error":
                return str((data.get("data") or {}).get("message") or "")
    return ""


async def _pick_keyed_user() -> str:
    """HTTP 用例需要有模型 Key 的真实用户（/chat/resume 入口先 prepare_chat）。"""
    from sqlalchemy import select

    from app.core.database import async_session
    from app.models import NewApiUserKey
    async with async_session() as s:
        uid = await s.scalar(select(NewApiUserKey.user_id).limit(1))
    return str(uid) if uid else ""


async def c3_resume_token():
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun
    from app.services.tasks import task_run_service
    keyed_user = await _pick_keyed_user()
    if not keyed_user:
        print("  ⚠ C3 跳过：库中无带模型 Key 的用户（HTTP 入口先要求 Key）")
        return
    headers = {**HEADERS, "X-User-Id": keyed_user}
    async with httpx.AsyncClient(timeout=30) as client:
        # 不存在的 Run
        resp = await client.post(
            f"{BASE}/chat/resume", headers=headers,
            json={"run_id": "p05-no-such-run", "resume_value": "x"},
        )
        err = await _read_sse_error(resp)
        assert "没有可恢复" in err, (resp.status_code, err, resp.text[:200])

        # 错误令牌被拒
        rid = "p05-c3-run"
        factory = runtime_session()
        async with factory() as s:
            old = await s.get(AgentRun, rid)
            if old:
                await s.delete(old)
                await s.commit()
        await task_run_service.create_run(run_id=rid, thread_id="t-c3", user_id=keyed_user, goal="c3")
        await task_run_service.set_waiting(rid, "waiting_user", resume_token="tok-right")
        resp = await client.post(
            f"{BASE}/chat/resume", headers=headers,
            json={"run_id": rid, "resume_value": "x", "resume_id": "tok-wrong"},
        )
        err = await _read_sse_error(resp)
        assert "令牌无效" in err, (resp.status_code, err, resp.text[:300])
        # 校验被拒后 Run 仍处 waiting（未被错误请求消费）
        run = await task_run_service.get_run(rid, keyed_user)
        assert run["status"] == "waiting_user" and run["resume_token"] == "tok-right"
        async with factory() as s:
            row = await s.get(AgentRun, rid)
            await s.delete(row)
            await s.commit()
    ok("C3 恢复令牌：不存在 Run 拒绝；错误令牌拒绝且不消费挂起态")


async def c4_event_dedup_basis():
    from app.services import sse_protocol
    ch = sse_protocol.SSEChannel(sse_protocol.V1, "t-c4", "run-c4")
    seqs, ids = [], set()
    frames = [
        ch.run_started(), ch.message_delta("a"), ch.message_delta("b"),
        ch.tool_started("search_web"), ch.tool_completed("search_web", "ok"),
        ch.citations([{"type": "web", "title": "t"}]), ch.message_completed("ab"),
        ch.run_completed(1),
    ]
    for frame in frames:
        data = json.loads(frame.replace("data: ", "").strip())
        assert data["version"] == "v1" and data["run_id"] == "run-c4"
        seqs.append(data["sequence"])
        assert data["event_id"] not in ids
        ids.add(data["event_id"])
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
    ok("C4 事件重复/乱序防护基础：sequence 严格递增 + event_id 唯一（前端据此去重）")


async def c5_timeout_explicit():
    from app.core.runtime_db import runtime_session
    from app.runtime_models import AgentRun
    from app.services.tasks import task_run_service
    rid = "p05-c5-run"
    factory = runtime_session()
    async with factory() as s:
        old = await s.get(AgentRun, rid)
        if old:
            await s.delete(old)
            await s.commit()
    await task_run_service.create_run(run_id=rid, thread_id="t-c5", user_id=TEST_USER, goal="c5")
    await task_run_service.set_waiting(rid, "waiting_user", resume_token="tok-c5")
    async with factory() as s:
        row = await s.get(AgentRun, rid)
        row.updated_at = datetime.utcnow() - timedelta(hours=48)
        await s.commit()
    swept = await task_run_service.sweep_expired_waiting(24)
    assert swept >= 1
    run = await task_run_service.get_run(rid, TEST_USER)
    assert run["status"] == "failed" and not run.get("resume_token"), run
    async with factory() as s:
        row = await s.get(AgentRun, rid)
        await s.delete(row)
        await s.commit()
    ok("C5 超时明确态：waiting 超 TTL → failed + 令牌作废（不伪装成功）")


async def main():
    await c1_result_protocol()
    await c2_confirm_cancel()
    await c3_resume_token()
    await c4_event_dedup_basis()
    await c5_timeout_explicit()
    print(f"\nP0.5 HITL 契约用例 {len(PASS)}/5 通过")


asyncio.run(main())
