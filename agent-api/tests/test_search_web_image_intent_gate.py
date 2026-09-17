# -*- coding: utf-8 -*-
"""search_web 默认不搜图：只有查询词自带图意图时才 with_images=True。

根因（2026-08-05）：旧实现只要 image_sink 未满就开图片路，天气/新闻等纯文本
检索也并行打 images，浪费预算并把文章缩略图塞进配图池。PPT 提示词已要求
搜图查询带「图片/照片/配图」，与此结构条件对齐——禁止靠领域词表（天气|新闻）
反推。
"""
import pytest

from app.services.chat.tools.web import build_web_tools
from app.services.knowledge import web_search_service


def _ok_payload(*, images=None):
    return {
        "enabled": True,
        "results": [{"title": "t", "url": "https://example.com/a", "content": "s"}],
        "text": "搜到 1 条",
        "scraped_pages": [],
        "images": images or [],
        "error": "",
    }


@pytest.mark.asyncio
async def test_plain_info_query_does_not_request_images(monkeypatch):
    """天气/新闻类纯信息检索：即使有 image_sink 也不得 with_images。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["query"] = q
        seen["with_images"] = with_images
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(image_sink=sink, user_message="帮我看看漳州天气")[0]
    await tool.execute({"query": "漳州市今天天气状况"})
    assert seen["with_images"] is False
    assert sink == []


@pytest.mark.asyncio
async def test_image_intent_query_requests_images_and_fills_sink(monkeypatch):
    """查询词带「图片/配图」等：with_images=True，图片进 sink。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        return _ok_payload(images=[
            {"url": "https://cdn.example.com/a.jpg", "title": "漳州海边风景", "source": "https://example.com/p"},
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(image_sink=sink, user_message="请给我几张漳州海边风景照片")[0]
    text = await tool.execute({"query": "漳州海边风景 配图"})
    assert seen["with_images"] is True
    assert len(sink) == 1
    assert sink[0]["url"].endswith("a.jpg")
    assert "[图1]" in str(text)


@pytest.mark.asyncio
async def test_image_intent_without_sink_still_skips_images(monkeypatch):
    """无 image_sink 时即便查询带图意图也不开图片路（无处收集）。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools()[0]  # no image_sink
    await tool.execute({"query": "城市夜景 图片"})
    assert seen["with_images"] is False


@pytest.mark.asyncio
async def test_photo_english_image_intent(monkeypatch):
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools(image_sink=[], user_message="show me a zhangzhou beach photo")[0]
    await tool.execute({"query": "zhangzhou beach photo"})
    assert seen["with_images"] is True


@pytest.mark.asyncio
async def test_user_message_image_intent_enables_images_even_if_query_plain(monkeypatch):
    """用户消息要配图、模型 query 只写主题：仍应 with_images，避免二次搜图。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        seen["query"] = q
        seen["with_images"] = with_images
        seen["image_query"] = image_query
        return _ok_payload(images=[
            {"url": "https://cdn.example.com/island.jpg", "title": "东山岛实景", "source": "https://example.com/p"},
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="查一下漳州天气，并在对话里附上几张东山岛照片",
    )[0]
    text = await tool.execute({"query": "漳州市今天天气"})
    assert seen["with_images"] is True
    # 正文 query 不被配图词污染
    assert "图片" not in seen["query"]
    assert "东山" not in seen["query"]
    iq0 = str(seen.get("image_query") or "")
    # 配图 query 用「实景 照片」等，不要求字面「图片」
    assert ("照片" in iq0 or "图片" in iq0 or "实景" in iq0)
    assert "东山" in iq0 or "东山岛" in iq0
    assert len(sink) == 1
    assert "[图1]" in str(text)


@pytest.mark.asyncio
async def test_plain_user_message_still_skips_images_on_plain_query(monkeypatch):
    """用户只问天气、未提图：即使有 image_sink 也不搜图。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        seen["query"] = q
        return _ok_payload()

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(image_sink=sink, user_message="帮我看看今天漳州市的天气状况")[0]
    await tool.execute({"query": "漳州市今天天气状况"})
    assert seen["with_images"] is False
    # 天气类会服务端压紧/补「气温」等 token，只要仍是漳州天气主题即可
    assert "漳州" in str(seen.get("query") or "")
    assert sink == []


@pytest.mark.asyncio
async def test_user_message_topics_merge_into_image_search_query(monkeypatch):
    """用户要附图且点了另一主题时：正文 query 保持干净，配图主题只进 image_query。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        seen["query"] = q
        seen["with_images"] = with_images
        seen["image_query"] = image_query
        return _ok_payload(images=[
            {"url": "https://cdn.example.com/island.jpg", "title": "东山岛实景", "source": "https://example.com/p"},
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="查一下漳州天气，并在对话里附上几张东山岛照片",
    )[0]
    await tool.execute({"query": "漳州市今天天气"})
    assert seen["with_images"] is True
    q = seen["query"]
    assert "图片" not in q
    assert "东山" not in q and "东山岛" not in q
    iq = str(seen.get("image_query") or "")
    assert ("照片" in iq or "图片" in iq or "实景" in iq)
    assert "东山" in iq or "东山岛" in iq


@pytest.mark.asyncio
async def test_curry_html_images_reject_unrelated_search_fallback(monkeypatch):
    """Curry HTML 回归：包子/室内图必须被丢弃，不得在过滤全灭后兜底回填。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        seen["query"] = q
        seen["with_images"] = with_images
        seen["image_query"] = image_query
        return _ok_payload(images=[
            {
                "url": "https://img.example.com/baozi.jpg",
                "title": "面要包 要包谷 实景照片",
                "source": "https://m.dianping.com/shop/baozi",
            },
            {
                "url": "https://img.example.com/house.jpg",
                "title": "自建房村里限高 看看人家怎么做",
                "source": "https://example.com/house",
            },
            {
                "url": "https://cdn.nba.com/stephen-curry.jpg",
                "title": "Stephen Curry during a Golden State Warriors game",
                "source": "https://www.nba.com/player/stephen-curry",
            },
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="帮我制作一个 Curry 的 HTML 页面，配上相关图片",
        image_delivery_mode="artifact_only",
        artifact_asset_tool="fetch_ppt_asset",
    )[0]
    obs = await tool.execute({"query": "制作 Curry HTML 页面 图片"})

    assert seen["with_images"] is True
    assert "curry" in str(seen["image_query"]).lower()
    assert "html" not in str(seen["image_query"]).lower()
    assert [item["url"] for item in sink] == ["https://cdn.nba.com/stephen-curry.jpg"]
    content = getattr(obs, "model_content", None) or str(obs)
    assert "[图1]" in content
    assert 'fetch_ppt_asset(url="图N")' in content
    assert "搜索结果不是已下载文件" in content
    assert "baozi" not in content and "house.jpg" not in content
    assert sink[0]["display_scope"] == "artifact_only"
    assert not getattr(obs, "ui", {}).get("images")


@pytest.mark.asyncio
async def test_artifact_image_query_uses_focused_model_query_not_long_brief(monkeypatch):
    seen = {}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        seen["image_query"] = image_query
        return _ok_payload(images=[{
            "url": "https://cdn.example.com/westbrook.jpg",
            "title": "Russell Westbrook Thunder game action dunk",
            "source": "https://nba.example.com/westbrook",
        }])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    tool = build_web_tools(
        image_sink=[],
        user_message=(
            "制作8页威少PPT，必须使用至少4张真实比赛照片，不要每页铺底，"
            "还要数据页、时间轴和拼贴页"
        ),
        image_delivery_mode="artifact_only",
        artifact_asset_tool="fetch_ppt_asset",
    )[0]
    await tool.execute({"query": "Russell Westbrook Thunder game action photos"})

    image_query = str(seen["image_query"] or "")
    assert "Westbrook" in image_query
    assert "时间轴" not in image_query and "铺底" not in image_query


@pytest.mark.asyncio
async def test_artifact_search_does_not_confuse_web_result_numbers_with_image_numbers(monkeypatch):
    calls = {"n": 0}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        calls["n"] += 1
        images = ([{
            "url": "https://cdn.example.com/one.jpg",
            "title": "Russell Westbrook game photo",
            "source": "https://nba.example.com/one",
        }] if calls["n"] == 1 else [])
        payload = _ok_payload(images=images)
        payload["results"][0]["content"] = (
            "Russell Westbrook career and retirement report with verified game context, "
            "team history, dates, and statistical milestones for presentation research."
        )
        return payload

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="制作威少PPT，使用真实比赛照片",
        image_delivery_mode="artifact_only",
        artifact_asset_tool="fetch_ppt_asset",
    )[0]
    await tool.execute({"query": "Russell Westbrook game photo"})
    second = await tool.execute({"query": "Russell Westbrook retirement news"})
    content = getattr(second, "model_content", None) or str(second)

    assert "当前可用图片编号仅为 [图1]" in content
    assert "网页来源编号" in content


@pytest.mark.asyncio
async def test_all_unrelated_images_leave_gallery_empty(monkeypatch):
    """相关性无法证明时宁可无图，正文/产物仍可正常完成。"""

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        return _ok_payload(images=[
            {
                "url": "https://img.example.com/baozi.jpg",
                "title": "面要包 要包谷 实景照片",
                "source": "https://m.dianping.com/shop/baozi",
            },
            {
                "url": "https://img.example.com/house.jpg",
                "title": "自建房村里限高",
                "source": "https://example.com/house",
            },
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="请给这个 Curry HTML 页面配图",
        image_delivery_mode="artifact_only",
    )[0]
    obs = await tool.execute({"query": "Curry HTML 页面 图片"})

    assert sink == []
    content = getattr(obs, "model_content", None) or str(obs)
    assert "[图1]" not in content
    assert not getattr(obs, "ui", {}).get("images")


def test_image_relevance_requires_all_multiword_latin_subjects():
    from app.services.chat.tools.web import _image_is_on_topic

    tokens = ["Stephen", "Curry"]
    assert _image_is_on_topic(
        {"title": "Stephen Curry game photo", "source": "https://nba.com/curry"},
        tokens,
    ) is True
    assert _image_is_on_topic(
        {"title": "Stephen King portrait", "source": "https://example.com/stephen"},
        tokens,
    ) is False


def test_photo_subject_does_not_extract_nonsense_from_long_chinese_sentence():
    from app.services.chat.tools.web import _photo_subject_tokens

    user_text = "我想制作一个关于史蒂芬库里的演讲html，里面要包含他的比赛图片，要求精美"
    search_query = "斯蒂芬库里 比赛 图片 高清"

    # 不应从「里面要包含」的字符 n-gram 中伪造出「面要包」「要包含」。
    assert _photo_subject_tokens(user_text, search_query) == []


@pytest.mark.asyncio
async def test_long_chinese_html_request_drops_baozi_image(monkeypatch):
    """真实复现：NBA HTML 请求不能把「里面要包含」误当成包子主题。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, image_query=None, **_kw):
        seen["image_query"] = image_query
        return _ok_payload(images=[
            {
                "url": "https://img.example.com/baozi.jpg",
                "title": "面要包 要包含 实景照片 大众点评",
                "source": "https://m.dianping.com/ugcdetail/266958696",
            },
            {
                "url": "https://img.example.com/curry.jpg",
                "title": "斯蒂芬库里 NBA 赛场高清照片",
                "source": "https://nba.com/stephen-curry",
            },
        ])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(
        image_sink=sink,
        user_message="我想制作一个关于史蒂芬库里的演讲html，里面要包含他的比赛图片，要求精美",
        image_delivery_mode="artifact_only",
    )[0]
    await tool.execute({"query": "斯蒂芬库里 比赛 图片 高清"})

    assert "面要包" not in str(seen["image_query"] or "")
    assert [item["url"] for item in sink] == ["https://img.example.com/curry.jpg"]


@pytest.mark.asyncio
async def test_model_query_cannot_enable_chat_images_without_user_request(monkeypatch):
    """模型自行在 query 中加「图片」不构成用户授权，防止普通问答随机带图。"""
    seen = {}

    async def _search(q, start_index=1, with_images=False, **_kw):
        seen["with_images"] = with_images
        return _ok_payload(images=[{
            "url": "https://cdn.example.com/random.jpg",
            "title": "随机缩略图",
            "source": "https://example.com/random",
        }])

    monkeypatch.setattr(web_search_service, "search_web", _search)
    sink: list = []
    tool = build_web_tools(image_sink=sink, user_message="帮我看看漳州天气")[0]
    obs = await tool.execute({"query": "漳州天气 图片"})
    assert seen["with_images"] is False
    assert sink == []
    assert not getattr(obs, "ui", {}).get("images")
