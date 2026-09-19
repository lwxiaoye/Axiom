from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class OfficialDomainRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    include_subdomains: bool = True


class KnowledgeBindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_id: str
    knowledge_name_snapshot: Optional[str] = None
    category: str = ""
    department: str = ""
    priority: int = 100
    enabled: bool = True


class DraftUpdateRequest(BaseModel):
    # 皮肤系统已移除；旧前端 / 旧草稿请求体里可能仍带 main_chat_skin_id 之类的字段，静默忽略而不是 422。
    model_config = ConfigDict(extra="ignore")

    expected_revision: int
    model_id: str
    official_domains: List[OfficialDomainRule] = Field(default_factory=list)
    knowledge_bindings: List[KnowledgeBindingInput] = Field(default_factory=list)
    change_note: str = ""
    enabled: Optional[bool] = None


class DraftValidateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expected_revision: Optional[int] = None
    model_id: Optional[str] = None
    official_domains: Optional[List[OfficialDomainRule]] = None
    knowledge_bindings: Optional[List[KnowledgeBindingInput]] = None


class DraftPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    change_note: str = ""
    confirm_warnings: bool = False


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    change_note: str = ""

