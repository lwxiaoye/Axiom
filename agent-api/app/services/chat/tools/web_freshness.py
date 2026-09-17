"""search_web 时效可用闸（结构信号，非领域词表）。

- 用户问今天/实时：过期数值不得锁死二次搜索
- 结果是跨年旧档、且用户未锚定过去年份：同样不得锁死（避免「去年同日」冒充可用）
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.knowledge import web_search_service

_TEMPORAL_FRESH_RE = re.compile(
    r"今天|今日|实时|最新|现在|当前|此刻|today|tonight|now",
    re.I,
)
_STALE_MAX_AGE_DAYS = 2


def local_now() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # noqa: BLE001
        return datetime.now()


def query_wants_fresh(*parts: str) -> bool:
    blob = " ".join(str(p or "") for p in parts)
    return bool(blob and _TEMPORAL_FRESH_RE.search(blob))


def extract_result_dates(blob: str, *, today: date) -> list[date]:
    text_blob = str(blob or "")
    out: list[date] = []
    seen: set[date] = set()

    def _push(y: int, mo: int, d: int) -> None:
        try:
            dt = date(int(y), int(mo), int(d))
        except ValueError:
            return
        if dt in seen:
            return
        seen.add(dt)
        out.append(dt)

    year_hits = 0
    for m in re.finditer(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})", text_blob):
        _push(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        year_hits += 1
    for m in re.finditer(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text_blob):
        _push(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        year_hits += 1
    # 已有带年份日期时，不再把同一串里的「M月D日」按今年重解——否则
    # 「2025年8月6日」会同时抽出 2025-08-06 和 2026-08-06，跨年旧档被误判为新鲜。
    if year_hits == 0:
        for m in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text_blob):
            mo, d = int(m.group(1)), int(m.group(2))
            y = today.year
            try:
                cand = date(y, mo, d)
            except ValueError:
                continue
            if cand > today + timedelta(days=45):
                try:
                    cand = date(y - 1, mo, d)
                except ValueError:
                    continue
            _push(cand.year, cand.month, cand.day)
    return out


def _collect_result_dates(results: list, *, today: date) -> list[date]:
    dates: list[date] = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        blob = " ".join(
            str(r.get(k) or "")
            for k in ("publishedDate", "title", "content", "url")
        )
        dates.extend(extract_result_dates(blob, today=today))
    return dates


def _result_dates(result: dict, *, today: date) -> list[date]:
    if not isinstance(result, dict):
        return []
    blob = " ".join(
        str(result.get(k) or "")
        for k in ("publishedDate", "title", "content", "url")
    )
    return extract_result_dates(blob, today=today)


def _row_has_concrete_signal(result: dict) -> bool:
    """单条摘要是否带可核验数值（与 structurally_usable 同源信号，非领域词表）。"""
    if not isinstance(result, dict):
        return False
    c = str(result.get("content") or "").strip()
    if not c:
        return False
    unit_re = re.compile(
        r"[0-9]+[ ]*(?:℃|°C|%|mm|hPa|km(?:/h)?|m/s|级|万|亿|元|美元|USD|¥)"
    )
    range_re = re.compile(
        r"(?<![0-9])[0-9]{1,3}[ ]*[~/～-][ ]*[0-9]{1,3}(?![0-9])"
    )
    return bool(unit_re.search(c) or range_re.search(c))


def _row_is_date_stale(result: dict, *, today: date) -> bool:
    dates = _result_dates(result, today=today)
    if not dates:
        return False  # 无明确日期 ≠ 过期；常见于预报卡/聚合摘要
    return (today - max(dates)).days > _STALE_MAX_AGE_DAYS


def has_fresh_enough_concrete(results: list, *, now: datetime | None = None) -> bool:
    """是否存在「带可核验数值且未被明确过期日期钉死」的摘要。

    关键根因：整组 results 里只要混进一条旧新闻日期，旧逻辑用 max(dates)
    会把同组无日期的 26–35℃ 预报也判 stale，模型被提示拒答并二次空搜。
    """
    now = now or local_now()
    today = now.date() if hasattr(now, "date") else now
    for r in results or []:
        if not isinstance(r, dict):
            continue
        if not _row_has_concrete_signal(r):
            continue
        if not _row_is_date_stale(r, today=today):
            return True
    return False


def results_look_stale(results: list, *, now: datetime | None = None) -> bool:
    """整组是否应视为过期。

    有「新鲜足够的 concrete 行」时整组不算 stale。
    否则回退：若能解析到日期且最新日期超窗，则 stale。
    """
    now = now or local_now()
    today = now.date() if hasattr(now, "date") else now
    if has_fresh_enough_concrete(results, now=now):
        return False
    dates = _collect_result_dates(results, today=today)
    if not dates:
        return False
    newest = max(dates)
    return (today - newest).days > _STALE_MAX_AGE_DAYS


def results_are_cross_year_archive(results: list, *, now: datetime | None = None) -> bool:
    """最新可解析日期的年份严格小于当前年 → 跨年旧档。"""
    now = now or local_now()
    today = now.date() if hasattr(now, "date") else now
    dates = _collect_result_dates(results, today=today)
    if not dates:
        return False
    return max(dates).year < today.year


def query_mentions_past_year(*parts: str, now: datetime | None = None) -> bool:
    """用户/query 是否明确锚定了过去年份（历史问答应允许旧结果）。"""
    now = now or local_now()
    blob = " ".join(str(p or "") for p in parts)
    years = [int(y) for y in re.findall(r"20\d{2}", blob)]
    return any(y < now.year for y in years)


def results_should_not_lock_budget(
    results: list,
    *,
    query: str = "",
    user_message: str = "",
    now: datetime | None = None,
) -> bool:
    """True = 虽有内容，但时效不足，不得 had_results 锁二次搜索。"""
    now = now or local_now()
    if not results_look_stale(results, now=now):
        return False
    if query_wants_fresh(query, user_message):
        return True
    if results_are_cross_year_archive(results, now=now) and not query_mentions_past_year(
        query, user_message, now=now
    ):
        return True
    return False


def search_results_structurally_usable(results: list) -> bool:
    """有可核验细节才算可用。多条入口/黄历壳的总字数不得冒充可用。"""
    rows = [r for r in (results or []) if isinstance(r, dict)]
    if not rows:
        return False
    contents = [str(r.get("content") or "").strip() for r in rows]
    contents = [c for c in contents if c]
    total = sum(len(c) for c in contents)
    if not contents:
        return False
    concrete = 0
    digit_long = 0
    # 用 [0-9] / [ ]：避免执行通道吞 \d \s 导致可用性闸静默失效。
    unit_re = re.compile(
        r"[0-9]+[ ]*(?:℃|°C|%|mm|hPa|km(?:/h)?|m/s|级|万|亿|元|美元|USD|¥)"
    )
    range_re = re.compile(
        r"(?<![0-9])[0-9]{1,3}[ ]*[~/～-][ ]*[0-9]{1,3}(?![0-9])"
    )
    digit_re = re.compile(r"[0-9]{2,}")
    for c in contents:
        if unit_re.search(c):
            concrete += 2
            continue
        if range_re.search(c):
            concrete += 2
            continue
        if len(c) >= 120 and digit_re.search(c):
            digit_long += 1
            concrete += 1
    if concrete >= 1:
        return True
    if digit_long >= 2 and total >= 320:
        return True
    return False


def search_results_usable(
    results: list,
    *,
    query: str = "",
    user_message: str = "",
    now: datetime | None = None,
) -> bool:
    if not search_results_structurally_usable(results):
        return False
    # 有无过期钉死的 concrete 行 → 直接可用（即使同组混有旧闻）
    if has_fresh_enough_concrete(results, now=now):
        return True
    if results_should_not_lock_budget(
        results, query=query, user_message=user_message, now=now
    ):
        return False
    return True


def with_today_date_token(query: str, *, now: datetime | None = None) -> str:
    """时效查询不再往 query 塞「今天/日历日期」。

    实测中文 SERP：短主题词（如「漳州 天气」）更易出带 ℃ 的实时摘要；
    塞「今天」「8月6日」「2026年8月6日」常把黄历/往年/月均温顶上来。
    结果侧靠 concrete 排序 + stale/usable 闸处理过期内容。
    """
    return str(query or "").strip()

# 模型常把礼貌/时效填充词整段塞进 query。
# SERP 质量会断崖。这里做结构压紧，不维护领域词表：去掉填充，保留 2-6 个主题词。
_POLITE_FILLERS = (
    "请帮我", "麻烦你", "能不能", "可以吗", "请问", "帮我", "麻烦",
    "告诉我", "说一下", "看一下", "查一下", "查询一下", "查询",
    "看看", "了解一下", "介绍一下", "分析一下",
)
_TEMPORAL_FILLERS_RE = re.compile(
    r"今天|今日|实时|最新|现在|当前|此刻|实况|预报|天气状况|状况|情况|"
    r"today|tonight|now|latest|current|\blive\b|forecast",
    re.I,
)
_DATE_FILLERS_RE = re.compile(
    r"20[0-9]{2}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日?"
    r"|[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日"
    r"|20[0-9]{2}[-/.][0-9]{1,2}[-/.][0-9]{1,2}"
    r"|20[0-9]{2}\s*年"
)
_NOISE_PARTICLES_RE = re.compile(
    r"[的地得了着过吗呢吧啊呀嘛]|[，,。.!！？?；;：:\"'“”‘’（）()【】\[\]<>《》/\\|]+"
)


def compact_search_query(query: str, *, max_tokens: int = 6) -> str:
    """把过长/过礼貌/过时效的搜索 query 压成短主题词（结构规则，非领域词表）。"""
    q = str(query or "").strip()
    if not q:
        return q
    if len(q) <= 12 and not _DATE_FILLERS_RE.search(q) and not _TEMPORAL_FILLERS_RE.search(q):
        return q

    s = q
    changed = True
    while changed and s:
        changed = False
        for filler in sorted(_POLITE_FILLERS, key=len, reverse=True):
            if s.startswith(filler):
                s = s[len(filler):].lstrip(" ，,。")
                changed = True
                break
            if filler in s and len(s) > 16:
                s2 = s.replace(filler, " ")
                if s2 != s:
                    s = s2
                    changed = True
    s = _DATE_FILLERS_RE.sub(" ", s)
    s = _TEMPORAL_FILLERS_RE.sub(" ", s)
    s = _NOISE_PARTICLES_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()

    tokens = []
    for m in re.finditer(r"[一-鿿]{2,}|[A-Za-z][A-Za-z0-9_\-]{1,}|[0-9]{2,}", s):
        tok = m.group(0).strip()
        if not tok:
            continue
        if _TEMPORAL_FILLERS_RE.fullmatch(tok):
            continue
        if tok in {"一下", "什么", "怎么", "如何", "多少", "哪些", "这个", "那个"}:
            continue
        if tok not in tokens:
            tokens.append(tok)
        if len(tokens) >= max(2, int(max_tokens)):
            break

    if not tokens:
        fallback = re.sub(r"\s+", " ", s).strip() or q
        return fallback[:24]
    return " ".join(tokens[: max(2, int(max_tokens))])
