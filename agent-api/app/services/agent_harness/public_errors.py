"""Public-safe Run failure messages.

Raw provider and worker exceptions belong in server-side audit fields. RunState is returned by the
public snapshot API, so its terminal reason must always be safe and actionable.
"""
from __future__ import annotations


GENERIC_RUN_FAILURE = "本次回复未能完成。你的问题已保留，请稍后重试。"
RUN_ACCEPT_FAILURE = "任务受理失败，请稍后重试。"
WORKER_INPUT_MISSING = "任务恢复所需的运行信息缺失，请重新发起任务。"
WORKER_TERMINAL_MISSING = "任务执行未能正常收尾，请重新发起任务。"
SENSITIVE_WORDS_REJECTION_MESSAGE = (
    "请求触发了网关敏感词策略，模型未生成内容，本轮已停止。"
    "请调整输入后重试；如认为是误判，请联系管理员检查敏感词配置。"
)


class TerminalRunError(RuntimeError):
    """A deterministic rejection that must end this Run instead of entering recovery."""

    public_message = GENERIC_RUN_FAILURE
    exclude_run_messages_from_future_context = False


class ConfigurationRunError(TerminalRunError):
    """平台配置/目录缺失导致的**确定性**失败——同一检查点重放多少次结果都一样，不进恢复。

    判定依据（2026-09-18）：`_pump_background_run` 只把 TerminalRunError 当终态，其余异常一律
    `recover_run_after_error` → waiting_system → worker 一秒后重排 → 再撞同一个错，用户端
    只看到「等待任务恢复...」且永远看不到原因（演示文稿助手因 ppt-studio 不在目录里就是
    这样死的）。「瞬时错误」（DB/模型抖动、租约丢失、进程重启）继续走恢复；「配置性错误」
    的特征是**换个时间重放不会自愈**：技能不在目录、说明书为空、助手依赖的能力未启用。
    这类错误在抛出点就用本类型标明，把原因原样交给用户，而不是靠正则去猜消息文案。

    public_message 就是原因本身：它是面向用户写的中文说明（「演示文稿助手暂不可用：…」），
    不含栈、不含内部路径。
    """

    def __init__(self, public_message: str):
        text = str(public_message or "").strip()
        self.public_message = text or GENERIC_RUN_FAILURE
        super().__init__(self.public_message)


class ModelResponseContractError(TerminalRunError):
    """The model returned a control action in a tool-free report phase."""

    public_message = (
        "模型未能按本轮要求完成报告，已有研究资料已保留，本轮已结束。"
        "请重试或切换模型。"
    )


class ModelRequestRejected(TerminalRunError):
    """An invalid provider request cannot be repaired by replaying the checkpoint."""

    public_message = (
        "模型未能接受本轮请求，本轮已结束。问题与已有资料已保留；"
        "请重试或切换模型，若仍失败请联系管理员。"
    )
    statuses = frozenset({400, 401, 402, 403, 404, 405, 415, 422})

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        if status_code == 401:
            self.public_message = (
                "模型服务鉴权失败，本轮已结束。你的问题与已有资料已保留；"
                "请联系管理员检查模型密钥，或切换模型后重试。"
            )
        elif status_code == 402 or (status_code == 403 and any(marker in str(body or "").lower() for marker in (
            "insufficient_quota", "insufficient balance", "free quota exhausted", "use free tier only",
        ))):
            self.public_message = (
                "模型额度不足或免费配额已用完，本轮已结束。你的问题与已有资料已保留；"
                "请联系管理员处理额度，或切换模型后重试。"
            )
        elif status_code == 403:
            self.public_message = (
                "模型服务拒绝了本次访问，本轮已结束。你的问题与已有资料已保留；"
                "请联系管理员检查模型访问权限，或切换模型后重试。"
            )
        super().__init__(self.public_message)


class ResearchReportRejected(TerminalRunError):
    """A persisted citation rejection cannot be repaired by replaying the same Run."""

    public_message = "完成验证未通过：研究报告的来源引用未通过核验，已有研究资料已保留。本轮已结束，请重新发起研究。"


class ResearchSourcesUnavailable(TerminalRunError):
    """Bounded collection could not recover an unavailable search dependency."""

    public_message = (
        "联网检索暂时不可用，本轮未能取得可核验的来源，因此没有生成研究报告。"
        "问题与检索记录已保留；这不代表相关信息不存在。请稍后重试。"
    )


class ResearchEvidenceMissing(TerminalRunError):
    public_message = (
        "本轮检索没有获得足以核验报告的来源，因此没有生成研究报告。"
        "这不能证明研究对象不存在。问题与检索记录已保留，可补充官方链接或更明确的名称后重试。"
    )


def public_run_error(error: BaseException | str) -> str:
    """Translate internal/provider failures without exposing implementation details."""
    explicit = str(getattr(error, "public_message", "") or "").strip()
    if explicit:
        return explicit
    status = getattr(error, "status_code", None)
    message = str(error or "")
    lowered = message.lower()
    if (
        status in {402, 403}
        or "insufficient_quota" in lowered
        or "insufficient balance" in lowered
        or "free quota exhausted" in lowered
        or "use free tier only" in lowered
    ):
        return "模型额度不足或免费配额已用完。你的问题已保留；请联系管理员充值/关闭仅免费模式，或切换可用模型后重试。"
    if status == 503 or "service_unavailable" in lowered:
        return "模型服务当前繁忙，已自动重试但仍未成功。你的问题已保留，请稍后重试或切换模型。"
    if status == 429 or "rate limit" in lowered:
        return "模型服务请求过于频繁，请稍后重试或切换模型。"
    return GENERIC_RUN_FAILURE


def public_terminal_reason(reason: object, *, phase: str) -> str | None:
    """Sanitize historical RunState values at the public snapshot boundary."""
    if phase == "cancelled":
        return "已停止生成"
    if phase != "failed":
        return None
    text = str(reason or "").strip()
    if not text:
        return None
    public_prefixes = (
        "本次回复未能完成",
        "模型额度不足",
        "模型服务当前繁忙",
        "模型服务请求过于频繁",
        "任务受理失败",
        "任务恢复所需的运行信息缺失",
        "任务执行未能正常收尾",
        "运行进程已断开",
        "完成验证未通过",
        "请求触发了网关敏感词策略",
        "联网检索暂时不可用",
        "本轮检索没有获得足以核验报告的来源",
        "模型未能按本轮要求完成报告",
        "模型未能接受本轮请求",
        "模型服务鉴权失败",
        "模型服务拒绝了本次访问",
        # ConfigurationRunError 的原因文案（演示文稿助手依赖的 ppt-studio 不在目录/说明书为空）
        "演示文稿助手暂不可用",
    )
    if text.startswith(public_prefixes):
        return text
    return public_run_error(text)
