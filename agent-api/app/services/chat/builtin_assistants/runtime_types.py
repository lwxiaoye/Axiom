"""Bounded extension points for built-ins, not another execution loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services.chat.types import TurnContext


@dataclass(frozen=True)
class BuiltinTurnInput:
    message: str
    token: str
    user_id: str
    thread_id: str
    run_id: str
    root_run_id: str
    budget: float


@dataclass(frozen=True)
class BuiltinTurnServices:
    get_catalog_records: Callable[[str], Awaitable[list]]
    fetch_trusted_skills: Callable[[list[str], str], Awaitable[list]]
    recall_memory: Callable[..., Awaitable[Any]]
    format_memory: Callable[[Any], str]
    personalization: Callable[[str], Awaitable[str]]
    lesson_block: Callable[[str, str, str], Awaitable[str]]
    selected_skill_metadata: Callable[[list, list], list[dict]]


@dataclass(frozen=True)
class BuiltinRuntimePolicy:
    preset: str
    prepare_request: Callable[[dict], Awaitable[None]]
    prepare_turn: Callable[[BuiltinTurnInput, BuiltinTurnServices], Awaitable[TurnContext]]
    turn_guard: Callable[[], str]
    filter_tools: Callable[[list], list]
    validate_tools: Callable[[list], list]
    pinned_tool_names: frozenset[str]
    build_allowed_tool_names: frozenset[str] | None = None
    image_delivery_mode: str | None = None
    official_domain_scope: bool = False
    allow_choice_tool: bool = True
    allow_plain_fallback: bool = True
    forced_resume_mode: str | None = None
    hide_selected_skill_references: bool = False
    validate_resume_skills: Callable[[list], None] | None = None
    search_hint: Callable[[], str] | None = None
    knowledge_prompt: Callable[[str, str], str] | None = None
    knowledge_source_label: str = ""
    accept_input: Callable[[dict], Awaitable[dict]] | None = None
    additional_tools: Callable[[Any], list] | None = None
    action_authority: str | None = None
    project_answer: Callable[[Any, str], Awaitable[str]] | None = None
    allow_memory_extraction: bool = True
    public_preamble_guidance: Callable[[dict], str] | None = None
    # 内置助手自带本轮上下文（2026-09-19 面试助手耗时盘点）：为 True 时 Worker 不把附件
    # 灌进会话工作区/沙箱（面试每轮为此白建一个沙箱，约 20 秒）、不按本轮问题重检索附件
    # 片段注入（简历已由工具按需提供，再注入就是重复）、也不叠加通用回合决策与目标契约
    # 提示块（学生回答里的「写进接口文档」会被正则判成「交付文件」任务，与面试契约互相矛盾）。
    owns_turn_context: bool = False
    # 回合开场时平台直接交给模型的观察文本（如面试的冻结状态与材料），省掉首轮只读工具往返。
    # 入参为 {"user_id","thread_id","run_id","message"}；返回空串表示不注入。
    initial_observation: Callable[[dict], Awaitable[str]] | None = None
    # 回合一开始就公开给用户的阶段句（等待期间显示，例如「正在评估你的回答，准备第 2 题」）。
    # 它同时作为 public_preamble 告知模型已公开、不必复述。入参同 initial_observation。
    turn_opening: Callable[[dict], Awaitable[str]] | None = None
    # 执行卡事件的公开投影工厂：入参为 TurnEnv，返回把内部 tool 事件改写成用户可读阶段的函数。
    # 结构化提交型助手（project_answer 非空）用它隐藏草稿、评分与工具参数。
    public_loop_event: Callable[[Any], Callable[[dict], dict]] | None = None

    def bound_tools(self, tools: list) -> list:
        """Filter full assembly, then enforce the same boundary used on resume."""
        return self.validate_tools(self.filter_tools(tools))
