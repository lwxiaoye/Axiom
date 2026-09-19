"""我的智能体审核摘要与重复提交保护的回归测试。"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import workflow as wf


class _DefinitionResult:
    def __init__(self, definition):
        self.definition = definition

    def scalar_one_or_none(self):
        return self.definition


class _PendingReviewResult:
    def __init__(self, versions):
        self.versions = versions

    def scalars(self):
        return SimpleNamespace(all=lambda: self.versions)


class _SubmitWithPendingSession:
    def __init__(self, definition, pending_versions):
        self.definition = definition
        self.pending_versions = pending_versions
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _DefinitionResult(self.definition)
        return _PendingReviewResult(self.pending_versions)

    def add(self, _item):
        return None

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None


class WorkflowReviewSummaryTests(IsolatedAsyncioTestCase):
    def test_app_payload_exposes_pending_review_without_internal_comment_to_viewer(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            name="测试智能体",
            description="",
            app_category=None,
            app_icon="",
            config_json="{}",
            status="published",
            owner_user_id="owner-1",
            published_at=None,
            published_by=None,
        )
        version = SimpleNamespace(
            id="version-2",
            version_no=2,
            status="pending_review",
            submitted_at=datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc),
            reviewed_at=None,
            reviewed_by_name="",
            review_comment="仅限审核人和作者可见",
        )

        result = wf._app_dict(app, "VIEWER", review_version=version)

        self.assertEqual(
            result["reviewSummary"],
            {
                "versionId": "version-2",
                "versionNo": 2,
                "status": "pending_review",
                "submittedAt": "2026-08-29T08:00:00+00:00",
                "reviewedAt": None,
                "reviewedByName": "",
            },
        )

    def test_app_payload_exposes_rejection_reason_to_editor(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            name="测试智能体",
            description="",
            app_category=None,
            app_icon="",
            config_json="{}",
            status="unpublished",
            owner_user_id="owner-1",
            published_at=None,
            published_by=None,
        )
        version = SimpleNamespace(
            id="version-3",
            version_no=3,
            status="rejected",
            submitted_at=datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc),
            reviewed_at=datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc),
            reviewed_by_name="审核员",
            review_comment="请补充工具权限说明",
        )

        result = wf._app_dict(app, "EDITOR", review_version=version)

        self.assertEqual(result["reviewSummary"]["status"], "rejected")
        self.assertEqual(result["reviewSummary"]["reviewComment"], "请补充工具权限说明")
        self.assertEqual(result["reviewSummary"]["reviewedByName"], "审核员")

    async def test_submit_rejects_when_another_version_is_still_pending_review(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            config_json="{}",
            status="unpublished",
        )
        definition = SimpleNamespace(draft_json=None, published_json='{"nodes":[]}', published_version=4)
        pending = SimpleNamespace(id="version-5", status="pending_review")
        session = _SubmitWithPendingSession(definition, [pending])
        owner = UserContext(user_id="owner-1", username="owner")

        # 运行页外观分配（presentation_service.sync_draft_assignment）不是本用例的对象：它会先查
        # 一次 assignment 并比对 app/user 的 tenant_id，而这里的假 session 只按「定义 → 待审版本」
        # 两次 execute 编排、app 也没有 tenant_id，所以和发布前校验一样整体 mock 掉。
        with (
            mock.patch.object(wf.presentation_service, "sync_draft_assignment", new=mock.AsyncMock()),
            mock.patch.object(wf, "_validate_before_publish", new=mock.AsyncMock()),
            mock.patch.object(wf, "_next_version_no", new=mock.AsyncMock(return_value=6)),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await wf._do_submit_review(session, app, '{"nodes":[]}', None, owner)

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("待审核", ctx.exception.detail)
