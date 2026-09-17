"""Authoritative context compilation and cache-stable model projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PlanSnapshot, RunSnapshot, ToolObservation
from .terminal_report import compact_terminal_fact


CONTEXT_STATE_KIND = "harness_context_state"
PROJECTION_LEDGER_VERSION = 2
PROJECTION_LEDGER_MODES = frozenset({"shadow", "canary"})
CONTEXT_EPOCH_REASONS = frozenset({
    "initial",
    "resume_rebuild",
    "compaction",
    "history_replaced",
    "tool_schema_changed",
    "base_prompt_changed",
    "transport_changed",
})
_OBSERVATION_STRING_MAX = 1_000
_OBSERVATION_LIST_MAX = 12
_OBSERVATION_DICT_MAX = 32
_WORKSPACE_STATUS_MAX = 4_000
_WORLD_STATE_TEXT_MAX = 40_000
_WORLD_STATE_TOTAL_MAX = 64_000
_WORLD_STATE_FIELDS = (
    "current_date",
    "timezone",
    "turn_constraints",
    "conversation_summary",
    "turn_guard",
    "knowledge_context",
    "selected_skills",
    "skill_catalog",
    "knowledge_bases",
    "connectors",
    "memory",
)
_DISPLAY_ATTACHMENT_FIELDS = (
    "file_id",
    "sha256",
    "filename",
    "name",
    "kind",
    "status",
    "note",
    "reference_id",
    "reference_type",
)
_DISPLAY_ATTACHMENT_TEXT_MAX = 4_000


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_display_history(
    messages: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return the durable user-visible transcript used to authorize canonical replay.

    The chat transcript and the Provider transcript intentionally have different shapes: the
    latter also contains tool calls, tool outputs, reasoning cursors, and context-state events.
    A hash of the persisted public user/assistant rows and stable attachment identities lets a
    later Run prove that the user did not edit, regenerate, or replace history before the
    Provider-only items are restored.
    """

    normalized: list[dict[str, Any]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        normalized_item: dict[str, Any] = {
            "role": role,
            "content": json.loads(canonical_json(item.get("content", ""))),
        }
        attachments: Any = item.get("attachments")
        if attachments is None:
            attachments = item.get("attachments_json")
        if isinstance(attachments, str) and attachments.strip():
            try:
                attachments = json.loads(attachments)
            except (TypeError, ValueError):
                attachments = {
                    "malformed_attachment_sha256": hashlib.sha256(
                        attachments.strip().encode("utf-8")
                    ).hexdigest(),
                }
        if isinstance(attachments, dict):
            # Legacy rows occasionally stored one object instead of an array. Normalize both
            # shapes identically so a serializer cleanup cannot masquerade as a history edit.
            attachments = [attachments]
        elif attachments not in (None, "", [], {}) and not isinstance(
            attachments, list
        ):
            attachments = [{"legacy_attachment_sha256": canonical_hash(attachments)}]
        if isinstance(attachments, list):
            stable_attachments = []
            for attachment in attachments[:64]:
                if not isinstance(attachment, dict):
                    stable_attachments.append({
                        "legacy_attachment_sha256": canonical_hash(attachment),
                    })
                    continue
                stable = {}
                for key in _DISPLAY_ATTACHMENT_FIELDS:
                    value = attachment.get(key)
                    if value in (None, "", [], {}):
                        continue
                    if isinstance(value, str):
                        value = value[:_DISPLAY_ATTACHMENT_TEXT_MAX]
                    stable[key] = json.loads(canonical_json(value))
                if not stable:
                    # If legacy data has no stable identity, bind an opaque digest rather than
                    # silently treating attachment replacement as unchanged. Preview/base64 bytes
                    # never enter the persisted fingerprint; only their fixed-size digest does.
                    stable["legacy_attachment_sha256"] = canonical_hash(attachment)
                if stable:
                    stable_attachments.append(stable)
            attachments = stable_attachments
        if attachments not in (None, "", [], {}):
            normalized_item["attachments"] = json.loads(canonical_json(attachments))
        normalized.append(normalized_item)
    return normalized


def display_history_hash(messages: Iterable[dict[str, Any]]) -> str:
    return canonical_hash(normalize_display_history(messages))


def json_merge_patch(previous: Any, current: Any) -> Any:
    """Return an RFC 7386 merge patch that transforms ``previous`` into ``current``."""
    if previous == current:
        return {}
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return current
    patch: dict[str, Any] = {}
    for key in previous.keys() - current.keys():
        patch[key] = None
    for key, value in current.items():
        if key not in previous:
            patch[key] = value
            continue
        child = json_merge_patch(previous[key], value)
        if child != {}:
            patch[key] = child
    return patch


def apply_json_merge_patch(target: Any, patch: Any) -> Any:
    """Apply an RFC 7386 merge patch without mutating either input."""
    if not isinstance(patch, dict):
        return json.loads(canonical_json(patch))
    output = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            output.pop(key, None)
            continue
        output[key] = apply_json_merge_patch(output.get(key), value)
    return json.loads(canonical_json(output))


class ContextFacts(BaseModel):
    """Authoritative facts for one model sample; operational budgets stay server-side."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run: RunSnapshot
    observations: tuple[ToolObservation, ...] = ()
    plan: PlanSnapshot | None = None
    workspace_status: str = ""
    world_state: "ThreadWorldState | dict[str, Any]" = Field(default_factory=dict)


class StableBasePrompt(BaseModel):
    """Provider-stable rules plane; dynamic facts must never be embedded in ``text``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str

    @property
    def prompt_hash(self) -> str:
        return canonical_hash({"system_prompt": self.text})


class ThreadWorldState(BaseModel):
    """Thread-scoped dynamic facts projected through full/merge-patch events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_date: str = ""
    timezone: str = ""
    memory: str = ""
    connectors: str = ""
    selected_skills: str = ""
    knowledge_bases: tuple[str, ...] = ()
    conversation_summary: str = ""
    skill_catalog: str = ""
    knowledge_context: str = ""
    turn_guard: str = ""

    def as_context_section(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.model_dump(mode="json").items()
            if value not in ("", [], (), None)
        }


class ContextSnapshot(BaseModel):
    """Canonical model-relevant state, independent from message placement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    state_version: int
    goal_revision: int
    plan_version: int
    sections: dict[str, Any]
    snapshot_hash: str
    observations: tuple[ToolObservation, ...]


# Compatibility name for callers that imported the old result type.
CompiledContext = ContextSnapshot


class ProjectedContext(BaseModel):
    """Provider-visible messages plus cache-diagnostic identity for one request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[dict[str, Any], ...]
    context_epoch: int
    epoch_reason: str
    base_prompt_hash: str
    tool_schema_hash: str
    state_snapshot_hash: str
    transport: str


class ProjectionLedgerState(BaseModel):
    """Serializable thread-scoped projection state.

    ``projected_items`` deliberately stores only the complete stateless input prefix. Provider
    continuation identifiers such as ``previous_response_id`` are not part of this contract.
    The default mode is shadow: callers may evaluate and persist eligibility, but must continue
    sending their ordinary full request until the row is explicitly promoted to canary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = PROJECTION_LEDGER_VERSION
    storage_revision: int = 0
    mode: str = "shadow"
    thread_id: str
    model: str
    transport: str
    context_epoch: int
    epoch_reason: str
    base_prompt_hash: str
    tool_schema_hash: str
    state_snapshot_hash: str
    source_cursor: int
    source_history_hash: str
    display_history_count: int = 0
    display_history_hash: str = ""
    projected_items: tuple[dict[str, Any], ...]

    def after_successful_save(self) -> "ProjectionLedgerState":
        """Return the CAS token that follows one successful store write."""

        return self.model_copy(update={
            "storage_revision": max(0, int(self.storage_revision or 0)) + 1,
        })


class ProjectionEligibility(BaseModel):
    """Strict decision for reusing a persisted projection as an append-only prefix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible: bool
    provider_reuse_allowed: bool = False
    reason: str
    reset_reason: str | None = None
    source_cursor: int = 0
    source_history_hash: str = ""
    projected_item_count: int = 0


class ContextCompiler:
    """Compile a fresh, deterministic state snapshot from authoritative inputs."""

    @staticmethod
    def _execution_control_section(raw: Any) -> dict[str, Any]:
        """Expose recovery semantics without provider-visible clocks or counters."""
        control = raw if isinstance(raw, dict) else {}
        recovery_reason = str(control.get("recovery_reason") or "").strip()
        return {"recovery_reason": recovery_reason[:160]} if recovery_reason else {}

    @staticmethod
    def _completion_fact_summary(raw: Any) -> dict[str, Any] | None:
        """Keep verifier/terminal facts short and exclude model-authored claims."""
        if not isinstance(raw, dict):
            return None
        if raw.get("kind") == "run_terminal_report" or raw.get("facts"):
            return compact_terminal_fact(raw)
        reasons = raw.get("reason_codes") or raw.get("unmet_conditions") or []
        if not isinstance(reasons, (list, tuple)):
            reasons = [reasons]
        evidence = raw.get("evidence") or raw.get("existing_evidence") or []
        compact_evidence: list[dict[str, Any]] = []
        if isinstance(evidence, (list, tuple)):
            for item in evidence[:6]:
                if not isinstance(item, dict):
                    continue
                row = {
                    key: str(item[key])[:240]
                    for key in (
                        "call_id", "tool_name", "status", "error_code", "summary",
                        "id", "file_id", "filename", "url",
                    )
                    if item.get(key) not in (None, "")
                }
                compact_evidence.append(row)
        return {
            "kind": str(raw.get("kind") or "completion_verification_gap"),
            "source": str(raw.get("source") or "completion_verifier"),
            "resolution": str(raw.get("resolution") or "continue"),
            "reason_codes": [str(item)[:160] for item in reasons if str(item).strip()][:8],
            "unmet_conditions": [
                str(item)[:240]
                for item in (raw.get("unmet_conditions") or reasons)
                if str(item).strip()
            ][:8],
            "existing_evidence": compact_evidence,
            "verified": bool(raw.get("verified")),
        }

    @staticmethod
    def _plan_section(plan: PlanSnapshot) -> dict[str, Any]:
        active_steps: list[dict[str, Any]] = []
        for step in plan.steps:
            row = step.model_dump(mode="json")
            if str(row.get("status") or "") == "invalidated":
                continue
            row["order"] = len(active_steps)
            active_steps.append(row)
        return {
            "goal_revision": plan.goal_revision,
            "plan_version": plan.plan_version,
            "goal": plan.goal,
            "steps": active_steps,
            "execution_order": "array_order",
        }

    @staticmethod
    def _bounded_value(value: Any, *, depth: int = 0) -> Any:
        if depth >= 6:
            return "[depth-limit]"
        if isinstance(value, dict):
            return {
                str(key): ContextCompiler._bounded_value(value[key], depth=depth + 1)
                for key in sorted(value, key=str)[:_OBSERVATION_DICT_MAX]
            }
        if isinstance(value, (list, tuple)):
            return [
                ContextCompiler._bounded_value(item, depth=depth + 1)
                for item in value[:_OBSERVATION_LIST_MAX]
            ]
        if isinstance(value, str):
            return value[:_OBSERVATION_STRING_MAX]
        return value

    @staticmethod
    def _observation_rows(observations: Iterable[ToolObservation]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for observation in list(observations)[-8:]:
            row = observation.model_dump(mode="json")
            row.pop("started_at", None)
            row.pop("completed_at", None)
            rows.append(ContextCompiler._bounded_value(row))
        return rows

    @staticmethod
    def _world_state_section(raw: dict[str, Any]) -> dict[str, Any]:
        """Apply field caps and one deterministic aggregate budget.

        Field-local limits alone allow several individually-valid sections to multiply into an
        unbounded prompt. Higher-priority facts are admitted first; a lower-priority string is
        clipped to the exact remaining serialized budget instead of evicting earlier facts.
        """

        result: dict[str, Any] = {}

        def fits(candidate: dict[str, Any]) -> bool:
            return len(canonical_json(candidate)) <= _WORLD_STATE_TOTAL_MAX

        for key in _WORLD_STATE_FIELDS:
            value = raw.get(key)
            if value in (None, "", [], ()):
                continue
            if key == "knowledge_bases":
                values = value if isinstance(value, (list, tuple)) else [value]
                accepted: list[str] = []
                for item in values[:64]:
                    rendered_item = (
                        item if isinstance(item, str) else canonical_json(item)
                    )
                    candidate_values = [*accepted, rendered_item[:512]]
                    if not fits({**result, key: candidate_values}):
                        break
                    accepted = candidate_values
                if accepted:
                    result[key] = accepted
                continue
            text = (
                value if isinstance(value, str) else canonical_json(value)
            )[:_WORLD_STATE_TEXT_MAX]
            if fits({**result, key: text}):
                result[key] = text
                continue
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                if fits({**result, key: text[:middle]}):
                    low = middle
                else:
                    high = middle - 1
            if low:
                result[key] = text[:low]
            break
        return result

    @classmethod
    def bounded_world_state(cls, raw: dict[str, Any]) -> dict[str, Any]:
        """Public boundary used by checkpoint writers before persisting dynamic facts."""

        return cls._world_state_section(dict(raw or {}))

    def compile(self, facts: ContextFacts) -> ContextSnapshot:
        runtime = {
            "agent_mode": facts.run.agent_mode.value,
            "execution_profile": facts.run.execution_profile,
            "phase": facts.run.phase.value,
            "capability_scope": facts.run.capability_scope,
            "execution_control": self._execution_control_section(
                facts.run.execution_control
            ),
        }
        sections: dict[str, Any] = {"runtime": runtime}
        if facts.run.goal_contract:
            sections["goal"] = dict(facts.run.goal_contract)
        if facts.plan is not None:
            sections["plan"] = self._plan_section(facts.plan)
        if facts.run.completion_observation:
            completion = self._completion_fact_summary(facts.run.completion_observation)
            if completion:
                sections["completion_verification"] = completion
        raw_world_state = (
            facts.world_state.as_context_section()
            if isinstance(facts.world_state, ThreadWorldState)
            else dict(facts.world_state or {})
        )
        world_state = self._world_state_section(raw_world_state)
        if world_state:
            sections["world_state"] = world_state
        observation_rows = self._observation_rows(facts.observations)
        if observation_rows:
            sections["recent_observations"] = observation_rows
        workspace_status = str(facts.workspace_status or "").strip()
        if workspace_status:
            sections["workspace"] = {
                "status": workspace_status[:_WORKSPACE_STATUS_MAX]
            }
        canonical_sections = json.loads(canonical_json(sections))
        return ContextSnapshot(
            run_id=facts.run.run_id,
            state_version=facts.run.state_version,
            goal_revision=facts.run.goal_revision,
            plan_version=facts.run.plan_version,
            sections=canonical_sections,
            snapshot_hash=canonical_hash(canonical_sections),
            observations=facts.observations,
        )


class ContextProjectionLedger:
    """Append-only projection of fresh state snapshots into provider-visible messages."""

    def __init__(self, *, initial_reason: str = "initial") -> None:
        self._validate_reason(initial_reason)
        self._context_epoch = 0
        self._pending_reason = initial_reason
        self._epoch_reason = initial_reason
        self._initialized = False
        self._source_messages: list[dict[str, Any]] = []
        self._projected_messages: list[dict[str, Any]] = []
        self._sections: dict[str, Any] = {}
        self._snapshot_hash = ""
        self._base_prompt_hash = ""
        self._tool_schema_hash = ""
        self._transport = ""
        self._storage_revision = 0

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if reason not in CONTEXT_EPOCH_REASONS:
            raise ValueError(f"unknown context epoch reason: {reason}")

    @staticmethod
    def _copy(value: Any) -> Any:
        return json.loads(canonical_json(value))

    @staticmethod
    def _context_state_payload(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or str(item.get("role") or "") != "system":
            return None
        content = item.get("content")
        if not isinstance(content, str) or not content.startswith("{"):
            return None
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict) or payload.get("kind") != CONTEXT_STATE_KIND:
            return None
        return payload

    @classmethod
    def _source_from_projected_items(
        cls, projected_items: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            cls._copy(item)
            for item in projected_items
            if cls._context_state_payload(item) is None
        ]

    @classmethod
    def _replay_projected_state(
        cls, projected_items: Iterable[dict[str, Any]],
    ) -> tuple[dict[str, Any], str, int, str] | None:
        sections: dict[str, Any] | None = None
        snapshot_hash = ""
        context_epoch = -1
        epoch_reason = ""
        for item in projected_items:
            payload = cls._context_state_payload(item)
            if payload is None:
                continue
            mode = str(payload.get("mode") or "")
            try:
                payload_epoch = int(payload.get("context_epoch"))
            except (TypeError, ValueError):
                return None
            if mode == "full":
                candidate = payload.get("sections")
                reason = str(payload.get("epoch_reason") or "")
                claimed_hash = str(payload.get("snapshot_hash") or "")
                if (
                    sections is not None
                    or not isinstance(candidate, dict)
                    or reason not in CONTEXT_EPOCH_REASONS
                    or canonical_hash(candidate) != claimed_hash
                ):
                    return None
                sections = cls._copy(candidate)
                snapshot_hash = claimed_hash
                context_epoch = payload_epoch
                epoch_reason = reason
                continue
            if mode == "merge_patch":
                patch = payload.get("patch")
                if (
                    sections is None
                    or not isinstance(patch, dict)
                    or payload_epoch != context_epoch
                    or str(payload.get("base_snapshot_hash") or "") != snapshot_hash
                ):
                    return None
                candidate = apply_json_merge_patch(sections, patch)
                claimed_hash = str(payload.get("snapshot_hash") or "")
                if not isinstance(candidate, dict) or canonical_hash(candidate) != claimed_hash:
                    return None
                sections = candidate
                snapshot_hash = claimed_hash
                continue
            return None
        if sections is None:
            return None
        return sections, snapshot_hash, context_epoch, epoch_reason

    @classmethod
    def evaluate_persisted_state(
        cls,
        state: ProjectionLedgerState,
        *,
        thread_id: str,
        model: str,
        source_messages: Iterable[dict[str, Any]],
        base_prompt_hash: str,
        tool_schema_hash: str,
        transport: str,
        display_history: Iterable[dict[str, Any]] | None = None,
    ) -> ProjectionEligibility:
        """Check every condition required for exact cross-Run prefix extension."""
        source = cls._copy(list(source_messages))
        default = {
            "source_cursor": max(0, int(state.source_cursor or 0)),
            "source_history_hash": str(state.source_history_hash or ""),
            "projected_item_count": len(state.projected_items),
        }

        def reject(reason: str, reset_reason: str) -> ProjectionEligibility:
            return ProjectionEligibility(
                eligible=False,
                provider_reuse_allowed=False,
                reason=reason,
                reset_reason=reset_reason,
                **default,
            )

        if state.version != PROJECTION_LEDGER_VERSION:
            return reject("unsupported_version", "history_replaced")
        if state.mode not in PROJECTION_LEDGER_MODES:
            return reject("unsupported_mode", "history_replaced")
        if state.epoch_reason not in CONTEXT_EPOCH_REASONS:
            return reject("invalid_epoch_reason", "history_replaced")
        if str(state.thread_id or "") != str(thread_id or ""):
            return reject("thread_changed", "history_replaced")
        if str(state.model or "") != str(model or ""):
            return reject("model_changed", "history_replaced")
        if str(state.transport or "") != str(transport or ""):
            return reject("transport_changed", "transport_changed")
        if str(state.base_prompt_hash or "") != str(base_prompt_hash or ""):
            return reject("base_prompt_changed", "base_prompt_changed")
        if str(state.tool_schema_hash or "") != str(tool_schema_hash or ""):
            return reject("tool_schema_changed", "tool_schema_changed")
        display = normalize_display_history(display_history or [])
        if state.display_history_count != len(display):
            return reject("display_history_count_mismatch", "history_replaced")
        if str(state.display_history_hash or "") != canonical_hash(display):
            return reject("display_history_hash_mismatch", "history_replaced")
        if state.source_cursor < 0 or state.source_cursor > len(source):
            return reject("source_cursor_invalid", "history_replaced")

        projected_items = cls._copy(list(state.projected_items))
        previous_source = cls._source_from_projected_items(projected_items)
        if len(previous_source) != state.source_cursor:
            return reject("projection_source_cursor_mismatch", "history_replaced")
        if canonical_hash(previous_source) != state.source_history_hash:
            return reject("projection_source_hash_mismatch", "history_replaced")
        source_prefix = source[:state.source_cursor]
        if source_prefix != previous_source:
            return reject("source_not_append_only", "history_replaced")
        if canonical_hash(source_prefix) != state.source_history_hash:
            return reject("source_history_hash_mismatch", "history_replaced")

        replayed = cls._replay_projected_state(projected_items)
        if replayed is None:
            return reject("projection_state_invalid", "history_replaced")
        _, replayed_hash, replayed_epoch, replayed_reason = replayed
        if (
            replayed_hash != state.state_snapshot_hash
            or replayed_epoch != state.context_epoch
            or replayed_reason != state.epoch_reason
        ):
            return reject("projection_metadata_mismatch", "history_replaced")
        return ProjectionEligibility(
            eligible=True,
            provider_reuse_allowed=state.mode == "canary",
            reason="append_only_canary" if state.mode == "canary" else "append_only_shadow",
            reset_reason=None,
            **default,
        )

    @classmethod
    def from_persisted_shadow(
        cls,
        state: ProjectionLedgerState,
        *,
        forced_reset_reason: str | None = None,
        **eligibility_inputs: Any,
    ) -> tuple["ContextProjectionLedger", ProjectionEligibility]:
        """Restore a diagnostic ledger; its output must not replace the live request body."""
        if forced_reset_reason:
            cls._validate_reason(forced_reset_reason)
            ledger = cls(initial_reason=forced_reset_reason)
            ledger._context_epoch = max(0, int(state.context_epoch or 0) + 1)
            ledger._storage_revision = max(0, int(state.storage_revision or 0))
            return ledger, ProjectionEligibility(
                eligible=False,
                provider_reuse_allowed=False,
                reason=f"forced_{forced_reset_reason}",
                reset_reason=forced_reset_reason,
                source_cursor=max(0, int(state.source_cursor or 0)),
                source_history_hash=str(state.source_history_hash or ""),
                projected_item_count=len(state.projected_items),
            )
        decision = cls.evaluate_persisted_state(state, **eligibility_inputs)
        if not decision.eligible:
            ledger = cls(initial_reason=decision.reset_reason or "history_replaced")
            ledger._context_epoch = max(0, int(state.context_epoch or 0) + 1)
            # A semantic reset starts a new projection epoch, not a new database row. Preserve the
            # optimistic revision so the replacement baseline can CAS over the row that triggered
            # the reset. Dropping it to zero makes every base/schema/history transition conflict
            # forever and leaves the stale ledger authoritative.
            ledger._storage_revision = max(0, int(state.storage_revision or 0))
            return ledger, decision
        replayed = cls._replay_projected_state(state.projected_items)
        if replayed is None:  # Defensive: evaluation above already rejected this state.
            ledger = cls(initial_reason="history_replaced")
            ledger._context_epoch = max(0, int(state.context_epoch or 0) + 1)
            ledger._storage_revision = max(0, int(state.storage_revision or 0))
            return ledger, ProjectionEligibility(
                eligible=False,
                reason="projection_state_invalid",
                reset_reason="history_replaced",
                source_cursor=decision.source_cursor,
                source_history_hash=decision.source_history_hash,
                projected_item_count=decision.projected_item_count,
            )
        sections, snapshot_hash, context_epoch, epoch_reason = replayed
        ledger = cls(initial_reason=epoch_reason)
        ledger._context_epoch = context_epoch
        ledger._pending_reason = epoch_reason
        ledger._epoch_reason = epoch_reason
        ledger._initialized = True
        ledger._source_messages = cls._source_from_projected_items(state.projected_items)
        ledger._projected_messages = cls._copy(list(state.projected_items))
        ledger._sections = sections
        ledger._snapshot_hash = snapshot_hash
        ledger._base_prompt_hash = str(state.base_prompt_hash or "")
        ledger._tool_schema_hash = str(state.tool_schema_hash or "")
        ledger._transport = str(state.transport or "")
        ledger._storage_revision = max(0, int(state.storage_revision or 0))
        return ledger, decision

    @classmethod
    def from_persisted_for_provider(
        cls,
        state: ProjectionLedgerState,
        **eligibility_inputs: Any,
    ) -> tuple[ContextProjectionLedger | None, ProjectionEligibility]:
        """Restore only an explicitly promoted canary; shadow rows are never provider-active."""
        shadow, decision = cls.from_persisted_shadow(state, **eligibility_inputs)
        if not decision.provider_reuse_allowed:
            return None, decision
        return shadow, decision

    @staticmethod
    def _is_prefix(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
        return len(previous) <= len(current) and current[:len(previous)] == previous

    def reset(self, reason: str) -> None:
        self._validate_reason(reason)
        if self._initialized:
            self._context_epoch += 1
        self._pending_reason = reason
        self._initialized = False
        self._source_messages = []
        self._projected_messages = []
        self._sections = {}
        self._snapshot_hash = ""
        self._base_prompt_hash = ""
        self._tool_schema_hash = ""
        self._transport = ""

    def to_persisted_state(
        self,
        *,
        thread_id: str,
        model: str,
        mode: str = "shadow",
        display_history: Iterable[dict[str, Any]] | None = None,
    ) -> ProjectionLedgerState:
        """Snapshot this epoch for a later Run; shadow is the safe default."""
        if not self._initialized:
            raise RuntimeError("projection ledger has not been initialized")
        if mode not in PROJECTION_LEDGER_MODES:
            raise ValueError(f"unsupported projection ledger mode: {mode}")
        source = self._copy(self._source_messages)
        display = normalize_display_history(display_history or [])
        return ProjectionLedgerState(
            storage_revision=self._storage_revision,
            mode=mode,
            thread_id=str(thread_id or ""),
            model=str(model or ""),
            transport=self._transport,
            context_epoch=self._context_epoch,
            epoch_reason=self._epoch_reason,
            base_prompt_hash=self._base_prompt_hash,
            tool_schema_hash=self._tool_schema_hash,
            state_snapshot_hash=self._snapshot_hash,
            source_cursor=len(source),
            source_history_hash=canonical_hash(source),
            display_history_count=len(display),
            display_history_hash=canonical_hash(display),
            projected_items=tuple(self._copy(self._projected_messages)),
        )

    @classmethod
    def source_messages_from_state(
        cls,
        state: ProjectionLedgerState,
    ) -> list[dict[str, Any]] | None:
        """Return a verified canonical Provider source cursor from persisted projection items."""

        projected_items = cls._copy(list(state.projected_items))
        source = cls._source_from_projected_items(projected_items)
        if len(source) != int(state.source_cursor or 0):
            return None
        if canonical_hash(source) != str(state.source_history_hash or ""):
            return None
        return source

    @classmethod
    def restore_canonical_source(
        cls,
        state: ProjectionLedgerState,
        *,
        display_history: Iterable[dict[str, Any]],
        current_run_delta: Iterable[dict[str, Any]],
        replacement_base_message: dict[str, Any] | None = None,
        replace_base_message: bool = False,
        target_transport: str = "",
    ) -> list[dict[str, Any]] | None:
        """Restore Provider-only history only when the durable public transcript still matches.

        A new epoch may legitimately change the base prompt, tool schema, or transport. Hidden
        assistant/tool history remains semantically valuable, but the old leading system item is
        no longer authoritative. Replace only that base item; history edits/compaction still fail
        the public-transcript check and therefore rebuild from the caller's ordinary source.
        """

        display = normalize_display_history(display_history)
        if (
            state.version != PROJECTION_LEDGER_VERSION
            or state.mode not in PROJECTION_LEDGER_MODES
            or state.epoch_reason not in CONTEXT_EPOCH_REASONS
        ):
            return None
        if int(state.display_history_count or 0) != len(display):
            return None
        if str(state.display_history_hash or "") != canonical_hash(display):
            return None
        source = cls.source_messages_from_state(state)
        if source is None:
            return None
        replayed = cls._replay_projected_state(state.projected_items)
        if replayed is None:
            return None
        _, replayed_hash, replayed_epoch, replayed_reason = replayed
        if (
            replayed_hash != state.state_snapshot_hash
            or replayed_epoch != state.context_epoch
            or replayed_reason != state.epoch_reason
        ):
            return None
        if replace_base_message and replacement_base_message is None:
            if source and str(source[0].get("role") or "") == "system":
                source.pop(0)
        elif isinstance(replacement_base_message, dict):
            replacement = cls._copy(replacement_base_message)
            if source and str(source[0].get("role") or "") == "system":
                source[0] = replacement
            else:
                source.insert(0, replacement)
        if target_transport and str(state.transport or "") != str(target_transport):
            # Native Responses cursors are not valid Chat Completions messages. Their public text,
            # tool calls, and paired tool results remain replayable after removing the opaque item.
            if str(target_transport) == "chat_completions":
                for item in source:
                    item.pop("_responses_output_items", None)
        source.extend(
            cls._copy(item)
            for item in current_run_delta
            if isinstance(item, dict) and cls._context_state_payload(item) is None
        )
        return source

    @classmethod
    def from_persisted_transport_transition(
        cls,
        state: ProjectionLedgerState,
        *,
        display_history: Iterable[dict[str, Any]],
        current_run_delta: Iterable[dict[str, Any]],
        replacement_base_message: dict[str, Any] | None,
        target_transport: str,
        replace_base_message: bool = False,
    ) -> (
        tuple["ContextProjectionLedger", list[dict[str, Any]], ProjectionEligibility]
        | None
    ):
        """Seed a new transport-scoped row from an authorized canonical source.

        Storage revisions are scoped by ``(thread, model, transport)``. A transport transition
        therefore inherits the semantic epoch and Provider history, but starts with revision zero
        for the new row instead of reusing the old transport's CAS token.
        """

        source = cls.restore_canonical_source(
            state,
            display_history=display_history,
            current_run_delta=current_run_delta,
            replacement_base_message=replacement_base_message,
            replace_base_message=replace_base_message,
            target_transport=target_transport,
        )
        if source is None:
            return None
        ledger = cls(initial_reason="transport_changed")
        ledger._context_epoch = max(0, int(state.context_epoch or 0) + 1)
        decision = ProjectionEligibility(
            eligible=False,
            provider_reuse_allowed=False,
            reason="transport_changed",
            reset_reason="transport_changed",
            source_cursor=max(0, int(state.source_cursor or 0)),
            source_history_hash=str(state.source_history_hash or ""),
            projected_item_count=len(state.projected_items),
        )
        return ledger, source, decision

    @classmethod
    def with_committed_source_items(
        cls,
        state: ProjectionLedgerState,
        *,
        source_items: Iterable[dict[str, Any]],
        display_history: Iterable[dict[str, Any]],
    ) -> ProjectionLedgerState | None:
        """Advance a pending request snapshot after its canonical response is durable.

        Provider attempts prepare projection state before the network request. Assistant/tool
        items become cross-Run history only after the caller has committed the corresponding chat
        row or tool checkpoint. This helper performs that append without reordering the state event
        that preceded the successful request.
        """

        source = cls.source_messages_from_state(state)
        if source is None:
            return None
        appended = [
            cls._copy(item)
            for item in source_items
            if isinstance(item, dict) and cls._context_state_payload(item) is None
        ]
        if not appended:
            return state
        source.extend(appended)
        projected = cls._copy(list(state.projected_items))
        projected.extend(appended)
        display = normalize_display_history(display_history)
        return state.model_copy(update={
            "source_cursor": len(source),
            "source_history_hash": canonical_hash(source),
            "display_history_count": len(display),
            "display_history_hash": canonical_hash(display),
            "projected_items": tuple(projected),
        })

    def mark_persisted(self) -> None:
        """Advance the local optimistic revision after a successful Runtime CAS write."""

        self._storage_revision += 1

    def _full_event(self, snapshot: ContextSnapshot) -> dict[str, Any]:
        payload = {
            "kind": CONTEXT_STATE_KIND,
            "mode": "full",
            "context_epoch": self._context_epoch,
            "epoch_reason": self._pending_reason,
            "snapshot_hash": snapshot.snapshot_hash,
            "sections": snapshot.sections,
        }
        return {"role": "system", "content": canonical_json(payload)}

    def _patch_event(self, snapshot: ContextSnapshot, patch: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "kind": CONTEXT_STATE_KIND,
            "mode": "merge_patch",
            "context_epoch": self._context_epoch,
            "base_snapshot_hash": self._snapshot_hash,
            "snapshot_hash": snapshot.snapshot_hash,
            "patch": patch,
        }
        return {"role": "system", "content": canonical_json(payload)}

    def project(
        self,
        *,
        source_messages: Iterable[dict[str, Any]],
        snapshot: ContextSnapshot,
        base_prompt_hash: str,
        tool_schema_hash: str,
        transport: str,
    ) -> ProjectedContext:
        source = self._copy(list(source_messages))
        if self._initialized:
            reset_reason = ""
            if base_prompt_hash != self._base_prompt_hash:
                reset_reason = "base_prompt_changed"
            elif transport != self._transport:
                reset_reason = "transport_changed"
            elif tool_schema_hash != self._tool_schema_hash:
                reset_reason = "tool_schema_changed"
            elif not self._is_prefix(self._source_messages, source):
                reset_reason = "history_replaced"
            if reset_reason:
                self.reset(reset_reason)

        if not self._initialized:
            self._epoch_reason = self._pending_reason
            self._source_messages = source
            self._projected_messages = self._copy(source)
            self._projected_messages.append(self._full_event(snapshot))
            self._sections = self._copy(snapshot.sections)
            self._snapshot_hash = snapshot.snapshot_hash
            self._base_prompt_hash = str(base_prompt_hash or "")
            self._tool_schema_hash = str(tool_schema_hash or "")
            self._transport = str(transport or "")
            self._initialized = True
        else:
            self._projected_messages.extend(
                self._copy(source[len(self._source_messages):])
            )
            self._source_messages = source
            if snapshot.snapshot_hash != self._snapshot_hash:
                patch = json_merge_patch(self._sections, snapshot.sections)
                if patch != {}:
                    self._projected_messages.append(self._patch_event(snapshot, patch))
                self._sections = self._copy(snapshot.sections)
                self._snapshot_hash = snapshot.snapshot_hash

        return ProjectedContext(
            messages=tuple(self._copy(self._projected_messages)),
            context_epoch=self._context_epoch,
            epoch_reason=self._epoch_reason,
            base_prompt_hash=self._base_prompt_hash,
            tool_schema_hash=self._tool_schema_hash,
            state_snapshot_hash=self._snapshot_hash,
            transport=self._transport,
        )


# Canonical cross-Run interface names.  The shorter aliases remain for compatibility with the
# runtime_0015 same-Run projection implementation.
ThreadContextProjectionLedger = ContextProjectionLedger
ProjectedThreadContext = ProjectedContext
