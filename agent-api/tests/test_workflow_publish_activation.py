"""发布审核必须重新启用此前下架的智能体。"""
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from app.core.auth import UserContext
from app.routers import workflow as wf


class _DefinitionResult:
    def __init__(self, definition):
        self.definition = definition

    def scalar_one_or_none(self):
        return self.definition


class _ReviewSession:
    def __init__(self, app, version, definition):
        self.app = app
        self.version = version
        self.definition = definition

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, _key):
        return self.version if model is wf.WorkflowVersion else self.app

    async def execute(self, _statement):
        return _DefinitionResult(self.definition)

    def add(self, _item):
        return None

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None


class _SubmitSession:
    def __init__(self, definition):
        self.definition = definition
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _DefinitionResult(self.definition)
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    def add(self, _item):
        return None

    async def commit(self):
        return None

    async def refresh(self, _item):
        return None


class WorkflowPublishActivationTests(IsolatedAsyncioTestCase):
    async def test_review_approval_reactivates_a_previously_unpublished_app(self):
        app = SimpleNamespace(id="app-1", status="unpublished", published_at=None, published_by=None)
        version = SimpleNamespace(
            id="version-1",
            app_id="app-1",
            version_no=7,
            status="pending_review",
            definition_json='{"nodes":[]}',
            submitted_by="owner-1",
            reviewed_by=None,
            reviewed_by_name="",
            reviewed_at=None,
            review_comment="",
            published_at=None,
        )
        definition = SimpleNamespace(published_json='{"nodes":[]}', published_version=6, status="published")
        session = _ReviewSession(app, version, definition)
        reviewer = UserContext(user_id="reviewer-1", username="reviewer")
        catalog_sync = mock.AsyncMock()

        with mock.patch.object(wf, "_require_review_permission", new=mock.AsyncMock()):
            with mock.patch.object(wf, "async_session", return_value=session):
                with mock.patch.object(wf, "sync_app_info_for_approved_version", new=catalog_sync):
                    with mock.patch.object(wf.capability_registry, "sync_from_app", new=mock.AsyncMock()):
                        with mock.patch.object(wf, "_version_dict", return_value={"id": "version-1"}):
                            # 上线时会再次核对外观分配（皮肤可能在审核期间被删，走库）；本用例只钉状态机
                            with mock.patch.object(wf.presentation_service, "promote_published_assignment", new=mock.AsyncMock()):
                                result = await wf.review_approve(
                                    wf.ReviewActionRequest(versionId="version-1"), reviewer
                                )

        self.assertEqual(app.status, "published")
        self.assertEqual(definition.published_version, 7)
        self.assertEqual(version.status, "approved")
        self.assertEqual(result["message"], "已通过并上线")
        # 每次审核通过都是广场发布：目录同步以 (session, app, version) 调一次，不再有渠道开关
        catalog_sync.assert_awaited_once_with(session, app, version)

    async def test_direct_publish_reactivates_a_previously_unpublished_app(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            config_json="{}",
            status="unpublished",
            published_at=None,
            published_by=None,
        )
        definition = SimpleNamespace(published_version=8)
        session = _ReviewSession(app, None, definition)
        publisher = UserContext(user_id="owner-1", username="owner")
        catalog_sync = mock.AsyncMock()

        with mock.patch.object(wf, "_validate_before_publish", new=mock.AsyncMock()):
            with mock.patch.object(wf, "_upsert_definition", new=mock.AsyncMock(return_value=definition)):
                with mock.patch.object(wf, "sync_app_info_for_approved_version", new=catalog_sync):
                    with mock.patch.object(wf.presentation_service, "sync_draft_assignment", new=mock.AsyncMock()):
                        with mock.patch.object(wf.presentation_service, "promote_published_assignment", new=mock.AsyncMock()):
                            await wf._do_publish_now(session, app, '{"nodes":[]}', None, publisher)

        self.assertEqual(app.status, "published")
        catalog_sync.assert_awaited_once()
        self.assertIs(catalog_sync.await_args.args[1], app)

    async def test_unpublished_app_stays_unpublished_when_its_resubmission_is_not_approved(self):
        app = SimpleNamespace(
            id="app-1",
            ai_app_type="workflow",
            config_json="{}",
            status="unpublished",
        )
        definition = SimpleNamespace(draft_json=None, published_json='{"nodes":[]}', published_version=4)
        session = _SubmitSession(definition)
        owner = UserContext(user_id="owner-1", username="owner")

        with mock.patch.object(wf, "_validate_before_publish", new=mock.AsyncMock()):
            with mock.patch.object(wf, "_next_version_no", new=mock.AsyncMock(return_value=5)):
                # 提交审核会顺手校验并同步草稿的外观分配（走库、读 app.tenant_id）；本用例只钉状态机
                with mock.patch.object(wf.presentation_service, "sync_draft_assignment", new=mock.AsyncMock()):
                    version = await wf._do_submit_review(session, app, '{"nodes":[]}', None, owner)

        self.assertEqual(version.status, "pending_review")
        self.assertEqual(app.status, "unpublished")
        # 驳回/撤回共用的状态回退函数在这种场景下不会把旧线上快照重新激活。
        await wf._reset_app_status_after_pending_clear(session, app)
        self.assertEqual(app.status, "unpublished")
