"""会话附件资产（设计稿 §5）：build_file_context 合并「已发送的会话附件 + 本轮新发」并按
sha256 去重的纯逻辑（不依赖 DB，mock 加载与检索）。"""
import pytest

from app.services.files import thread_attachment_service as tas
from app.services.agent_harness.orchestrator import _loads_attachments


def test_loads_attachments_valid_json_not_dead_code():
    """P1 回归：_loads_attachments 的 json.loads 曾被误挪到另一函数 return 之后成死代码，
    合法附件 JSON 返回 None → 刷新后历史附件卡消失。此处锁死正常反序列化路径。"""
    assert _loads_attachments('[{"filename": "a.pdf", "kind": "pdf"}]') == [{"filename": "a.pdf", "kind": "pdf"}]
    assert _loads_attachments("[]") is None          # 空列表视作无附件
    assert _loads_attachments(None) is None
    assert _loads_attachments("{坏 json") is None    # 坏数据静默 None，不炸整个会话加载


@pytest.fixture(autouse=True)
def _stub_retrieve(monkeypatch):
    # retrieve_relevant 直接回原文，聚焦合并/去重逻辑
    async def _retrieve(query, text, **_kwargs):
        return text
    monkeypatch.setattr(tas.session_file_service, "retrieve_relevant", _retrieve)
    async def _generated(_user_id, _thread_id=None):
        return []
    from app.services.files import user_file_service
    monkeypatch.setattr(user_file_service, "list_generated_files", _generated)


@pytest.mark.asyncio
async def test_cross_turn_attachments_visible_without_reattach(monkeypatch):
    """核心规则：上传发送后一直可读——本轮没带任何附件，仍注入历史发过的会话附件。"""
    async def _load(thread_id, user_id):
        return [{"filename": "报告.docx", "text": "季度报告正文", "sha256": "a"}]
    monkeypatch.setattr(tas, "_load_thread_attachments", _load)

    ctx = await tas.build_file_context(thread_id="t1", user_id="u1", query="报告说了啥", attachments=None)
    assert "报告.docx" in ctx and "季度报告正文" in ctx
    assert "整个会话期间都可引用" in ctx  # header 语义已改为跨轮可读


@pytest.mark.asyncio
async def test_dedup_persisted_and_turn_by_sha(monkeypatch):
    """本轮又发了一个与历史资产内容相同的文件：sha256 去重，不重复注入。"""
    async def _load(thread_id, user_id):
        return [{"filename": "同文件.txt", "text": "重复内容", "sha256": tas._sha("重复内容")}]
    monkeypatch.setattr(tas, "_load_thread_attachments", _load)

    # 本轮再发同内容（不同文件名）+ 一个新文件
    turn = [{"filename": "又发一次.txt", "text": "重复内容"},
            {"filename": "新文件.txt", "text": "全新内容"}]
    ctx = await tas.build_file_context(thread_id="t1", user_id="u1", query="q", attachments=turn)
    assert ctx.count("重复内容") == 1, "相同内容按 sha256 去重，只注入一次"
    assert "全新内容" in ctx


@pytest.mark.asyncio
async def test_no_attachments_returns_empty(monkeypatch):
    async def _load(thread_id, user_id):
        return []
    monkeypatch.setattr(tas, "_load_thread_attachments", _load)
    ctx = await tas.build_file_context(thread_id="t1", user_id="u1", query="q", attachments=None)
    assert ctx == ""
