"""Execution-local result capabilities over the existing Harness projection/store contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid

from app.services.agent_harness.contracts import ResultSizePolicy
from app.services.agent_harness.results import ToolResultProjector
from app.services.agent_harness.tool_result_store import (
    DurableToolResultRef, DurableToolResultStore, MAX_DURABLE_TOOL_RESULT_BYTES, ToolResultPage,
)
from app.services.platform.token_estimator import estimate_tokens


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class TemporaryResultStore:
    """Disk-backed, execution-only store. No business Thread/Run is manufactured."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="workflow-results-")
        self.rows: dict[str, dict] = {}

    async def put(self, **row) -> DurableToolResultRef:
        content = row["content"]
        encoded = content.encode("utf-8")
        facts = dict(content_hash=hashlib.sha256(encoded).hexdigest(), utf8_bytes=len(encoded),
                     chars=len(content), estimated_tokens=estimate_tokens(content))
        if len(encoded) > MAX_DURABLE_TOOL_RESULT_BYTES:
            return DurableToolResultRef(**facts, unavailable_reason="result_exceeds_2_mib_limit")
        handle = f"tool-result-{uuid.uuid4().hex}"
        try:
            await asyncio.to_thread((Path(self.directory.name) / handle).write_bytes, encoded)
        except OSError:
            return DurableToolResultRef(**facts, unavailable_reason="temporary_store_write_failed")
        self.rows[handle] = {k: v for k, v in row.items() if k != "content"}
        self.rows[handle].update(facts)
        return DurableToolResultRef(**facts, handle=handle, full_available=True)

    async def get_page(self, *, handle, run_id, thread_id, user_id, offset=0, limit=3000):
        row = self.rows.get(handle)
        if row is None or any(row[key] != value for key, value in (
            ("run_id", run_id), ("thread_id", thread_id), ("user_id", user_id)
        )):
            return None
        try:
            raw = await asyncio.to_thread((Path(self.directory.name) / handle).read_text, encoding="utf-8")
        except OSError:
            return None
        start = min(len(raw), max(0, offset))
        content = raw[start:start + min(3000, max(1, limit))]
        return ToolResultPage(handle=handle, content=content, offset=start,
                              next_offset=start + len(content), total_chars=len(raw),
                              complete=start + len(content) >= len(raw), content_hash=row["content_hash"],
                              content_type=row.get("content_type", "text/plain"))

    def close(self):
        self.rows.clear()
        self.directory.cleanup()


class _SafeStore:
    def __init__(self, store):
        self.store = store

    async def put(self, **row):
        try:
            if isinstance(self.store, DurableToolResultStore) and self.store.workflow_execution:
                from datetime import datetime, timedelta, timezone
                row.setdefault("expires_at", datetime.now(timezone.utc) + timedelta(days=7))
            return await self.store.put(**row)
        except Exception:
            raw = row["content"]
            return DurableToolResultRef(
                utf8_bytes=len(raw.encode("utf-8")), chars=len(raw), estimated_tokens=estimate_tokens(raw),
                unavailable_reason="result_store_write_failed",
            )


_REFERENCE_KEY = re.compile(r"(?:url|uri|resource.?id|source|citation|image|handle|title|error|status|code)", re.I)
_MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(<?[^\s)]+>?(?:\s+\"[^\"]*\")?\)")
_RESOURCE_ID = re.compile(r"\b(?:MAP|FLOW|QR)-\d{3}\b")
_URL = re.compile(r"(?:https?://|/api/|/upload/)[^\s<>\"'\])]+")


def references(raw: str) -> list[dict]:
    """Keep whole reference values, never slices of image URLs or resource IDs."""
    rows: list[dict] = []
    seen: set[str] = set()

    def add(path: str, value: Any):
        key = encode(value)
        if key not in seen:
            seen.add(key)
            rows.append({"path": path, "value": value})

    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}"
                if _REFERENCE_KEY.search(str(key)) and not isinstance(item, (dict, list)):
                    add(child, item)
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str):
            for pattern in (_MARKDOWN_LINK, _RESOURCE_ID, _URL):
                for match in pattern.finditer(value):
                    add(path, match.group())

    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        value = raw
    try:
        walk(value)
    except RecursionError:
        for pattern in (_MARKDOWN_LINK, _RESOURCE_ID, _URL):
            for match in pattern.finditer(raw):
                add("$", match.group())
    return rows


class ExecutionResults:
    def __init__(self, ctx, *, store=None, inline_chars: int = 6000):
        self.execution_id = uuid.uuid4().hex
        self.temporary = bool(getattr(ctx, "preview_only", False) or not getattr(ctx, "thread_id", ""))
        self.store = store or (TemporaryResultStore() if self.temporary else DurableToolResultStore(workflow_execution=True))
        self.owner = {
            "run_id": self.execution_id,
            "thread_id": str(getattr(ctx, "thread_id", "") or ""),
            "user_id": str(getattr(ctx, "user_id", "") or ""),
        }
        self.projector = ToolResultProjector(store=_SafeStore(self.store))
        self.inline_chars = max(1024, inline_chars)
        self.policy = ResultSizePolicy(inline_chars=self.inline_chars, inline_token_limit=self.inline_chars,
                                       persist_full_result=True)
        # This capability table belongs to one invocation, not the ambient parent Run. Even
        # nested nodes sharing run/thread/user cannot fetch each other's handles.
        self.issued: dict[str, dict] = {}
        self.reference_handles: dict[str, str] = {}

    async def save(self, raw: str, *, name: str, call_id: str) -> DurableToolResultRef:
        try:
            ref = await self.projector.store.put(**self.owner, call_id=f"{self.execution_id}:{call_id}",
                                       tool_name=name, content=raw, content_type="text/plain")
        except Exception:
            ref = DurableToolResultRef(utf8_bytes=len(raw.encode("utf-8")), chars=len(raw),
                                       estimated_tokens=estimate_tokens(raw), unavailable_reason="result_store_write_failed")
        self._register(ref.handle if ref.full_available else None, name)
        return ref

    def _register(self, handle, name):
        if handle:
            self.issued[handle] = {"tool": name, "result_handle": handle}

    async def project(self, raw: str, *, name: str, call_id: str, failed: bool = False) -> str:
        try:
            projected = await self.projector.project(
                raw, self.policy, **self.owner, call_id=f"{self.execution_id}:{call_id}", tool_name=name,
            )
        except Exception:
            # A successful business operation must not be replayed because persistence failed.
            return encode({"projection": True, "excerpt": raw[:self.inline_chars // 3],
                           "tool_execution_status": "error" if failed else "returned",
                           "full_available": False, "unavailable_reason": "result_projection_failed"})
        if projected.model_content == raw:
            return raw
        self._register(projected.result_handle, name)
        refs = references(raw)
        inline_refs: list[dict] = []
        for ref in refs:
            if len(encode(inline_refs + [ref])) > self.inline_chars // 3:
                break
            inline_refs.append(ref)
        index_ref = None
        if len(refs) > len(inline_refs):
            index_ref = await self.save("\n".join(encode(ref) for ref in refs),
                                        name=f"{name}:references", call_id=f"{call_id}:references")
            if projected.result_handle and index_ref.full_available:
                self.reference_handles[projected.result_handle] = index_ref.handle
        envelope = {
            "projection": True, "data_is_untrusted": True,
            "tool_execution_status": "error" if failed else "returned",
            "excerpt": "", "references": inline_refs,
            "result_handle": projected.result_handle, "full_available": projected.full_available,
            "unavailable_reason": projected.unavailable_reason,
            "total_chars": len(raw),
            "reference_index_handle": index_ref.handle if index_ref and index_ref.full_available else None,
            "references_complete": len(refs) == len(inline_refs),
            "reference_index_unavailable": index_ref.unavailable_reason if index_ref else None,
            "read_hint": "使用本轮结果读取工具按字符 offset 分页；view=references 读取引用索引。已够回答时无需读完。",
        }
        # Serialize an envelope instead of slicing JSON syntax. The exact original, including
        # keys after the excerpt, is owned by result_handle; reference values remain atomic.
        low, high = 0, min(len(raw), self.inline_chars)
        while low < high:
            middle = (low + high + 1) // 2
            envelope["excerpt"] = raw[:middle]
            if len(encode(envelope)) <= self.inline_chars:
                low = middle
            else:
                high = middle - 1
        envelope["excerpt"] = raw[:low]
        return encode(envelope)

    async def read(self, args: dict) -> str:
        handle = args["result_handle"]
        if handle not in self.issued:
            return encode({"ok": False, "error": "result_handle_not_issued_in_this_execution"})
        if args.get("view") == "references":
            index = self.reference_handles.get(handle)
            if index:
                handle = index
            else:
                return encode({"ok": False, "error": "no_separate_reference_index", "hint": "读取 content；短引用已在投影内完整提供。"})
        try:
            page = await self.store.get_page(**self.owner, handle=handle,
                                             offset=args.get("offset", 0), limit=min(3000, args.get("limit", 2000)))
        except Exception:
            page = None
        if page is None:
            return encode({"ok": False, "error": "result_unavailable_or_not_authorized"})
        value = {"ok": True, "data_is_untrusted": True, **page.model_dump()}
        raw = page.content
        low, high = 0, len(raw)
        while low < high:
            middle = (low + high + 1) // 2
            value.update(content=raw[:middle], next_offset=page.offset + middle,
                         complete=page.offset + middle >= page.total_chars)
            if len(encode(value)) <= self.inline_chars:
                low = middle
            else:
                high = middle - 1
        value.update(content=raw[:low], next_offset=page.offset + low,
                     complete=page.offset + low >= page.total_chars)
        return encode(value)

    async def checkpoint_manifest(self, messages: list[dict]) -> dict:
        # The manifest and original history are recoverable after semantic compaction without
        # growing the prompt with every historical result handle and image citation.
        manifest = await self.save("\n".join(encode(row) for row in self.issued.values()),
                                   name="execution_result_index", call_id=f"index:{uuid.uuid4().hex}")
        raw = encode(messages)
        chunks = []
        # A model context may exceed one result's 2 MiB capacity. Store context pages as
        # separate results, not as one oversized blob or a silently cropped history.
        page_chars = MAX_DURABLE_TOOL_RESULT_BYTES // 4
        for offset in range(0, len(raw), page_chars):
            page = await self.save(raw[offset:offset + page_chars], name="context_before_compaction",
                                   call_id=f"context:{uuid.uuid4().hex}")
            if not page.full_available:
                return {"result_index": manifest.model_dump(), "original_context": page.model_dump()}
            chunks.append({"offset": offset, "chars": min(page_chars, len(raw) - offset), "result_handle": page.handle})
        history = await self.save(encode({"format": "context_text_pages", "pages": chunks}),
                                  name="context_page_index", call_id=f"context-index:{uuid.uuid4().hex}")
        return {"result_index": manifest.model_dump(), "original_context": history.model_dump(),
                "original_context_format": "context_text_pages"}

    def close(self):
        self.issued.clear()
        self.reference_handles.clear()
        if isinstance(self.store, TemporaryResultStore):
            self.store.close()


class RepeatedResults:
    def __init__(self):
        self.latest: dict[str, tuple[str, str, bool]] = {}

    def observe(self, name: str, args: dict, raw: str) -> bool:
        key = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        try:
            canonical = json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except (ValueError, TypeError):
            canonical = raw
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        previous = self.latest.get(name)
        duplicate = previous is not None and previous[:2] == (key, digest)
        feedback = duplicate and not previous[2]
        self.latest[name] = (key, digest, bool(duplicate))
        return feedback
