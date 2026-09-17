import pytest

from app.services.agents import agent_executor


class _Ctx:
    user_id = "u-1"
    token = "token-1"


class _Engine:
    ctx = _Ctx()

    def input_value(self, node, key, default=None):
        return node.get("inputs", {}).get(key, default)

    def interpolate(self, value):
        return value


def test_selected_skills_are_split_by_source():
    node = {
        "inputs": {
            "skills": [
                {"skillId": "mine-1", "source": "mine"},
                {"skillId": "market-1", "source": "system"},
                {"skillId": "legacy-mine"},
                {"skillId": "market-1", "source": "system"},
            ]
        }
    }

    agent_ids, market_ids = agent_executor._split_selected_skill_ids(_Engine(), node)

    assert agent_ids == ["mine-1", "legacy-mine"]
    assert market_ids == ["market-1"]


@pytest.mark.asyncio
async def test_selected_market_skill_prompt_uses_authoritative_content(monkeypatch):
    async def fake_fetch(skill_ids, token):
        assert skill_ids == ["market-1"]
        assert token == "token-1"
        return [{
            "id": "market-1",
            "record_id": "record-1",
            "name": "权威系统技能",
            "description": "权威描述",
            "instructions": "权威 SKILL.md 正文",
        }]

    monkeypatch.setattr(agent_executor, "_fetch_trusted_skills", fake_fetch)
    node = {
        "inputs": {
            "systemPrompt": "基础提示",
            "skills": [{
                "skillId": "market-1",
                "source": "system",
                "name": "前端缓存名称",
                "description": "前端缓存描述不可信",
            }],
        }
    }

    prompt = await agent_executor._build_system_prompt(_Engine(), node, [])

    assert "基础提示" in prompt
    assert "权威系统技能" in prompt
    assert "权威 SKILL.md 正文" in prompt
    assert "前端缓存描述不可信" not in prompt


@pytest.mark.asyncio
async def test_selected_market_skill_packages_are_loaded_from_skill_market(monkeypatch):
    async def fake_agent_packages(skill_ids, owner_user_id=None):
        assert skill_ids == ["mine-1"]
        assert owner_user_id == "u-1"
        return [{"skillId": "mine-1", "name": "我的技能", "files": {"SKILL.md": b"mine"}, "hasScripts": False}]

    async def fake_fetch(skill_ids, token):
        assert skill_ids == ["market-1"]
        assert token == "token-1"
        return [{"id": "market-1", "record_id": "record-1", "name": "系统技能", "instructions": "系统正文"}]

    async def fake_market_packages(skills, token):
        assert skills[0]["record_id"] == "record-1"
        assert token == "token-1"
        return [{
            "skillId": "market-1",
            "recordId": "record-1",
            "name": "系统技能",
            "files": {"scripts/run.py": b"print(1)"},
            "hasScripts": True,
        }]

    monkeypatch.setattr("app.routers.agent_skill.load_skill_packages", fake_agent_packages)
    monkeypatch.setattr(agent_executor, "_fetch_trusted_skills", fake_fetch)
    monkeypatch.setattr(agent_executor.skill_package_bridge, "fetch_skill_packages", fake_market_packages)

    node = {
        "inputs": {
            "skills": [
                {"skillId": "mine-1", "source": "mine", "name": "我的技能"},
                {"skillId": "market-1", "source": "system", "name": "系统技能"},
            ]
        }
    }

    packages = await agent_executor._load_selected_skill_packages(_Engine(), node)

    assert [pkg["skillId"] for pkg in packages] == ["mine-1", "market-1"]
    assert packages[1]["hasScripts"] is True
