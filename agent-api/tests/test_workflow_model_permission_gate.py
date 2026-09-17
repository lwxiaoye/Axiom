import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers.workflow import _merge_available_model_ids, _reject_missing_required_models, model_options


class WorkflowModelPermissionGateTest(unittest.TestCase):
    def test_rejects_missing_published_workflow_model_with_403(self):
        with self.assertRaises(HTTPException) as raised:
            _reject_missing_required_models(["gpt-4o", "gpt-4o-mini"], ["gpt-4o-mini"])

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("gpt-4o", str(raised.exception.detail))

    def test_allows_when_every_required_model_is_available(self):
        _reject_missing_required_models(["gpt-4o"], ["gpt-4o", "gpt-4o-mini"])

    def test_allows_embedding_model_when_embedding_is_available(self):
        available = _merge_available_model_ids(
            [type("Model", (), {"id": "gpt-4o"})()],
            ["text-embedding-v3"],
        )

        _reject_missing_required_models(["gpt-4o", "text-embedding-v3"], available)


class WorkflowModelOptionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_lookup_failure_returns_503_instead_of_empty_options(self):
        user = UserContext(user_id="u1", username="tester")

        class FailedGatewayClient:
            async def __aenter__(self):
                raise RuntimeError("upstream down")

            async def __aexit__(self, *args):
                return False

        with patch("app.services.platform.key_service.key_service.get_user_key", AsyncMock(return_value="key")):
            with patch("app.services.agents.agent_service.httpx.AsyncClient", return_value=FailedGatewayClient()):
                with self.assertRaises(HTTPException) as raised:
                    await model_options(user)

        self.assertEqual(raised.exception.status_code, 503)

    async def test_missing_user_key_still_returns_an_empty_list(self):
        user = UserContext(user_id="u1", username="tester")
        with patch("app.services.platform.key_service.key_service.get_user_key", AsyncMock(return_value=None)):
            self.assertEqual(await model_options(user), [])


if __name__ == "__main__":
    unittest.main()
