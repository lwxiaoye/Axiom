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


class _FakeVisionResponse:
    def __init__(self, content):
        self.status_code = 200
        self.text = ''
        self._content = content

    def json(self):
        return {'choices': [{'message': {'content': self._content}}]}


class _FakeVisionClient:
    """替身 httpx.AsyncClient：记录发出去的请求，按预设内容回一个 200。"""
    sent = []
    reply = ''

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, *, headers=None, json=None):
        _FakeVisionClient.sent.append({'url': url, 'headers': headers, 'json': json})
        return _FakeVisionResponse(_FakeVisionClient.reply)


@pytest.mark.asyncio
@pytest.mark.parametrize('reply, expected', [('图片里写着 TEST 123', 'success'), ('', 'failed')])
async def test_ocr_probe_requires_non_empty_description(monkeypatch, reply, expected):
    """视觉模型测试的成功标准是「真返回了描述」：200 + 空内容要判失败，别让管理员误以为可用。"""
    _FakeVisionClient.sent = []
    _FakeVisionClient.reply = reply
    monkeypatch.setattr(router.httpx, 'AsyncClient', _FakeVisionClient)
    async def stored_secret(key, field, incoming):
        return incoming or 'stored-vision-key'
    monkeypatch.setattr(router.cfg, 'resolve_secret', stored_secret)

    result = await router.test_ocr(
        {'strategy': 'multimodal_model', 'model': 'qwen3-vl-plus',
         'visionBaseUrl': 'https://vision.example/v1/', 'visionApiKey': ''},
        UserContext(user_id='admin-user', username='admin'),
    )

    assert result['status'] == expected
    assert len(_FakeVisionClient.sent) == 1
    request = _FakeVisionClient.sent[0]
    assert request['url'] == 'https://vision.example/v1/chat/completions'
    # 密钥留空 → 用库里已存的那把；且密钥绝不能回显到结果里
    assert request['headers']['Authorization'] == 'Bearer stored-vision-key'
    assert 'stored-vision-key' not in str(result)
    parts = request['json']['messages'][0]['content']
    assert parts[1]['image_url']['url'].startswith('data:image/png;base64,')


@pytest.mark.asyncio
async def test_ocr_probe_without_base_url_does_not_call_out(monkeypatch):
    """没填请求地址就没法替管理员实测（运行时走平台网关 + 用户自己的 Key），只给说明不发请求。"""
    _FakeVisionClient.sent = []
    monkeypatch.setattr(router.httpx, 'AsyncClient', _FakeVisionClient)
    result = await router.test_ocr(
        {'strategy': 'multimodal_model', 'model': 'qwen3-vl-plus'},
        UserContext(user_id='admin-user', username='admin'),
    )
    assert result['status'] == 'info'
    assert _FakeVisionClient.sent == []
