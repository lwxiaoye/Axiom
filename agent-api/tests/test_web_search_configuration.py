import json
from types import SimpleNamespace

import pytest

from app.services.platform import platform_config_service as config


class StoredConfig:
    def __init__(self, values):
        self.values = values

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass

    async def get(self, _model, key):
        assert key == config.WEB_SEARCH_KEY
        return SimpleNamespace(config_json=json.dumps(self.values))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,env_field", [
    ("searxngEngines", "WEB_SEARCH_SEARXNG_ENGINES"),
    ("searxngImageEngines", "WEB_SEARCH_SEARXNG_IMAGE_ENGINES"),
])
@pytest.mark.parametrize("stored", [None, "", "saved-engine"])
@pytest.mark.parametrize("env_value", ["", "local-engine"])
async def test_deployment_engine_override_and_saved_fallback(monkeypatch, field, env_field, stored, env_value):
    monkeypatch.setattr(config.settings, env_field, env_value)
    monkeypatch.setitem(config.WEB_SEARCH_DEFAULTS, field, env_value)
    values = {} if stored is None else {field: stored}
    monkeypatch.setattr(config, "async_session", lambda: StoredConfig(values))

    displayed = await config.get_web_search_masked()
    used = await config.get_web_search_config()

    # env 只是缺省值：页面上看到什么，运行时就用什么。此前 env 非空会反过来覆盖
    # 库里保存的值，管理员改完引擎保存成功、检索却仍打旧引擎（2af2f19 修掉）。
    expected = env_value if stored is None else stored
    assert displayed[field] == expected
    assert used[field] == expected


@pytest.mark.asyncio
async def test_saved_values_win_over_deployment_defaults(monkeypatch):
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_URL", "http://127.0.0.1:8085")
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_ENGINES", "")
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_IMAGE_ENGINES", "")
    monkeypatch.setattr(config, "async_session", lambda: StoredConfig({
        "searxngUrl": "http://search.internal:8085",
        "searxngEngines": "saved-engine",
    }))

    used = await config.get_web_search_config()

    # 保存过的地址与引擎都生效；env 里的地址不再在运行时覆盖它
    assert used["searxngUrl"] == "http://search.internal:8085"
    assert used["searxngEngines"] == "saved-engine"
