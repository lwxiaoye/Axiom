import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.core.auth import UserContext, current_user
from app.core.config import settings
from app.routers.rerank_config import router
from app.services.knowledge import rerank_service as service

DASHSCOPE_BASE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
DASHSCOPE_NATIVE = 'https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank'
DOCS = ['社团注册流程', '食堂供餐时间', '图书馆开放时间：周一至周五 8:00-22:00']


@pytest.fixture
def storage(monkeypatch):
    data = {}

    async def read(key, defaults):
        return dict(defaults) | data.get(key, {})

    async def save(key, value):
        data[key] = dict(value)

    monkeypatch.setattr(service, '_get_raw', read)
    monkeypatch.setattr(service, '_save_raw', save)
    monkeypatch.setattr(settings, 'CONNECTOR_SECRET_KEY', 'rerank-test-encryption-key')
    from app.services.connectors import crypto
    monkeypatch.setattr(crypto, '_fernet', None)
    monkeypatch.setattr(service, '_native_only', set())
    yield data


@pytest.fixture
def transport(monkeypatch):
    """用 MockTransport 替换 httpx.AsyncClient，记录每次请求，按 handler 返回。"""
    calls = []
    state = {'handler': None}
    real_client = httpx.AsyncClient

    def handle(request):
        calls.append(request)
        return state['handler'](request)

    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handle)))
    state['calls'] = calls
    return state


def body(**overrides):
    return service.RerankInput(**(dict(base_url='https://rerank.example/v1', model='test-rerank', api_key='test-key') | overrides))


def config(**overrides):
    return service.RerankConfig(**(dict(base_url='https://rerank.example/v1', model='test-rerank', api_key='test-key', enabled=True) | overrides))


# ---- 存储 ----

@pytest.mark.asyncio
async def test_encrypted_persistence_and_masked_read(storage):
    result = await service.save(body())
    assert result == {'base_url': 'https://rerank.example/v1', 'model': 'test-rerank', 'enabled': True, 'has_api_key': True}
    assert storage[service.CONFIG_KEY]['api_key_cipher'] != 'test-key'
    active = await service.get_active_rerank_config()
    assert active.api_key == 'test-key' and active.model == 'test-rerank'
    await service.save(body(api_key='', model='changed'))
    assert (await service.get_active_rerank_config()).model == 'changed'
    await service.save(body(api_key='', enabled=False))
    assert await service.get_active_rerank_config() is None


@pytest.mark.asyncio
async def test_unconfigured_is_none_and_rerank_raises_unavailable(storage):
    assert await service.get_active_rerank_config() is None
    with pytest.raises(service.RerankUnavailable):
        await service.rerank('q', DOCS)


@pytest.mark.asyncio
async def test_host_change_requires_key_reentry(storage):
    await service.save(body())
    with pytest.raises(HTTPException) as error:
        await service.prepare(body(base_url='https://other.example/v1', api_key=''))
    assert error.value.status_code == 422
    assert (await service.get_active_rerank_config()).base_url == 'https://rerank.example/v1'


@pytest.mark.parametrize('url', ['file:///tmp/a', 'https://user:pass@example.com/v1', 'https://example.com/v1?key=a', 'not-a-url'])
def test_invalid_addresses_rejected(url):
    with pytest.raises(HTTPException):
        service.normalize(body(base_url=url))


def test_rerank_suffix_normalized_but_native_path_kept():
    assert service.normalize(body(base_url='https://rerank.example/v1/rerank/'))['base_url'] == 'https://rerank.example/v1'
    assert service.normalize(body(base_url=DASHSCOPE_NATIVE))['base_url'] == DASHSCOPE_NATIVE


# ---- 调用 ----

@pytest.mark.asyncio
async def test_generic_rerank_endpoint(storage, transport):
    def handler(request):
        assert request.url == 'https://rerank.example/v1/rerank'
        assert request.headers['Authorization'] == 'Bearer test-key'
        return httpx.Response(200, json={'results': [
            {'index': 0, 'relevance_score': 0.1}, {'index': 2, 'relevance_score': 0.9}, {'index': 1, 'relevance_score': 0.5},
        ]})
    transport['handler'] = handler
    assert await service.rerank('图书馆几点关门', DOCS, config=config()) == [(2, 0.9), (1, 0.5), (0, 0.1)]
    assert await service.rerank('图书馆几点关门', DOCS, top_n=1, config=config()) == [(2, 0.9)]


@pytest.mark.asyncio
async def test_dashscope_falls_back_to_native_and_remembers(storage, transport):
    def handler(request):
        if request.url == DASHSCOPE_BASE + '/rerank':
            return httpx.Response(404)
        assert request.url == DASHSCOPE_NATIVE
        payload = request.read()
        assert b'"input"' in payload and b'"parameters"' in payload
        return httpx.Response(200, json={'output': {'results': [
            {'index': 2, 'relevance_score': 0.98}, {'index': 1, 'relevance_score': 0.23}, {'index': 0, 'relevance_score': 0.02},
        ]}})
    transport['handler'] = handler
    cfg = config(base_url=DASHSCOPE_BASE)
    assert await service.rerank('图书馆几点关门', DOCS, config=cfg) == [(2, 0.98), (1, 0.23), (0, 0.02)]
    assert [str(c.url) for c in transport['calls']] == [DASHSCOPE_BASE + '/rerank', DASHSCOPE_NATIVE]
    # 第二次不再白打 404
    await service.rerank('图书馆几点关门', DOCS, config=cfg)
    assert [str(c.url) for c in transport['calls']][2:] == [DASHSCOPE_NATIVE]


@pytest.mark.asyncio
async def test_non_dashscope_404_is_an_error_not_silence(storage, transport):
    transport['handler'] = lambda request: httpx.Response(404, json={'error': 'no such route'})
    with pytest.raises(service.RerankError, match='HTTP 404'):
        await service.rerank('q', DOCS, config=config())


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'results': [{'index': 5, 'relevance_score': 0.9}]},          # 越界
    {'results': [{'index': 0}]},                                    # 缺分数
    {'results': [{'index': '0', 'relevance_score': 0.9}]},        # 类型错
    {'foo': []},                                                    # 没有 results
])
async def test_malformed_response_raises(storage, transport, payload):
    transport['handler'] = lambda request: httpx.Response(200, json=payload)
    with pytest.raises(service.RerankError):
        await service.rerank('q', DOCS, config=config())


@pytest.mark.asyncio
async def test_http_error_message_has_hint_but_no_key(storage, transport):
    transport['handler'] = lambda request: httpx.Response(401, json={'code': 'InvalidApiKey', 'message': 'Invalid API-key provided.'})
    with pytest.raises(service.RerankError) as error:
        await service.rerank('q', DOCS, config=config())
    assert 'API Key 无效' in str(error.value) and 'InvalidApiKey' in str(error.value)
    assert 'test-key' not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_is_total_budget(storage, transport):
    async def slow(request):
        await asyncio.sleep(10)
    transport['handler'] = slow
    with pytest.raises(service.RerankError, match='超时'):
        await service.rerank('q', DOCS, config=config(), timeout=0.05)


@pytest.mark.asyncio
async def test_empty_documents_skip_request(storage, transport):
    transport['handler'] = lambda request: pytest.fail('不该发请求')
    assert await service.rerank('q', [], config=config()) == []


@pytest.mark.asyncio
async def test_probe_reports_result_shape(storage, transport):
    transport['handler'] = lambda request: httpx.Response(200, json={'results': [
        {'index': 0, 'relevance_score': 0.9}, {'index': 1, 'relevance_score': 0.1},
    ]})
    result = await service.test_rerank(config())
    assert result['success'] is True and isinstance(result['latency_ms'], int)
    transport['handler'] = lambda request: httpx.Response(200, json={'results': [
        {'index': 1, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.1},
    ]})
    assert (await service.test_rerank(config()))['success'] is False
    transport['handler'] = lambda request: httpx.Response(429, text='quota')
    result = await service.test_rerank(config())
    assert result['success'] is False and '限流' in result['message']


# ---- 用量记账 ----

@pytest.fixture
def audit_log(monkeypatch):
    """替换 model_usage_audit 为记录器，并挂一个工具上下文让记账有 run_id 可归属。"""
    from app.services.agent_harness import model_usage_audit as real
    from app.services.chat.tools import base as tools_base

    events = []

    async def begin_logical_call(**kwargs):
        events.append(('logical', kwargs))
        return SimpleNamespace(logical_call_id='lg1')

    async def begin_attempt(logical, **kwargs):
        events.append(('attempt', kwargs))
        return SimpleNamespace(attempt_id=f"at{sum(1 for e in events if e[0] == 'attempt')}")

    async def finish_attempt(attempt, **kwargs):
        events.append(('finish_attempt', dict(kwargs, attempt_id=getattr(attempt, 'attempt_id', None))))

    async def finish_logical_call(logical, **kwargs):
        events.append(('finish_logical', kwargs))

    monkeypatch.setattr(real, 'begin_logical_call', begin_logical_call)
    monkeypatch.setattr(real, 'begin_attempt', begin_attempt)
    monkeypatch.setattr(real, 'finish_attempt', finish_attempt)
    monkeypatch.setattr(real, 'finish_logical_call', finish_logical_call)
    token = tools_base.CURRENT_TOOL_CONTEXT.set(tools_base.ToolExecutionContext(call_id='tc1', run_id='run1', thread_id='th1'))
    yield events
    tools_base.CURRENT_TOOL_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_audit_records_usage_with_purpose_detail(storage, transport, audit_log):
    transport['handler'] = lambda request: httpx.Response(200, json={
        'results': [{'index': 0, 'relevance_score': 0.9}], 'usage': {'total_tokens': 42}, 'id': 'resp-1',
    })
    await service.rerank('q', DOCS, config=config(), audit_purpose_detail='knowledge_rerank')
    logical = audit_log[0][1]
    assert logical['run_id'] == 'run1' and logical['parent_tool_call_id'] == 'tc1'
    assert logical['transport'] == 'rerank_api' and logical['purpose'] == 'tool_internal'
    assert logical['purpose_detail'] == 'knowledge_rerank' and logical['model'] == 'test-rerank'
    assert 'test-key' not in str(audit_log[1])
    finish = next(e[1] for e in audit_log if e[0] == 'finish_attempt')
    assert finish['terminal_status'] == 'completed' and finish['usage'] == {'total_tokens': 42}
    assert finish['response_id'] == 'resp-1' and finish['http_status'] == 200 and finish['committed'] is True
    assert audit_log[-1] == ('finish_logical', {'terminal_status': 'completed', 'selected_attempt_id': 'at1', 'committed': True})


@pytest.mark.asyncio
async def test_audit_default_purpose_and_fallback_attempts(storage, transport, audit_log):
    def handler(request):
        if request.url == DASHSCOPE_BASE + '/rerank':
            return httpx.Response(404)
        return httpx.Response(200, json={'output': {'results': [{'index': 2, 'relevance_score': 0.9}]},
                                         'usage': {'total_tokens': 7}, 'request_id': 'ds-req'})
    transport['handler'] = handler
    await service.rerank('q', DOCS, config=config(base_url=DASHSCOPE_BASE))
    assert audit_log[0][1]['purpose_detail'] == 'rerank_provider:platform'
    kinds = [e[1]['attempt_kind'] for e in audit_log if e[0] == 'attempt']
    assert kinds == ['initial', 'endpoint_fallback']
    finishes = [e[1] for e in audit_log if e[0] == 'finish_attempt']
    assert finishes[0]['http_status'] == 404 and finishes[0]['committed'] is False
    assert finishes[1]['response_id'] == 'ds-req' and finishes[1]['committed'] is True


@pytest.mark.asyncio
async def test_audit_marks_incomplete_when_paid_but_unusable(storage, transport, audit_log):
    transport['handler'] = lambda request: httpx.Response(200, json={'results': [{'index': 9, 'relevance_score': 1}], 'usage': {'total_tokens': 3}})
    with pytest.raises(service.RerankError):
        await service.rerank('q', DOCS, config=config())
    finish = next(e[1] for e in audit_log if e[0] == 'finish_attempt')
    assert finish['terminal_status'] == 'incomplete' and finish['committed'] is False and finish['usage'] == {'total_tokens': 3}
    assert audit_log[-1][1]['terminal_status'] == 'incomplete'


@pytest.mark.asyncio
async def test_audit_records_transport_failure(storage, transport, audit_log):
    def handler(request):
        raise httpx.ConnectError('boom')
    transport['handler'] = handler
    with pytest.raises(service.RerankError):
        await service.rerank('q', DOCS, config=config())
    finish = next(e[1] for e in audit_log if e[0] == 'finish_attempt')
    assert finish['terminal_status'] == 'failed' and finish['error_code'] == 'ConnectError'
    assert audit_log[-1][1] == {'terminal_status': 'failed', 'committed': False}


# ---- 路由 ----

@pytest.mark.asyncio
@pytest.mark.parametrize('username,status', [(None, 401), ('student', 403)])
async def test_endpoints_require_admin(username, status):
    app = FastAPI()
    app.include_router(router)
    if username:
        app.dependency_overrides[current_user] = lambda: UserContext(user_id='u1', username=username)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        for method, url in [('GET', '/rerank-config'), ('PUT', '/rerank-config'), ('POST', '/rerank-config/test')]:
            response = await client.request(method, url, json=body().model_dump())
            assert response.status_code == status


@pytest.mark.asyncio
async def test_probe_uses_draft_without_saving_and_ignores_switch(storage, transport):
    from app.routers import rerank_config as routes
    transport['handler'] = lambda request: httpx.Response(200, json={'results': [
        {'index': 0, 'relevance_score': 0.9}, {'index': 1, 'relevance_score': 0.1},
    ]})
    result = await routes.test_config(body(enabled=False), UserContext(user_id='u1', username='admin'))
    assert result['success'] is True
    assert not storage


# ---- 知识库检索接入 ----

@pytest.fixture
def kb(monkeypatch):
    from app.services.knowledge import knowledge_base_service as kb_service

    class FakeClient:
        def __init__(self):
            self.limits = []

        async def collection_exists(self, name):
            return True

        async def search(self, *, limit, **kwargs):
            self.limits.append(limit)
            return [SimpleNamespace(score=1 - i * 0.01, payload={'content': f'doc{i}', 'document_name': f'd{i}',
                                    'knowledge_id': 'k', 'document_id': f'id{i}'}) for i in range(min(limit, 30))]

    fake = FakeClient()
    monkeypatch.setattr(kb_service, '_get_client', lambda: fake)

    async def active_embedding():
        return SimpleNamespace(model='emb', api_key='k', base_url='https://emb.example/v1')

    async def embed_query(text, **kwargs):
        return [0.1, 0.2]

    monkeypatch.setattr(kb_service, '_active_embedding', active_embedding)
    monkeypatch.setattr(kb_service.embedding_service, 'embed_query', embed_query)
    return kb_service, fake


@pytest.mark.asyncio
async def test_search_chunks_reranks_wide_candidates(kb, monkeypatch):
    kb_service, fake = kb
    seen = {}

    async def active():
        return config()

    async def fake_rerank(query, documents, *, top_n=None, config=None, timeout=20.0, audit_purpose_detail=None):
        seen['count'] = len(documents)
        seen['purpose'] = audit_purpose_detail
        return [(7, 0.95), (0, 0.4), (3, 0.2)]

    monkeypatch.setattr(kb_service.rerank_service, 'get_active_rerank_config', active)
    monkeypatch.setattr(kb_service.rerank_service, 'rerank', fake_rerank)
    hits = await kb_service.search_chunks(knowledge_ids=['k'], query='q', top_k=3, score_threshold=0.3)
    assert fake.limits == [20] and seen['count'] == 20 and seen['purpose'] == 'knowledge_rerank'
    assert [h['content'] for h in hits] == ['doc7', 'doc0', 'doc3']
    assert hits[0]['score'] == 0.95 and hits[0]['vectorScore'] == pytest.approx(0.93)


@pytest.mark.asyncio
async def test_search_chunks_without_rerank_keeps_vector_order(kb, monkeypatch):
    kb_service, fake = kb

    async def none():
        return None

    monkeypatch.setattr(kb_service.rerank_service, 'get_active_rerank_config', none)
    hits = await kb_service.search_chunks(knowledge_ids=['k'], query='q', top_k=3, score_threshold=0.3)
    assert fake.limits == [3]
    assert [h['content'] for h in hits] == ['doc0', 'doc1', 'doc2']
    assert all(h['score'] == h['vectorScore'] for h in hits)
    # 显式 False 连配置都不读
    async def boom():
        pytest.fail('rerank=False 不应读取重排配置')
    monkeypatch.setattr(kb_service.rerank_service, 'get_active_rerank_config', boom)
    await kb_service.search_chunks(knowledge_ids=['k'], query='q', top_k=3, score_threshold=0.3, rerank=False)


@pytest.mark.asyncio
async def test_search_chunks_rerank_failure_falls_back_with_warning(kb, monkeypatch, caplog):
    kb_service, fake = kb

    async def active():
        return config()

    async def failing(query, documents, **kwargs):
        raise service.RerankError('HTTP 500')

    monkeypatch.setattr(kb_service.rerank_service, 'get_active_rerank_config', active)
    monkeypatch.setattr(kb_service.rerank_service, 'rerank', failing)
    with caplog.at_level('WARNING'):
        hits = await kb_service.search_chunks(knowledge_ids=['k'], query='q', top_k=2, score_threshold=0.3)
    assert [h['content'] for h in hits] == ['doc0', 'doc1']
    assert any('重排失败' in r.getMessage() and 'HTTP 500' in r.getMessage() for r in caplog.records)
