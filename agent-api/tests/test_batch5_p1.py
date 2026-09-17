"""第五批 P1 回归（2026-07-17）。

1. cancel 撞终态：无任务分支下 Run 恰好先完成 → 统一 dict 结构（此前返回 bool，
   router 取 .get() 直接 500）；
2. 无任务取消分支：run.failed 终态帧落事件库 + 历史锚点（此前只投内存，刷新即失）；
3. input.accepted 帧：Harness Protocol 1 形状。
"""
import asyncio
import json
import unittest
from datetime import datetime

from app.services import sse_protocol
from app.services.tasks import task_run_service as trs


class CancelTerminalRaceTests(unittest.TestCase):
    def _setup(self, svc, run_id):
        svc._run_meta[run_id] = {
            "user_id": "u1", "thread_id": "t1", "protocol": sse_protocol.HARNESS,
        }
        svc._run_tasks.pop(run_id, None)  # 无本地执行任务 → else 分支

    def test_completed_before_cancel_returns_dict(self):
        import app.services.agent_harness.orchestrator as cs_mod
        svc = cs_mod.harness_orchestrator
        run_id = "run-race-test"
        self._setup(svc, run_id)

        get_run_results = [None, {"status": "completed", "thread_id": "t1"}]

        async def _fake_get_run(rid, uid):
            return get_run_results.pop(0) if get_run_results else None

        async def _fake_cancel(rid):
            return False  # CAS 未命中＝已终态

        orig_get, orig_cancel = trs.get_run, trs.cancel_run
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        trs.cancel_run = _fake_cancel  # type: ignore[assignment]
        try:
            result = asyncio.run(svc.cancel_chat_run("u1", run_id))
        finally:
            trs.get_run = orig_get  # type: ignore[assignment]
            trs.cancel_run = orig_cancel  # type: ignore[assignment]
            svc._run_meta.pop(run_id, None)
        # 必须是统一 dict 结构（router 按 .get("status") 消费），且如实回传真实终态
        self.assertEqual(result, {"status": "completed"})

    def test_no_task_cancel_persists_terminal_frame_and_anchor(self):
        import app.services.agent_harness.orchestrator as cs_mod
        from app.services.tasks import run_reconcile_service as rrs
        svc = cs_mod.harness_orchestrator
        run_id = "run-notask-test"
        self._setup(svc, run_id)

        recorded: list = []
        anchors: list = []

        async def _fake_get_run(rid, uid):
            return None  # Runtime 降级：走 meta 兜底

        async def _fake_cancel(rid):
            return True

        async def _fake_last_seq(rid, uid):
            return 7

        async def _fake_record(rid, payload):
            recorded.append(payload)

        async def _fake_anchor(rid, thread_id, placeholder=None):
            anchors.append((rid, thread_id))
            return 1

        orig = (trs.get_run, trs.cancel_run, trs.get_last_event_sequence,
                trs.record_sse_payload, rrs.ensure_terminal_anchor)
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        trs.cancel_run = _fake_cancel  # type: ignore[assignment]
        trs.get_last_event_sequence = _fake_last_seq  # type: ignore[assignment]
        trs.record_sse_payload = _fake_record  # type: ignore[assignment]
        rrs.ensure_terminal_anchor = _fake_anchor  # type: ignore[assignment]
        try:
            result = asyncio.run(svc.cancel_chat_run("u1", run_id))
        finally:
            (trs.get_run, trs.cancel_run, trs.get_last_event_sequence,
             trs.record_sse_payload, rrs.ensure_terminal_anchor) = orig
            svc._run_meta.pop(run_id, None)
            svc._run_buffers.pop(run_id, None)
        self.assertEqual(result, {"status": "cancelled"})
        self.assertEqual(len(recorded), 1)
        frame = json.loads(recorded[0].replace("data: ", "", 1))
        self.assertEqual(frame["type"], "run.cancelled")
        self.assertEqual(frame["sequence"], 8)  # 从 last_seq 续号，前端 seq 闸不丢
        self.assertEqual(anchors, [(run_id, "t1")])

    def test_foreign_worker_cancel_waits_for_real_terminal_ack(self):
        """V2 API 不得先宣布 cancelled；要等 Worker pump 真正收敛。"""
        import app.services.agent_harness.orchestrator as cs_mod
        from app.services.chat import run_hub as hub_mod

        svc = cs_mod.harness_orchestrator
        run_id = "run-cross-worker-cancel"
        self._setup(svc, run_id)
        running = {
            "id": run_id,
            "thread_id": "t1",
            "user_id": "u1",
            "status": "running",
            "owner_instance_id": "worker-process",
            "heartbeat_at": datetime.utcnow().isoformat(),
        }
        reads = [running, {**running, "status": "cancelled"}]
        requested: list = []
        direct_cancelled: list = []

        async def _fake_get_run(_rid, _uid):
            return reads.pop(0) if reads else {**running, "status": "cancelled"}

        async def _fake_request(_rid, _uid):
            requested.append((_rid, _uid))
            return "requested"

        async def _forbidden_direct_cancel(_rid):
            direct_cancelled.append(_rid)
            return True

        orig = (trs.get_run, trs.request_run_cancel, trs.cancel_run,
                hub_mod._CANCEL_ACK_TIMEOUT_S, hub_mod._CANCEL_ACK_POLL_S)
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        trs.request_run_cancel = _fake_request  # type: ignore[assignment]
        trs.cancel_run = _forbidden_direct_cancel  # type: ignore[assignment]
        hub_mod._CANCEL_ACK_TIMEOUT_S = 0.1
        hub_mod._CANCEL_ACK_POLL_S = 0.001
        try:
            result = asyncio.run(svc.cancel_chat_run("u1", run_id))
        finally:
            (trs.get_run, trs.request_run_cancel, trs.cancel_run,
             hub_mod._CANCEL_ACK_TIMEOUT_S, hub_mod._CANCEL_ACK_POLL_S) = orig
            svc._run_meta.pop(run_id, None)
        self.assertEqual(result, {"status": "cancelled"})
        self.assertEqual(requested, [(run_id, "u1")])
        self.assertEqual(direct_cancelled, [])

    def test_foreign_worker_cancel_request_failure_never_fake_cancels(self):
        """请求位写失败时只能 pending，不能绕回直接终态化。"""
        import app.services.agent_harness.orchestrator as cs_mod

        svc = cs_mod.harness_orchestrator
        run_id = "run-cross-worker-request-failed"
        self._setup(svc, run_id)
        running = {
            "id": run_id,
            "thread_id": "t1",
            "user_id": "u1",
            "status": "running",
            "owner_instance_id": "worker-process",
            "heartbeat_at": datetime.utcnow().isoformat(),
        }
        direct_cancelled: list = []

        async def _fake_get_run(_rid, _uid):
            return dict(running)

        async def _failed_request(_rid, _uid):
            return None

        async def _forbidden_direct_cancel(_rid):
            direct_cancelled.append(_rid)
            return True

        orig = (trs.get_run, trs.request_run_cancel, trs.cancel_run)
        trs.get_run = _fake_get_run  # type: ignore[assignment]
        trs.request_run_cancel = _failed_request  # type: ignore[assignment]
        trs.cancel_run = _forbidden_direct_cancel  # type: ignore[assignment]
        try:
            result = asyncio.run(svc.cancel_chat_run("u1", run_id))
        finally:
            trs.get_run, trs.request_run_cancel, trs.cancel_run = orig
            svc._run_meta.pop(run_id, None)
        self.assertEqual(result, {"status": "pending"})
        self.assertEqual(direct_cancelled, [])


class ResumeAcceptedFrameTests(unittest.TestCase):
    def test_v1_frame_shape(self):
        channel = sse_protocol.SSEChannel(sse_protocol.HARNESS, "t1", "r1", start_sequence=3)
        frame = json.loads(channel.input_accepted().replace("data: ", "", 1))
        self.assertEqual(frame["type"], "input.accepted")
        self.assertEqual(frame["sequence"], 4)
        self.assertEqual(frame["run_id"], "r1")

if __name__ == "__main__":
    unittest.main()
