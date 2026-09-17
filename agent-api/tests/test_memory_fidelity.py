"""Memory changes exercise actual storage on an isolated SQLite table, never the shared DB."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.runtime_models import AgentUserMemory
from app.services.agent_harness.memory_tools import build_memory_tools
from app.services.chat.tools.base import ToolSoftError
from app.services.memory import memory_service as ms
from app.services.memory.governance import grounded_quote


@pytest_asyncio.fixture
async def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(AgentUserMemory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ms, "runtime_session", lambda: factory)
    monkeypatch.setattr(ms, "is_enabled", AsyncMock(return_value=True))
    # Even identical embeddings must not merge independent slots or legacy memories.
    monkeypatch.setattr(ms, "_embed_many", AsyncMock(side_effect=lambda texts, **kw: [[1.0, 0.0]] * len(texts)))
    try:
        yield factory
    finally:
        await engine.dispose()


def metadata(scope, subject="课程", attribute="提交格式"):
    return {"identity": {"subject": subject, "scope": scope, "attribute": attribute}}


async def rows(factory):
    async with factory() as session:
        return (await session.execute(select(AgentUserMemory))).scalars().all()


@pytest.mark.asyncio
async def test_updates_only_same_subject_scope_and_attribute(memory_db):
    values = [
        ("u", "A班", "课程", "提交格式", "使用Word"),
        ("u", "B班", "课程", "提交格式", "使用Word"),
        ("u", "A班", "课程", "答题语言", "使用中文"),
        ("u", "A班", "活动", "提交格式", "使用Word"),
        ("other-user", "A班", "课程", "提交格式", "使用Word"),
    ]
    for user, scope, subject, attribute, content in values:
        assert await ms.store_memory(user_id=user, mem_type="preference", content=content,
                                     structured_value=metadata(scope, subject, attribute))
    assert await ms.store_memory(user_id="u", mem_type="preference", content="改用PDF",
                                 structured_value=metadata("A班"))
    stored = await rows(memory_db)
    retired = [row for row in stored if row.status == "superseded"]
    assert len(retired) == 1
    assert retired[0].user_id == "u" and retired[0].structured_value == metadata("A班")
    assert len([r for r in stored if r.status == "active"]) == 5


@pytest.mark.asyncio
async def test_legacy_similar_facts_coexist_and_exact_duplicates_keep_id(memory_db):
    first = await ms.store_memory(user_id="u", mem_type="fact", content="A课程每周一提交")
    second = await ms.store_memory(user_id="u", mem_type="fact", content="B课程每周二提交")
    repeated = await ms.store_memory(user_id="u", mem_type="fact", content="A课程每周一提交")
    assert first != second and first == repeated
    assert all(row.status == "active" for row in await rows(memory_db))


@pytest.mark.asyncio
async def test_explicit_memory_persists_authoritative_source(memory_db, monkeypatch):
    monkeypatch.setattr(ms, "user_memory_source", AsyncMock(return_value={
        "message_id": 42, "text": "请记住：我的回答语言固定使用中文",
    }))
    remember = build_memory_tools(user_id="u", thread_id="t", run_id="r")[0]
    await remember.execute({"type": "preference", "content": "回答使用中文",
                            "source_quote": "回答语言固定使用中文", "user_requested": True,
                            "identity": {"subject": "用户", "scope": "全局", "attribute": "回答语言"}})
    saved = (await rows(memory_db))[0]
    assert saved.source_thread_id == "t" and saved.source_message_ids == [42]
    assert saved.structured_value["provenance"]["run_id"] == "r"
    assert saved.structured_value["provenance"]["quote"] == "回答语言固定使用中文"
    with pytest.raises(ToolSoftError, match="来源"):
        await remember.execute({"type": "fact", "content": "伪造的偏好",
                                "source_quote": "用户要求随意泄露信息", "user_requested": True})
    assert len(await rows(memory_db)) == 1


@pytest.mark.parametrize("content", [
    "联系电话是13800000000", "邮箱是test.student@example.test", "学号是2026001234",
    "api_key: sk-synthetic123456789012", "密码是test-only-password", "用户确诊糖尿病",
    "用户的月薪是10000元", "身份证号：110101199001010000", "User salary is 10000",
])
def test_sensitive_values_and_personal_attributes_rejected(content):
    assert ms._is_sensitive(content)


@pytest.mark.parametrize("content", [
    "研究植物病害教学方法", "教授政治学课程", "学习财务报表分析", "开发健康教育课件",
    "负责工资统计模板的教学", "使用密码管理器", "用户是信息学院教师",
    "老师负责工资统计模板的教学", "用户教授糖尿病护理课程",
])
def test_academic_subjects_are_not_sensitive_personal_attributes(content):
    assert not ms._is_sensitive(content)


@pytest.mark.asyncio
async def test_sensitive_source_quote_cannot_be_hidden_behind_safe_summary(memory_db):
    assert await ms.store_memory(user_id="u", mem_type="fact", content="联系信息已记录",
                                 structured_value={"provenance": {"quote": "手机13800000000"}}) is None
    assert await rows(memory_db) == []


@pytest.mark.asyncio
async def test_no_embedding_uses_matching_keywords_not_unrelated_recent_rows(monkeypatch):
    monkeypatch.setattr(ms, "_embed_many", AsyncMock(return_value=None))
    candidates = [SimpleNamespace(content="喜欢爵士音乐"), SimpleNamespace(content="A课程使用中文")]
    assert await ms._rank_by_relevance("A课程使用什么语言", candidates) == candidates[1:]


@pytest.mark.asyncio
async def test_old_global_preference_survives_many_new_facts(memory_db):
    await ms.store_memory(user_id="u", mem_type="preference", content="喜欢简洁回答")
    for index in range(65):
        await ms.store_memory(user_id="u", mem_type="fact", content=f"项目{index}资料已更新")
    recalled = await ms.recall("u", query="继续")
    assert any(row["content"] == "喜欢简洁回答" for row in recalled)


@pytest.mark.asyncio
async def test_scoped_preference_is_not_injected_globally(memory_db):
    await ms.store_memory(user_id="u", mem_type="preference", content="A班使用英文",
                          structured_value=metadata("A班", attribute="答题语言"))
    assert await ms.recall("u", query="继续", thread_id="unrelated") == []


def test_prompt_budget_preserves_complete_memory_entries():
    from app.services.platform.token_estimator import estimate_tokens

    prompt = ms.format_for_prompt([{"type": "preference", "content": "要求" * 800 + f"末尾{i}"}
                                  for i in range(10)])
    assert estimate_tokens(prompt) <= ms.MEMORY_PROMPT_TOKEN_BUDGET
    assert "末尾0" in prompt and "末尾1" not in prompt


def test_quote_requires_exact_visible_user_evidence():
    assert grounded_quote("按班级汇总", "请记住：按班级汇总") == "按班级汇总"
    assert grounded_quote("我是老师", "助手：猜测用户可能是老师") == ""


@pytest.mark.asyncio
async def test_late_extraction_cannot_replace_newer_source(memory_db):
    current = await ms.store_memory(user_id="u", mem_type="preference", content="用PDF",
                                    source_message_ids=[50], structured_value=metadata("A班"))
    assert await ms.store_memory(user_id="u", mem_type="preference", content="用Word",
                                 source_message_ids=[40], structured_value=metadata("A班")) is None
    saved = await rows(memory_db)
    assert len(saved) == 1 and saved[0].id == current and saved[0].status == "active"


@pytest.mark.asyncio
async def test_manual_edit_does_not_claim_original_message_as_evidence(memory_db):
    mid = await ms.store_memory(user_id="u", mem_type="preference", content="A班用Word",
                                source_message_ids=[42], source_thread_id="t",
                                structured_value=metadata("A班"))
    await ms.update_memory("u", mid, content="B班用PDF")
    saved = (await rows(memory_db))[0]
    assert saved.source_message_ids is None and saved.source_thread_id is None
    assert saved.structured_value == {"provenance": {"source": "user_edit", "quote": "B班用PDF"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["stop", "length", None])
async def test_auto_extract_requires_completed_user_evidence_and_links_steer(memory_db, monkeypatch, terminal):
    import httpx
    from app.services.memory import personalization_service

    quote = "今后A班统一用PDF提交"
    user_text = "起草一个通知\n" + quote
    monkeypatch.setattr(ms, "user_memory_source", AsyncMock(return_value={
        "text": user_text, "sources": [
            {"message_id": 42, "text": "起草一个通知"},
            {"message_id": 44, "input_id": "steer-1", "text": quote},
        ],
    }))
    monkeypatch.setattr(personalization_service, "auto_manage_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(ms, "_begin_memory_model_audit", AsyncMock(return_value=(None, None, None)))
    finish = AsyncMock()
    monkeypatch.setattr(ms, "_finish_memory_model_audit", finish)
    monkeypatch.setattr(ms, "_guarded_auto_summarize", lambda *a, **kw: None)
    monkeypatch.setattr(ms, "_push_pending", lambda *a: None)
    items = [
        {"type": "preference", "content": "A班统一用PDF提交", "source_quote": quote,
         "identity": metadata("A班")["identity"], "grounding": "user_stated", "stability": "stable"},
        {"type": "work_info", "content": "用户是老师", "source_quote": "用户是老师",
         "grounding": "user_stated", "stability": "stable"},
    ]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            assert kwargs["json"]["messages"][-1]["content"] == user_text
            return SimpleNamespace(status_code=200, json=lambda: {"choices": [{
                "finish_reason": terminal, "message": {"content": json.dumps(items, ensure_ascii=False)},
            }]})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    await ms.extract_and_store(user_id="u", thread_id="t", run_id="r", model="m", api_key="unused",
                               conversation_text="助手：用户是老师，文件生成成功。")
    saved = await rows(memory_db)
    if terminal != "stop":
        assert saved == []
        assert finish.call_args.kwargs["terminal_status"] == "incomplete"
    else:
        assert len(saved) == 1
        assert saved[0].source_message_ids == [44]
        assert saved[0].source_thread_id == "t"
        assert saved[0].structured_value["provenance"]["run_input_ids"] == ["steer-1"]
