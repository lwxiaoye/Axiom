"""Review a research draft against receipt-backed evidence before publishing it."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import aclosing

from app.services.agent_harness import run_store
from app.services.agent_harness.context import ProjectionLedgerState
from app.services.agent_harness.public_errors import ResearchReportRejected
from .engine import _get_research_blob, evidence_brief, synthesis_sources
from .editing import REVIEW_PROMPT, apply_review


def _draft_for_storage(final):
    # Keep the deferred projection commit intact across the Runtime JSON boundary.
    bundle = final.get("_projection_commit")
    if isinstance(bundle, dict) and isinstance(bundle.get("state"), ProjectionLedgerState):
        return {**final, "_projection_commit": {
            **bundle, "state": bundle["state"].model_dump(mode="json")}}
    return final


def _restore_draft(final):
    bundle = final.get("_projection_commit") if isinstance(final, dict) else None
    if isinstance(bundle, dict) and isinstance(bundle.get("state"), dict):
        return {**final, "_projection_commit": {
            **bundle, "state": ProjectionLedgerState.model_validate(bundle["state"])}}
    return final


async def reviewed_report(env, driver, **kwargs):
    parent = await run_store.get_run_snapshot(env.run_id)
    if parent is None:
        raise RuntimeError("研究报告运行状态不可用")
    if parent.cancel_requested:
        raise asyncio.CancelledError()
    try:
        async with aclosing(_reviewed_report(env, driver, **kwargs)) as events:
            async for event in events:
                yield event
    except Exception:
        snapshot = await run_store.get_run_snapshot(env.run_id)
        if snapshot is None or snapshot.goal_revision != parent.goal_revision:
            raise RuntimeError("研究目标已变更，报告需要重新核对")
        if snapshot.cancel_requested:
            raise asyncio.CancelledError()
        # Transport/storage failures retain their recoverable type. Only a
        # persisted citation rejection may terminally fail the shared Run.
        raise


async def _reviewed_report(env, driver, **kwargs):
    parent = await run_store.get_run_snapshot(env.run_id)
    if parent is None:
        raise RuntimeError("研究报告运行状态不可用")
    if parent.cancel_requested:
        raise asyncio.CancelledError()
    revision = parent.goal_revision
    packed = await run_store.get_run_state(env.run_id)
    failure = ((packed or {}).get("state") or {}).get("research_report_review_failure") or {}
    if failure.get("goalRevision") == revision:
        if failure.get("reason") == "report_call_timeout":
            if await run_store.patch_run_state(env.run_id, {"research_report_review_failure": None}) is None:
                raise RuntimeError("研究报告恢复状态保存失败")
        else:
            raise ResearchReportRejected("报告引用核验未通过；已保留核验结果，不重复生成未经核验的报告。")

    async def current():
        snapshot = await run_store.get_run_snapshot(env.run_id)
        if snapshot is None or snapshot.goal_revision != revision:
            raise RuntimeError("研究目标已变更，报告需要重新核对")
        if snapshot.cancel_requested:
            raise asyncio.CancelledError()

    ledger, _ = await _get_research_blob(env.run_id)
    evidence_key = hashlib.sha256(evidence_brief(ledger).encode()).hexdigest()
    cached = ((packed or {}).get("state") or {}).get("research_report_draft") or {}
    final = _restore_draft(cached.get("final")) if cached.get("goalRevision") == revision and cached.get("evidenceKey") == evidence_key else None
    if final is None:
        async with aclosing(driver(**kwargs)) as events:
            async for event in events:
                if event.get("type") == "final":
                    final = event
                    await current()
                    # Closing a completed model stream can itself fail. Commit
                    # its result first so recovery never pays to regenerate it.
                    saved = await run_store.patch_run_state(env.run_id, {"research_report_draft": {
                        "goalRevision": revision, "evidenceKey": evidence_key, "final": _draft_for_storage(final)}})
                    if saved is None:
                        raise RuntimeError("研究报告草稿保存失败")
                    break
                elif event.get("type") != "delta":
                    yield event
    if final is None:
        raise RuntimeError("研究报告模型未返回完整正文")
    await current()
    draft = str(final.get("answer") or "")
    ledger, _ = await _get_research_blob(env.run_id)
    key = hashlib.sha256((draft + evidence_brief(ledger)).encode()).hexdigest()
    packed = await run_store.get_run_state(env.run_id)
    prior = ((packed or {}).get("state") or {}).get("research_report_review") or {}
    reviewed = str(prior.get("answer") or "") if prior.get("key") == key and prior.get("goalRevision") == revision else ""
    if not reviewed:
        response = ((packed or {}).get("state") or {}).get("research_report_review_response") or {}
        same_review = response.get("key") == key and response.get("goalRevision") == revision
        review_answer = response.get("answer") if same_review else None
        if review_answer is None:
            async def save_review_cursor(messages, **_):
                await current()
                saved = await run_store.patch_run_state(env.run_id, {"research_report_review_response": {
                    "key": key, "goalRevision": revision,
                    "messages": run_store.trim_loop_checkpoint_messages(messages), "answer": None,
                }})
                if saved is None:
                    raise RuntimeError("研究报告核验现场保存失败")

            async with aclosing(driver(
                model=env.resolved_model, api_key=env.newapi_key, tools=[], research_profile=True,
                # Drafts and sources are evidence, not authorization for memory actions.
                raw_user_message=env.message,
                system_prompt=REVIEW_PROMPT,
                initial_messages=response.get("messages") if same_review else None,
                user_input=json.dumps({"用户研究问题": ledger.query or env.message,
                    "本次要求": env.message, "待核验草稿": draft,
                    "实际证据台账": evidence_brief(ledger), "覆盖不足": not ledger.cross_validated()}, ensure_ascii=False),
                gateway={"run_id": "", "thread_id": "", "user_id": str(env.user_id),
                    "audit_run_id": env.run_id, "audit_thread_id": env.thread_id,
                    "root_run_id": env.run_id, "audit_purpose": "subagent_model",
                    "checkpoint_sink": save_review_cursor,
                    "audit_scope": f"research_report_review|{env.run_id}", "research_synthesis_only": True})) as events:
                async for event in events:
                    if event.get("type") == "final":
                        review_answer = str(event.get("answer") or "").strip()
                        await current()
                        saved = await run_store.patch_run_state(env.run_id, {"research_report_review_response": {
                            "key": key, "goalRevision": revision, "answer": review_answer, "messages": None}})
                        if saved is None:
                            raise RuntimeError("研究报告核验响应保存失败")
                        break
        if review_answer is None:
            raise RuntimeError("研究报告核验模型未返回完整结果")
        await current()
        count = len(synthesis_sources(ledger))
        try:
            reviewed, decision = apply_review(draft, review_answer, count)
        except ValueError as exc:
            saved = await run_store.patch_run_state(env.run_id, {"research_report_review_failure": {
                "key": key, "goalRevision": revision, "answer": review_answer,
                "sourceCount": count, "reason": str(exc),
            }})
            if saved is None:
                raise RuntimeError("研究报告核验失败记录保存失败")
            raise ResearchReportRejected("报告尚未通过来源引用核验，未发布未经核验的草稿。")
        patch = {"research_report_review": {
            "key": key, "goalRevision": revision, "answer": reviewed, "decision": decision}}
        if decision["coverage"] == "partial":
            patch["research"] = ledger.model_copy(update={"coverage_outcome": "partial",
                "reason_codes": list(dict.fromkeys([*ledger.reason_codes, "research_quality_incomplete"]))}).to_state()
        saved = await run_store.patch_run_state(env.run_id, patch)
        if saved is None:
            raise RuntimeError("研究报告核验结果保存失败")
    await current()
    yield {"type": "delta", "text": reviewed}
    published = {**final, "answer": reviewed}
    if isinstance(final.get("_projection_commit"), dict):
        # The next turn must continue from the published revision, not replay a
        # provider-native draft item that bypasses its replacement content.
        published["_projection_commit"] = {
            **final["_projection_commit"],
            "assistant_item": {"role": "assistant", "content": reviewed},
        }
    yield published
