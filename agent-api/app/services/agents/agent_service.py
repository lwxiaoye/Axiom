import hashlib
import logging
from typing import List, Optional

import httpx
from sqlalchemy import select

from app.core.model_endpoint import get_model_base_url
from app.core.config import settings
from app.core.database import async_session
from app.models import ChatModel, EmbeddingModel
from app.schemas.schemas import ModelItem, AgentItem
from app.services.agent_harness.responses_protocol import responses_capability_from_metadata

logger = logging.getLogger(__name__)


class AgentService:
    def __init__(self):
        self.agents = []
        # Refreshed together with the authenticated NewAPI model catalog.  It lets
        # the main Agent recognize an opaque model id by display/provider metadata
        # without changing the public model id persisted on Runs.
        self._model_transport_aliases: dict[str, tuple[str, ...]] = {}
        # A user key can expose the same model id through a different NewAPI channel.
        # Keep only a one-way key fingerprint so capability facts cannot bleed across users
        # and the credential itself is never retained in process memory by this cache.
        self._model_transport_aliases_by_key: dict[tuple[str, str], tuple[str, ...]] = {}
        self._model_responses_capabilities: dict[tuple[str, str], Optional[bool]] = {}

    @staticmethod
    def _key_fingerprint(user_key: Optional[str]) -> str:
        raw = str(user_key or "")
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24] if raw else ""

    def model_transport_aliases(
        self,
        model_id: str,
        user_key: Optional[str] = None,
    ) -> tuple[str, ...]:
        key = (self._key_fingerprint(user_key), str(model_id or ""))
        if key[0] and key in self._model_transport_aliases_by_key:
            return self._model_transport_aliases_by_key[key]
        return self._model_transport_aliases.get(str(model_id or ""), ())

    def model_responses_capability(
        self,
        model_id: str,
        user_key: Optional[str] = None,
    ) -> Optional[bool]:
        return self._model_responses_capabilities.get(
            (self._key_fingerprint(user_key), str(model_id or ""))
        )

    def remember_model_responses_capability(
        self,
        model_id: str,
        user_key: Optional[str],
        supported: bool,
    ) -> None:
        fingerprint = self._key_fingerprint(user_key)
        resolved_model = str(model_id or "")
        if fingerprint and resolved_model:
            self._model_responses_capabilities[(fingerprint, resolved_model)] = bool(supported)

    async def get_models(
        self, user_key: Optional[str] = None, *, raise_on_lookup_failure: bool = False
    ) -> List[ModelItem]:
        """返回用户 Key 可用的全部模型，但排除后端专用 Embedding 模型。

        ai_chat_model 仅用于覆盖显示名、默认项、排序和显式停用，不要求为每个
        New API 模型都维护一条记录。
        """
        if not user_key:
            return []

        from app.core.model_endpoint import get_model_connection
        connection = get_model_connection()
        if connection and connection["api_key"] == user_key:
            return [ModelItem(id=connection["model"], name=connection["model"], is_default=True)]

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{get_model_base_url()}/models",
                    headers={"Authorization": f"Bearer {user_key}"},
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
        except Exception as e:
            logger.warning("Failed to fetch models from new-api: %s", e)
            if raise_on_lookup_failure:
                raise
            return []

        async with async_session() as session:
            chat_rows = (
                await session.execute(
                    select(ChatModel)
                    .order_by(ChatModel.sort_order.asc(), ChatModel.id.asc())
                )
            ).scalars().all()
            embedding_ids = set(
                (
                    await session.execute(
                        select(EmbeddingModel.model_id)
                        .where(EmbeddingModel.enabled == 1)
                    )
                ).scalars().all()
            )

        chat_config = {row.model_id: row for row in chat_rows}
        upstream_by_id = {
            str(item.get("id") or "").strip(): item
            for item in data
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        available_ids = [
            str(item.get("id") or "").strip()
            for item in data
            if str(item.get("id") or "").strip()
        ]
        gateway_order = {
            model_id: index
            for index, model_id in enumerate(available_ids)
        }
        visible_ids = [
            model_id
            for model_id in available_ids
            if model_id not in embedding_ids
            and not (
                model_id in chat_config
                and chat_config[model_id].enabled != 1
            )
        ]
        visible_ids.sort(key=lambda model_id: (
            chat_config[model_id].sort_order if model_id in chat_config else 10_000,
            gateway_order[model_id],
        ))

        result = [
            ModelItem(
                id=model_id,
                name=chat_config[model_id].display_name if model_id in chat_config else model_id,
                is_default=bool(chat_config[model_id].is_default) if model_id in chat_config else False,
            )
            for model_id in visible_ids
        ]
        self._model_transport_aliases = {
            model_id: tuple(dict.fromkeys(
                str(value).strip()
                for value in (
                    chat_config[model_id].display_name if model_id in chat_config else "",
                    (upstream_by_id.get(model_id) or {}).get("name"),
                    (upstream_by_id.get(model_id) or {}).get("display_name"),
                    (upstream_by_id.get(model_id) or {}).get("owned_by"),
                    (upstream_by_id.get(model_id) or {}).get("provider"),
                )
                if str(value or "").strip()
            ))
            for model_id in visible_ids
        }
        fingerprint = self._key_fingerprint(user_key)
        if fingerprint:
            # Replace only this credential's current catalog rows. Other users and channels
            # remain isolated even when their visible model ids happen to be identical.
            learned_capabilities = {
                key[1]: value
                for key, value in self._model_responses_capabilities.items()
                if key[0] == fingerprint and value is not None
            }
            for cache in (
                self._model_transport_aliases_by_key,
                self._model_responses_capabilities,
            ):
                stale = [key for key in cache if key[0] == fingerprint]
                for key in stale:
                    cache.pop(key, None)
            for model_id in visible_ids:
                cache_key = (fingerprint, model_id)
                self._model_transport_aliases_by_key[cache_key] = (
                    self._model_transport_aliases.get(model_id) or ()
                )
                declared_capability = responses_capability_from_metadata(
                    upstream_by_id.get(model_id)
                )
                self._model_responses_capabilities[cache_key] = (
                    declared_capability
                    if declared_capability is not None
                    else learned_capabilities.get(model_id)
                )

        if result and not any(m.is_default for m in result):
            result[0].is_default = True

        return result

    async def get_agents(
        self,
        recommend: bool = False,
        search: Optional[str] = None
    ) -> List[AgentItem]:
        result = self.agents

        if recommend:
            result = [a for a in result if a.is_recommend]

        if search:
            search_lower = search.lower()
            result = [
                a for a in result
                if search_lower in a.name.lower() or
                (a.description and search_lower in a.description.lower())
            ]

        return result

    async def sync_agents(self, agents: List[dict]):
        self.agents = [
            AgentItem(
                id=str(a.get("id", "")),
                name=a.get("name", a.get("appName", "")),
                description=a.get("description", a.get("appRemark", "")),
                icon=a.get("icon", a.get("appIcon", "")),
                is_recommend=a.get("is_recommend", a.get("isRecommend", False)),
                status=a.get("status", 0)
            )
            for a in agents
        ]


agent_service = AgentService()
