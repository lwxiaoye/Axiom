"""已发布智能体更新发布的草稿差异与重复提交保护。"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import workflow as wf


def _app(*, status: str = "published"):
    return SimpleNamespace(
        id="app-1",
        ai_app_type="workflow",
        name="测试智能体",
        description="",
        app_category=None,
        app_icon="",
        config_json="{}",
        status=status,
        owner_user_id="owner-1",
        published_at=None,
        published_by=None,
    )


class WorkflowReleaseUpdateTests(TestCase):
    def test_published_app_payload_marks_a_different_draft_as_unpublished_change(self):
        definition = SimpleNamespace(
            draft_json='{"nodes":[{"id":"draft"}]}',
            published_json='{"nodes":[{"id":"live"}]}',
        )

        payload = wf._app_dict(_app(), definition=definition)

        self.assertTrue(payload["hasUnpublishedChanges"])

    def test_published_app_ignores_json_key_order_when_comparing_draft_to_live(self):
        definition = SimpleNamespace(
            draft_json='{"nodes":[],"settings":{"title":"same"}}',
            published_json='{"settings":{"title":"same"},"nodes":[]}',
        )

        payload = wf._app_dict(_app(), definition=definition)

        self.assertFalse(payload["hasUnpublishedChanges"])

    def test_published_legacy_app_without_a_draft_is_not_marked_as_changed(self):
        definition = SimpleNamespace(draft_json=None, published_json='{"nodes":[]}')

        payload = wf._app_dict(_app(), definition=definition)

        self.assertFalse(payload["hasUnpublishedChanges"])

    def test_publishing_an_unchanged_online_definition_is_rejected(self):
        definition = SimpleNamespace(published_json='{"nodes":[]}', draft_json='{"nodes":[]}')

        with self.assertRaises(HTTPException) as raised:
            wf._reject_unchanged_published_definition(_app(), definition, '{"nodes":[]}')

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("\u6ca1\u6709\u53ef\u53d1\u5e03", raised.exception.detail)


class _AsyncSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self):
        return None


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RollbackSession:
    def __init__(self, target, definition):
        self.target = target
        self.definition = definition
        self.added = []
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarResult(None)
        return _ScalarResult(self.target if self.execute_count == 2 else self.definition)

    def add(self, item):
        self.added.append(item)


class _PendingRollbackSession:
    async def execute(self, _statement):
        return _ScalarResult(SimpleNamespace(id="pending-v7", status="pending_review"))


class _PendingVersionListResult:
    def __init__(self, versions):
        self.versions = versions

    def scalars(self):
        return self

    def all(self):
        return self.versions


class _UnpublishSession:
    def __init__(self, versions):
        self.versions = versions

    async def execute(self, _statement):
        return _PendingVersionListResult(self.versions)


class WorkflowOwnerRollbackTests(IsolatedAsyncioTestCase):
    async def test_owner_rollback_creates_a_new_live_version_and_syncs_runtime(self):
        app = SimpleNamespace(id="app-1")
        owner = UserContext(user_id="owner-1", username="owner")
        session = _AsyncSession()
        rollback = mock.AsyncMock(return_value=(8, False))  # (新线上版本号, 是否需失效 API 密钥)
        sync_runtime = mock.AsyncMock()

        with mock.patch.object(wf, "async_session", return_value=session):
            with mock.patch.object(wf, "_require_permission", new=mock.AsyncMock(return_value=(app, "OWNER"))):
                with mock.patch.object(wf, "_rollback_to_version", new=rollback):
                    with mock.patch.object(wf.capability_registry, "sync_from_app", new=sync_runtime):
                        result = await wf.rollback_version(wf.RollbackRequest(appId="app-1", versionNo=2), owner)

        self.assertEqual(result, {"message": "已回滚到 v2（新线上版本 v8）", "liveVersion": 8})
        self.assertEqual(rollback.await_args.args[1:], (app, 2, owner))
        sync_runtime.assert_awaited_once_with("app-1")

    async def test_rollback_restores_the_historical_runtime_config_with_the_definition(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            config_json='{"model":"new"}',
            status="published",
            published_at=None,
            published_by=None,
        )
        target = SimpleNamespace(
            version_no=2,
            status="approved",
            definition_json='{"nodes":[{"id":"old"}]}',
            config_json='{"model":"old"}',
            visible_role_ids="[]",
            visible_dept_ids="[]",
            routing_json=None,
        )
        definition = SimpleNamespace(published_json='{"nodes":[{"id":"new"}]}', published_version=5, status="published")
        session = _RollbackSession(target, definition)
        owner = UserContext(user_id="owner-1", username="owner")

        with mock.patch.object(wf, "_next_version_no", new=mock.AsyncMock(return_value=6)):
            with mock.patch.object(wf, "sync_app_info_for_approved_version", new=mock.AsyncMock()):
                new_version, should_invalidate_api = await wf._rollback_to_version(
                    session,
                    app,
                    2,
                    owner,
                    review_comment="所有者回滚",
                )

        self.assertEqual(new_version, 6)
        self.assertFalse(should_invalidate_api)
        self.assertEqual(app.config_json, '{"model":"old"}')
        self.assertEqual(definition.published_json, '{"nodes":[{"id":"old"}]}')
        self.assertEqual(session.added[0].version_no, 6)

    async def test_rollback_rejects_when_an_update_is_still_pending_review(self):
        app = SimpleNamespace(id="app-1", ai_app_type="workflow")
        owner = UserContext(user_id="owner-1", username="owner")

        with self.assertRaises(HTTPException) as raised:
            await wf._rollback_to_version(
                _PendingRollbackSession(),
                app,
                2,
                owner,
                review_comment="所有者回滚",
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("\u5ba1\u6838", raised.exception.detail)


class WorkflowUnpublishTests(IsolatedAsyncioTestCase):
    async def test_unpublish_cancels_pending_updates_so_they_cannot_republish_later(self):
        pending = SimpleNamespace(status="pending_review", review_comment="")

        cancelled = await wf._cancel_pending_versions_for_unpublish(_UnpublishSession([pending]), "app-1")

        self.assertEqual(cancelled, 1)
        self.assertEqual(pending.status, "cancelled")
        self.assertIn("\u4e0b\u67b6", pending.review_comment)
