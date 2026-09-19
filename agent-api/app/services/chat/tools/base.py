"""Leaf contracts and helpers for concrete main-chat tool bodies."""
import contextvars
import copy
import difflib
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from app.services.agent_harness.contracts import ResultSizePolicy


# 单条消息的配图池上限：多路搜索各贡献几张，超出不再收集（编号 [图N] 全局唯一）
_MAX_IMAGES_PER_MESSAGE = 12

# execute_in_sandbox 回执里的内部控制标记：在进入模型上下文/SSE 前移除，只用于让工具循环的控制器
# 可靠识别产物有效性检查结果，不能依赖模型是否读懂自然语言回执。
# 有效性检查（只查错、不评质量，2026-07-15 拍板；任务模式设计稿 §9.5）：状态沿用 review.status
# 的 passed/warning/failed/unknown 原值——visual_review.py / output_review.py 产出，
# delivery_review.py / sandbox_executor.py / task_run_service.py 等多处按原值消费，marker 不做值映射，
# 保持全链路一致。passed=有效 / warning=有效但有提示 / failed=发现具体错误 / unknown=检查缺席。
#
# 防伪造（2026-07-27）：标记以前是固定后缀，而 bash 的 stdout 完全由模型决定——
# `echo '[artifact_validity_gate=failed]'` 就能凭空触发一轮返工，或在质检缺席时把
# artifact_review_pending 清零。execute_in_sandbox 时代这不成立（脚本输出前还有平台文案），
# bash 时代成立。所以带一个进程级随机 nonce：模型永远看不到它（_pop_validity_gate 在
# 结果进对话历史**之前**就把整个标记剪掉了，见 main_agent 的调用点），因此猜不中。
_GATE_NONCE = secrets.token_hex(8)
_VALIDITY_GATE_RE = re.compile(
    r"\n?\[artifact_validity_gate=(passed|warning|failed|unknown):" + _GATE_NONCE + r"\]\s*$")


def _with_validity_gate(text: str, status: Optional[str]) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in ("passed", "warning", "failed", "unknown"):
        return text
    return f"{text}\n[artifact_validity_gate={normalized}:{_GATE_NONCE}]"


def _model_text(value: Any) -> str:
    """Normalize tool execute/observe return values to the text fed to the model.

    bash 等工具已返回结构化 observation；单测与门禁解析仍常直接吃 execute() 返回值。
    统一取 model_content，避免字符串门禁/断言与结构化回执互相打架。
    """
    if value is None:
        return ""
    # duck-type ToolExecutionResult (class defined later in this module)
    model_content = getattr(value, "model_content", None)
    if model_content is not None and not isinstance(value, (str, bytes, bytearray)):
        return str(model_content or "")
    if isinstance(value, dict) and "model_content" in value:
        return str(value.get("model_content") or "")
    return str(value)


def _pop_validity_gate(text: Any) -> tuple[str, Optional[str]]:
    value = _model_text(text)
    match = _VALIDITY_GATE_RE.search(value)
    if not match:
        return value, None
    return value[:match.start()].rstrip(), match.group(1)


def _line_diff_counts(before: str, after: str) -> tuple[int, int]:
    """返回与 Codex 文件行动行一致的新增/删除行数，不保存具体 diff 正文。"""
    added = 0
    removed = 0
    matcher = difflib.SequenceMatcher(a=str(before or "").splitlines(), b=str(after or "").splitlines())
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += a1 - a0
        if tag in ("replace", "insert"):
            added += b1 - b0
    return added, removed


def _file_action(operation: str, filename: str, *, file_id: str = "", added: int = 0, removed: int = 0) -> dict:
    return {
        "operation": operation,
        "target": str(filename or "")[:240],
        "file_id": str(file_id or ""),
        "added": max(0, int(added or 0)),
        "removed": max(0, int(removed or 0)),
    }


def _file_origin(tool: str, run_id: Optional[str], skills: Optional[list] = None) -> dict:
    """产物血缘（P1）：随 meta.files[].origin 下发，前端文件卡显示来源并支持跳回执行步骤。"""
    origin: dict = {"tool": tool}
    if run_id:
        origin["runId"] = run_id
    names = [str(s.get("name") or "").strip() for s in (skills or []) if isinstance(s, dict)]
    names = [n for n in names if n]
    if names:
        origin["skills"] = names[:5]
    return origin


def _host_of(url: Optional[str]) -> str:
    """图片目录里给模型看的来源域名（辅助它判断图片可信度/相关性）。"""
    try:
        from urllib.parse import urlparse
        return urlparse(str(url or "")).hostname or "未知来源"
    except Exception:  # noqa: BLE001
        return "未知来源"


class ToolFailure(Exception):
    """Typed business/runtime failure exposed to the tool pipeline."""

    def __init__(self, message: str, *, code: str = "tool_failed",
                 retryable: bool = False, details: Optional[dict] = None):
        super().__init__(str(message))
        self.code = str(code or "tool_failed")
        self.message = str(message)
        self.retryable = bool(retryable)
        self.details = dict(details or {})


class ToolSoftError(ToolFailure):
    """工具软失败（2026-07-22）：业务上未完成——文件不存在、old_string 匹配不到、
    技能不存在等。文案原样回灌模型供自纠，同时以 failed=True 发 tool.failed，
    终结「失败文案被渲染成『已读取/已编辑』完成态」的状态撒谎。
    仅用于**无副作用**的失败路径；部分成功（沙箱已执行仅保存失败等）仍走正常返回+文内警告。"""

    def __init__(self, message: str, *, code: str = "business_failure",
                 retryable: bool = False, details: Optional[dict] = None):
        super().__init__(message, code=code, retryable=retryable, details=details)


class ToolContractViolation(ToolFailure):
    def __init__(self, message: str, *, details: Optional[dict] = None):
        super().__init__(
            message, code="tool_contract_violation", retryable=False, details=details,
        )


class SubagentNeedsInput(Exception):
    """工具循环挂起等待用户输入（HITL；名字沿用自历史的子智能体 needs_input 协议）。

    由 ask_user_choice / 计划确认等交互工具抛出、穿透工具循环上抛到 harness_orchestrator——
    挂起不是失败，不能被 except Exception 收敛成错误文本；携带 needs_input 结果协议全量
    （resume_id/interactive/ask_user/...），供调用方置 Run waiting 并持久化循环游标。
    """

    def __init__(self, result: dict):
        super().__init__(str(result.get("text") or "等待用户输入"))
        self.result = dict(result or {})


@dataclass
class ToolExecutionResult:
    """Concrete tool-body result before the Harness gateway creates an Observation.

    ``model_content`` is intentionally separate from UI and audit details.  This type is not the
    canonical Harness ``ToolObservation``; the gateway maps it at the single execution boundary.
    """

    status: str = "succeeded"
    model_content: str = ""
    ui: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    # 可改变用户状态的工具必须返回可核验回执。终答里的“已记住/已提交/已修改”
    # 只能由同轮回执解锁，不能再靠 model_content 的自然语言猜测是否真的成功。
    receipts: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[dict[str, Any]] = None
    # ``result_handle`` is the durable fetch identity.  ``raw_ref`` remains as a compatibility
    # alias while existing checkpoints and call sites migrate to the explicit field.
    result_handle: Optional[str] = None
    raw_ref: Optional[str] = None
    # Frozen projection decision persisted with provider history.  Resume paths reuse the already
    # projected model_content and this policy instead of applying new deployment defaults.
    applied_result_policy: Optional[dict[str, Any]] = None
    added_capabilities: list[str] = field(default_factory=list)
    terminate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)



class ToolValue(BaseModel):
    """Canonical, lossless value returned by every production main-chat tool body."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    status: Literal["succeeded", "failed", "unknown", "needs_approval"] = "succeeded"
    model_content: str
    ui: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    receipts: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[dict[str, Any]] = None
    result_handle: Optional[str] = None
    raw_ref: Optional[str] = None
    applied_result_policy: Optional[dict[str, Any]] = None
    added_capabilities: list[str] = Field(default_factory=list)
    terminate: bool = False


@dataclass(frozen=True)
class ToolExecutionContext:
    call_id: str
    run_id: str = ""
    root_run_id: str = ""
    user_id: str = ""
    thread_id: str = ""
    deadline_monotonic: float = 0.0
    parent_call_id: str = ""
    parent_logical_call_id: str = ""
    execution_segment: str = ""


ExecutionMode = Literal["parallel", "exclusive"]


@dataclass(frozen=True)
class ToolOutputContract:
    output_model: Type[BaseModel]
    render_model: Callable[[BaseModel], str]
    render_ui: Callable[[BaseModel], dict]

    @property
    def json_schema(self) -> dict:
        return self.output_model.model_json_schema()


def _tool_value_model(value: BaseModel) -> str:
    return str(getattr(value, "model_content", "") or "")


def _tool_value_ui(value: BaseModel) -> dict:
    return dict(getattr(value, "ui", {}) or {})


def text_tool_body(fn: Callable[[dict], Awaitable[str]]) -> Callable[[dict], Awaitable[ToolValue]]:
    """Declare a text-only implementation as a canonical Harness tool body.

    This adapter is part of the tool definition, not the runtime: it accepts only ``str`` and
    fails closed for arbitrary values.
    """
    async def _body(args: dict) -> ToolValue:
        value = await fn(args)
        if not isinstance(value, str):
            raise ToolContractViolation(
                f"{getattr(fn, '__name__', 'tool')} 应返回 str，实际为 {type(value).__name__}"
            )
        return ToolValue(model_content=value)

    return _body


def _validated_args(parameters: dict, args: dict) -> dict:
    """Validate the stable JSON-Schema subset used by main-chat tools, then deep-freeze by copy."""
    if not isinstance(args, dict):
        raise ToolFailure("工具参数必须是对象", code="invalid_tool_arguments", retryable=True)
    schema = parameters if isinstance(parameters, dict) else {}
    required = schema.get("required") or []
    missing = [str(key) for key in required if key not in args]
    if missing:
        raise ToolFailure(
            "缺少必填参数: " + ", ".join(missing),
            code="invalid_tool_arguments", retryable=True,
            details={"missing": missing},
        )
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    type_map = {
        "string": str, "object": dict, "array": list, "boolean": bool,
        "integer": int, "number": (int, float),
    }
    for key, value in args.items():
        item_schema = properties.get(key)
        if not isinstance(item_schema, dict) or value is None:
            continue
        expected = item_schema.get("type")
        expected_type = type_map.get(expected)
        if expected_type is not None:
            # bool is an int subclass in Python but is never a valid JSON integer here.
            valid = isinstance(value, expected_type) and not (
                expected in {"integer", "number"} and isinstance(value, bool)
            )
            if not valid:
                raise ToolFailure(
                    f"参数 {key} 类型错误，应为 {expected}",
                    code="invalid_tool_arguments", retryable=True,
                    details={"field": str(key), "expected": str(expected)},
                )
        enum = item_schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise ToolFailure(
                f"参数 {key} 不在允许值范围内",
                code="invalid_tool_arguments", retryable=True,
                details={"field": str(key), "allowed": enum[:20]},
            )
    return copy.deepcopy(args)

# 当前正在执行的工具调用 id（contextvars：asyncio.create_task 会复制当前上下文，
# 所以每个并发执行的工具各自看到自己的值）。只读工具并发执行后，多个 _drive_tool_call
# 会同时从**同一条** tool_progress_queue 取数据，仅按工具名分辨会串台（两次并发
# search_web）或误吞（list_files 把 search_web 的「正在阅读」取走丢弃）——进度上报方
# 用它给每条消息盖上调用 id，消费方据此精确归属。
CURRENT_TOOL_CALL_ID: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "current_tool_call_id", default="")


def current_tool_call_id() -> str:
    """进度上报方用：拿到自己所属的工具调用 id（拿不到就返回空串，消费方回退按名匹配）。"""
    try:
        return CURRENT_TOOL_CALL_ID.get() or ""
    except LookupError:  # pragma: no cover - default 已给，理论上不会走到
        return ""


# 本次工具调用的墙钟死线（`time.monotonic()` 刻度，0=没有死线）。由主循环
# `_drive_tool_call` 在起 task 之前设置。
#
# 为什么工具需要看得见它：到点时主循环是 `task.cancel()`——**异常路径**，工具的收尾代码
# （bash 的 `sync.persist()`）根本不会执行，沙箱里已经产出的 PPT/文档一个都不落库，
# 而模型只收到「已中止本次调用」。与其事后补救，不如让长跑的工具自己把内部超时收进死线
# 之内：命令被沙箱正常终止（exit 124）→ 工具正常返回 → 产物照常落库。
CURRENT_TOOL_DEADLINE: "contextvars.ContextVar[float]" = contextvars.ContextVar(
    "current_tool_deadline", default=0.0)


def remaining_tool_budget_s() -> float:
    """距本次工具调用被主循环强制取消还剩几秒；0 = 无死线（不限）。"""
    try:
        deadline = float(CURRENT_TOOL_DEADLINE.get() or 0.0)
    except LookupError:  # pragma: no cover - default 已给
        return 0.0
    if deadline <= 0:
        return 0.0
    import time as _time
    return max(0.0, deadline - _time.monotonic())


CURRENT_TOOL_CONTEXT: "contextvars.ContextVar[Optional[ToolExecutionContext]]" = contextvars.ContextVar(
    "current_tool_context", default=None,
)


def current_tool_context() -> Optional[ToolExecutionContext]:
    return CURRENT_TOOL_CONTEXT.get()


class ToolMetaSink(dict):
    """工具元信息回收池：写入按**当前调用 id** 归属，消费端按 call_id 取走。

    与进度队列同一个病、同一种修法。按工具名寻址在并发下必然串台：只读工具并发化后，
    同一轮里两次 read_file 都写 sink["read_file"]，后写覆盖先写——第一张执行卡显示的是
    另一次调用的文件名，第二张干脆没有动作标签（pop 到 None）；两次 search_web 则会丢掉
    其中一次的 [图N]→URL 映射。

    写入方一律不改（工具里照旧 `sink["read_file"] = {...}`）：这里在 __setitem__ 上自动
    盖当前调用 id 的章。拿不到 id（非工具上下文、单测直接构造 dict）时退回按名存取，
    行为与旧实现完全一致——消费端 pop_tool_meta 也按同样的顺序回退。
    """

    @staticmethod
    def _key(name: str, call_id: str = ""):
        cid = call_id or current_tool_call_id()
        return (cid, str(name)) if cid else str(name)

    def __setitem__(self, name, value) -> None:
        super().__setitem__(self._key(name), value)

    def __getitem__(self, name):
        # 同一次调用内的二次读写（如 download_url 先 _emit_file_meta 再补 urls/size）
        key = self._key(name)
        if not super().__contains__(key):
            key = str(name)
        return super().__getitem__(key)

    def __contains__(self, name) -> bool:
        return super().__contains__(self._key(name)) or super().__contains__(str(name))

    def get(self, name, default=None):
        try:
            return self[name]
        except KeyError:
            return default

    def setdefault(self, name, default=None):
        if name in self:
            return self[name]
        self[name] = default
        return default


def pop_tool_meta(sink, call_id: str, name: str):
    """消费端：取走某次工具调用的元信息（取走即消费，不留给下一条 tool.completed）。

    先按 (call_id, name) 精确归属；没有（旧格式/非工具上下文写入的）才退回按名取。
    sink 为普通 dict 时等价于旧的按名 pop。
    """
    if not sink:
        return None
    if call_id:
        hit = sink.pop((str(call_id), str(name)), None)
        if hit is not None:
            return hit
    return sink.pop(str(name), None)


class MainTool:
    def __init__(self, name: str, description: str, parameters: dict,
                 execute: Callable[[dict], Awaitable[Any]], sensitive: bool = False,
                 internal: bool = False,
                 readonly: bool = False, parallel_safe: Optional[bool] = None,
                 output_model: Optional[Type[BaseModel]] = None,
                 render_model: Optional[Callable[[BaseModel], str]] = None,
                 render_ui: Optional[Callable[[BaseModel], dict]] = None,
                 dispatch_mode: Optional[Callable[[dict], ExecutionMode]] = None,
                 capability: Optional[str] = None,
                 semantic_tags: tuple[str, ...] = (),
                 effect_scope: Optional[str] = None,
                 idempotent: Optional[bool] = None,
                 resource_locks: tuple[str, ...] = (),
                 approval_policy: Optional[str] = None,
                 timeout_seconds: float = 600.0,
                 cancellable: bool = True,
                 public_action: str = "完成当前操作",
                 control_command: bool = False,
                 visible_to_user: bool = True,
                 allowed_profiles: Optional[tuple[str, ...]] = None,
                 allowed_phases: Optional[tuple[str, ...]] = None,
                 allowed_execution_profiles: tuple[str, ...] = (
                     "interactive", "artifact_coding",
                 ),
                 result_size_policy: Optional["ResultSizePolicy"] = None,
                 package_result_size_policy: Optional["ResultSizePolicy"] = None,
                 administrator_result_size_policy: Optional["ResultSizePolicy"] = None,
                 result_safety_tail: str = ""):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.execute = execute
        self.output_contract = (
            ToolOutputContract(
                output_model=output_model,
                render_model=render_model or _tool_value_model,
                render_ui=render_ui or _tool_value_ui,
            )
            if output_model is not None else None
        )
        # 敏感工具（写/副作用）经 Tool Gateway 审批后才执行（§11）；只读检索工具为 False。
        self.sensitive = sensitive
        # 内部工具（如 fetch_tool_result）直连执行、不经网关（避免为读取再记一条网关调用）。
        self.internal = internal
        # Static safety boundaries (for example the connector prompt-injection footer) belong at
        # the end of every projected window and must survive truncation.
        self.result_safety_tail = str(result_safety_tail or "")
        # 真正无副作用（重跑安全）的非内部工具（如 search_web/search_knowledge）：仅这类
        # 工具允许在 Tool Gateway 异常时降级直连重试；sensitive=False 但有副作用的写工具
        # （edit_file/update_file/create_file/execute_in_sandbox 等）不设它，网关异常时不得重跑。
        self.readonly = readonly
        # 可与同批其它调用并发执行（无副作用、不依赖同批别人的结果）。默认沿用 readonly：
        # readonly 语义是「重跑安全」，重跑安全即无副作用，天然可并发。但 readonly 同时
        # 兼作「网关异常可降级直连重试」的开关，list_files/read_file 这类真只读的工具出于
        # 谨慎没有开它——所以这里允许单独打开并发而不改动 readonly 的既有语义。
        # 写工具（create/update/edit_file、execute_in_sandbox）与子智能体一律保持 False：并发写会互相
        # 踩踏，且子智能体有 HITL 挂起语义。
        self.parallel_safe = bool(readonly) if parallel_safe is None else bool(parallel_safe)
        self._dispatch_mode = dispatch_mode
        from app.services.agent_harness.contracts import (
            AgentMode, ApprovalPolicy, EffectScope, ExecutionProfileId, ResultSizePolicy,
            RunPhase, ToolSpec,
        )
        from app.services.agent_harness.results import most_restrictive_result_policy
        resolved_scope = EffectScope(
            effect_scope
            or ("none" if readonly or internal else "user_files" if sensitive else "scratch")
        )
        resolved_idempotent = bool(readonly or internal) if idempotent is None else bool(idempotent)
        locks = tuple(str(item) for item in resource_locks if str(item))
        if resolved_scope in {EffectScope.USER_FILES, EffectScope.EXTERNAL, EffectScope.MEMORY} and not locks:
            locks = ("run-resource",)
        resolved_approval = ApprovalPolicy(
            approval_policy or ("conditional" if sensitive else "never")
        )
        resolved_capability = str(capability or "").strip()
        if not resolved_capability:
            resolved_capability = {
                EffectScope.NONE: "data.read",
                EffectScope.SCRATCH: "workspace.scratch",
                EffectScope.USER_FILES: "files.write",
                EffectScope.EXTERNAL: "external.write",
                EffectScope.MEMORY: "memory.write",
            }[resolved_scope]
        declared_tags = {str(item).strip() for item in semantic_tags if str(item).strip()}
        removed_tags = {item[1:] for item in declared_tags if item.startswith("-")}
        tags = {item for item in declared_tags if not item.startswith("-")}
        if control_command:
            tags.add("control")
        elif resolved_scope in {EffectScope.NONE, EffectScope.SCRATCH}:
            tags.add("investigate")
        if resolved_scope in {EffectScope.USER_FILES, EffectScope.EXTERNAL, EffectScope.MEMORY}:
            tags.update({"mutate", "productive"})
        if resolved_scope is EffectScope.USER_FILES:
            tags.update({"artifact_producer", "revision_mutation"})
        tags.difference_update(removed_tags)
        output_schema = (
            self.output_contract.json_schema
            if self.output_contract is not None
            else ToolValue.model_json_schema()
        )
        self.spec = ToolSpec(
            name=name,
            description=description,
            input_schema=parameters,
            output_schema=output_schema,
            capability=resolved_capability,
            semantic_tags=frozenset(tags),
            effect_scope=resolved_scope,
            idempotent=resolved_idempotent,
            timeout_seconds=timeout_seconds,
            cancellable=cancellable,
            resource_locks=locks,
            parallel_safe=bool(self.parallel_safe and resolved_scope in {EffectScope.NONE, EffectScope.SCRATCH}),
            approval_policy=resolved_approval,
            visible_to_user=bool(visible_to_user),
            public_action=public_action,
            control_command=control_command,
            allowed_profiles=frozenset(
                AgentMode(value) for value in (allowed_profiles or tuple(mode.value for mode in AgentMode))
            ),
            allowed_execution_profiles=frozenset(
                ExecutionProfileId(value) for value in allowed_execution_profiles
            ),
            allowed_phases=frozenset(
                RunPhase(value) for value in (
                    allowed_phases or (RunPhase.PLANNING.value, RunPhase.EXECUTING.value)
                )
            ),
            # Output projection and execution approval are deliberately independent. A tool,
            # its Skill/plugin package, and an administrator may each narrow the model-visible
            # result; no later layer can widen an earlier explicit limit.
            result_size_policy=most_restrictive_result_policy(
                result_size_policy,
                package_result_size_policy,
                administrator_result_size_policy,
            ),
        )
        # 可选**纯校验**准入钩子 (args) -> 拒绝话术 or ""/None（2026-07-28）。
        # 工具循环在 `tool_started` 事件**之前**调用它：护栏拒绝的调用一帧都不该发出去。
        # 必须无副作用——真正的计数仍在 execute 内完成。
        self.precheck: Optional[Callable[[dict], Optional[str]]] = None

    async def observe(self, args: dict) -> ToolExecutionResult:
        """Validate canonical output and project it into the runtime-owned observation."""
        try:
            frozen_args = _validated_args(self.parameters, args)
            value = await self.execute(frozen_args)
        except ToolFailure as exc:
            return ToolExecutionResult(
                status="failed",
                model_content=exc.message,
                ui={"summary": self.name, "detail": exc.message[:500]},
                error={
                    "code": exc.code,
                    "message": exc.message[:1000],
                    "retryable": exc.retryable,
                    **({"details": exc.details} if exc.details else {}),
                },
            )

        return self.project_output(value)

    def project_output(self, value: Any) -> ToolExecutionResult:
        """Validate and project an already-produced canonical value.

        Streaming tools execute their body while emitting progress, so they cannot call
        ``observe`` without executing twice. They still pass their terminal value through this
        exact contract gate before it is persisted or shown to the model.
        """
        contract = self.output_contract
        if contract is None:
            # Kept only for isolated unit fixtures. Production assembly calls
            # assert_tool_contracts and therefore cannot enter this path.
            if isinstance(value, str):
                parsed: BaseModel = ToolValue(model_content=value)
            elif isinstance(value, ToolValue):
                parsed = value
            else:
                raise ToolContractViolation(
                    f"工具 {self.name} 未声明 output_model 且返回 {type(value).__name__}"
                )
        else:
            try:
                parsed = contract.output_model.model_validate(value)
            except ValidationError as exc:
                raise ToolContractViolation(
                    f"工具 {self.name} 输出不符合声明的 JSON Schema",
                    details={"errors": exc.errors(include_input=False)[:8]},
                ) from exc
        raw_ref = getattr(parsed, "raw_ref", None)
        result_handle = getattr(parsed, "result_handle", None) or raw_ref
        return ToolExecutionResult(
            status=str(getattr(parsed, "status", "succeeded") or "succeeded"),
            model_content=(contract.render_model(parsed) if contract else _tool_value_model(parsed)),
            ui=(contract.render_ui(parsed) if contract else _tool_value_ui(parsed)),
            artifacts=list(getattr(parsed, "artifacts", []) or []),
            citations=list(getattr(parsed, "citations", []) or []),
            receipts=list(getattr(parsed, "receipts", []) or []),
            error=getattr(parsed, "error", None),
            result_handle=result_handle,
            raw_ref=result_handle or raw_ref,
            applied_result_policy=getattr(parsed, "applied_result_policy", None),
            added_capabilities=list(getattr(parsed, "added_capabilities", []) or []),
            terminate=bool(getattr(parsed, "terminate", False)),
        )

    def dispatch_mode(self, args: dict) -> ExecutionMode:
        if self._dispatch_mode is not None:
            mode = self._dispatch_mode(dict(args or {}))
            return "parallel" if mode == "parallel" else "exclusive"
        return "parallel" if self.spec.parallel_safe else "exclusive"

    def to_openai(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.spec.name,
                "description": self.spec.description,
                "parameters": self.spec.input_schema,
            },
        }


def assert_tool_contracts(tools: list[MainTool]) -> None:
    missing = [tool.name for tool in tools if tool.output_contract is None]
    if missing:
        raise RuntimeError(
            "Agent Harness tools require output contracts: " + ", ".join(sorted(missing))
        )


# intent：模型用一句任务语言描述本次调用意图，仅供前端执行流做行标题展示；
# 执行逻辑不读它，缺省时前端回退到工具类型文案（弱模型漏填不致损）。
INTENT_PROP: dict = {
    "type": "string",
    "description": (
        "用一句话（30字内）以任务语言描述这次调用要做的事，给用户看，"
        "例如「查阅国家节能政策的量化指标」；不要出现沙箱/工具/函数等实现词"
    ),
}


def _query_param() -> dict:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词或问题"},
            "intent": dict(INTENT_PROP),
        },
        "required": ["query"],
    }
