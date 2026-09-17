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
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    model_id: str
    # None/empty means the standard main-chat appearance.  The field is deliberately separate from
    # child-agent presentation assignments and is only interpreted by the campus release service.
    main_chat_skin_id: Optional[str] = None
    official_domains: List[OfficialDomainRule] = Field(default_factory=list)
    knowledge_bindings: List[KnowledgeBindingInput] = Field(default_factory=list)
    change_note: str = ""
    enabled: Optional[bool] = None


class DraftValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Optional[int] = None
    model_id: Optional[str] = None
    main_chat_skin_id: Optional[str] = None
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


class MainChatSkinMetadataUpdateRequest(BaseModel):
    """Mutable installation metadata; portable package contents remain immutable."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
