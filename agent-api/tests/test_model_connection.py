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
        # 与真实 _get_raw 同款：只保留 defaults 里有的键。之前这里是 defaults | data，
        # 把旧单连接行的 base_url/model 也透传出去，测试因此没抓到「名册读成空」的上线事故。
        stored = data.get(key, {})
        return dict(defaults) | {k: v for k, v in stored.items() if k in defaults}

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


def entry(**overrides):
    payload = dict(
        name='demo',
        base_url='https://model.example/v1',
        model='test-model',
        api_key='test-key',
    )
    payload.update(overrides)
    return service.EntryInput(**payload)


def roster(*entries, **overrides):
    items = list(entries) if entries else [entry()]
    payload = dict(enabled=True, default_id='', entries=items)
    payload.update(overrides)
    return service.RosterInput(**payload)


def personal(**overrides):
    payload = dict(base_url='https://model.example/v1', model='test-model', api_key='test-key')
    payload.update(overrides)
    return service.ConnectionInput(**payload)


@pytest.mark.asyncio
async def test_platform_encrypted_persistence_and_masked_read(storage):
    result = await service.save_platform(roster())
    assert result['entries'][0]['has_api_key'] is True
    assert 'api_key' not in result['entries'][0] and 'api_key_cipher' not in result['entries'][0]
    stored = storage[service.PLATFORM_KEY]['entries'][0]
    assert stored['api_key_cipher'] != 'test-key'
    runtime = await service.runtime_platform()
    assert runtime['api_key'] == 'test-key'
    assert runtime['model'] == 'test-model'
    entry_id = result['entries'][0]['id']
    await service.save_platform(roster(entry(id=entry_id, model='changed', api_key='')))
    assert (await service.runtime_platform())['model'] == 'changed'
    assert (await service.runtime_platform())['api_key'] == 'test-key'
    await service.save_platform(roster(enabled=False, entries=[entry(id=entry_id, api_key='')]))
    assert await service.runtime_platform() is None


@pytest.mark.asyncio
async def test_user_override_is_per_user_and_separate_from_platform(storage):
    await service.save_user('u1', personal(model='mine'))
    assert storage[service.user_config_key('u1')]['api_key_cipher'] != 'test-key'
    assert (await service.runtime_user('u1'))['model'] == 'mine'
    assert await service.runtime_user('u2') is None
    assert await service.runtime_platform() is None
    assert service.legacy_config_key('u1') not in storage


@pytest.mark.asyncio
async def test_host_change_requires_key_reentry(storage):
    saved = await service.save_platform(roster())
    entry_id = saved['entries'][0]['id']
    with pytest.raises(HTTPException) as error:
        await service.prepare_entry(entry(id=entry_id, base_url='https://other.example/v1', api_key=''))
    assert error.value.status_code == 422
    assert (await service.runtime_platform())['base_url'] == 'https://model.example/v1'


@pytest.mark.asyncio
async def test_legacy_admin_row_migrates_to_platform_once_and_keeps_old_row(storage):
    from app.services.connectors.crypto import encrypt_secret
    storage[service.legacy_config_key('1')] = {
        'base_url': 'https://legacy.example/v1',
        'model': 'grok-4.6',
        'enabled': True,
        'api_key_cipher': encrypt_secret('legacy-key'),
    }
    assert await service.migrate_legacy_to_platform() is True
    roster_data = service.coerce_roster(storage[service.PLATFORM_KEY])
    assert roster_data['entries'][0]['model'] == 'grok-4.6'
    assert service.legacy_config_key('1') in storage
    storage[service.legacy_config_key('1')]['model'] = 'changed-later'
    assert await service.migrate_legacy_to_platform() is False
    assert (await service.runtime_platform())['api_key'] == 'legacy-key'
    assert (await service.runtime_platform())['model'] == 'grok-4.6'


@pytest.mark.asyncio
async def test_legacy_single_record_without_entries_key_becomes_one_card(storage):
    from app.services.connectors.crypto import encrypt_secret
    storage[service.PLATFORM_KEY] = {
        'base_url': 'https://wushaoran.me/v1',
        'model': 'grok-4.6',
        'enabled': True,
        'api_key_cipher': encrypt_secret('legacy-key'),
    }
    public = service.public_roster(await service.read_platform())
    assert len(public['entries']) == 1
    assert public['entries'][0]['model'] == 'grok-4.6'
    assert public['entries'][0]['base_url'] == 'https://wushaoran.me/v1'
    runtime = await service.runtime_platform()
    assert runtime['model'] == 'grok-4.6'
    assert runtime['roster'][0]['model'] == 'grok-4.6'


@pytest.mark.asyncio
async def test_two_independent_entries_keep_their_own_keys_and_urls(storage, no_newapi_rows):
    saved = await service.save_platform(roster(
        entry(name='Grok', base_url='https://ctsafe.top/v1', model='grok-4.6', api_key='grok-key'),
        entry(name='GPT', base_url='https://fastai.example/v1', anthropic_base_url='https://fastai.example/anthropic', model='gpt-5.6-sol', api_key='gpt-key'),
        default_id='',
    ))
    assert [item['model'] for item in saved['entries']] == ['grok-4.6', 'gpt-5.6-sol']
    assert saved['entries'][0]['base_url'] == 'https://ctsafe.top/v1'
    assert saved['entries'][1]['anthropic_base_url'] == 'https://fastai.example/anthropic'
    from app.services.platform.key_service import key_service
    from app.services.agents.agent_service import agent_service
    key = await key_service.get_user_key('student')
    catalog = await agent_service.get_models(user_key=key)
    assert [item.id for item in catalog] == ['grok-4.6', 'gpt-5.6-sol']
    assert catalog[0].name == 'Grok'
    service.bind_selected('gpt-5.6-sol')
    assert get_model_base_url() == 'https://fastai.example/v1'
    from app.core.model_endpoint import get_model_connection
    assert get_model_connection()['api_key'] == 'gpt-key'


@pytest.mark.asyncio
async def test_duplicate_model_ids_rejected(storage):
    with pytest.raises(HTTPException) as error:
        await service.save_platform(roster(
            entry(model='same', api_key='a', base_url='https://a.example/v1'),
            entry(model='same', api_key='b', base_url='https://b.example/v1'),
        ))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_resolution_order_user_then_platform_then_403(storage, no_newapi_rows):
    from app.services.platform.key_service import key_service, NO_MODEL_MESSAGE
    with pytest.raises(HTTPException) as error:
        await key_service.require_user_key('student')
    assert error.value.status_code == 403
    assert error.value.detail == NO_MODEL_MESSAGE

    await service.save_platform(roster(entry(model='platform-model', api_key='platform-key')))
    credential = await key_service.resolve_chat_credential('student')
    assert credential['source'] == 'platform' and credential['model'] == 'platform-model'
    assert await key_service.require_user_key('student') == 'platform-key'
    assert get_model_base_url() == 'https://model.example/v1'

    await service.save_user('student', personal(base_url='https://mine.example/v1', model='my-model', api_key='my-key'))
    credential = await key_service.resolve_chat_credential('student')
    assert credential['source'] == 'user' and credential['model'] == 'my-model'
    assert get_model_base_url() == 'https://mine.example/v1'
    assert (await key_service.resolve_chat_credential('other'))['source'] == 'platform'


@pytest.mark.parametrize('url', ['file:///tmp/a', 'https://user:pass@example.com/v1', 'https://example.com/v1?key=a', 'https://example.com/#fragment', 'not-a-url'])
def test_invalid_addresses_rejected(url):
    with pytest.raises(HTTPException):
        service.normalize_url(url, required=True)


def test_completion_url_normalized():
    assert service.normalize_url('https://model.example/v1/chat/completions/', required=True) == 'https://model.example/v1'


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
    result = await routes.test_connection(entry(), UserContext(user_id='u1', username='admin'))
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
    from app.services.platform.key_service import key_service
    from app.services.agents.agent_service import agent_service
    await service.save_platform(roster())
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
        payloads = {
            'GET': None,
            'PUT': roster().model_dump(),
            'POST': entry().model_dump(),
        }
        for method, url in [('GET', '/model-connection'), ('PUT', '/model-connection'), ('POST', '/model-connection/test')]:
            response = await client.request(method, url, json=payloads[method])
            assert response.status_code == status


@pytest.mark.asyncio
async def test_personal_endpoints_open_to_any_user_and_expose_platform_summary_only(storage):
    await service.save_platform(roster(entry(name='Grok', model='grok-4.6', base_url='https://secret-host.example/v1')))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: UserContext(user_id='u1', username='student')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.get('/model-connection/personal')
        assert response.status_code == 200
        payload = response.json()
        assert payload['has_api_key'] is False
        assert payload['platform']['configured'] is True
        assert payload['platform']['model'] == 'grok-4.6'
        assert payload['platform']['models'] == ['grok-4.6']
        assert 'secret-host' not in response.text and 'test-key' not in response.text
        response = await client.put('/model-connection/personal', json=personal(model='mine').model_dump())
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
    result = await routes.test_connection(entry(), UserContext(user_id='u1', username='admin'))
    assert result['success'] is success
    assert 'test-key' not in str(result)
    assert seen['request'].url == 'https://model.example/v1/chat/completions'
    assert seen['request'].headers['Authorization'] == 'Bearer test-key'
    assert not storage


@pytest.mark.asyncio
async def test_legacy_shaped_platform_row_is_rewritten_as_roster_at_startup(storage):
    """存量平台行还是单连接形状：迁移要原地收成名册并写回（2026-09-19 上线当天全站
    「平台尚未配置对话模型」的根因——_get_raw 按 ROSTER_DEFAULTS 裁键后 entries 为空）。"""
    from app.services.connectors.crypto import encrypt_secret
    storage[service.PLATFORM_KEY] = {
        'base_url': 'https://wushaoran.me/v1',
        'model': 'grok-4.6',
        'enabled': True,
        'api_key_cipher': encrypt_secret('legacy-key'),
    }
    assert await service.migrate_legacy_to_platform() is True
    stored = storage[service.PLATFORM_KEY]
    assert [item['model'] for item in stored['entries']] == ['grok-4.6']
    assert stored['default_id'] == stored['entries'][0]['id']
    # 已是名册形状：不再重写
    service._migration_checked = False
    assert await service.migrate_legacy_to_platform() is False


@pytest.mark.asyncio
async def test_prepare_chat_returns_the_selected_entry_key(storage, no_newapi_rows):
    """选中名册里非默认模型时，密钥要跟着切到那条记录——否则拿默认模型的密钥请求所选
    模型的地址（管理页测速成功、对话却 401，2026-09-19 正式站冒烟抓到）。"""
    from app.services.agent_harness.orchestrator import harness_orchestrator
    await service.save_platform(roster(
        entry(name='Grok', base_url='https://ctsafe.top/v1', model='grok-4.6', api_key='grok-key'),
        entry(name='DeepSeek', base_url='https://api.deepseek.com', model='deepseek-flash', api_key='deepseek-key'),
        default_id='',
    ))
    key, model = await harness_orchestrator.prepare_chat('student', 'deepseek-flash')
    assert (key, model) == ('deepseek-key', 'deepseek-flash')
    assert get_model_base_url() == 'https://api.deepseek.com'
    key, model = await harness_orchestrator.prepare_chat('student', None)
    assert (key, model) == ('grok-key', 'grok-4.6')
    assert get_model_base_url() == 'https://ctsafe.top/v1'
