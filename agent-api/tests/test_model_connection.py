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
    """内存版 agent_platform_config：迁移检查每个用例重新来（模块级标记会缓存）。"""
    data = {}

    async def read(key, defaults):
        return dict(defaults) | data.get(key, {})

    async def save(key, value):
        data[key] = dict(value)

    async def list_by_prefix(prefix):
        return {key: dict(value) for key, value in data.items() if key.startswith(prefix)}

    monkeypatch.setattr(service, '_get_raw', read)
    monkeypatch.setattr(service, '_save_raw', save)
    monkeypatch.setattr(service, '_list_raw_by_prefix', list_by_prefix)
    monkeypatch.setattr(service, '_migration_checked', False)
    monkeypatch.setattr(settings, 'CONNECTOR_SECRET_KEY', 'model-connection-test-encryption-key')
    from app.services.connectors import crypto
    monkeypatch.setattr(crypto, '_fernet', None)
    yield data
    bind_model_connection(None)


@pytest.fixture
def no_newapi_rows(monkeypatch):
    """key_service 第三层（new_api_user_key 表）不碰真库：当作空表。"""
    from app.services.platform import key_service as module

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args, **kwargs):
            return _Result()

    monkeypatch.setattr(module, 'async_session', lambda: _Session())


def body(**overrides):
    return service.ConnectionInput(**(dict(base_url='https://model.example/v1', model='test-model', api_key='test-key') | overrides))


@pytest.mark.asyncio
async def test_platform_encrypted_persistence_and_masked_read(storage):
    result = await service.save_platform(body())
    assert result['has_api_key'] is True
    assert 'api_key' not in result and 'api_key_cipher' not in result
    assert storage[service.PLATFORM_KEY]['api_key_cipher'] != 'test-key'
    assert (await service.runtime_platform())['api_key'] == 'test-key'
    await service.save_platform(body(api_key='', model='changed'))
    assert (await service.runtime_platform())['model'] == 'changed'
    assert (await service.runtime_platform())['api_key'] == 'test-key'
    await service.save_platform(body(api_key='', enabled=False))
    assert await service.runtime_platform() is None


@pytest.mark.asyncio
async def test_user_override_is_per_user_and_separate_from_platform(storage):
    await service.save_user('u1', body(model='mine'))
    assert storage[service.user_config_key('u1')]['api_key_cipher'] != 'test-key'
    assert (await service.runtime_user('u1'))['model'] == 'mine'
    assert await service.runtime_user('u2') is None
    assert await service.runtime_platform() is None
    # 个人覆盖不落旧前缀：否则会被当成旧的管理员记录迁成平台默认
    assert service.legacy_config_key('u1') not in storage


@pytest.mark.asyncio
async def test_host_change_requires_key_reentry(storage):
    await service.save_platform(body())
    with pytest.raises(HTTPException, match='') as error:
        await service.prepare_platform(body(base_url='https://other.example/v1', api_key=''))
    assert error.value.status_code == 422
    assert (await service.runtime_platform())['base_url'] == 'https://model.example/v1'


# ---- 旧记录迁移 ----

def _legacy_row(**overrides):
    from app.services.connectors.crypto import encrypt_secret
    return dict(base_url='https://legacy.example/v1', model='grok-4.6', enabled=True,
                api_key_cipher=encrypt_secret('legacy-key')) | overrides


@pytest.mark.asyncio
async def test_legacy_admin_row_migrates_to_platform_once_and_keeps_old_row(storage):
    storage[service.legacy_config_key('1')] = _legacy_row()
    assert await service.migrate_legacy_to_platform() is True
    assert storage[service.PLATFORM_KEY]['model'] == 'grok-4.6'
    assert service.legacy_config_key('1') in storage
    # 幂等：平台级已存在就不再动它（哪怕旧行后来变了）
    storage[service.legacy_config_key('1')]['model'] = 'changed-later'
    assert await service.migrate_legacy_to_platform() is False
    assert storage[service.PLATFORM_KEY]['model'] == 'grok-4.6'
    assert (await service.runtime_platform())['api_key'] == 'legacy-key'


@pytest.mark.asyncio
async def test_migration_skips_disabled_or_keyless_legacy_rows(storage):
    storage[service.legacy_config_key('1')] = _legacy_row(enabled=False)
    storage[service.legacy_config_key('2')] = _legacy_row(api_key_cipher='')
    assert await service.migrate_legacy_to_platform() is False
    assert service.PLATFORM_KEY not in storage
    assert await service.runtime_platform() is None


@pytest.mark.asyncio
async def test_first_platform_read_triggers_migration_lazily(storage):
    storage[service.legacy_config_key('1')] = _legacy_row()
    assert (await service.runtime_platform())['model'] == 'grok-4.6'
    assert service.PLATFORM_KEY in storage


def test_pick_legacy_row_prefers_enabled_and_ignores_platform_key():
    rows = {
        service.PLATFORM_KEY: _legacy_row(model='platform'),
        service.legacy_config_key('b'): _legacy_row(model='b', enabled=False),
        service.legacy_config_key('a'): _legacy_row(model='a'),
    }
    assert service.pick_legacy_row(rows)['model'] == 'a'
    assert service.pick_legacy_row({}) is None


# ---- 统一解析顺序 ----

@pytest.mark.asyncio
async def test_resolution_order_user_then_platform_then_403(storage, no_newapi_rows):
    from app.services.platform.key_service import key_service, NO_MODEL_MESSAGE
    with pytest.raises(HTTPException) as error:
        await key_service.require_user_key('student')
    assert error.value.status_code == 403
    assert error.value.detail == NO_MODEL_MESSAGE
    assert '未分配' not in error.value.detail

    await service.save_platform(body(model='platform-model', api_key='platform-key'))
    credential = await key_service.resolve_chat_credential('student')
    assert credential['source'] == 'platform' and credential['model'] == 'platform-model'
    assert await key_service.require_user_key('student') == 'platform-key'
    assert get_model_base_url() == 'https://model.example/v1'

    await service.save_user('student', body(base_url='https://mine.example/v1', model='my-model', api_key='my-key'))
    credential = await key_service.resolve_chat_credential('student')
    assert credential['source'] == 'user' and credential['model'] == 'my-model'
    assert get_model_base_url() == 'https://mine.example/v1'
    # 其他用户不受这个人的覆盖影响
    assert (await key_service.resolve_chat_credential('other'))['source'] == 'platform'


@pytest.mark.asyncio
async def test_search_fallback_key_prefers_caller_key_then_unified_resolution(storage, no_newapi_rows):
    from app.services.platform.key_service import key_service
    assert await key_service.resolve_search_fallback_key({'deepseekApiKey': 'caller-key', '_callerUserId': 'u'}) == 'caller-key'
    assert await key_service.resolve_search_fallback_key({'deepseekApiKey': ''}) == ''
    config = {'deepseekApiKey': '', '_callerUserId': 'student'}
    assert await key_service.resolve_search_fallback_key(config) == ''
    await service.save_platform(body(api_key='platform-key'))
    assert await key_service.resolve_search_fallback_key(config) == 'platform-key'
    assert config['deepseekApiKey'] == 'platform-key'


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
async def test_platform_connection_drives_model_catalog_for_ordinary_user(storage, no_newapi_rows):
    """管理员配的平台默认对普通用户生效：/models 能列出那一个模型，LLM 打到配置的地址。"""
    from app.services.platform.key_service import key_service
    from app.services.agents.agent_service import agent_service
    await service.save_platform(body())
    key = await key_service.get_user_key('student')
    assert key == 'test-key'
    assert get_model_base_url() == 'https://model.example/v1'
    models = await agent_service.get_models(user_key=key)
    assert len(models) == 1 and models[0].id == 'test-model' and models[0].is_default
    from app.services.agent_harness.orchestrator import harness_orchestrator
    llm = harness_orchestrator._create_llm('test-model', key)
    assert llm.openai_api_base == 'https://model.example/v1'


@pytest.mark.asyncio
@pytest.mark.parametrize('username,status', [(None, 401), ('student', 403)])
async def test_platform_endpoints_require_admin(username, status):
    app = FastAPI()
    app.include_router(router)
    if username:
        app.dependency_overrides[current_user] = lambda: UserContext(user_id='u1', username=username)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        for method, url in [('GET', '/model-connection'), ('PUT', '/model-connection'), ('POST', '/model-connection/test')]:
            response = await client.request(method, url, json=body().model_dump())
            assert response.status_code == status


@pytest.mark.asyncio
async def test_personal_endpoints_open_to_any_user_and_expose_platform_summary_only(storage):
    await service.save_platform(body(model='grok-4.6', base_url='https://secret-host.example/v1'))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: UserContext(user_id='u1', username='student')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/model-connection/personal')
        assert response.status_code == 200
        payload = response.json()
        assert payload['has_api_key'] is False
        assert payload['platform'] == {'configured': True, 'model': 'grok-4.6'}
        assert 'secret-host' not in response.text and 'test-key' not in response.text
        response = await client.put('/model-connection/personal', json=body(model='mine').model_dump())
        assert response.status_code == 200 and response.json()['has_api_key'] is True
        assert (await service.runtime_user('u1'))['model'] == 'mine'
        assert (await service.runtime_platform())['model'] == 'grok-4.6'


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
