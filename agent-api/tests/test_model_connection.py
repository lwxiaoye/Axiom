import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core.auth import UserContext, current_user
from app.core.config import settings
from app.core.model_endpoint import bind_model_connection, get_model_base_url
from app.routers.model_connection import router
from app.services.platform import model_connection as service


@pytest.fixture
def storage(monkeypatch):
    data = {}

    async def read(key, defaults):
        return dict(defaults) | data.get(key, {})

    async def save(key, value):
        data[key] = dict(value)

    monkeypatch.setattr(service, '_get_raw', read)
    monkeypatch.setattr(service, '_save_raw', save)
    monkeypatch.setattr(settings, 'CONNECTOR_SECRET_KEY', 'model-connection-test-encryption-key')
    from app.services.connectors import crypto
    monkeypatch.setattr(crypto, '_fernet', None)
    yield data
    bind_model_connection(None)


def body(**overrides):
    return service.ConnectionInput(**(dict(base_url='https://model.example/v1', model='test-model', api_key='test-key') | overrides))


@pytest.mark.asyncio
async def test_encrypted_persistence_and_masked_read(storage):
    result = await service.save('u1', body())
    assert result['has_api_key'] is True
    assert 'api_key' not in result and 'api_key_cipher' not in result
    assert storage[service.config_key('u1')]['api_key_cipher'] != 'test-key'
    assert (await service.runtime('u1'))['api_key'] == 'test-key'
    assert await service.runtime('u2') is None
    await service.save('u1', body(api_key='', model='changed'))
    assert (await service.runtime('u1'))['model'] == 'changed'
    assert (await service.runtime('u1'))['api_key'] == 'test-key'
    await service.save('u1', body(api_key='', enabled=False))
    assert await service.runtime('u1') is None


@pytest.mark.asyncio
async def test_host_change_requires_key_reentry(storage):
    await service.save('u1', body())
    with pytest.raises(HTTPException, match='') as error:
        await service.prepare('u1', body(base_url='https://other.example/v1', api_key=''))
    assert error.value.status_code == 422
    assert (await service.runtime('u1'))['base_url'] == 'https://model.example/v1'


@pytest.mark.parametrize('url', ['file:///tmp/a', 'https://user:pass@example.com/v1', 'https://example.com/v1?key=a', 'https://example.com/#fragment', 'not-a-url'])
def test_invalid_addresses_rejected(url):
    with pytest.raises(HTTPException):
        service.normalize(body(base_url=url))


def test_completion_url_normalized():
    assert service.normalize(body(base_url='https://model.example/v1/chat/completions/'))['base_url'] == 'https://model.example/v1'


@pytest.mark.asyncio
async def test_probe_has_total_deadline(storage, monkeypatch):
    from app.routers import model_connection as routes
    real_client = httpx.AsyncClient
    cancelled = asyncio.Event()

    async def slow(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    monkeypatch.setattr(routes, 'TEST_TIMEOUT_SECONDS', 0.02)
    monkeypatch.setattr(routes.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(slow)))
    result = await routes.test_connection(body(), UserContext(user_id='u1', username='admin'))
    assert result['success'] is False
    assert '超时' in result['message']
    assert cancelled.is_set()
    assert not storage


@pytest.mark.asyncio
async def test_task_connections_do_not_bleed():
    async def one(url):
        bind_model_connection({'base_url': url})
        await asyncio.sleep(0)
        return get_model_base_url()
    assert await asyncio.gather(one('https://a.example/v1'), one('https://b.example/v1')) == ['https://a.example/v1', 'https://b.example/v1']


@pytest.mark.asyncio
async def test_saved_connection_drives_model_catalog_and_key(storage):
    from app.services.platform.key_service import key_service
    from app.services.agents.agent_service import agent_service
    await service.save('u1', body())
    key = await key_service.get_user_key('u1')
    assert key == 'test-key'
    assert get_model_base_url() == 'https://model.example/v1'
    models = await agent_service.get_models(user_key=key)
    assert len(models) == 1 and models[0].id == 'test-model' and models[0].is_default
    from app.services.agent_harness.orchestrator import harness_orchestrator
    llm = harness_orchestrator._create_llm('test-model', key)
    assert llm.openai_api_base == 'https://model.example/v1'


@pytest.mark.asyncio
@pytest.mark.parametrize('username,status', [(None, 401), ('student', 403)])
async def test_endpoints_require_admin(username, status):
    app = FastAPI()
    app.include_router(router)
    if username:
        app.dependency_overrides[current_user] = lambda: UserContext(user_id='u1', username=username)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        for method, url in [('GET', '/model-connection'), ('PUT', '/model-connection'), ('POST', '/model-connection/test')]:
            response = await client.request(method, url, json=body().model_dump())
            assert response.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize('status,payload,success', [(200, {'choices': [{'message': {'content': 'OK'}}]}, True), (401, {'secret': 'test-key'}, False), (200, {'error': 'test-key'}, False)])
async def test_probe_uses_draft_without_saving_or_leaking(storage, monkeypatch, status, payload, success):
    from app.routers import model_connection as routes
    seen = {}
    real_client = httpx.AsyncClient

    def handle(request):
        seen['request'] = request
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(routes.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle)))
    result = await routes.test_connection(body(), UserContext(user_id='u1', username='admin'))
    assert result['success'] is success
    assert 'test-key' not in str(result)
    assert seen['request'].url == 'https://model.example/v1/chat/completions'
    assert seen['request'].headers['Authorization'] == 'Bearer test-key'
    assert not storage
