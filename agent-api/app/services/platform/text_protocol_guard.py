"""模型正文里的「文本工具协议」标记检测与清洗（源头层防线）。

背景：deepseek 系模型（经统一 new-api 网关、OpenAI 协议）偶发把内部文本工具协议
当正文吐出来，例如 ``<|DSML|tool_calls>…`` 或 ``<｜tool▁calls▁begin｜>…`` 这类
special token 后面跟着 JSON 参数。这些内容既不该下发给前端，也不该入库。

语义决策（本迭代）：命中任一标记即认为模型在文本层做工具调用——正文从标记处
截断，标记及其后的所有内容（JSON 参数等内部协议）一律丢弃；**不尝试解析执行**
（那是后续迭代的事）。

用法：
- 流式：每次 LLM 调用建一个 :class:`StreamingProtocolScrubber`，每帧 content 过
  ``feed()``，流结束调 ``flush()`` 补发残余，然后检查 ``leaked``/``marker``。
  分帧会把标记切碎（如 ``<|DS`` + ``ML|tool_calls>``），feed 内部带跨帧缓冲。
- 非流式：``scrub_text(text) -> (safe_text, leaked)`` 一次性版。
"""

from __future__ import annotations

import logging
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 标记家族（易扩展：新家族直接往列表里加字符串即可）
# ---------------------------------------------------------------------------

# DeepSeek 工具 special token 的「基名」：自动展开 半角|/全角｜ × 普通_/▁下划线 变体
_DEEPSEEK_TOKEN_NAMES: Tuple[str, ...] = (
    "tool_calls_begin",
    "tool_call_begin",
    "tool_calls_end",
    "tool_call_end",
    "tool_sep",
    "tool_calls",  # 兼容 <|tool_calls|> 单标记形态
)


# 竖线包裹变体：模型半角 `|` / 全角 `｜` 都吐过，且**竖线数量不稳定**——单竖线
# `<｜DSML｜` 与双竖线 `<｜｜DSML｜｜` 都真机出现过（后者曾漏网，2026-07-21 用户反馈）。
# 统一展开 1~2 个竖线 × 半/全角，前后对称包裹，覆盖两种数量。
def _pipe_wrap(inner: str, *, closed: bool) -> List[str]:
    out: List[str] = []
    for pipe in ("|", "｜"):
        for run in (pipe, pipe * 2):
            out.append(f"<{run}{inner}{run}>" if closed else f"<{run}{inner}{run}")
    return out


def _expand_deepseek_variants() -> List[str]:
    out: List[str] = []
    for name in _DEEPSEEK_TOKEN_NAMES:
        for body in (name, name.replace("_", "▁")):
            out.extend(_pipe_wrap(body, closed=True))
    return out


# 命中任一标记 => 正文从该处截断。注意 DSML 只匹配到开头 "<|DSML|" 即算命中
# （后面跟什么标签名都不重要，反正整段丢弃）；单/双竖线两种数量都收。
PROTOCOL_MARKERS: Tuple[str, ...] = tuple(dict.fromkeys(
    [
        # DeepSeek DSML 文本协议（半/全角 × 单/双竖线，尾部标签名不定故不带闭合尖括号）
        *_pipe_wrap("DSML", closed=False),
        # DeepSeek 工具调用 special tokens（半角/全角 × _/▁ × 单/双竖线 全组合）
        *_expand_deepseek_variants(),
        # 通用 XML 风格文本工具协议（Qwen/Hermes 等家族也用这对标签）
        "<tool_call>",
        "</tool_call>",
        # 部分模型把具体工具名当 XML 标签（如 <update_plan>{...}</update_plan>）
        "<update_plan>",
        "</update_plan>",
        "<function_call>",
        "</function_call>",
    ]
))

# 最长标记长度：跨帧缓冲上限的依据（缓冲永远 < 该值，防内存积压/流式卡顿）
_MAX_MARKER_LEN: int = max(len(m) for m in PROTOCOL_MARKERS)

# 命中后为日志留存的「泄漏样本」上限
_SAMPLE_CAP: int = 200
# 恢复用完整泄漏缓冲（任务计划 JSON 常远超 200 字）
_RECOVERY_CAP: int = 50000


def find_first_marker(text: str) -> Optional[Tuple[int, str]]:
    """返回 text 中最靠前的协议标记 (索引, 标记)；无命中返回 None。

    供最终兜底闸（入库前整段检查）等一次性场景复用。
    """
    best_idx, best_marker = -1, None
    for marker in PROTOCOL_MARKERS:
        idx = text.find(marker)
        if idx != -1 and (best_idx == -1 or idx < best_idx):
            best_idx, best_marker = idx, marker
    if best_marker is None:
        return None
    return best_idx, best_marker


class StreamingProtocolScrubber:
    """流式正文清洗器：每次 LLM 调用一个实例（有状态，不可复用）。

    - ``feed(chunk)`` 返回可安全下发的文本。内部只缓冲「可能是标记前缀」的尾部
      （长度 < 最长标记），其余文本立刻放行，不会造成流式卡顿。
    - 命中标记后 ``leaked=True``、``marker`` 记录命中的标记、``dropped_sample``
      留存被丢弃内容的前 200 字符（日志用）；之后的 feed 一律返回 ""。
    - ``flush()`` 冲出残余安全缓冲（流结束时那段「像前缀但没凑成标记」的文本）。
    """

    def __init__(self) -> None:
        self._buf: str = ""
        self.leaked: bool = False
        self.marker: Optional[str] = None
        self.dropped_sample: str = ""
        self.dropped_full: str = ""

    def _record_dropped(self, text: str) -> None:
        if len(self.dropped_sample) < _SAMPLE_CAP:
            self.dropped_sample += text[: _SAMPLE_CAP - len(self.dropped_sample)]
        if len(self.dropped_full) < _RECOVERY_CAP:
            self.dropped_full += text[: _RECOVERY_CAP - len(self.dropped_full)]

    def feed(self, chunk: str) -> str:
        if not isinstance(chunk, str):
            chunk = str(chunk)
        if self.leaked:
            self._record_dropped(chunk)
            return ""
        data = self._buf + chunk
        hit = find_first_marker(data)
        if hit is not None:
            idx, marker = hit
            self.leaked = True
            self.marker = marker
            self._record_dropped(data[idx:])
            self._buf = ""
            return data[:idx]
        # 无完整标记：只留「可能是某个标记前缀」的最长尾部，其余立刻放行
        max_tail = min(len(data), _MAX_MARKER_LEN - 1)
        for k in range(max_tail, 0, -1):
            tail = data[-k:]
            if any(m.startswith(tail) for m in PROTOCOL_MARKERS):
                self._buf = tail
                return data[:-k]
        self._buf = ""
        return data

    def flush(self) -> str:
        """流结束：残余缓冲已确定凑不成标记，安全放行。命中过标记则无可冲出。"""
        if self.leaked:
            self._buf = ""
            return ""
        out, self._buf = self._buf, ""
        return out


def scrub_text(text: str) -> Tuple[str, bool]:
    """非流式一次性版：返回 (安全正文, 是否命中协议标记)。

    命中时安全正文=标记前的部分（可能为空串）；未命中原样返回。
    """
    scrubber = StreamingProtocolScrubber()
    out = scrubber.feed(text or "") + scrubber.flush()
    return out, scrubber.leaked

# Re-export recovery helpers (implementation in text_protocol_recover.py)
from app.services.platform.text_protocol_recover import (  # noqa: E402
    looks_like_text_tool_payload,
    recover_text_tool_calls,
)

__all__ = [
    "PROTOCOL_MARKERS",
    "StreamingProtocolScrubber",
    "find_first_marker",
    "scrub_text",
    "looks_like_text_tool_payload",
    "recover_text_tool_calls",
]
