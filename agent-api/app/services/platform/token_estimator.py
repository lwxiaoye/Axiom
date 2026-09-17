"""CJK 感知 token 估算（Phase 6：替换纯字符近似 len()）。

不引入 tokenizer 依赖（tiktoken 需在运行期联网下载编码表，校园内网不可靠）：
- CJK（中日韩统一表意 + 全角标点）≈ 1 token/字符；
- 其余（ASCII/西文）≈ 1 token / 4 字符。
对中文为主的会话，比 len() 的偏差小一个量级。模型 API 回传真实 usage 时应以真实值为准，
本估算用于发送前的窗口裁剪与上下文用量指示。
"""
from typing import Iterable


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF      # CJK 统一表意
        or 0x3400 <= code <= 0x4DBF   # 扩展 A
        or 0x3000 <= code <= 0x303F   # CJK 标点
        or 0xFF00 <= code <= 0xFFEF   # 全角形式
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return cjk + max(1, other // 4) if other else cjk


def estimate_messages(contents: Iterable[str]) -> int:
    return sum(estimate_tokens(c or "") for c in contents)


# ---- 真值校准（§13）：模型回传 usage.prompt_tokens 时回灌，修正启发式的系统性偏差 ----
# 进程级 per-model EMA(实际/估算)；重启回到 1.0（无持久化——校准是漂移修正，不是精确 tokenizer）。
_CALIB: dict = {}
_CALIB_ALPHA = 0.3           # EMA 步长
_CALIB_CLAMP = (0.5, 3.0)    # 单次样本比值截断，防离群样本打飞因子


def record_usage(model: str, estimated: int, actual: int) -> None:
    """一轮结束后回灌真实 prompt_tokens。estimated 为同轮发送前的估算值。"""
    if not model or estimated <= 0 or actual <= 0:
        return
    ratio = min(_CALIB_CLAMP[1], max(_CALIB_CLAMP[0], actual / estimated))
    prev = _CALIB.get(model)
    _CALIB[model] = ratio if prev is None else prev * (1 - _CALIB_ALPHA) + ratio * _CALIB_ALPHA


def calibration_factor(model: str) -> float:
    """当前模型的校准因子（估算 × 因子 ≈ 真实）；无样本时 1.0。"""
    return _CALIB.get(model or "", 1.0)


# ---- 线程级真实 usage 账本（Claude/pi 式，2026-07-27）----
# 渠道回传的 usage.prompt_tokens 就是「上一次请求的真实上下文体积」，比任何估算都准。
# 按 thread 记最近一次，压缩触发判断用它做**下限锚**（max(估算×因子, 账本值)）：
# 估算系统性偏低时不再漏触发。进程内存、不上 UI、不持久化——重启/多 worker 丢失只是
# 退回纯估算路径，方向安全。压缩/清空会话后必须 reset：旧值反映压缩前体积，
# 不清会「刚压完立刻又触发」（pi 的 stale-usage 守卫同款问题，我们用清账解决）。
_THREAD_PROMPT: dict = {}
_THREAD_PROMPT_CAP = 2000  # 防慢泄漏：超上限时随插入顺序淘汰最旧一半


def record_thread_prompt(thread_id: str, prompt_tokens: int) -> None:
    if not thread_id or prompt_tokens <= 0:
        return
    if len(_THREAD_PROMPT) > _THREAD_PROMPT_CAP:
        for k in list(_THREAD_PROMPT)[: _THREAD_PROMPT_CAP // 2]:
            _THREAD_PROMPT.pop(k, None)
    _THREAD_PROMPT[thread_id] = int(prompt_tokens)


def thread_prompt_floor(thread_id: str) -> int:
    """该会话最近一次真实上下文体积（token）；无记录返回 0。"""
    return int(_THREAD_PROMPT.get(thread_id or "", 0))


def reset_thread_prompt(thread_id: str) -> None:
    _THREAD_PROMPT.pop(thread_id or "", None)
