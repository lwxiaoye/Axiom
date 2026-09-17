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

    assert displayed[field] == (env_value if stored is None else stored)
    assert used[field] == (env_value or displayed[field])


@pytest.mark.asyncio
async def test_deployment_endpoint_override_preserves_saved_engine_choice(monkeypatch):
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_URL", "http://127.0.0.1:8085")
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_ENGINES", "")
    monkeypatch.setattr(config.settings, "WEB_SEARCH_SEARXNG_IMAGE_ENGINES", "")
    monkeypatch.setattr(config, "async_session", lambda: StoredConfig({
        "searxngUrl": "http://search.internal:8085",
        "searxngEngines": "saved-engine",
    }))

    used = await config.get_web_search_config()

    assert used["searxngUrl"] == "http://127.0.0.1:8085"
    assert used["searxngEngines"] == "saved-engine"
