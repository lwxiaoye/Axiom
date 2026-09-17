# -*- coding: utf-8 -*-
"""对话附图 = [图N] 内联；download_url 仅文件产物。防天气+照片过交付。"""
from pathlib import Path

import pytest

from app.services.chat.tools.paths import build_path_tools
from app.services.chat.tools.web import build_web_tools
from app.services.knowledge import web_search_service
import app.services.chat.turn_context_builder as tcb


def test_system_prompt_separates_chat_images_from_file_artifacts():
    src = Path(tcb.__file__).read_text(encoding="utf-8")
    assert "对话附图 vs 文件产物" in src
    assert "普通问答不得自行配图" in src
    assert "仅当本轮在做演示文稿" in src
    assert "产物素材不进对话附图" in src


def test_delivery_summary_is_natural_instead_of_fixed_three_sections():
    rule = tcb.DELIVERY_ANSWER_STRUCTURE_RULE
    nudge = tcb.DELIVERY_ANSWER_STRUCTURE_NUDGE
    assert "核心变化" in rule and "验证结果" in rule
    assert "不要机械套用" in rule
    assert "必须按下面顺序" not in rule
    assert "不要强制套" in nudge


def test_download_url_description_forbids_chat_gallery():
    tools = {t.name: t for t in build_path_tools(user_id="u1")}
    dl = tools["download_url"]
    assert "[图N]" in dl.description
    assert "不要" in dl.description
    assert "文件产物" in dl.description


@pytest.mark.asyncio
async def test_search_web_image_footer_prefers_inline_not_download(monkeypatch):
    async def _search(q, start_index=1, with_images=False, **_kw):
        assert with_images is True
        return {
            "enabled": True,
            "results": [{"title": "t", "url": "https://example.com", "content": "s"}],
            "text": "x",
            "scraped_pages": [],
            "images": [{
                "url": "https://cdn.example.com/a.jpg",
                "title": "东山岛海景",
                "source": "https://example.com/p",
            }],
            "error": "",
        }

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(image_sink=sink, user_message="在对话里给我几张东山岛照片")[0]
    obs = await tool.execute({"query": "东山岛 照片"})
    content = getattr(obs, "model_content", None) or str(obs)
    assert "[图1]" in content
    assert "不要 download_url" in content
    assert "不要另存文件" in content


@pytest.mark.asyncio
async def test_artifact_images_stay_downloadable_but_leave_no_chat_gallery(monkeypatch):
    async def _search(q, start_index=1, with_images=False, **_kw):
        assert with_images is True
        return {
            "enabled": True,
            "results": [{"title": "t", "url": "https://example.com", "content": "s"}],
            "text": "x",
            "scraped_pages": [],
            "images": [{
                "url": "https://cdn.example.com/a.jpg",
                "title": "东山岛海景",
                "source": "https://example.com/p",
            }],
            "error": "",
        }

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="制作一份东山岛 PPT，并配上实景照片",
        image_delivery_mode="artifact_only",
    )[0]
    obs = await tool.execute({"query": "东山岛 实景照片"})
    content = getattr(obs, "model_content", None) or str(obs)
    assert sink[0]["display_scope"] == "artifact_only"
    assert "download_url" in content
    assert "最终对话正文不得输出" in content
    assert not getattr(obs, "ui", {}).get("images")
