import asyncio
import time

import pytest

from app.services.knowledge import web_search_service as search
from app.services.knowledge.web_request_scope import WebRequestScope
from app.services.platform import platform_config_service as config


@pytest.fixture(autouse=True)
def reset_health():
    search._provider_health.clear()
    yield
    search._provider_health.clear()


def runtime_config(**extra):
    value = config._normalize_provider_pool({
        'providerMode': 'deepseek_first',
        'providerPool': [
            {'id': 'searxng', 'enabled': True, 'fallbackOnly': False},
            {'id': 'deepseek-official', 'enabled': True, 'fallbackOnly': True},
        ],
        'deepseekResearchHybrid': True,
        'searxngUrl': 'http://search.internal',
    })
    value.update(deepseekApiKey='assigned-key', _callerUserId='user-1')
    value.update(extra)
    return value


def test_new_private_deployment_does_not_enable_official_model_search():
    cfg = config._normalize_provider_pool({})
    assert cfg['providerMode'] == 'primary_fallback'
    assert not any(row['id'] == 'deepseek-official' and row['enabled'] for row in cfg['providerPool'])


@pytest.mark.asyncio
async def test_private_deployment_never_uses_official_even_when_its_key_exists(monkeypatch):
    cfg = runtime_config()
    cfg['providerPool'][0]['enabled'] = False
    cfg = config._normalize_provider_pool(cfg)
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        return [], 'HTTP 503'
    monkeypatch.setattr(search, '_stage_search_single', single)
    rows, error = await search._stage_search('school query', cfg)
    assert not rows and error
    assert calls == ['searxng']


@pytest.mark.asyncio
async def test_self_hosted_first_mode_is_preserved(monkeypatch):
    cfg = runtime_config(providerMode='primary_fallback')
    cfg = config._normalize_provider_pool(cfg)
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        return [{'url': 'https://example.com/source'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    await search._stage_search('q', cfg)
    assert calls == ['searxng']


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback(monkeypatch):
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        return [{'url': 'https://example.com/source'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    cfg = runtime_config()
    rows, error = await search._stage_search('original query', cfg)
    assert rows and not error
    assert calls == ['deepseek-official']
    assert cfg['_searchProvidersUsed'] == ['deepseek-official']
    assert cfg['searchProvider'] == 'searxng'  # Image search remains supported.


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['HTTP 429', 'HTTP 503', 'ReadTimeout', '未执行服务端联网搜索', ''])
async def test_primary_failure_or_empty_uses_original_query_for_fallback(monkeypatch, failure):
    calls = []
    async def single(query, cfg, provider):
        calls.append((provider, query))
        if provider == 'deepseek-official':
            return [], failure
        return [{'url': 'https://example.com/fallback'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    rows, error = await search._stage_search('original query', runtime_config())
    assert rows and not error
    assert calls == [('deepseek-official', 'original query'), ('searxng', 'original query')]


@pytest.mark.asyncio
async def test_empty_fallback_preserves_primary_failure(monkeypatch):
    async def single(query, cfg, provider):
        return [], 'HTTP 429' if provider == 'deepseek-official' else ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    rows, error = await search._stage_search('q', runtime_config())
    assert not rows and '429' in error


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['deepseek_first', 'primary_fallback'])
@pytest.mark.parametrize('fallback_error', ['', 'captcha', 'HTTP 503'])
async def test_exhausted_native_budget_does_not_misclassify_healthy_empty_search(monkeypatch, mode, fallback_error):
    scope = WebRequestScope()
    await scope.claim_budget(search._RESEARCH_HYBRID_BUDGET_KEY, limit=1)
    cfg = config._normalize_provider_pool(runtime_config(providerMode=mode))
    cfg.update(_requestScope=scope, deepseekResearchMaxQueries=1)
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        return [], fallback_error
    monkeypatch.setattr(search, '_stage_search_single', single)
    rows, error = await search._stage_research_search('Kimi K3 official technical report', cfg)
    assert rows == []
    assert calls == ['searxng']
    assert bool(error) == bool(fallback_error)
    assert '额度' not in error
    assert cfg['_searchProviderFailures'] == ({'searxng': search.failure_code(fallback_error)} if fallback_error else {})
    await scope.close()


@pytest.mark.asyncio
async def test_cancellation_does_not_start_fallback(monkeypatch):
    search._provider_health['deepseek-official:user-1'] = {
        'opened_at': time.monotonic() - search._PROVIDER_OPEN_SECONDS - 1,
        'half_open': False,
    }
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        raise asyncio.CancelledError()
    monkeypatch.setattr(search, '_stage_search_single', single)
    with pytest.raises(asyncio.CancelledError):
        await search._stage_search('q', runtime_config())
    assert calls == ['deepseek-official']
    assert search._provider_health['deepseek-official:user-1']['half_open'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('native_error', ['', 'ReadTimeout'])
async def test_team_paid_budget_cannot_be_bypassed_by_failure_or_parallel_queries(monkeypatch, native_error):
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        await asyncio.sleep(0)
        if provider == 'deepseek-official' and native_error:
            return [], native_error
        return [{'url': 'https://example.com/source'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    scope = WebRequestScope()
    try:
        results = await asyncio.gather(*(
            search._stage_research_search(f'q{i}', runtime_config(_requestScope=scope, deepseekResearchMaxQueries=3))
            for i in range(6)
        ))
        assert all(rows and not error for rows, error in results)
        assert calls.count('deepseek-official') == 3
        assert calls.count('searxng') == (6 if native_error else 3)
    finally:
        await scope.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['missing_key', 'missing_scope', 'disabled', 'cooldown'])
async def test_unavailable_primary_uses_fallback_without_spending(monkeypatch, reason):
    cfg = runtime_config()
    if reason == 'missing_key':
        cfg['deepseekApiKey'] = ''
    if reason == 'disabled':
        cfg['providerPool'][0]['enabled'] = False
        cfg = config._normalize_provider_pool(cfg)
    if reason == 'cooldown':
        search._provider_health['deepseek-official:user-1'] = {'opened_at': time.monotonic(), 'half_open': False}
    calls = []
    async def single(query, cfg, provider):
        calls.append(provider)
        return [{'url': 'https://example.com/source'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    run = search._stage_research_search if reason == 'missing_scope' else search._stage_search
    rows, error = await run('q', cfg)
    assert rows and not error and calls == ['searxng']


@pytest.mark.asyncio
async def test_unused_fallback_does_not_reserve_recovery_probe(monkeypatch):
    search._provider_health['searxng'] = {
        'opened_at': time.monotonic() - search._PROVIDER_OPEN_SECONDS - 1,
        'half_open': False,
    }
    async def single(query, cfg, provider):
        assert provider == 'deepseek-official'
        return [{'url': 'https://example.com/source'}], ''
    monkeypatch.setattr(search, '_stage_search_single', single)
    await search._stage_search('q', runtime_config())
    assert search._provider_health['searxng']['half_open'] is False
