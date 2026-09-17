import pytest
from fastapi import HTTPException

from app.core.auth import UserContext
from app.routers import platform_config as router
from app.services.knowledge import web_search_service as search
from app.services.platform.key_service import key_service


@pytest.mark.asyncio
async def test_non_admin_cannot_trigger_paid_search(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail('credentials must not be read before authorization')
    monkeypatch.setattr(key_service, 'get_user_key', forbidden)
    with pytest.raises(HTTPException) as caught:
        await router.test_web_search({'searchProvider': 'deepseek-official'}, UserContext(user_id='u', username='student'))
    assert caught.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('available', [True, False])
async def test_admin_probe_uses_assigned_key_not_submitted_key(monkeypatch, available):
    calls = []
    async def key(user_id):
        assert user_id == 'admin-user'
        return 'assigned-key' if available else ''
    async def configuration():
        return {}
    async def request(query, cfg, provider):
        calls.append(provider)
        assert cfg['deepseekApiKey'] == 'assigned-key'
        assert cfg['deepseekMaxTokens'] == 1024
        return [{'url': 'https://example.com'}], ''
    monkeypatch.setattr(key_service, 'get_user_key', key)
    monkeypatch.setattr(router.cfg, 'get_web_search_config', configuration)
    monkeypatch.setattr(search, '_stage_search_single', request)
    result = await router.test_web_search(
        {'searchProvider': 'deepseek-official', 'deepseekApiKey': 'submitted-key', 'deepseekMaxTokens': 1},
        UserContext(user_id='admin-user', username='admin'),
    )
    assert result['status'] == ('success' if available else 'failed')
    assert calls == (['deepseek-official'] if available else [])
    assert 'assigned-key' not in str(result) and 'submitted-key' not in str(result)
