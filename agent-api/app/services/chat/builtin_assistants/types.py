"""Identity-only types for built-in main-chat assistants.

This module must not import campus knowledge/domain policy or presentation
Skill/tool guards.  Those stay in the independent strategy modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


class BuiltinAssistantIdentity(TypedDict):
    app_id: str
    preset: str
    name: str
    origin: str
    ui_policy_key: str


@dataclass(frozen=True)
class BuiltinAppSpec:
    preset: str
    name: str
    description: str
    icon: str
    category: str
    category_label: str
    route: str
    order_num: int


@dataclass(frozen=True)
class BuiltinAssistantDefinition:
    identity: BuiltinAssistantIdentity
    catalog: BuiltinAppSpec
