"""发布链路补全的离线回归：审核员本人提交自动通过、对话 Agent 未配置的可读报错。

全部不落库：session / 发布函数用 mock 掐断，只验证分支选择与返回契约。
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import workflow as wf
from app.routers.workflow import DefinitionSaveRequest

ADMIN = UserContext(user_id="1", username="admin", real_name="管理员")
MEMBER = UserContext(user_id="u-member", username="zhangsan", real_name="张三")

MINIMAL_CANVAS = json.dumps({"version": 1, "fastgpt": {"nodes": [{"nodeId": "n1", "flowNodeType": "workflowStart"}], "edges": []}})


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    """_submit_or_publish 只用到 execute(definition 查询 / 待审查询)；其余全部空实现。"""

    def __init__(self, definition=None, pending_id=None):
        self.definition = definition
        self.pending_id = pending_id
        self.queries = 0

    async def execute(self, _statement):
        self.queries += 1
        if self.queries == 1:
            return _ScalarResult(self.definition)
        return _ScalarResult(self.pending_id)

    async def commit(self):
        return None


def _session_factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


class SelfReviewAutoApproveTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = SimpleNamespace(id="app-1", ai_app_type="chatAgent", status="draft", config_json="{}", api_enabled=False)
        self.version = SimpleNamespace(
            id="v-1", app_id="app-1", version_no=1, ai_app_type="chatAgent", status="approved",
            change_note="", visible_role_ids="[]", visible_dept_ids="[]", submitted_by="1",
            submitted_by_name="管理员", submitted_at=None, reviewed_by="1", reviewed_by_name="管理员",
            reviewed_at=None, review_comment="", published_at=None, routing_json=None,
        )

    def _patches(self, session):
        return [
            mock.patch.object(wf, "async_session", new=_session_factory(session)),
            mock.patch.object(wf, "_require_permission", new=mock.AsyncMock(return_value=(self.app, "OWNER"))),
            mock.patch.object(wf, "load_published_visibility_version", new=mock.AsyncMock(return_value=None)),
            mock.patch.object(wf.capability_registry, "sync_from_app", new=mock.AsyncMock()),
            mock.patch.object(wf.settings, "PUBLISH_APPROVAL_REQUIRED", True),
        ]

    async def test_reviewer_submission_publishes_immediately_without_pending_queue(self):
        session = _Session(definition=None, pending_id=None)
        publish_now = mock.AsyncMock(return_value=self.version)
        submit_review = mock.AsyncMock()
        patches = self._patches(session) + [
            mock.patch.object(wf, "_do_publish_now", new=publish_now),
            mock.patch.object(wf, "_do_submit_review", new=submit_review),
        ]
        for patch in patches:
            patch.start()
        try:
            result = await wf._submit_or_publish(DefinitionSaveRequest(appId="app-1", workflowJson=MINIMAL_CANVAS), ADMIN)
        finally:
            for patch in patches:
                patch.stop()

        submit_review.assert_not_awaited()
        publish_now.assert_awaited_once()
        self.assertEqual(publish_now.await_args.kwargs["review_comment"], "审核员本人提交，自动通过")
        self.assertFalse(result["approvalRequired"])
        self.assertTrue(result["autoApproved"])
        self.assertIn("自动通过", result["message"])

    async def test_member_submission_still_enters_pending_review(self):
        session = _Session(definition=None, pending_id=None)
        self.version.status = "pending_review"
        publish_now = mock.AsyncMock()
        submit_review = mock.AsyncMock(return_value=self.version)
        patches = self._patches(session) + [
            mock.patch.object(wf, "_do_publish_now", new=publish_now),
            mock.patch.object(wf, "_do_submit_review", new=submit_review),
        ]
        for patch in patches:
            patch.start()
        try:
            result = await wf._submit_or_publish(DefinitionSaveRequest(appId="app-1", workflowJson=MINIMAL_CANVAS), MEMBER)
        finally:
            for patch in patches:
                patch.stop()

        publish_now.assert_not_awaited()
        submit_review.assert_awaited_once()
        self.assertTrue(result["approvalRequired"])
        self.assertNotIn("autoApproved", result)

    async def test_reviewer_direct_publish_is_blocked_while_another_version_is_pending(self):
        session = _Session(definition=None, pending_id="v-pending")
        publish_now = mock.AsyncMock()
        patches = self._patches(session) + [mock.patch.object(wf, "_do_publish_now", new=publish_now)]
        for patch in patches:
            patch.start()
        try:
            with self.assertRaises(HTTPException) as ctx:
                await wf._submit_or_publish(DefinitionSaveRequest(appId="app-1", workflowJson=MINIMAL_CANVAS), ADMIN)
        finally:
            for patch in patches:
                patch.stop()

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("待审核", ctx.exception.detail)
        publish_now.assert_not_awaited()

    async def test_capabilities_flag_self_publish_auto_approval(self):
        with mock.patch.object(wf.settings, "PUBLISH_APPROVAL_REQUIRED", True):
            admin_caps = await wf.my_capabilities(ADMIN)
            member_caps = await wf.my_capabilities(MEMBER)
        self.assertTrue(admin_caps["isReviewer"])
        self.assertTrue(admin_caps["selfPublishAutoApproved"])
        self.assertFalse(member_caps["isReviewer"])
        self.assertFalse(member_caps["selfPublishAutoApproved"])


class ChatAgentValidationMessageTests(IsolatedAsyncioTestCase):
    async def _validate(self, ai_app_type: str, workflow_json: str):
        app = SimpleNamespace(id="app-1", ai_app_type=ai_app_type)
        with mock.patch.object(wf, "_get_app", new=mock.AsyncMock(return_value=app)):
            with mock.patch.object(wf, "_validate_dynamic_node_resources", new=mock.AsyncMock(return_value=[])):
                await wf._validate_before_publish(object(), MEMBER, workflow_json, "app-1")

    async def test_unconfigured_chat_agent_gets_guidance_instead_of_canvas_jargon(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._validate("chatAgent", "{}")
        detail = ctx.exception.detail
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(detail["code"], "definition_invalid")
        self.assertEqual(len(detail["problems"]), 1)
        self.assertIn("配置页", detail["problems"][0]["reason"])
        self.assertNotIn("fastgpt", detail["problems"][0]["reason"])

    async def test_workflow_keeps_original_canvas_reason(self):
        with self.assertRaises(HTTPException) as ctx:
            await self._validate("workflow", "{}")
        self.assertIn("画布模型", ctx.exception.detail["problems"][0]["reason"])
