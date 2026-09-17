"""时效问句无新鲜 concrete 时，不应仅因「有数值摘要」就 skip scrape。"""

from app.services.chat.tools.web_freshness import (
    has_fresh_enough_concrete,
    query_wants_fresh,
)
from app.services.knowledge.web_search_service import _snippets_already_concrete


def test_query_wants_fresh_structural():
    assert query_wants_fresh("漳州市今天天气怎么样") is True
    assert query_wants_fresh("帮我写一份周报") is False


def test_stale_dated_concrete_not_fresh_enough():
    rows = [
        {
            "title": "漳州天气",
            "content": "漳州气温 26℃～35℃，发布于 2026年7月30日",
            "publishedDate": "2026-07-30",
        }
    ]
    # 有数值，可被 concrete 命中
    assert _snippets_already_concrete(rows, min_hits=1) is True
    # 但日期过旧 → 不算新鲜可用
    assert has_fresh_enough_concrete(rows) is False
    # 门闩：时效问句 + 无新鲜 concrete → 应强制抓取（与 web_search_service 条件一致）
    assert query_wants_fresh("今天漳州天气") and not has_fresh_enough_concrete(rows)


def test_undated_concrete_counts_fresh():
    rows = [{"title": "实况", "content": "漳州今天小雨，气温 26℃～37℃，湿度 70%"}]
    assert _snippets_already_concrete(rows, min_hits=1) is True
    assert has_fresh_enough_concrete(rows) is True
