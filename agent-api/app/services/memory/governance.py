"""Deterministic memory identity, evidence and sensitive-value checks."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


_VALUE_PATTERNS = (
    r"(?<!\d)1[3-9]\d{9}(?!\d)",
    r"(?<!\d)\+\d[\d ()-]{7,}\d(?!\d)",
    r"(?<!\d)\d{17}[\dXx](?!\w)",
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}",
    r"(?i)(?:password|passwd|api[_ -]?key|access[_ -]?token|secret|密码|口令|授权码|验证码|密钥)\s*(?:是|为|[:：=])\s*[^\s，。；;]{4,}",
    r"(?:银行卡号|银行账号|身份证号|护照号|学号|工号|家庭住址|家庭地址|手机号码?|联系电话)\s*(?:是|为|[:：=])?\s*[A-Za-z0-9\u4e00-\u9fff-]{5,}",
)
_PERSONAL_ATTRIBUTES = (
    r"(?:我|用户|本人|学生|老师|教师|家人|父亲|母亲|他|她|[\u4e00-\u9fff]{2,4}同学).{0,16}"
    r"(?:患有|确诊|患病|(?:有|患|得了)(?:抑郁症|糖尿病)|病史为|病史是|治疗中|服用.{0,8}药|宗教信仰|信奉|政治面貌|党员|党派|(?:工资|薪资|月薪|年薪)\s*(?:是|为|[:：=]|\d)|收入为|收入是|存款为|负债|银行余额)",
    r"(?i)\b(?:i|user|student|teacher|he|she)\b.{0,30}\b(?:diagnosed|suffers from|salary|religion|political affiliation|medical history)\b",
)


def sensitive_reason(content: str) -> str | None:
    text = unicodedata.normalize("NFKC", str(content or ""))
    if any(re.search(pattern, text) for pattern in _VALUE_PATTERNS):
        return "sensitive_value"
    if any(re.search(pattern, text) for pattern in _PERSONAL_ATTRIBUTES):
        return "personal_sensitive_attribute"
    return None


def memory_identity(value: Any) -> tuple[str, str, str] | None:
    """A single-valued fact slot. Missing legacy identities never match by inference."""
    if not isinstance(value, dict):
        return None
    identity = value.get("identity")
    if not isinstance(identity, dict):
        return None
    parts = []
    for field in ("subject", "scope", "attribute"):
        item = identity.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > 160:
            return None
        parts.append(unicodedata.normalize("NFKC", item).strip().casefold())
    return tuple(parts)


def grounded_quote(quote: Any, source: str) -> str:
    """Only caller-supplied user text can substantiate a model-proposed memory."""
    if not isinstance(quote, str) or not 2 <= len(quote.strip()) <= 1_000:
        return ""
    quote = quote.strip()
    return quote if quote in source and not sensitive_reason(quote) else ""


IDENTITY_INSTRUCTIONS = (
    "可选 identity={subject,scope,attribute} 描述单值事实的对象、适用范围和属性，"
    "例如 subject=用户、scope=全局、attribute=回答语言；课程/班级/项目必须写入具体 scope。"
    "identity 各字段不得携带敏感信息。只能依据用户原话填写，无法确定就省略；"
    "不得把不同对象或范围的相似要求视为同一事实。"
)
