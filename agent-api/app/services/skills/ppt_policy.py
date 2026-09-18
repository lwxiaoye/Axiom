"""PPT 产物意图与专用 Skill 的确定性路由。

**边界（2026-07-27 用户拍板）**：本模块只做**路由** —— 判断"这是不是一个 PPT 产物请求"、
把请求接到某个 PPT 技能上。它**不知道、也不该知道**技能内部怎么生产 PPT。

原先这里还有一个 `ppt_pipeline_violation()`：靠正则匹配 `skills/*/svg_to_pptx.py`、
`build_deck.py` 来判定"有没有走技能管线"。那是平台自研 ppt-html 的内部文件名，用户换成
第三方技能（ppt-studio）后这套判据既拦不住该拦的、又会拦住照 SKILL.md 照做的代码。
「别绕开技能、别退回裸 python-pptx」这条约束改由提示词以**行为**表述，不点名任何脚本。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


_PPT_NOUN_RE = re.compile(r"(?:pptx?|power\s*point|slides?|幻灯片|演示文稿|演示稿|课件)", re.I)
_PPT_ACTION_RE = re.compile(
    r"(?:生成|制作|创建|做|写|导出|转换|改|修改|编辑|优化|美化|精修|重做|润色|更新|替换|设计)",
    re.I,
)
_PPT_QUESTION_RE = re.compile(r"^(?:为什么|什么是|如何评价|怎么理解|能否介绍|介绍一下)", re.I)
# 「这份 ppt 做的怎么样」含「做」会被动作正则误伤成制作请求。评价/观感应走看图，不进制作管线。
_PPT_REVIEW_RE = re.compile(
    r"(?:怎么样|怎样|好不好|如何评价|怎么评价|评分|打分|点评|观感|"
    r"看起来怎么样|质量如何|看看这份|看看这个|帮我看看|帮我看一下)",
    re.I,
)
# 往回看几条用户消息判断 PPT 语境（2026-07-27）。3 条 ≈ 一轮澄清问答的跨度
# （做 PPT 前先问清主题/页数/受众是我们自己要求的行为，答完往往就该干活了）。
# 放太大会让「上午做过 PPT，下午问别的」也带上技能；放成 1 挡不住多问一句的场景。
_PPT_CONTEXT_LOOKBACK = 3
_NO_PPT_IMAGE_RE = re.compile(
    r"(?:不要|不需要|无需|禁止|不用).{0,8}(?:照片|图片|影像|配图)|"
    r"(?:纯|仅).{0,5}(?:文字|图形|形状|数据图表)|"
    r"\b(?:no|without)\s+(?:photos?|images?|photography)\b",
    re.I,
)
_PPT_PHOTO_RE = re.compile(
    r"(?:真实|高清|相关|比赛|赛事|赛场|现场|实拍|官方|人物|产品|风景|建筑)"
    r".{0,8}(?:照片|摄影|图片|影像)|"
    r"(?:照片|摄影|实拍|比赛图片|赛事图片|赛场图片)|"
    r"\b(?:real|game|event|action|official)\s+(?:photos?|photographs?|images?)\b",
    re.I,
)
_DISTINCT_PHOTO_SOURCES_RE = re.compile(
    r"(?:不同|多个|多种).{0,8}(?:来源|网站|媒体)|"
    r"(?:来自|源自).{0,20}(?:不同|多个|多种).{0,8}(?:来源|网站|媒体)|"
    r"\bdifferent\s+(?:sources?|sites?|publishers?)\b",
    re.I,
)
_CN_IMAGE_COUNTS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
_PPT_SKILL_ALIASES = {
    "ppt", "pptx", "pptskill", "pptxskill", "powerpoint", "powerpointskill",
    "ppt技能", "pptx技能", "ppt大师", "pptx大师",
    # 2026-07-22 真机实证的模型臆造名：改稿场景会猜 ppt-editor 之类，收进别名让它
    # 落到真实 PPT 技能而不是收到「未找到」再自行摸索
    "ppteditor", "ppt编辑", "ppt编辑器", "pptmaker", "ppt生成", "ppt生成器",
}


def _normalized_skill_alias(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def is_ppt_skill_alias(value: str) -> bool:
    """识别模型常臆造的 PPT Skill 别名，不把任意含 ppt 的长文本当成 ID。"""
    return _normalized_skill_alias(value) in _PPT_SKILL_ALIASES


def is_document_review_request(
    message: str,
    attachment_names: Optional[Iterable[str]] = None,
) -> bool:
    """True when the user wants a look/score, not a new or rewritten deck."""
    text = str(message or "").strip()
    if not text or not _PPT_REVIEW_RE.search(text):
        return False
    names = " ".join(str(x or "") for x in (attachment_names or []))
    has_doc = bool(re.search(r"\.(?:pptx?|pdf|docx?)\b", names, re.I))
    has_noun = bool(_PPT_NOUN_RE.search(text) or re.search(r"(?:pdf|文档|文件|附件)", text, re.I))
    return has_doc or has_noun


def is_ppt_artifact_request(message: str, attachment_names: Optional[Iterable[str]] = None) -> bool:
    """只识别需要创建/修改 PPT 的请求，避免把关于 PPT 的普通咨询误路由成产物任务。"""
    text = str(message or "").strip()
    names = " ".join(str(x or "") for x in (attachment_names or []))
    if is_document_review_request(text, attachment_names):
        return False
    attached_ppt = bool(re.search(r"\.pptx?\b", names, re.I))
    if attached_ppt and _PPT_ACTION_RE.search(text):
        return True
    if not (_PPT_NOUN_RE.search(text) and _PPT_ACTION_RE.search(text)):
        return False
    return not bool(_PPT_QUESTION_RE.search(text))


def requested_ppt_image_count(text: str) -> int:
    """Return the user's minimum photographic-image count, capped for QA.

    ``几张``/``several`` are deliberately normalized to three.  Leaving those
    phrases as zero previously let one arbitrary JPEG satisfy an explicit
    request for multiple match photos.
    """
    value = str(text or "")
    explicit = re.search(
        r"(?:至少|不少于)\s*([0-9]{1,2}|[一二两三四五六七八九十])\s*(?:张|幅|个)?"
        r"(?=[^。！？\n]{0,80}(?:照片|图片|影像|配图))|"
        r"(?:at\s+least|minimum\s+of)\s*([0-9]{1,2})\s*(?:photos?|images?|pictures?)",
        value,
        re.I,
    )
    if explicit:
        raw = str(explicit.group(1) or explicit.group(2) or "")
        count = int(raw) if raw.isdigit() else int(_CN_IMAGE_COUNTS.get(raw) or 0)
        return min(12, max(0, count))

    general = re.search(
        r"([0-9]{1,2}|[一二两三四五六七八九十几])\s*张"
        r"(?=[^。！？\n]{0,80}(?:照片|摄影|实拍|比赛图片|赛事图片|赛场图片))|"
        r"\b([0-9]{1,2})\s+(?:real\s+|game\s+|event\s+|action\s+)?"
        r"(?:photos?|photographs?|images?)\b",
        value,
        re.I,
    )
    if general:
        raw = str(general.group(1) or general.group(2) or "")
        if raw == "几":
            return 3
        count = int(raw) if raw.isdigit() else int(_CN_IMAGE_COUNTS.get(raw) or 0)
        return min(12, max(0, count))
    if re.search(r"(?:几张|多张).{0,10}(?:照片|摄影|实拍|比赛图片|赛事图片|赛场图片)", value):
        return 3
    if re.search(r"\b(?:a\s+few|several)\s+(?:photos?|photographs?|images?)\b", value, re.I):
        return 3
    return 0


def ppt_photo_requirement(message: str) -> Optional[dict]:
    """Return an explicit, immutable PPT photo requirement or ``None``.

    This is intentionally a tri-state contract.  ``None`` means the user did
    not express a preference and the Skill may decide; ``mode=none`` is an
    explicit opt-out; ``mode=searched_photos`` requires verifiable external
    photographic assets rather than files that merely have a JPEG suffix.
    """
    text = str(message or "").strip()
    if not text:
        return None
    if _NO_PPT_IMAGE_RE.search(text):
        return {"mode": "none", "min_images": 0, "min_sources": 0, "brief": ""}
    if not _PPT_PHOTO_RE.search(text):
        return None
    min_images = max(1, requested_ppt_image_count(text))
    return {
        "mode": "searched_photos",
        "min_images": min_images,
        "min_sources": min_images if _DISTINCT_PHOTO_SOURCES_RE.search(text) else 0,
        "brief": text[:400],
    }


def _ppt_skill_score(record: dict) -> int:
    """一条技能记录「像不像 PPT 专用 Skill」的评分（0 = 不是）。"""
    name = str(record.get("name") or "").strip().lower()
    desc = str(record.get("description") or "").lower()
    score = 0
    # ppt studio 是本项目的唯一 PPT 技能（2026-07-27 用户拍板「只需要有 ppt-studio，
    # 其他的 ppt skill 要删掉」）。给它最高分是为了让它在任何组合下都确定性胜出——
    # 广场上万一又混进第二个 PPT 技能，也不会退化成下面 find_ppt_skill_id 的同分歧义。
    if name.replace("-", " ").replace("_", " ") in {"ppt studio", "pptstudio"}:
        score += 200
    if name == "pptx":
        score += 100
    if name in {"ppt大师", "ppt master", "powerpoint", "html ppt 大师", "html ppt大师",
                "ppt skill", "pptskill", "ppt 技能"}:  # 2026-07-22 技能更名 ppt skill
        score += 80
    if "pptx" in name or "powerpoint" in name:
        score += 40
    if "svg" in desc and ("ppt" in desc or "演示" in desc):
        score += 20
    # html-ppt 技能（2026-07-21 上架）：名字含 ppt 也计分，描述带 html+可编辑 pptx 加权
    if "ppt" in name:
        score += 30
    if "html" in desc and "pptx" in desc:
        score += 20
    return score


def _looks_like_ppt_skill(record: dict) -> bool:
    """判断一条技能记录是否「PPT 类」——比 _ppt_skill_score 宽松：名称/描述里出现任一 PPT 名词
    （ppt/pptx/powerpoint/幻灯片/演示文稿/slides/课件…）即算。

    2026-07-24：_ppt_skill_score 只认特定名字/别名，自定义命名的 PPT 技能（如「演示文稿大师」）
    会被判 0 分而漏识别，导致「用户已自己选了 PPT 技能」的守卫失灵、平台默认仍被追加。
    这里改用意图同款名词正则兜底，覆盖任意命名的 PPT 技能。"""
    if not isinstance(record, dict):
        return False
    blob = f"{record.get('name') or ''} {record.get('description') or ''}"
    return bool(_PPT_NOUN_RE.search(blob)) or _ppt_skill_score(record) > 0


def _ppt_skill_ids(records: list) -> set:
    """目录里所有「看起来是 PPT 技能」的 enabled skill_id 集合（名称/描述含 PPT 名词，或评分>0）。"""
    out = set()
    for record in records or []:
        if not isinstance(record, dict) or record.get("enabled") not in (1, True, "1"):
            continue
        sid = str(record.get("skillId") or record.get("id") or "").strip()
        if sid and _looks_like_ppt_skill(record):
            out.add(sid)
    return out


def find_ppt_skill_id(records: list, *, tie_breaks: bool = True) -> Optional[str]:
    """从 auth-api 权威技能目录中选择已启用的 PPT 专用 Skill，不依赖随机生成的 skill_id。

    ``tie_breaks`` 保留给兼容的候选解析调用：同分时可稳定返回一个候选，或在模糊
    ``use_skill`` 解析中返回 None 让模型二选一。它不再驱动 preparation 阶段的静默 Skill
    路由；``effective_ppt_skill_ids`` 只保留显式选择。
    """
    ranked: list[tuple[int, str]] = []
    for record in records or []:
        if not isinstance(record, dict) or record.get("enabled") not in (1, True, "1"):
            continue
        sid = str(record.get("skillId") or record.get("id") or "").strip()
        if not sid:
            continue
        score = _ppt_skill_score(record)
        if score:
            ranked.append((score, sid))
    if not ranked:
        return None
    ranked.sort(reverse=True)
    # 同分时默认保持稳定候选，供兼容调用或确定性候选展示；这不代表 preparation
    # 会加载它。真正加载仍须来自用户显式选择或模型的 use_skill 调用。
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        tied = [sid for score, sid in ranked if score == ranked[0][0]]
        if not tie_breaks:
            return None  # 交给调用方把候选列给模型
        logger.warning(
            "PPT_DIAG 目录里有多个同分 PPT 技能 tied=%s，确定性取 %s（广场上应只保留一个；"
            "同分意味着评分表没能区分它们，需要补判据或下架多余的）",
            tied, ranked[0][1],
        )
    return ranked[0][1] or None


def ppt_intent_in_context(
    message: str,
    attachment_names: Optional[Iterable[str]] = None,
    recent_user_messages: Optional[Iterable[str]] = None,
) -> bool:
    """当前这一轮是否处在「要做 PPT」的语境里——**看最近几轮，不只看当轮**。

    2026-07-27 真机事故：澄清式对话把意图和执行拆到了两轮 ——

        轮1 用户：我想做一份ppt                      → 命中（但这轮助手只是反问，没干活）
        轮2 助手：请问主题是什么？多少页？受众是谁？
        轮3 用户：主题是 Kimi K3 的模型能力宣讲，竞品分析报告，需要10页，受众是…
                  ↑ **真正干活的那轮，文字里没有"PPT"二字**

    该事实仍供执行 profile 和兼容诊断使用，但不再触发 Skill 加载；模型需要时可在目录中
    发现能力并显式调用 ``use_skill``。
    """
    if is_ppt_artifact_request(message, attachment_names):
        return True
    for prior in list(recent_user_messages or [])[-_PPT_CONTEXT_LOOKBACK:]:
        if is_ppt_artifact_request(prior):
            return True
    return False


def effective_ppt_skill_ids(
    message: str,
    explicit_ids: Optional[Iterable[str]],
    records: list,
    attachment_names: Optional[Iterable[str]] = None,
    recent_user_messages: Optional[Iterable[str]] = None,
) -> list[str]:
    """Return only the user's explicitly selected Skill IDs.

    The catalog remains available to the model for capability discovery. An
    unselected PPT Skill is loaded only after the model explicitly calls
    ``use_skill``; preparation must not silently select ``ppt-studio`` or any
    other generator, because third-party Skills own their generation stack.
    """
    result = list(dict.fromkeys(str(x).strip() for x in (explicit_ids or []) if str(x).strip()))
    return result
