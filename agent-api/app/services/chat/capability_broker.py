"""Small, provider-neutral capability broker for the web Agent.

Providers that cannot defer function schemas still get a small initial tool list. The discovery
tool activates matching descriptors for the following model round and the active names are stored
in RunState by the caller.

Core tools (bash/files/search_web) are **pinned at Run start** when platform-enabled — models must
not pay a search_capabilities round-trip tax before basic work (Codex/Claude Code style). Discovery
only unlocks secondary surfaces (browser session, connectors, rare extras).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.services.chat.tools.base import MainTool, ToolValue, text_tool_body


MAX_DISCOVERED = 5


def detect_explicit_memory_tools(user_message: str) -> set[str]:
    """把用户明确的记忆写/删意图映射为必须首轮可见的工具。

    这是能力可见性判定，不负责抽取要保存的内容；内容与敏感治理仍由工具执行层处理。
    刻意不把“你记得 X 吗/介绍记忆功能”算写入，避免只因出现“记忆/记得”就误改状态。
    """
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return set()
    forget = bool(
        re.search(r"(?:请|麻烦|帮我|你(?:要)?|把).{0,6}(?:忘记|忘掉)", text)
        or re.search(r"(?:忘记|忘掉)(?:我|关于|之前|这条|那条)", text)
        or re.search(r"(?:删掉|删除|清除).{0,10}(?:记忆|记住的|偏好|那条|这条)", text)
        or re.search(r"(?:别|不要|不用)再记(?:住|着)?", text)
    )
    if forget:
        return {"forget_memory"}
    remember = bool(
        re.search(r"(?:请|麻烦|帮我|你(?:要)?|一定要)?\s*(?:记住|记下|记一下|记着)", text)
        or re.search(r"(?:保存|写入).{0,8}(?:长期)?记忆", text)
    )
    return {"remember_fact"} if remember else set()


def resolve_core_pins(
    *,
    action_authority: str = "mutate",
    web_enabled: bool = False,
    plan_mode: bool = False,
    turn_intent: str = "",
    has_kb: bool = False,
    has_selected_files: bool = False,
    has_trusted_skills: bool = False,
    explicit_memory_tools: Iterable[str] = (),
    pin_plan_tool: bool = False,
    progress_tools: Iterable[str] = (),
) -> set[str]:
    """Compute the initial active tool set for an agent turn.

    Contract (2026-08-05 harness root fix):
    - When platform web search is enabled, **search_web is always pinned** on agent paths.
      The UI toggle web_search only upgrades the prompt to force-search this turn; it must
      **not** be the gate that makes search_web visible. That gate caused natural-language
      weather/news queries to waste discovery rounds or never surface search_web at all.
    - Scratch bash is always pinned; persistent workspace writers pin only under mutate authority.
    - browser_*/connectors stay behind search_capabilities (secondary surfaces).
    """
    authority = str(action_authority or "mutate").strip() or "mutate"
    intent = str(turn_intent or "").strip()
    pinned: set[str] = {
        "ask_user_choice", "read_file", "glob", "bash",
        "get_current_time", "get_user_location",
    }
    if plan_mode or pin_plan_tool or intent in {"execute", "revise", "continue"}:
        pinned.add("update_plan")
    if has_kb:
        pinned.add("search_knowledge")
    # ChatGPT-style always-on search: platform enablement, not the + menu toggle.
    if web_enabled:
        pinned.add("search_web")
    if has_selected_files:
        pinned.update({"read_file", "glob"})
    if has_trusted_skills:
        pinned.add("use_skill")
    if authority == "mutate":
        pinned.update({"write_file", "edit_file", "download_url", "fetch_ppt_asset"})
        pinned.update(
            name for name in explicit_memory_tools
            if name in {"remember_fact", "forget_memory"}
        )
    pinned.update(str(name) for name in (progress_tools or ()) if str(name).strip())
    return pinned


@dataclass(frozen=True)
class CapabilityDescriptor:
    name: str
    group: str
    description: str
    side_effect: str
    approval: str
    tool: MainTool


def _group(name: str) -> tuple[str, str, str]:
    if name in {"search_web", "browser_fetch", "browser_open", "browser_act", "browser_close"}:
        return "web", "read" if name != "browser_act" else "external", "ask" if name == "browser_act" else "auto"
    if name.startswith("search_knowledge"):
        return "knowledge", "read", "auto"
    if name in {"read_file", "glob", "list_files", "fetch_tool_result"}:
        return "workspace", "read", "auto"
    if name in {
        "write_file", "edit_file", "download_url", "bash",
        "fetch_ppt_asset", "publish_ppt_artifact",
    }:
        return "workspace", "write", "ask"
    if name == "use_skill":
        return "skill", "read", "auto"
    if name.startswith("remember_") or name.startswith("forget_"):
        return "memory", "write", "ask"
    if name.startswith("connector_") or "github" in name.lower():
        return "connector", "external", "ask"
    return "core", "none", "auto"


class CapabilityBroker:
    def __init__(self, tools: Iterable[MainTool], *, pinned: Iterable[str] = ()):
        self._all: dict[str, CapabilityDescriptor] = {}
        for tool in tools:
            group, effect, approval = _group(tool.name)
            self._all[tool.name] = CapabilityDescriptor(
                name=tool.name, group=group, description=tool.description[:300],
                side_effect=effect, approval=approval, tool=tool,
            )
        # A tool can be pinned by both the generic authority policy and a specialized
        # execution profile.  Keep insertion order but never expose duplicate function
        # schemas: OpenAI-compatible gateways reject the whole request when names repeat.
        self._active: list[str] = list(dict.fromkeys(
            name for name in pinned if name in self._all
        ))
        # Run 内 schema 冻结：首个非 discovery 真实工具执行后不可再改 payload_tools
        self._frozen: bool = False

    def register(self, tool: MainTool, *, active: bool = False) -> None:
        group, effect, approval = _group(tool.name)
        self._all[tool.name] = CapabilityDescriptor(
            name=tool.name, group=group, description=tool.description[:300],
            side_effect=effect, approval=approval, tool=tool,
        )
        if active and tool.name not in self._active:
            self._active.insert(0, tool.name)

    @property
    def active_names(self) -> list[str]:
        return list(self._active)

    @property
    def frozen(self) -> bool:
        return bool(self._frozen)

    def freeze(self) -> None:
        """冻结本 Run 的工具 schema。之后 search_capabilities 只可读，不再改 active 集合。"""
        self._frozen = True

    def initial_tools(self) -> list[MainTool]:
        return [self._all[name].tool for name in self._active if name in self._all]

    def catalogue(self, query: str) -> list[CapabilityDescriptor]:
        terms = {part.lower() for part in str(query or "").replace("_", " ").split() if len(part) > 1}
        scored: list[tuple[int, CapabilityDescriptor]] = []
        for descriptor in self._all.values():
            if descriptor.name in self._active:
                continue
            haystack = f"{descriptor.name} {descriptor.group} {descriptor.description}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, descriptor))
        if not scored:
            scored = [(0, d) for d in self._all.values() if d.name not in self._active]
        scored.sort(key=lambda item: (-item[0], item[1].group, item[1].name))
        return [item[1] for item in scored[:MAX_DISCOVERED]]

    def activate(self, query: str) -> list[MainTool]:
        if self._frozen:
            return []
        selected = self.catalogue(query)
        for descriptor in selected:
            if descriptor.name not in self._active:
                self._active.append(descriptor.name)
        return [descriptor.tool for descriptor in selected]

    def discovery_text(self, query: str) -> str:
        if self._frozen:
            return (
                "本轮工具表已冻结，不能再加载新能力。"
                "请基于当前已加载的能力继续完成任务。"
            )
        selected = self.catalogue(query)
        if not selected:
            return "没有可加载的额外能力。请基于已有信息继续。"
        lines = ["已为下一步加载以下能力："]
        for item in selected:
            lines.append(f"- {item.name}：{item.description[:120]}")
        return "\n".join(lines)


def build_capability_search_tool(broker: CapabilityBroker) -> MainTool:
    async def _search(args: dict) -> str:
        query = str(args.get("query") or "")
        return broker.discovery_text(query)

    return MainTool(
        name="search_capabilities",
        description="核工具（search_web/bash/读写文件等）已常驻。仅当需要浏览器会话、连接器或其他未显示扩展能力时，再搜索并加载最相关的少量能力。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "所需能力或任务描述"}},
            "required": ["query"],
        },
        execute=text_tool_body(_search),
        output_model=ToolValue,
        internal=True,
        readonly=True,
    )
