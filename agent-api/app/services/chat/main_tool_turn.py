"""模型/工具回合的事件投影与轨迹换算：
- map_tool_loop_events：工具循环事件流 → SSE 帧（call_subagent 映射 subagent.*，其余 tool.*）
- trace_to_steps：编排轨迹 → agent_steps 记录（§16.5 可观测/回放）
- strip_images_for_state：挂起游标持久化前剥离多模态图片
"""
import inspect
import re
import logging
import time
from typing import Any, Callable, Optional

from app.services.chat.tools.base import pop_tool_meta

logger = logging.getLogger(__name__)


def trace_to_steps(trace: list, sub_names: dict, message_id: Optional[int],
                   answer: str = "") -> list:
    """编排轨迹 → agent_steps 记录（§16.5/开发计划 Phase 3 可观测）。

    subagent 步骤 meta 带 message_id + 名称，供 chip 持久化回放；最终回答落一条 message。
    """
    steps = []
    for t in trace or []:
        if "control" in set(t.get("semantic_tags") or []):
            continue
        is_sub = t.get("name") == "call_subagent"
        sid = str((t.get("args") or {}).get("subagent_id") or "")
        steps.append({
            "type": "subagent" if is_sub else "tool",
            "content": (t.get("preview") or "")[:1000],
            "meta": {
                "name": t.get("name"), "status": t.get("status"),
                **({"subagent_id": sid, "subagent_name": sub_names.get(sid, ""),
                    "message_id": message_id} if is_sub else {}),
            },
        })
    if answer:
        steps.append({"type": "message", "content": answer[:2000],
                      "meta": {"message_id": message_id}})
    return steps




def _tool_plan_detail(tool_name: str, tool_args=None, preview: str = "") -> str:
    """从真实工具参数提炼一步可读 detail，供任务协作绑定。"""
    args = tool_args if isinstance(tool_args, dict) else {}
    # 优先结构化参数（用户可见意图），再回落 preview 首行
    for key in (
        "query", "image_query", "filename", "path", "file_id",
        "url", "description", "skill_id", "command",
    ):
        value = args.get(key)
        if value is None:
            continue
        text_v = str(value).strip().replace("\n", " ")
        if not text_v:
            continue
        if key == "command":
            # bash 命令整段对用户无意义，取前段可识别目标
            text_v = re.sub(r"\s+", " ", text_v)[:120]
            if len(text_v) > 80:
                text_v = text_v[:79] + "…"
            return text_v
        if key in {"filename", "path"}:
            # 只留文件名，去掉工作区前缀噪音
            text_v = text_v.replace("\\", "/").rstrip("/")
            text_v = text_v.split("/")[-1] or text_v
        if key == "url":
            text_v = re.sub(r"^https?://(www\.)?", "", text_v, flags=re.I)
            text_v = text_v.split("?", 1)[0][:100]
        return text_v[:200]
    prev = str(preview or "").strip().replace("\n", " ")
    if prev:
        # 过滤纯协议/内部回执
        if re.search(r"^(工具|系统|已|失败|error)", prev, re.I):
            return ""
        return prev[:160]
    _ = tool_name  # 无参数时不要用裸工具名污染 detail（内部实现腔）
    return ""


def _merge_step_detail(existing, incoming: str, *, max_len: int = 240) -> str:
    """多轮检索/多动作时累加 detail，去重、控长。"""
    new = str(incoming or "").strip()
    if not new:
        return str(existing or "").strip()[:max_len]
    old = str(existing or "").strip()
    if not old:
        return new[:max_len]
    if new in old:
        return old[:max_len]
    # 新意图在前，保留最近上下文
    merged = f"{new} · {old}"
    if len(merged) <= max_len:
        return merged
    return (new[: max_len - 1] + "…") if len(new) >= max_len else merged[: max_len - 1] + "…"


def _sync_plan_with_tool(
    plan,
    tool_name: str,
    tool_args=None,
    preview: str = "",
    phase: str = "success",
):
    """任务协作状态推进 + 内容绑定（/ 2026-08-09）。

    状态规则：
    - **检索类**工具（search_web / browser_*）：只允许动「检索/查找…」类标题步骤——
      把它置为 running；**绝不**因又一次检索而完成「整理结论/交付」等后续步骤。
      （旧实现：检索标题步骤完成后，后续 search 会 fallthrough 到第一个 open，
      把 整理/交付 连标 completed，造成面板 3/3 全绿但消息里还在搜的假同步。）
    - **写/执行类**工具：当前 running 标 completed，下一 pending → running。

    内容绑定：
    - To-do 不能只剩目标模板。工具参数里的 query/filename 等写入命中步骤的 detail
      （多轮检索累加），让面板反映真实工作而非空标题。
    - phase=start：只标 running + 绑定 detail，不提前 completed
    - phase=success：写/执行类完成当前步并推进；检索类保持 running 且绑定 detail
    """
    if not isinstance(plan, list) or not plan:
        return plan
    name = str(tool_name or "").strip()
    if not name:
        return plan
    write_like = name in {
        "bash", "fetch_ppt_asset", "publish_ppt_artifact", "write_file", "edit_file", "download_url",
    }
    search_like = name in {
        "search_web", "browser_fetch", "web_search",
        "browser_open", "browser_act",
    }
    if not (write_like or search_like):
        return plan

    steps = [dict(s) for s in plan if isinstance(s, dict)]
    if not steps:
        return plan

    open_set = {"pending", "running", "in_progress", "active", "doing", "ongoing", ""}
    done_set = {"completed", "done", "finished", "complete", "ok", "succeeded"}
    running_set = {"running", "in_progress", "active", "doing", "ongoing"}
    search_keys = (
        "查找", "检索", "搜索", "天气", "信息", "调研", "查阅", "搜集", "收集", "爬取",
    )
    detail = _tool_plan_detail(name, tool_args, preview)
    phase = "start" if str(phase or "").lower() == "start" else "success"

    def _st(s):
        return str(s.get("status") or "").lower()

    def _title(s):
        return str(s.get("title") or "")

    def _is_search_title(s):
        t = _title(s)
        return any(k in t for k in search_keys)

    def _bind(i: int) -> bool:
        if not detail:
            return False
        merged = _merge_step_detail(steps[i].get("detail"), detail)
        if merged == str(steps[i].get("detail") or "").strip():
            return False
        steps[i]["detail"] = merged
        return True

    if search_like:
        # 只认检索类标题步骤：优先 running 的检索步，否则第一个未完成的检索步。
        # 命中则保持/置为 running，不 completed——多轮检索会继续挂在同一步下。
        target = None
        for i, s in enumerate(steps):
            if _st(s) in done_set or not _is_search_title(s):
                continue
            if _st(s) in running_set:
                target = i
                break
            if target is None:
                target = i
        if target is None:
            return plan  # 计划已过检索阶段：后续 search 不再误推 整理/交付
        changed = False
        if _st(steps[target]) != "running":
            steps[target]["status"] = "running"
            changed = True
        if _bind(target):
            changed = True
        return steps if changed else plan

    # 写/执行类
    target = None
    for i, s in enumerate(steps):
        if _st(s) in running_set:
            target = i
            break
    if target is None:
        for i, s in enumerate(steps):
            if _st(s) in open_set:
                target = i
                break
    if target is None:
        return plan

    def _bind_write_target(i: int) -> bool:
        """写工具 detail 不得污染检索步骤（用户会看到『检索…』下挂文件名）。"""
        if _is_search_title(steps[i]):
            return False
        return _bind(i)

    if phase == "start":
        # 动手瞬间：只标 running + 绑定 detail，不提前 completed。
        # 若当前 running 仍是检索步，detail 绑到后续第一个非检索 open 步（不改状态，
        # 避免 start 就把检索步标 completed，success 才负责推进）。
        changed = False
        bind_i = target
        if _is_search_title(steps[target]):
            for j in range(target + 1, len(steps)):
                if _st(steps[j]) in open_set and not _is_search_title(steps[j]):
                    bind_i = j
                    break
        if bind_i == target:
            if _st(steps[target]) != "running":
                steps[target]["status"] = "running"
                changed = True
            if _bind_write_target(target):
                changed = True
        else:
            if _bind_write_target(bind_i):
                changed = True
        return steps if changed else plan

    steps[target]["status"] = "completed"
    # 完成检索步时不要用写工具文件名覆盖其 query detail
    if detail and not _is_search_title(steps[target]) and not str(steps[target].get("detail") or "").strip():
        steps[target]["detail"] = detail[:240]
    for j in range(target + 1, len(steps)):
        if _st(steps[j]) in open_set:
            steps[j]["status"] = "running"
            # 把本轮写动作意图绑到新 running 步（若它不是检索）
            if detail and not _is_search_title(steps[j]):
                steps[j]["detail"] = _merge_step_detail(steps[j].get("detail"), detail)
            break
    return steps


def _advance_plan_after_tool(plan, tool_name: str, tool_args=None, preview: str = "", phase: str = "success"):
    """兼容旧调用；完整逻辑见 _sync_plan_with_tool。"""
    return _sync_plan_with_tool(
        plan, tool_name, tool_args=tool_args, preview=preview, phase=phase,
    )


def _merge_tool_result_meta(ev, tool_meta):
    """合并 tool_meta_sink 与 ToolExecutionResult.ui/artifacts。

    P1.6 之后 paths/shell 会返回带 ui/artifacts 的 observation；若只取 ui，会盖掉
    sink 里前端产物卡依赖的 files/action（executionTimeline 只认 meta.files）。
    合并规则：sink 为底（files/action/count/urls…），observation.ui 补产品文案，
    artifacts 在尚无 files 时回填为 files。
    """
    if ev.get("meta") is not None:
        return ev.get("meta")
    sink = pop_tool_meta(tool_meta, ev.get("call_id", ""), ev.get("name", ""))
    obs = ev.get("observation") if isinstance(ev.get("observation"), dict) else {}
    structured = obs.get("structured_data") if isinstance(obs.get("structured_data"), dict) else {}
    ui = structured.get("ui") if isinstance(structured.get("ui"), dict) else None
    arts = obs.get("artifact_refs") if isinstance(obs.get("artifact_refs"), list) else None
    receipts = obs.get("receipts") if isinstance(obs.get("receipts"), list) else None
    if not sink and not ui and not arts and not receipts:
        return None
    meta = {}
    if isinstance(sink, dict):
        meta.update(sink)
    if ui:
        # ui 侧 summary/detail 不覆盖 sink 已有结构化键；action 仅在 sink 无 action 时补
        for k, v in ui.items():
            if k == "action" and meta.get("action"):
                continue
            if k in ("files", "urls", "read", "images", "count", "review_status", "shot") and k in meta:
                continue
            if v is not None and v != "":
                meta[k] = v
    if arts and not meta.get("files"):
        files = []
        for a in arts:
            if not isinstance(a, dict):
                continue
            fid = str(a.get("file_id") or a.get("id") or "").strip()
            fn = str(a.get("filename") or a.get("name") or "").strip()
            if not fid and not fn:
                continue
            row = {
                "id": fid or None,
                "filename": fn or None,
                "size": a.get("bytes") if a.get("bytes") is not None else a.get("size"),
            }
            for key in (
                "mime", "source", "origin", "review", "versionNo",
                "deliverable", "draft", "previewOnly",
            ):
                if a.get(key) is not None:
                    row[key] = a[key]
            files.append({kk: vv for kk, vv in row.items() if vv is not None})
        if files:
            meta["files"] = files
    if receipts:
        # 状态变更回执进入 Runtime 事件审计，但只保留合同字段，不夹带记忆正文等用户数据。
        safe_receipts = []
        for receipt in receipts[:10]:
            if not isinstance(receipt, dict):
                continue
            row = {
                key: str(receipt.get(key) or "")
                for key in ("kind", "action", "id")
                if receipt.get(key) is not None
            }
            if row.get("kind") and row.get("id"):
                safe_receipts.append(row)
        if safe_receipts:
            meta["receipts"] = safe_receipts
    return meta or None


def _tool_result_delivered_artifact(ev: dict, meta: Optional[dict] = None) -> bool:
    """Use persisted structured receipts, never a successful tool name, as delivery truth."""
    from app.services.files.deliverable import is_deliverable

    obs = ev.get("observation") if isinstance(ev.get("observation"), dict) else {}
    if obs and str(obs.get("status") or "").lower() not in {
        "succeeded", "success", "completed", "ok",
    }:
        return False
    rows = []
    if isinstance(obs.get("artifact_refs"), list):
        rows.extend(obs["artifact_refs"])
    structured = obs.get("structured_data") if isinstance(obs.get("structured_data"), dict) else {}
    ui = structured.get("ui") if isinstance(structured.get("ui"), dict) else {}
    if isinstance(ui.get("files"), list):
        rows.extend(ui["files"])
    if isinstance(meta, dict) and isinstance(meta.get("files"), list):
        rows.extend(meta["files"])
    for row in rows:
        filename = str(
            (row.get("filename") or row.get("name") or "")
            if isinstance(row, dict) else row or ""
        )
        if is_deliverable(filename, "generated"):
            return True
    return False


def _delivered_artifact_rows(meta: Optional[dict]) -> list[dict]:
    """Return only persisted deliverables suitable for the canonical artifact.saved event."""
    from app.services.files.deliverable import is_deliverable

    rows = meta.get("files") if isinstance(meta, dict) else None
    delivered = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        file_id = str(row.get("id") or row.get("file_id") or "").strip()
        filename = str(row.get("filename") or row.get("name") or "").strip()
        if file_id and filename and is_deliverable(filename, "generated"):
            delivered.append(row)
    return delivered


def _append_tool_result_trace(out: dict, ev: dict) -> dict:
    """Retain each authoritative tool receipt before the model's final turn.

    The final event still replaces this incremental trace with the driver's complete trace.  The
    incremental copy is the recovery path when the next model request fails after a tool already
    returned, so Completion Verifier can see the persisted observation instead of guessing from
    streamed UI flags.
    """
    item = {
        "call_id": str(ev.get("call_id") or ""),
        "name": str(ev.get("name") or ""),
        "args": dict(ev.get("args") or {}) if isinstance(ev.get("args"), dict) else {},
        "status": str(ev.get("status") or ""),
        "preview": str(ev.get("preview") or ""),
    }
    if ev.get("revision_epoch") is not None:
        item["revision_epoch"] = ev["revision_epoch"]
    if isinstance(ev.get("semantic_tags"), (list, tuple, set)):
        item["semantic_tags"] = [str(tag) for tag in ev["semantic_tags"]]
    observation = ev.get("observation")
    if isinstance(observation, dict):
        item["observation"] = dict(observation)
        artifacts = observation.get("artifact_refs")
        if isinstance(artifacts, list) and artifacts:
            item["artifacts"] = list(artifacts)
    trace = out.get("trace")
    if not isinstance(trace, list):
        trace = []
        out["trace"] = trace
    trace.append(item)
    return item


async def convert_loop_failure(runtime_policy, env, error: BaseException):
    """交互式内置助手（面试）可把已耗尽重试的模型失败改判为终态错误。

    否则任何非 TerminalRunError 都进 `_pump_background_run` 的恢复路径：waiting_system →
    无限退避重排，用户只看到「正在自动恢复…」，作答框一直锁着。改判后是可读原因 + 可重发。
    已是终态错误的不改判；策略自身抛错时按未改判处理，绝不吞掉原异常。
    """
    if not runtime_policy or not getattr(runtime_policy, "terminal_failure", None):
        return None
    from app.services.agent_harness.public_errors import TerminalRunError
    if isinstance(error, TerminalRunError):
        return None
    try:
        converted = await runtime_policy.terminal_failure(env, error)
    except Exception:  # noqa: BLE001
        logger.warning("builtin terminal_failure hook failed; keep recovery path", exc_info=True)
        return None
    return converted if isinstance(converted, TerminalRunError) else None


async def map_tool_loop_events(channel, ev_iter, sub_names: dict, out: dict, tool_meta: Optional[dict] = None,
                               ctx_window: int = 0, strict_ppt_publish: bool = False,
                               subagent_icons: Optional[dict] = None,
                               research_profile: bool = False,
                               committed_text_only: bool = False,
                               public_event_mapper: Optional[Callable[[dict], dict]] = None):
    """把 drive_model 事件流映射为 SSE 帧；聚合结果写入 out（异步生成器无返回值）。

    call_subagent 映射为 subagent.*（前端渲染子智能体 chip），其余映射 tool.*。
    suspend 事件终止映射并存入 out["suspended"]，由调用方持久化游标。
    tool_meta：build_tools 的 tool_meta_sink——工具完成时取走（pop）对应元信息随
    tool.completed 下发（如 search_web 的结果数/已读页面，思考时间线渲染用）。
    ctx_window：上下文窗口大小，用于把逐轮真实用量换算成占比后下发（0=不发用量帧）。
    """
    reasoning_buf: list = []
    reasoning_t0 = 0.0
    sub_reasoning: dict = {}
    subagent_tasks: dict = {}

    def _flush_reasoning() -> str:
        nonlocal reasoning_t0
        if not reasoning_buf:
            return ""
        full = "".join(reasoning_buf)
        reasoning_buf.clear()
        seconds = max(0.0, time.monotonic() - reasoning_t0) if reasoning_t0 else 0.0
        reasoning_t0 = 0.0
        return channel.message_reasoning_completed(full, seconds)

    def _flush_sub_reasoning(sid: str) -> str:
        parts = sub_reasoning.pop(sid, None)
        if not parts:
            return ""
        return channel.subagent_reasoning_completed(sid, "".join(parts))

    async for ev in ev_iter:
        et = ev.get("type")
        # Structured applications publish the committed business receipt. Draft tokens
        # and reasoning stay private; process commentary and sanitized execution cards
        # can still show the same mid-turn narration as the main agent.
        if committed_text_only and et in {"delta", "reasoning"}:
            continue
        if committed_text_only and et in {"tool_started", "tool_progress", "tool_result"}:
            # 阶段文案由助手自己的投影给出（面试：按冻结动作说「评估你的回答，准备第 N 题」）；
            # 没传工厂时退回面试的无上下文投影，保证旧调用方仍不泄漏草稿与工具参数。
            if public_event_mapper is None:
                from app.services.chat.builtin_assistants.interview.tools import public_interview_loop_event
                public_event_mapper = public_interview_loop_event
            ev = public_event_mapper(ev)
            if inspect.isawaitable(ev):
                ev = await ev
        if et != "reasoning":
            completed = _flush_reasoning()
            if completed:
                yield completed
        if et == "delta":
            text = ev.get("text") or ""
            if text:
                out["streamed_any"] = True
                out["answer"] += text
                yield channel.message_delta(text)
        elif et == "reasoning":
            text = ev.get("text") or ""
            if text:
                if not reasoning_buf:
                    # model_driver 在工具轮内暂存 provider reasoning，确认 commentary 后才
                    # 投影。使用它记录的单调起点，避免 UI 把真实思考耗时误写成 0 秒。
                    try:
                        reasoning_t0 = float(ev.get("started_at") or 0.0) or time.monotonic()
                    except (TypeError, ValueError):
                        reasoning_t0 = time.monotonic()
                reasoning_buf.append(text)
                yield channel.message_reasoning_delta(text)
        elif et == "commentary":
            # 过程说明（开场白/衔接语）：已随 delta 流出并计入 out["answer"]，此处事后
            # 从正文剔除（与 drive_model 的 answer_parts 口径一致——中途失败持久化
            # 部分正文时不带过程说明），并下发 message.commentary 让前端把它挪位。
            # v3.01：stream 可能已剥「我来…。」，正文只剩过程残段——不能只 endsWith 全文。
            from app.services.chat import turn_finalizer as _tf_peel
            raw_text = ev.get("text") or ""
            text = _tf_peel.scrub_public_runtime_text(raw_text)
            if text:
                out["answer"] = _tf_peel.peel_commentary_from_answer(
                    out["answer"], raw_text,
                )
                # 同轮 tool call 旁的 content 可能是模型复述的内部 schema/脚本约束。
                # 它已留在模型 messages 中供下一轮纠偏，但不属于面向用户的执行进度。
                # 先从 answer 剥离，再决定是否下发，避免它在最终气泡或历史轨迹中泄漏。
                from app.services.agent_harness import model_driver as _main_agent
                previous_commentary = str(out.get("latest_public_commentary") or "").strip()
                if (
                    _main_agent.is_user_visible_commentary(
                        text,
                        kind=str(ev.get("kind") or ""),
                    )
                    and (
                        committed_text_only
                        or not _main_agent.is_low_value_action_commentary(text)
                    )
                    and not _main_agent.commentary_repeats_previous(
                        text, previous_commentary,
                    )
                    and not _main_agent.commentary_repeats_tool_output(
                        text, out.get("trace") or [],
                    )
                ):
                    out["latest_public_commentary"] = text
                    yield channel.message_commentary(
                        text,
                        str(ev.get("kind") or ""),
                        evidence_item_ids=(
                            list(ev.get("evidence_item_ids") or [])
                            if isinstance(ev.get("evidence_item_ids"), list)
                            else None
                        ),
                        next_action=str(ev.get("next_action") or ""),
                    )
        elif et == "model_connection":
            yield channel.model_connection(
                str(ev.get("status") or ""),
                str(ev.get("transport") or ""),
                attempt=(
                    int(ev["attempt"])
                    if ev.get("attempt") is not None else None
                ),
                max_retries=(
                    int(ev["max_retries"])
                    if ev.get("max_retries") is not None else None
                ),
                delay_seconds=(
                    float(ev["delay_seconds"])
                    if ev.get("delay_seconds") is not None else None
                ),
            )
        elif et == "input_applied":
            # 运行中引导已注入当前轮（不中断）：前端据此把队列卡收起、标记该条已生效
            yield channel.input_applied(
                str(ev.get("input_id") or ""), str(ev.get("content") or ""),
                str(ev.get("scope") or "turn"),
                int(ev["revision_epoch"]) if ev.get("revision_epoch") is not None else None,
            )
        elif et == "input_rejected":
            yield channel.input_rejected(
                str(ev.get("input_id") or ""),
                str(ev.get("content") or ""),
                str(ev.get("reason") or "无法安全合并到当前任务"),
            )
        elif et == "plan":
            yield channel.plan_updated(ev.get("items") or [], ev.get("round") or 1)
        elif et == "task_plan":
            # 语义任务计划整表投影：update_plan 修订结构，ToolObservation
            # 经 Plan Controller 推进状态/证据；前端「任务协作」只渲染权威快照。
            _steps = ev.get("steps") or []
            out["latest_task_plan"] = list(_steps) if isinstance(_steps, list) else []
            yield channel.task_plan_updated(_steps)
            # research.progress 是 Research Profile 的阶段机协议，不是通用的
            # “用过 update_plan”标记。旧逻辑会把 Plan/Standard 也染成研究轮，
            # 前端随后就把普通终答收进研究报告蓝框。
            if research_profile:
                try:
                    from app.services.agent_harness.research.engine import (
                        progress_payload, sync_topics_from_plan,
                    )
                    _ledger = await sync_topics_from_plan(channel.run_id, _steps)
                    yield channel.research_progress(progress_payload(_ledger))
                except Exception:  # noqa: BLE001
                    pass
        elif et == "compaction":
            status = str(ev.get("status") or "started")
            yield channel.context_compaction(
                status,
                seconds=ev.get("seconds"),
                tokens_before=ev.get("tokens_before"),
                tokens_after=ev.get("tokens_after"),
            )
            if status == "completed":
                yield channel.context_compacted("Context compacted")
        elif et == "capability_loaded":
            yield channel.capability_loaded(ev.get("names") or [])
        elif et == "tool_started":
            if ev.get("name") == "recommend_agent":
                # 推荐是无副作用控制工具：它只投影最终可选卡片，不在时间线
                # 显示“正在调用推荐工具”这种内部执行噪音。
                continue
            if ev.get("name") == "call_subagent":
                _args = ev.get("args") or {}
                sid = str(_args.get("subagent_id") or "")
                subagent_tasks[sid] = str(_args.get("input") or "")
                yield channel.subagent_preparing(
                    sid, sub_names.get(sid, ""),
                    task=str(_args.get("input") or ""),
                )
            else:
                yield channel.tool_started(
                    ev.get("name", ""), ev.get("args"), str(ev.get("call_id") or ""),
                )
        elif et == "tool_progress":
            yield channel.tool_progress(
                ev.get("name", ""), ev.get("stage", ""), ev.get("label", ""),
                ev.get("elapsed_ms", 0), ev.get("detail"), bool(ev.get("heartbeat")),
                str(ev.get("call_id") or ""),
            )
        elif et == "tool_result":
            if ev.get("name") == "recommend_agent":
                _obs = ev.get("observation") if isinstance(ev.get("observation"), dict) else {}
                _structured = (
                    _obs.get("structured_data")
                    if isinstance(_obs.get("structured_data"), dict)
                    else {}
                )
                _payload = _structured.get("ui") if isinstance(_structured.get("ui"), dict) else {}
                _rows = _payload.get("recommendations") if isinstance(_payload.get("recommendations"), list) else []
                if (
                    ev.get("status") != "failed"
                    and _payload.get("status") == "matched"
                    and not _payload.get("shadow")
                    and _rows
                ):
                    _ids = [
                        str(item.get("id") or "") for item in _rows
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    ]
                    _reasons = {
                        str(item.get("id")): str(item.get("reason") or "")[:120]
                        for item in _rows
                        if isinstance(item, dict) and str(item.get("id") or "").strip()
                    }
                    if _ids:
                        yield channel.recommend_agents(
                            _ids,
                            intent=str(_payload.get("intent") or "capability_gap"),
                            confidence=str(_payload.get("confidence") or "strong"),
                            reasons=_reasons,
                        )
                continue
            _trace_item = _append_tool_result_trace(out, ev)
            if ev.get("name") == "call_subagent":
                sid = str((ev.get("args") or {}).get("subagent_id") or "")
                _sfl = _flush_sub_reasoning(sid)
                if _sfl:
                    yield _sfl
                if ev.get("status") == "failed":
                    yield channel.subagent_failed(sid, sub_names.get(sid, ""), ev.get("preview", ""))
                else:
                    _result_meta = _merge_tool_result_meta(ev, tool_meta)
                    if isinstance(_result_meta, dict):
                        _trace_item["meta"] = _result_meta
                    _saved_artifacts = _delivered_artifact_rows(_result_meta)
                    if _saved_artifacts:
                        out["any_tool_succeeded"] = True
                        out["write_tool_succeeded"] = True
                    # 执行团队一期：验收单帧先于收尾帧（前端先见裁定明细，再收尾归档）
                    if isinstance(ev.get("acceptance"), dict):
                        yield channel.subagent_review(sid, sub_names.get(sid, ""), ev["acceptance"])
                    # 结果报告带全文（result_text，6000 上限）：执行卡滚动看完整内容
                    yield channel.subagent_completed(
                        sid, sub_names.get(sid, ""), ev.get("result_text") or ev.get("preview", ""),
                        acceptance=ev.get("acceptance"),
                        role_name=str((ev.get("args") or {}).get("role_name") or ""),
                        files=_saved_artifacts,
                    )
                    if _saved_artifacts:
                        yield channel.artifact_saved(_saved_artifacts, source="call_subagent")
            elif ev.get("quality_retry"):
                # 内部质检失败只驱动静默返工：丢弃本轮 review/files meta，不把评分、拦截原因、
                # 失败草稿或返工指令下发给用户。终端行保持正常“继续优化”语义。
                pop_tool_meta(tool_meta, ev.get("call_id", ""), ev.get("name", ""))
                yield channel.tool_completed(
                    ev.get("name", ""), ev.get("preview", ""),
                    call_id=str(ev.get("call_id") or ""),
                )
            elif ev.get("status") == "failed":
                _failed_meta = _merge_tool_result_meta(ev, tool_meta)
                if isinstance(_failed_meta, dict):
                    _trace_item["meta"] = _failed_meta
                from app.services.chat import turn_finalizer as _tf_public
                yield channel.tool_failed(
                    ev.get("name", ""),
                    _tf_public.scrub_public_runtime_text(ev.get("preview", "")),
                    meta=_failed_meta,
                    call_id=str(ev.get("call_id") or ""),
                )
            else:
                # meta 按 call_id 归属取走：同一轮里并发的两个 read_file 都写 sink["read_file"]，
                # 按名 pop 会让第一张卡拿到别人的文件名、第二张拿到 None（见 ToolMetaSink）。
                # P1.6：observation.ui 与 sink 必须合并，不能让 ui 盖掉 files/action。
                _tname = str(ev.get("name") or "")
                _result_meta = _merge_tool_result_meta(ev, tool_meta)
                if isinstance(_result_meta, dict):
                    _trace_item["meta"] = _result_meta
                _delivered_artifact = _tool_result_delivered_artifact(ev, _result_meta)
                out["any_tool_succeeded"] = True
                if _tname == "publish_ppt_artifact" and _delivered_artifact:
                    out["published_artifact_succeeded"] = True
                if _delivered_artifact:
                    out["write_tool_succeeded"] = True
                yield channel.tool_completed(
                    _tname, ev.get("preview", ""),
                    meta=_result_meta,
                    call_id=str(ev.get("call_id") or ""),
                )
                if research_profile and _tname in {"search_web", "deep_read"}:
                    try:
                        from app.services.agent_harness.research.engine import (
                            ingest_tool_receipt, progress_payload,
                        )
                        _obs = ev.get("observation") if isinstance(ev.get("observation"), dict) else {}
                        _ledger = await ingest_tool_receipt(
                            channel.run_id,
                            tool_name=_tname,
                            meta=_result_meta if isinstance(_result_meta, dict) else None,
                            citations=list(_obs.get("evidence_refs") or []),
                            query=str((ev.get("args") or {}).get("query") or (ev.get("args") or {}).get("url") or ""),
                        )
                        if _ledger is not None:
                            yield channel.research_progress(progress_payload(_ledger))
                    except Exception:  # noqa: BLE001
                        pass
                _saved_artifacts = _delivered_artifact_rows(_result_meta)
                if _saved_artifacts:
                    yield channel.artifact_saved(_saved_artifacts, source=_tname)
        elif et == "subagent_event":
            # 子智能体工作流内部过程实时冒泡（供前端「子智能体工作窗口」展示）
            sev = ev.get("event") or {}
            sid = str(sev.get("subagent_id") or "")
            st = sev.get("type")
            if st == "started":
                yield channel.subagent_started(
                    sid, str(sev.get("subagent_name") or sub_names.get(sid, "")),
                    task=str(sev.get("task") or subagent_tasks.get(sid) or ""),
                    icon=str(sev.get("icon") or (subagent_icons or {}).get(sid) or ""),
                )
            elif st == "node":
                yield channel.subagent_node(sid, sev.get("label", ""), sev.get("status", ""))
            elif st == "delta":
                yield channel.subagent_delta(sid, sev.get("text", ""))
            elif st == "reasoning":
                if sev.get("text"):
                    sub_reasoning.setdefault(sid, []).append(str(sev.get("text")))
                    yield channel.subagent_reasoning(sid, sev.get("text", ""))
        elif et == "usage":
            # 逐轮真实用量（main_agent 每轮发一次）→ 立刻转成 context_usage(actual)。
            # 不转的话工具期一帧 actual 都没有（唯一的 actual 在整轮收尾才发），前端计量行
            # 就一直显示 0 tokens —— 而它刻意忽略 estimate 帧（纯问答会把估算误差显示成产出）。
            _rt = int(ev.get("usage_prompt_tokens") or 0)
            if _rt > 0 and ctx_window > 0:
                out["usage_prompt_tokens"] = _rt
                yield channel.context_usage(
                    _rt, ctx_window, round(min(_rt / ctx_window, 1.0), 3), kind="actual")
        elif et == "suspend":
            # HITL 挂起（ask_user_choice 等）：此前已 tool.started，但不会再有 tool_result。
            # 不补 tool.completed 时，前端执行时间线永久停在「正在请求补充信息…」并持续闪烁，
            # 即使用户早已答完、后续 search 已开跑（2026-08-12 真机）。
            # call_subagent 走 subagent_* 通道，不在这里补 tool 行。
            _sus_name = str(ev.get("name") or "")
            if _sus_name and _sus_name != "call_subagent":
                yield channel.tool_completed(
                    _sus_name,
                    "已请求用户补充信息" if _sus_name == "ask_user_choice" else "已挂起等待用户",
                    call_id=str(ev.get("pending_tool_call_id") or ""),
                )
            out["suspended"] = ev
            return
        elif et == "final":
            out["answer"] = ev.get("answer") or out["answer"]
            out["trace"] = ev.get("trace") or []
            out["usage_prompt_tokens"] = int(ev.get("usage_prompt_tokens") or 0)
            out["projection_commit"] = ev.get("_projection_commit")
            # 任务模式 2.0：final 可携带终态处置（cancelled=跳过计划 / failed=上下文缺失）
            # 与直接 outcome（plan_only 成功交付计划）；调用方据此收尾 Run，不猜测
            if ev.get("run_disposition"):
                out["run_disposition"] = str(ev["run_disposition"])
            if ev.get("task_outcome"):
                out["task_outcome"] = str(ev["task_outcome"])
            if ev.get("force_converge"):
                out["force_converge"] = str(ev["force_converge"])
            if ev.get("loop_steps") is not None:
                try:
                    out["loop_steps"] = int(ev["loop_steps"])
                except (TypeError, ValueError):
                    pass
    completed = _flush_reasoning()
    if completed:
        yield completed
def strip_images_for_state(msgs: list) -> list:
    """Use the canonical recovery sanitizer; never create a second lossy HITL history."""

    from app.services.agent_harness.run_store import trim_loop_checkpoint_messages

    return trim_loop_checkpoint_messages(msgs or [])


def compose_tool_loop_system_prompt(
    base_prompt: str, *, summary_block: str = "",
    skill_catalog_block: str = "", turn_guard_prompt: str = "",
) -> str:
    """拼工具循环这一路的 system prompt。抽成纯函数是为了能被断言。

    2026-07-27 事故：Skill 目录只被拼进 plain_turn / fallback 直答的 prompt，而工具循环
    是**无条件进入**的默认路径 —— 模型手握 use_skill、工具描述还写着「id 取自系统提示词
    的 Skill 目录」，却从没见过任何 skill_id。真机问「平台上有哪些技能」，模型照那句描述
    编出了两个不存在的技能和 id。功能上线 12 天一天没生效过。

    没被拦住的原因是这段拼接内联在 run_agent_turn 里、无法单测，既有测试只能断言
    `_fetch_skill_catalog_block` 自己的产物。抽出来之后 tests/test_tool_loop_prompt.py
    直接对这个函数下断言。

    顺序即优先级：本轮约束（turn_guard_prompt）恒为最后一段，不被目录挤掉。
    """
    return "\n\n".join(
        p for p in [base_prompt, summary_block, skill_catalog_block, turn_guard_prompt] if p
    )


def tool_loop_projection_world_state(
    *,
    summary_block: str = "",
    skill_catalog_block: str = "",
    knowledge_context: str = "",
    turn_guard: str = "",
) -> dict[str, str]:
    """Name dynamic tool-loop sections so Context Compiler cannot silently drop them."""

    return {
        key: value
        for key, value in {
            "conversation_summary": str(summary_block or ""),
            "skill_catalog": str(skill_catalog_block or ""),
            "knowledge_context": str(knowledge_context or ""),
            "turn_guard": str(turn_guard or ""),
        }.items()
        if value
    }


def recovery_projection_world_state(
    fresh: dict[str, Any],
    *,
    initial_messages: Optional[list],
    checkpoint_world_state: Optional[dict[str, Any]],
    turn_constraints: str = "",
) -> dict[str, Any]:
    """Keep the message cursor and dynamic facts from the same recovery checkpoint."""

    result = dict(checkpoint_world_state if initial_messages and checkpoint_world_state else fresh or {})
    if turn_constraints:
        # Reassert the control plane without mixing freshly recomputed evidence
        # into a checkpoint's atomic facts/message cursor.
        result["turn_constraints"] = turn_constraints
    return result


def requires_truthful_action_failure(turn_intent: str) -> bool:
    """Execution requests must fail honestly when no action could be started."""
    return str(turn_intent or "").strip().lower() in {"execute", "revise"}


def truthful_action_failure_message(error: str, tool_count: int) -> str:
    """User-facing failure text must match whether tools already ran."""
    if int(tool_count or 0) > 0:
        detail = str(error or "未知错误").strip() or "未知错误"
        return (
            f"任务执行中断：{detail}。"
            f"本轮已执行过 {int(tool_count)} 次工具，请查看时间线后继续。"
        )
    return "任务未能启动，本轮未执行任何操作，请重试"


async def _persist_recovery_evidence(
    *,
    run_id: str,
    thread_id: str,
    out,
    assistant_persisted: bool,
    spawn_partial_persist,
    record_tool_observations,
    error: BaseException,
) -> None:
    """Checkpoint facts from a failed turn before the owner worker retries it.

    This helper deliberately has no Run disposition side effects.  Tool observations are the
    durable recovery input; a streamed answer is only a visible draft and is therefore stored as
    ``interrupted``.  Persistence is best effort so a database outage cannot replace the original
    model/tool/runtime exception that must reach RunHub/Worker.
    """
    trace = out.get("trace") or []
    if trace:
        try:
            await record_tool_observations(run_id, trace, fail_closed=True)
        except Exception as persist_exc:  # noqa: BLE001
            logger.warning(
                "恢复前工具 observation 持久化失败 run=%s: %s",
                run_id,
                persist_exc,
            )

    draft = str(out.get("answer") or "").strip()
    if (
        not assistant_persisted
        and bool(out.get("streamed_any"))
        and draft
        and spawn_partial_persist is not None
    ):
        try:
            spawn_partial_persist(
                run_id,
                thread_id,
                draft,
                status="interrupted",
            )
        except Exception as persist_exc:  # noqa: BLE001
            logger.warning(
                "恢复前可见草稿持久化失败 run=%s: %s",
                run_id,
                persist_exc,
            )

    logger.warning(
        "主对话回合异常，已交给恢复层 run=%s error=%s: %s",
        run_id,
        type(error).__name__,
        error,
    )


# ===== 结构手术 Phase 2c:工具循环回合(自 stream_chat 原样搬迁,行为零变化)=====
async def run_agent_turn(env):
    """主路径:恒进工具循环(orchestrator 按模式在 graph 与老 loop 间选择)。

    成功/挂起以 return 收尾；模型、工具、沙箱、上下文或运行时异常只保存恢复证据并
    原样上抛给 RunHub/Worker。普通对话不因执行异常切换到另一套终态。
    """
    # direct answers are intentionally tool-free.  This is before every preflight (skills,
    # connector discovery, memory retrieval and browser availability) so a simple question can
    # start streaming immediately.
    if getattr(env, "route", "agent") == "direct_answer":
        # 有意零工具问答：走 plain 主体，但不是「工具故障回退」——护栏文案不同
        # （禁止再说「工具系统临时不可用」）。
        env.fallback_plain = True
        env.intentional_pure_qa = True
        return

    import asyncio

    from sqlalchemy import func

    from app.core.config import settings

    from app.models import ChatMessage
    from app.services import sse_protocol
    from app.services.agent_harness import model_driver
    from app.services.agents import capability_registry
    from app.services.knowledge import citation_service, web_search_service
    from app.services.tasks import task_run_service
    from app.services.memory import memory_service
    from app.services.chat import subagent_turn, turn_finalizer
    from app.services.chat.turn_context_builder import (
        _WEB_SEARCH_AUTHORIZATION_HINT, _att_field, _build_system_prompt, _collect_knowledge_ids,
        _image_sources, _make_skill_packages_provider, _resolve_kb_tenant,
        _tool_env_snapshot,
    )
    from app.services.chat.tools import connectors as _connector_tools
    from app.services.chat.types import TurnOutcome
    from app.services.platform.token_estimator import record_usage
    session = env.session
    thread = env.thread
    channel = env.channel
    run_id = env.run_id
    thread_id = env.thread_id
    user_id = env.user_id
    user_context = env.user_context
    token = env.token
    newapi_key = env.newapi_key
    resolved_model = env.resolved_model
    message = env.message
    attachments = env.attachments
    regenerate = env.regenerate
    is_first_turn = env.is_first_turn
    prompt_rows = env.prompt_rows
    text_atts = env.text_atts
    protocol = env.channel.protocol
    trusted_skills = env.prep.trusted_skills
    selected_skill_records = env.prep.selected_skill_records
    memory_block = env.prep.memory_block
    summary_block = env.summary_block
    skill_catalog_block = env.prep.skill_catalog_block
    web_search = env.web_search
    skill_ids = env.prep.effective_skill_ids or env.skill_ids
    from app.services.chat.builtin_assistants.runtime import (
        builtin_tool_build_options,
        get_builtin_runtime_policy,
    )
    runtime_policy = get_builtin_runtime_policy(getattr(env, "assistant_preset", ""))
    assistant_snapshot = getattr(env, "assistant_preset_snapshot", None) or {}
    model_input_content = env.model_input_content
    plan_mode = env.plan_mode
    knowledge_ids = env.knowledge_ids
    selected_knowledge = env.selected_knowledge
    raw_est_tokens = env.raw_est_tokens
    ctx_window = env.ctx_window
    from app.services.skills.ppt_agentic_adapter import is_agentic_ppt_profile
    strict_ppt_publish = is_agentic_ppt_profile(getattr(env, "execution_profile", None))

    # 工具循环：主对话可调用 search_web / search_knowledge（ADR-011/033/037）。
    # 联网搜索是常驻工具（ChatGPT 式）：管理端启用即注册，模型自行判断何时搜索；
    # 用户在 + 菜单选中「网页搜索」（web_search=True）时，将这项授权事实告知模型；
    # 是否搜索、何时搜索和失败后的替代方案仍由模型基于目标与证据自行决定。
    kb_ids = _collect_knowledge_ids(knowledge_ids, selected_knowledge)
    # 选中「我的文件」（附件带 file_id）即需进工具循环——否则模型拿不到 execute_in_sandbox/read_file，
    # 无法对选中文件做任何操作（如「优化这个 ppt」）。
    has_selected_files = any(_att_field(a, "file_id") for a in (attachments or []))
    citation_sink: list = [
        dict(item)
        for item in (getattr(env, "research_citations", None) or [])
        if isinstance(item, dict) and str(item.get("url") or "").startswith("http")
    ]
    image_sink: list = []
    # 图片用途是回合级策略，不能等搜索结果回来后再猜。产物任务优先：
    # 搜到的图仍进 image_sink 供 download_url("图N") 嵌入，但不进对话 ui/citations。
    # Image routing follows the frozen execution capability, not a second keyword-based
    # artifact detector.  Unselected/third-party Skills own their image policy and the model
    # can choose the corresponding tools from the normal catalog.
    _profile = getattr(env, "execution_profile", None)
    _artifact_image_turn = bool(
        strict_ppt_publish
        or (
            isinstance(_profile, dict)
            and str(_profile.get("id") or "") == "artifact_coding"
            and str(_profile.get("artifact_kind") or "") in {"presentation", "document"}
        )
    )
    if _artifact_image_turn:
        image_delivery_mode = "artifact_only"
    elif re.search(
        r"(照片|图片|配图|附图|实景|截图|壁纸|来几张|发几张|贴几张|展示.{0,8}图|"
        r"photos?|images?|pictures?|screenshots?)",
        str(message or ""), re.I,
    ):
        image_delivery_mode = "chat_inline"
    else:
        image_delivery_mode = "off"
    approval_sink: list = []
    tool_meta_sink: dict = model_driver.ToolMetaSink()
    tool_progress_queue: asyncio.Queue = asyncio.Queue()
    tool_history = [
        {"role": m.role, "content": m.content}
        for m in prompt_rows[:-1]
        if m.role in ("user", "assistant")
    ]
    public_history = []
    for row in env.history_rows:
        role = str(row.role or "")
        if role not in {"user", "assistant"}:
            continue
        display_item = {"role": role, "content": row.content}
        if getattr(row, "attachments_json", None):
            display_item["attachments_json"] = row.attachments_json
        public_history.append(display_item)
    display_user_message = None
    if public_history and public_history[-1]["role"] == "user":
        display_user_message = dict(public_history[-1])
        public_history = public_history[:-1]

    # 2026-08-08 根治首字空白：开跑前预检（租户/联网/子智能体发现/连接器/KB 前置）
    # **全部并发**，并受 MAIN_TOOL_PREFLIGHT_BUDGET 总墙钟约束。旧实现串行 await，
    # 发现 1.7s + KB 12s + 连接器 DB 叠加到十几秒，用户只看到秒表空转。
    async def _preflight_kb_tenant():
        tenant = await _resolve_kb_tenant(session, kb_ids)
        # _resolve_kb_tenant 可能用回合 session 隐式开事务；立刻关掉，避免占连接。
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
        return tenant

    async def _preflight_web():
        return await web_search_service.is_enabled()

    async def _preflight_discover():
        # call_subagent 候选（ADR-046）：hybrid 发现常 300–1700ms。
        # Discovery is an available capability fact, not a keyword-based route gate.
        if runtime_policy or env.action_authority != "mutate" or not user_context:
            return []
        discovery_history = [
            {"role": m.role, "content": m.content}
            for m in prompt_rows[:-1]
            if m.role in ("user", "assistant")
        ][-4:]
        return await capability_registry.discover_for_call(
            user=user_context, query=message, history=discovery_history,
            top_k=settings.SUBAGENT_DISCOVERY_TOP_K,
            audit_run_id=str(env.run_id or ""),
            audit_thread_id=str(env.thread_id or ""),
        )

    async def _preflight_connectors():
        # 已连接的外部应用说明：无连接器时返回空串，prompt 逐字不变。
        return await _connector_tools.describe_active_connectors(user_id)

    async def _preflight_kb():
        if not (kb_ids and settings.KNOWLEDGE_PRE_RETRIEVE):
            return ""
        if runtime_policy and runtime_policy.knowledge_prompt:
            from app.services.chat.tools.knowledge import build_kb_pre_context_result
            result = await build_kb_pre_context_result(
                token, kb_ids,
                model_driver.kb_search_query(message, tool_history),
                raw_query=message,
                tenant_id=None,
                citation_sink=citation_sink,
                image_sink=image_sink,
                source_label=runtime_policy.knowledge_source_label,
                telemetry_user_id=str(user_id or ""),
                turn_id=str(run_id or ""),
                source="CHAT",
            )
            body = ""
            if result.status == "hit":
                body = result.prompt_block
                # Keep only the inner material block if the generic wrapper is present.
                marker = "===== 知识库参考资料 ====="
                if marker in body:
                    body = body.split(marker, 1)[1].rsplit("===== 资料结束 =====", 1)[0].strip()
            return runtime_policy.knowledge_prompt(result.status, body)
        return await model_driver.build_kb_pre_context(
            token, kb_ids,
            model_driver.kb_search_query(message, tool_history),
            raw_query=message,
            tenant_id=None,  # tenant 与本任务并行；retrieve 内部会再解析/可空
            citation_sink=citation_sink,
            telemetry_user_id=str(user_id or ""),
            turn_id=str(run_id or ""),
            source="CHAT",
        )

    preflight_budget = max(
        0.8, float(getattr(settings, "MAIN_TOOL_PREFLIGHT_BUDGET_SECONDS", 3.0) or 3.0),
    )
    kb_tenant = None
    web_enabled = False
    subagent_candidates: list = []
    connector_block = ""
    kb_ctx = ""
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _preflight_kb_tenant(),
                _preflight_web(),
                _preflight_discover(),
                _preflight_connectors(),
                _preflight_kb(),
                return_exceptions=True,
            ),
            timeout=preflight_budget,
        )
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.warning("tool preflight[%d] failed: %s", i, r)
                continue
            if i == 0:
                kb_tenant = r
            elif i == 1:
                web_enabled = bool(r)
            elif i == 2:
                subagent_candidates = list(r or [])
            elif i == 3:
                connector_block = str(r or "")
            elif i == 4:
                kb_ctx = str(r or "")
    except asyncio.TimeoutError:
        logger.warning(
            "tool preflight 预算 %.1fs 耗尽，以降级能力面开跑（首帧优先）",
            preflight_budget,
        )
        # 超时后至少再抢一次联网开关（几乎恒快，影响工具可见性）
        try:
            web_enabled = bool(await asyncio.wait_for(_preflight_web(), timeout=0.5))
        except Exception:  # noqa: BLE001
            web_enabled = False

    if env.action_authority != "mutate":
        # 只读边界下不得暴露 call_subagent 候选。
        subagent_candidates = []

    # 核心工具（execute_in_sandbox/create_file/edit_file/read_file/list_files/use_skill/search_web/
    # update_plan）恒可用：主对话始终进工具循环，由模型自行决定是否调用——修掉「无知识库/
    # 无联网/无子智能体/无选中文件时，说『生成一份 PPT』却拿不到干活工具」的能力门槛（任务
    # 模式设计稿 §7）。联网/知识库/子智能体/选中文件只按权限与选择追加能力，不再作门槛。
    # 恒进≠每轮执行：纯聊天模型不调工具；router 判 simple 时 orchestrator 直接回退老 loop，
    # 不做 graph 规划，成本可控。
    if True:
        # 恢复段只重验已成功读取的 Skill 的 ACL 目录元数据并恢复包挂载；不读 SKILL.md、
        # 不补发 use_skill 或 capability.loaded。仅 selected/authorized 状态仍必须由模型
        # 再调 use_skill，不能借恢复静默解锁。
        recovered_loaded_skills: list[dict] = []
        if run_id:
            try:
                from app.services.chat.turn_context_builder import _get_catalog_records, get_persisted_skill_state

                persisted = await get_persisted_skill_state(str(run_id))
                loaded_ids = {
                    str(item.get("skill_id") or item.get("id") or "").strip()
                    for item in persisted
                    if isinstance(item, dict) and str(item.get("status") or "").strip().lower() == "loaded"
                }
                if loaded_ids:
                    catalog = await _get_catalog_records(token)
                    for row in catalog:
                        if not isinstance(row, dict):
                            continue
                        sid = str(row.get("skillId") or row.get("id") or "").strip()
                        if sid not in loaded_ids:
                            continue
                        recovered_loaded_skills.append({
                            "id": sid,
                            "record_id": str(row.get("id") or row.get("recordId") or "").strip() or None,
                            "name": str(row.get("name") or sid).strip(),
                            "version": str(row.get("version") or row.get("skillVersion") or "").strip() or None,
                        })
            except Exception:  # noqa: BLE001
                logger.warning("Skill 恢复 ACL 重验失败，保持未挂载而不伪称已读取", exc_info=True)
        active_loaded_skills = [*(trusted_skills or []), *recovered_loaded_skills]
        from app.services.files.work_folders import folder_for_thread
        work_folder = await folder_for_thread(str(user_id or ""), str(thread_id or ""))
        tools = await model_driver.build_tools(
            token=token, knowledge_ids=kb_ids, web_enabled=web_enabled,
            citation_sink=citation_sink, image_sink=image_sink, tenant_id=kb_tenant,
            tool_meta_sink=tool_meta_sink, user_id=user_id, thread_id=thread_id,
            workspace_folder_id=str((work_folder or {}).get("id") or ""),
            tool_progress_queue=tool_progress_queue,
            skill_packages_provider=_make_skill_packages_provider(active_loaded_skills, token),
            loaded_skills=active_loaded_skills,
            selected_skills=selected_skill_records,
            user_message=message,
            artifact_task_brief=env.quality_brief or message,
            newapi_key=newapi_key or "",
            run_id=run_id,
            action_authority=env.action_authority,
            turn_intent=env.turn_intent,
            attachments=attachments,
            revision_mode=env.revision_mode,
            allow_create=env.allow_create,
            revision_target=env.revision_target,
            research_profile=bool(getattr(env, "research_profile", False)),
            execution_profile=env.execution_profile,
            **builtin_tool_build_options(
                runtime_policy, snapshot=assistant_snapshot, image_delivery_mode=image_delivery_mode,
            ),
            # PPT 语境要看最近几轮（与 turn_prepare 的技能预加载同一口径）：澄清式对话把
            # 「我想做个ppt」和「主题是…10页…」拆到两轮，干活那轮不含 PPT 字样，只看当轮
            # 会让工具侧的 PPT 判定与已经加载好的 PPT 技能对不上。
            recent_user_messages=[
                str(m.content or "") for m in prompt_rows[:-1] if m.role == "user"
            ][-3:],
        )
        if runtime_policy:
            tools = runtime_policy.bound_tools(tools)
        else:
            from app.services.chat.tools.recommendation import build_recommend_agent_tool
            _recommend_tool = await build_recommend_agent_tool(
                user=user_context,
                raw_message=message,
                recent_user_messages=[
                    str(m.content or "") for m in prompt_rows[:-1] if m.role == "user"
                ][-2:],
                attachments=list(attachments or []),
                thread_id=str(thread_id or ""),
                run_id=str(run_id or ""),
                selected_specialist=bool(selected_skill_records or getattr(env, "subagent_id", None)),
            )
            if _recommend_tool is not None:
                tools.append(_recommend_tool)
        from app.services.agent_harness.tool_registry import (
            assert_tool_specs, visible_main_tools,
        )
        from app.services.agent_harness import run_store
        await run_store.patch_run_state(
            run_id,
            {},
            phase="planning" if plan_mode else "executing",
        )
        assert_tool_specs(tools)
        export_authorized = bool(
            getattr(env, "research_profile", False)
            and re.search(r"(导出|保存|生成).{0,12}(md|docx|pdf|报告|文件)", message, re.I)
        )
        tools = await visible_main_tools(
            run_id, tools, export_authorized=export_authorized,
        )
        if runtime_policy:
            tools = runtime_policy.validate_tools(tools)
            subagent_candidates = []
        sub_names = {str(c.get("id")): str(c.get("name") or "") for c in subagent_candidates}
        subagent_icons = {str(c.get("id")): str(c.get("icon") or "") for c in subagent_candidates}
        # 子智能体工作流的内部工具权限不可由主 Harness 逐项裁剪；有些工作流会写文件或
        # 调外部系统。没有 mutate 授权时宁可不暴露 call_subagent，也不能让 feedback /
        # inspect 回合通过委派绕过主工具的只读门禁。
        if (
            runtime_policy is None
            and subagent_candidates
            and env.action_authority == "mutate"
        ):
            histories_for_sub = [
                {"role": m.role, "content": m.content}
                for m in prompt_rows[:-1]
                if m.role in ("user", "assistant")
            ]
            sub_tool = model_driver.build_call_subagent_tool(
                subagent_candidates,
                subagent_turn.make_subagent_runner(
                    user_context=user_context, token=token, newapi_key=newapi_key,
                    resolved_model=resolved_model, histories=histories_for_sub,
                    thread_id=thread_id, turn_attachments=text_atts, run_id=run_id,
                ),
                max_calls=settings.SUBAGENT_TOOL_MAX_CALLS,
                runner_stream=subagent_turn.make_subagent_stream_runner(
                    user_context=user_context, token=token, newapi_key=newapi_key,
                    resolved_model=resolved_model, histories=histories_for_sub,
                    thread_id=thread_id, turn_attachments=text_atts, run_id=run_id,
                ),
            )
            if sub_tool:
                tools.append(sub_tool)
        # 消歧工具（R5 编排者形态）：模型判断用户意图有真实歧义时弹选择卡（HITL 挂起）。
        # 仅注册在流式路径——非流式 chat() 无法承接挂起续接。
        if runtime_policy is None or runtime_policy.allow_choice_tool:
            tools.append(model_driver.build_ask_user_tool())
        # Atomic cutover: production main chat must never mix V3 tools with the historical
        # arbitrary-string execution path.  Unit fixtures can still construct isolated tools,
        # but assembled user-facing tools fail fast here if a contract is missing.
        model_driver.assert_tool_contracts(tools)
        # capability broker: the model sees a minimal stable set first. Full connector,
        # browser and workspace schemas are only exposed after search_capabilities in a later
        # model round. Explicit user selections remain pinned.
        from app.services.chat.capability_broker import (
            CapabilityBroker,
            build_capability_search_tool,
            detect_explicit_memory_tools,
            resolve_core_pins,
        )
        # 核工具常驻（2026-08-04 计划阶段 1 + 2026-08-05 根治）：
        # - agent 路径不得依赖 search_capabilities 才解锁 bash/文件读写/search_web
        # - search_web pin 看 web_enabled（平台启用），不看 UI 的 web_search 开关
        # - web_search=True 只提供真实的网页搜索授权事实，不规定调用顺序，也不作为可见性闸
        _progress_tools = frozenset()
        try:
            from app.services.agent_harness.progress_policy import (
                checkpoint_meta_from_profile,
                required_progress_tools,
            )
            from app.services.tasks import plan_service as _progress_plan_service
            _progress_plan = await _progress_plan_service.get_current_plan(run_id)
            _progress_tools = required_progress_tools(
                getattr(env, "execution_profile", None),
                _progress_plan,
                checkpoint_meta=checkpoint_meta_from_profile(
                    getattr(env, "execution_profile", None)
                ),
            )
        except Exception:  # noqa: BLE001
            _progress_tools = frozenset()
        pinned = resolve_core_pins(
            action_authority=str(getattr(env, "action_authority", "mutate") or "mutate"),
            web_enabled=bool(web_enabled),
            plan_mode=bool(plan_mode),
            turn_intent=str(getattr(env, "turn_intent", "") or ""),
            has_kb=bool(kb_ids),
            has_selected_files=bool(has_selected_files),
            has_trusted_skills=bool(trusted_skills),
            has_subagent_candidates=bool(subagent_candidates),
            explicit_memory_tools=detect_explicit_memory_tools(message),
            pin_plan_tool=bool(strict_ppt_publish) or bool(_progress_plan),
            progress_tools=_progress_tools,
        )
        if runtime_policy:
            pinned = set(runtime_policy.pinned_tool_names)
        if runtime_policy is None:
            if any(str(tool.name) == "recommend_agent" for tool in tools):
                pinned.add("recommend_agent")
            # Skill discovery is model-owned.  The absence of an explicitly preloaded Skill must not
            # hide use_skill behind a first tool call that freezes the schema.
            if any(str(tool.name) == "use_skill" for tool in tools):
                pinned = [*pinned, "use_skill"]
        # The broker freezes after the first real tool call.  If the neutral interactive profile
        # has the guarded first-party PPT candidate in its same-schema tool set, pin publish now;
        # the tool body still requires a durable successful use_skill(ppt-studio) fact before it
        # can execute.  This keeps native third-party/ordinary interactive Runs interactive while
        # avoiding a post-freeze second tool loop.
        if (
            str(getattr(env, "action_authority", "mutate") or "mutate") == "mutate"
            and not strict_ppt_publish
            and any(str(tool.name) == "publish_ppt_artifact" for tool in tools)
        ):
            pinned = [*pinned, "publish_ppt_artifact"]
        if strict_ppt_publish:
            pinned = [*pinned, "publish_ppt_artifact"]
        if runtime_policy:
            tools = runtime_policy.validate_tools(tools)
            broker = CapabilityBroker(tools, pinned={tool.name for tool in tools})
            tools = broker.initial_tools()
            tools = runtime_policy.validate_tools(tools)
        else:
            broker = CapabilityBroker(tools, pinned=pinned)
            capability_search_tool = build_capability_search_tool(broker)
            broker.register(capability_search_tool, active=True)
            tools = broker.initial_tools()
        if getattr(env, "research_team_synthesis_only", False):
            # A finished research team hands off to report writing, not another
            # capability-discovery/search loop. Keep the same model/SSE driver.
            broker = CapabilityBroker([], pinned=set())
            tools = []
        # P2.7 平台强制修订确认卡：多产物歧义且目标未解析时，不赌模型会不会调 ask_user_choice。
        # 工具面已物理收起写工具；这里直接挂起选择卡，resume 侧再把选择映射成 revision_target。
        _rev_cands = [
            c for c in (getattr(env, "revision_file_candidates", None) or [])
            if isinstance(c, dict)
            and str(c.get("filename") or "").strip()
            and str(c.get("file_id") or c.get("id") or "").strip()
        ][:8]
        if (
            env.revision_mode
            and not env.allow_create
            and not (env.revision_target or {}).get("file_id")
            and len(_rev_cands) >= 2
            and env.suspend_orchestration is not None
        ):
            import uuid as _uuid
            import json as _json
            _pending = f"ask_rev_{_uuid.uuid4().hex[:16]}"
            _resume = _uuid.uuid4().hex
            _options = []
            for _i, _c in enumerate(_rev_cands, 1):
                _label = str(_c.get("filename") or "").strip()
                _options.append({"key": f"opt{_i}", "value": _label})
            _options.append({"key": f"opt{len(_options)+1}", "value": "以上都不是/我指定其他文件"})
            _question = (
                "本轮要修改的目标文件未能唯一确定。请选择要修改的文件"
                "（确认后会按该文件原位修改，不会另起副本）。"
            )
            _args = {
                "question": _question,
                "options": [{"label": o["value"]} for o in _options],
            }
            _msgs = [
                {"role": "user", "content": message},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": _pending,
                        "type": "function",
                        "function": {
                            "name": "ask_user_choice",
                            "arguments": _json.dumps(_args, ensure_ascii=False),
                        },
                    }],
                },
            ]
            _suspended = {
                "subagent": {
                    "status": "needs_input",
                    "ask_user": True,
                    "resume_id": _resume,
                    "text": _question,
                    "subagent_id": "",
                    "subagent_name": "",
                    "interactive": {
                        "type": "userSelect",
                        "params": {
                            "description": _question,
                            "multiple": False,
                            "userSelectOptions": _options,
                        },
                    },
                },
                "name": "ask_user_choice",
                "args": _args,
                "messages": _msgs,
                "pending_tool_call_id": _pending,
                "answer_so_far": "",
            }
            partial, input_payload = await env.suspend_orchestration(
                run_id, _suspended, 1, kb_ids, web_enabled,
                subagent_candidates=subagent_candidates,
                tool_env=_tool_env_snapshot(
                    action_authority=env.action_authority,
                    turn_intent=env.turn_intent,
                    revision_mode=env.revision_mode,
                    allow_create=env.allow_create,
                    revision_target=env.revision_target,
                    skill_ids=skill_ids,
                    user_message=message,
                    attachments=attachments,
                    plan_mode=plan_mode,
                    revision_file_candidates=_rev_cands,
                    assistant_preset=getattr(env, "assistant_preset", ""),
                    assistant_preset_snapshot=getattr(env, "assistant_preset_snapshot", None),
                ),
            )
            if env.spawn_bg:
                env.spawn_bg(task_run_service.record_steps(run_id, [{
                    "type": "interrupt",
                    "content": _question[:500],
                    "meta": {"name": "ask_user_choice", "status": "waiting_user", "forced": True},
                }]))
            if input_payload is not None:
                yield (
                    channel.plan_confirmation_required(input_payload)
                    if plan_mode or (input_payload or {}).get("kind") == "plan_confirmation"
                    else channel.input_required(input_payload)
                )
            yield channel.done()
            return
        # 聚合器（生成器无法返回值）：answer/trace/usage/suspended/streamed_any
        # 聚合器（生成器无法返回值）：TurnOutcome 字段即此前 dict 键，dict 协议兼容
        out = TurnOutcome(
            latest_public_commentary=str(getattr(env, "public_preamble", "") or "").strip(),
        )
        def _profile_tools_delivered(trace) -> bool:
            if strict_ppt_publish:
                return bool(
                    out.get("published_artifact_succeeded")
                    or model_driver._trace_has_published_ppt_artifact(trace)
                )
            return bool(
                out.get("write_tool_succeeded")
                or turn_finalizer.tools_delivered_artifacts(trace)
            )
        # 一致性守卫（2026-07-22 死锁修复）：正文行是否已成功落库——失败分支据此决定
        # 是否回填，防「commit 已成功、后续收尾步骤抛错」时重复插行
        assistant_persisted = False
        if tools or getattr(env, "research_team_synthesis_only", False):
            try:
                # connector_block / kb_ctx 已在上方 preflight 并发完成（超时则空串）。
                base_prompt = compose_tool_loop_system_prompt(
                    _build_system_prompt(
                        None, trusted_skills, None, None, memory_block,
                        connector_block=connector_block,
                        research_profile=bool(getattr(env, "research_profile", False)),
                    ),
                    summary_block=summary_block,
                    skill_catalog_block=skill_catalog_block,
                    turn_guard_prompt=env.turn_guard_prompt,
                )
                # 用户显式选中「网页搜索」→ 告知模型授权事实；是否调用由模型决定
                # 演示文稿助手不依赖 UI 开关：配图/查证默认可用 search_web。
                if runtime_policy and runtime_policy.search_hint and any(str(tool.name) == "search_web" for tool in tools):
                    base_prompt = "\n\n".join([base_prompt, runtime_policy.search_hint()])
                elif web_search and web_enabled:
                    base_prompt = "\n\n".join([base_prompt, _WEB_SEARCH_AUTHORIZATION_HINT])
                # 选中知识库 → 前置强制检索结果（preflight 已注入 citation_sink）
                env.kb_pre_context = kb_ctx
                if kb_ctx:
                    base_prompt = "\n\n".join([base_prompt, kb_ctx])
                projection_turn_guard = "\n\n".join(
                    part
                    for part in [
                        env.turn_guard_prompt,
                        runtime_policy.search_hint()
                        if runtime_policy and runtime_policy.search_hint and any(str(tool.name) == "search_web" for tool in tools)
                        else _WEB_SEARCH_AUTHORIZATION_HINT
                        if web_search and web_enabled
                        else "",
                    ]
                    if part
                )
                projection_world_state = tool_loop_projection_world_state(
                    summary_block=summary_block,
                    skill_catalog_block=skill_catalog_block,
                    knowledge_context=kb_ctx,
                    turn_guard=projection_turn_guard,
                )
                # Recovery is a continuation of the same Run. Its checkpointed facts and message
                # cursor are one atomic snapshot; recomputing only the facts would create a hybrid
                # history that never existed before the worker fault.
                projection_world_state = recovery_projection_world_state(
                    projection_world_state,
                    initial_messages=getattr(env, "initial_messages", None),
                    checkpoint_world_state=getattr(
                        env, "checkpoint_world_state", None
                    ),
                    turn_constraints=str(
                        getattr(env, "research_synthesis_constraints", "") or ""
                    ) if getattr(env, "research_team_synthesis_only", False) else "",
                )
                # 工具调用经 Tool Gateway（§11）：幂等 + 敏感工具审批 + unknown 态；
                # call_subagent 直连（内部工具）。事件实时上抛、最终轮流式（开发计划 §4.5）。
                #
                # 生产路径直接进入唯一模型工具循环，不经过兼容分派层。
                # 计划模式不受影响——它已改走主循环（harness_orchestrator 的 plan_in_main_loop：
                # 压 authority=inspect + plan_mode=True），从来不经过这里。
                report_driver = model_driver.drive_model
                if getattr(env, "research_team_synthesis_only", False):
                    from app.services.agent_harness.research.review import reviewed_report
                    report_driver = lambda **kw: reviewed_report(env, model_driver.drive_model, **kw)
                async for payload in map_tool_loop_events(
                    channel,
                    report_driver(
                        model=resolved_model, api_key=newapi_key,
                        system_prompt=base_prompt, history=tool_history,
                        display_history=public_history,
                        display_user_message=display_user_message,
                        world_state=projection_world_state,
                        user_input=model_input_content,
                        raw_user_message=str(message or ""),
                        tools=tools,
                        # steerable：本 gateway 属于「根」工具循环，运行中引导在它的轮边界被消费。
                        gateway={
                            "thread_id": thread_id,
                            "user_id": user_id,
                            "run_id": run_id,
                            "steerable": True,
                            "kb_ids": list(kb_ids or []),
                            "web_enabled": bool(web_enabled),
                            "subagent_candidates": list(subagent_candidates or []),
                            "capability_broker": broker,
                            "research_synthesis_only": bool(getattr(env, "research_team_synthesis_only", False)),
                        },
                        approval_sink=approval_sink,
                        tool_progress_queue=tool_progress_queue,
                        # 计划反空壳闸只在计划轮生效：模型一个文件都没读就写计划时推回去
                        # 勘查一次（旧 graph 的 is_no_investigation 判定随模块删除丢失）。
                        plan_mode=plan_mode,
                        research_profile=bool(getattr(env, "research_profile", False)),
                        execution_profile=env.execution_profile,
                        initial_messages=getattr(env, "initial_messages", None),
                    ),
                    sub_names, out, tool_meta_sink,
                    # 逐轮真实用量要换算占比才能下发（见 mapper 里 et == "usage" 分支）
                    ctx_window=ctx_window,
                    strict_ppt_publish=strict_ppt_publish,
                    subagent_icons=subagent_icons,
                    research_profile=bool(getattr(env, "research_profile", False)),
                    committed_text_only=bool(runtime_policy and runtime_policy.project_answer),
                    public_event_mapper=(
                        runtime_policy.public_loop_event(env)
                        if runtime_policy and runtime_policy.public_loop_event else None
                    ),
                ):
                    yield payload

                if out["suspended"] is not None:
                    # 子智能体/ask_user_choice HITL 挂起：持久化编排游标（开发计划 Phase 2），
                    # 用户补全后经 /chat/resume 续接同一 Run
                    partial, input_payload = await env.suspend_orchestration(
                        run_id, out["suspended"], 1, kb_ids, web_enabled,
                        subagent_candidates=subagent_candidates,
                        # 本轮的工具构建上下文随游标一起存（P1 2026-07-27）：续接轮据此
                        # 复现同一套工具集。不传的话续接轮只能拿 build_tools 的默认值，
                        # 只读授权（「只分析别改」/计划模式）与 @ 选中的技能包会一起丢。
                        tool_env=_tool_env_snapshot(
                            action_authority=env.action_authority,
                            turn_intent=env.turn_intent,
                            revision_mode=env.revision_mode,
                            allow_create=env.allow_create,
                            revision_target=env.revision_target,
                            skill_ids=skill_ids,
                            user_message=message,
                            attachments=attachments,
                            # 续接时据此改写 messages[0]：计划轮的 system prompt 写着
                            # 「本轮到此为止、不要动手」，而游标带走的是整条 messages，
                            # 不改写就会和放开了写权限的工具集自相矛盾。
                            plan_mode=plan_mode,
                            revision_file_candidates=getattr(env, "revision_file_candidates", None),
                            assistant_preset=getattr(env, "assistant_preset", ""),
                            assistant_preset_snapshot=getattr(env, "assistant_preset_snapshot", None),
                        ),
                    )
                    # 挂起审计（§16.5）：interrupt 一条（chip 回放在续接完成时才带 message_id）
                    pend0 = out["suspended"].get("subagent") or {}
                    env.spawn_bg(task_run_service.record_steps(run_id, [{
                        "type": "interrupt",
                        "content": (pend0.get("text") or partial or "")[:500],
                        "meta": {"subagent_id": pend0.get("subagent_id"),
                                 "resume_id": pend0.get("resume_id")},
                    }]))
                    # 消歧提问（ask_user）的问题由选择卡卡头承载，不再作为正文重复流出
                    if not pend0.get("ask_user"):
                        yield channel.message_delta(partial)
                    # 任务模式挂起（需求卡/计划卡）payload=None：卡片帧已由
                    # task.requirements.required / task.plan.ready 下发，不再补 input.required
                    if input_payload is not None:
                        yield (
                            channel.plan_confirmation_required(input_payload)
                            if plan_mode or (input_payload or {}).get("kind") == "plan_confirmation"
                            else channel.input_required(input_payload)
                        )
                    yield channel.done()
                    return

                # P2.2：流式终态与落库共用同一份去重终答，避免 UI 仍见同句×2
                full_response = turn_finalizer.collapse_exact_double_answer(
                    out["answer"] or "（无输出）"
                )
                # 终答假故障二次闸（2026-08-05）：loop 内 scrub 漏检时，流式/落库前再洗一次
                _trace = out.get("trace") or []
                _tools_ok = bool(
                    out.get("any_tool_succeeded")
                    or out.get("write_tool_succeeded")
                    or turn_finalizer.tools_ran_successfully(_trace)
                    or _profile_tools_delivered(_trace)
                )
                full_response = turn_finalizer.scrub_false_tool_outage_claim(
                    full_response,
                    tools_succeeded=_tools_ok,
                    tools_delivered=_profile_tools_delivered(_trace),
                )
                # ：search 已给 ℃/% 却空口拒答 → 终答 scrub / 回执补答
                full_response = turn_finalizer.scrub_false_search_hedge(
                    full_response, trace=_trace,
                )
                full_response = turn_finalizer.strip_leading_mechanical_ack(full_response)
                full_response = turn_finalizer.strip_trailing_incomplete_process(full_response)
                full_response = turn_finalizer.scrub_contradictory_completion(full_response)
                full_response = turn_finalizer.scrub_process_narration_when_delivered(
                    full_response,
                    tools_delivered=_profile_tools_delivered(_trace),
                )
                # Markdown 结构以模型流为事实源。此处只做事实/安全 scrub，不再在 completed
                # 阶段按数字与标点重写排版；否则流式时正确、完成后会突然错位且错误落库。
                # 终答清洗只能依据冻结的执行契约，不再用中文关键词猜测用户是否要文件。
                # Research 交付物是对话正文；第三方 Skill 也由自己的生成/交付栈负责。
                _need_file_mt = bool(strict_ppt_publish)
                if not getattr(env, "research_profile", False):
                    full_response = turn_finalizer.scrub_false_file_delivery_claim(
                        full_response,
                        need_file=_need_file_mt,
                        tools_delivered=_profile_tools_delivered(_trace),
                    )
                # 硬剥正文 [1][2] 来源角标（不依赖模型；[图N] 保留）。
                # 深度研究报告要把 [n] 交给 compile_report 变成引用角标，这里不能剥。
                if not getattr(env, "research_profile", False):
                    full_response = turn_finalizer.scrub_inline_source_markers(full_response)
                else:
                    from app.services.agent_harness.research.report import strip_research_scaffold
                    full_response = strip_research_scaffold(full_response)
                # 回写清洗后的原始 Markdown，保证 message.completed / 落库 / 后续引用一致
                if runtime_policy and runtime_policy.project_answer:
                    full_response = await runtime_policy.project_answer(env, full_response)
                    # Candidate banks and evaluation drafts remain in the internal tool ledger.
                    out["trace"] = []
                    yield channel.message_delta(full_response)
                out["answer"] = full_response
                if getattr(env, "research_profile", False):
                    from app.services.agent_harness.research.engine import is_user_clarification
                    if is_user_clarification(full_response):
                        out["requires_citations"] = False
                    else:
                        out["requires_citations"] = True
                # 对话附图硬闭环。产物素材即使 image_sink 有值也不进这条路。
                # image_sink 才是权威图源；loop 内 normalize 若只读 trace.meta 会漏图。
                # 用户要图且 sink 非空时，终答缺 [图N] 一律补上，再落库/message.completed。
                try:
                    _img_urls = []
                    for _im in (image_sink or []):
                        if isinstance(_im, dict):
                            if str(_im.get("display_scope") or "chat_inline") != "chat_inline":
                                continue
                            _u = str(_im.get("url") or _im.get("image_url") or "").strip()
                        else:
                            _u = str(_im or "").strip()
                        if _u.startswith("http") and _u not in _img_urls:
                            _img_urls.append(_u)
                    # 兜底：trace 上若已挂 meta/observation 图片也并入
                    try:
                        from app.services.agent_harness.model_driver import _trace_image_urls as _tiu
                        for _u in _tiu(_trace):
                            if _u.startswith("http") and _u not in _img_urls:
                                _img_urls.append(_u)
                    except Exception:
                        pass
                    # 明确索图时，模型可能从普通网页正文中找到并验证了图片直链，随后写成
                    # ``[图1](https://...jpg)``。这类 URL 不一定来自图片搜索的 image_sink，
                    # 但仍是本轮已经公开给用户的已验证图源。只在 chat_inline 模式提升为
                    # image citation；普通问答/产物任务不会走到这里，避免重新引入随机配图。
                    if image_delivery_mode == "chat_inline":
                        for _m in re.finditer(
                            r"!?\[图\d{1,2}\]\((https?://[^)\s]+)\)",
                            full_response or "",
                            re.I,
                        ):
                            _u = str(_m.group(1) or "").strip()
                            if _u.startswith("http") and _u not in _img_urls:
                                _img_urls.append(_u)
                    # 问答附图最多 3 张。同步回 image_sink 后，SSE citations 与持久化
                    # 共用同一事实源，刷新历史时仍能渲染图片卡。
                    _img_urls = _img_urls[:3]
                    if image_delivery_mode == "chat_inline":
                        _known_sink_urls = {
                            str(_im.get("url") or _im.get("image_url") or "").strip()
                            for _im in (image_sink or [])
                            if isinstance(_im, dict)
                        }
                        for _idx, _u in enumerate(_img_urls, 1):
                            if _u in _known_sink_urls:
                                continue
                            image_sink.append({
                                "url": _u,
                                "title": f"相关图片 {_idx}",
                                "source": "",
                                "thumbnail": "",
                                "display_scope": "chat_inline",
                            })
                            _known_sink_urls.add(_u)
                    _wants_imgs_mt = False
                    try:
                        from app.services.agent_harness.model_driver import (
                            _chat_inline_image_only_goal as _cio,
                        )
                        _wants_imgs_mt = bool(_cio(message)) or bool(re.search(
                            r"(照片|图片|配图|附图|来几张|发几张|贴几张|展示.{0,8}图)",
                            str(message or ""),
                        ))
                    except Exception:
                        _wants_imgs_mt = bool(re.search(
                            r"(照片|图片|配图|附图)",
                            str(message or ""),
                        ))
                    if (
                        image_delivery_mode == "chat_inline"
                        and _img_urls
                        and (_wants_imgs_mt or re.search(r"\[图\d+\]", full_response or ""))
                    ):
                        _before_img = full_response
                        full_response = turn_finalizer.normalize_inline_image_refs(
                            full_response, image_urls=_img_urls,
                        )
                        if full_response != _before_img:
                            out["answer"] = full_response
                            logger.info(
                                "forced inline image refs n=%s answer_has_ref=%s",
                                len(_img_urls),
                                bool(re.search(r"\[图\d+\]", full_response or "")),
                            )
                except Exception:  # noqa: BLE001
                    logger.warning("inline image normalize failed", exc_info=True)
                # 敏感工具挂起审批：冒泡 approval.required 卡片（用户通过后重试即执行，§11.3 幂等）
                for ap in approval_sink:
                    # 卡片文案优先用危险动作识别给出的具体理由（danger.assess），它说的是
                    # 「这一下会发生什么」；没有理由时才退回工具名——后者用户判断不了该不该点
                    reason = str(ap.get("reason") or "").strip()
                    yield channel.approval_required({
                        "call_id": ap.get("callId"), "tool_name": ap.get("toolName"),
                        "reason": reason,
                        "prompt": (
                            reason or f"操作「{ap.get('toolName')}」需要你的确认后才能执行"
                        ),
                    })
                # 落库+提交收敛到 turn_finalizer.persist_assistant_turn（2026-07-22）：
                # MySQL 1213 死锁（与运行中引导注入/部分落库等并发短事务锁序反转）
                # 回滚→退避→整轮重放（首轮标题/regenerate 软标记随外层事务一起被回滚）
                tool_row = await turn_finalizer.persist_assistant_turn(
                    session, thread, thread_id=thread_id, content=full_response,
                    preserve_source_markers=bool(getattr(env, "research_profile", False)),
                    run_id=run_id, is_first_turn=is_first_turn, regenerate=regenerate,
                    first_message=message,
                    status=turn_finalizer.terminal_message_status(out),
                    tools_succeeded=bool(
                        out.get("any_tool_succeeded")
                        or out.get("write_tool_succeeded")
                        or turn_finalizer.tools_ran_successfully(out.get("trace") or [])
                        or _profile_tools_delivered(out.get("trace") or [])
                    ),
                    tools_delivered=_profile_tools_delivered(out.get("trace") or []),
                )
                assistant_persisted = True
                # The public MySQL transcript is now authoritative. Only at this boundary may the
                # Provider-only assistant/reasoning cursor advance the cross-Run projection ledger.
                if out.get("projection_commit") is not None:
                    try:
                        display_assistant_message = {
                            "role": "assistant",
                            "content": getattr(tool_row, "content", full_response),
                        }
                        if getattr(tool_row, "attachments_json", None):
                            display_assistant_message["attachments_json"] = (
                                tool_row.attachments_json
                            )
                        committed_projection = (
                            await model_driver.commit_terminal_projection(
                                out.get("projection_commit"),
                                answer=full_response,
                                run_id=run_id,
                                display_assistant_message=display_assistant_message,
                            )
                        )
                    except Exception:  # noqa: BLE001 - public transcript is authoritative
                        logger.warning(
                            "terminal projection commit failed run=%s thread=%s",
                            run_id,
                            thread_id,
                            exc_info=True,
                        )
                    else:
                        if not committed_projection:
                            logger.warning(
                                "terminal projection commit skipped run=%s thread=%s",
                                run_id,
                                thread_id,
                            )
                # legacy 的 message_completed 是整段重发帧：已流式过则跳过防双渲染；
                # v1 幂等（前端按长度守卫替换），仍发以携带 message_id 与权威全文
                if protocol == sse_protocol.HARNESS or not out["streamed_any"]:
                    yield channel.message_completed(full_response, tool_row.id)
                if citation_sink or _image_sources(image_sink):
                    # 搜索附带图片以 type=image 并入来源快照末尾（[图N] 编号 =
                    # image 类条目序号），复用 citations 事件下发与持久化
                    normalized = citation_service.normalize_sources(
                        citation_sink + _image_sources(image_sink)
                    )
                    yield channel.citations(normalized)
                    await citation_service.save(thread_id, tool_row.id, normalized)
                # 真实用量校准（§13）：流式 usage 透传时回灌 per-model 因子并校正指示
                if out["usage_prompt_tokens"] > 0:
                    record_usage(resolved_model, raw_est_tokens, out["usage_prompt_tokens"])
                    from app.services.platform.token_estimator import record_thread_prompt
                    record_thread_prompt(thread_id, out["usage_prompt_tokens"])  # 线程级账本（压缩触发下限锚）
                    yield channel.context_usage(
                        out["usage_prompt_tokens"], ctx_window,
                        round(min(out["usage_prompt_tokens"] / ctx_window, 1.0), 3),
                        kind="actual",
                    )
                # 发现观测（语义发现升级 §十三）：本轮实际选用的子智能体与其候选名次
                sel_sid = next((str((t.get("args") or {}).get("subagent_id") or "")
                                for t in out["trace"] if t.get("name") == "call_subagent"), "")
                if sel_sid:
                    cand_ids = [str(c.get("id")) for c in subagent_candidates]
                    logger.info(
                        "subagent_discovery_selected user_id=%s selected_subagent_id=%s selected_rank=%d",
                        user_id, sel_sid,
                        (cand_ids.index(sel_sid) + 1) if sel_sid in cand_ids else -1,
                    )
                # 编排轨迹落 agent_steps（§16.5 可观测 + chip 持久化回放）
                if out["trace"]:
                    env.spawn_bg(task_run_service.record_steps(
                        run_id, trace_to_steps(out["trace"], sub_names, tool_row.id, full_response)
                    ))
                    # Completion Verifier 只读取已持久化回执；必须在终态边界前提交，不能
                    # 用后台任务与验证并发，否则既会漏掉失败，也会暂时看不到产物证据。
                    await task_run_service.record_tool_observations(
                        run_id, out["trace"], fail_closed=True,
                    )
                # 终态 CAS 统一收尾（Phase 2b：turn_finalizer.finalize_terminal——
                # 假完成率=0 / 先迁移后发布的纪律见彼处 docstring，四处共享唯一实现）
                async for _terminal_frame in turn_finalizer.finalize_terminal(
                        channel, run_id, out, tool_row.id, full_response,
                        full_response or "plan_failed"):
                    yield _terminal_frame
                if is_first_turn and not regenerate and full_response.strip():
                    # spawn_bg 持引用托管（裸 create_task 可能被 GC 静默丢弃，B4 教训）
                    env.spawn_bg(
                        turn_finalizer.generate_title(
                            thread_id,
                            message,
                            resolved_model,
                            newapi_key,
                            attachments=attachments,
                            run_id=run_id,
                        )
                    )
                # 轮后记忆抽取（与普通直答收尾对齐）：内部工具常驻使 tools 几乎恒非空，
                # 绝大多数轮走本分支——此前只有直答路径有抽取，等于自动抽取长期失效。
                # _spawn_bg 持引用托管（裸 create_task 可能被 GC 静默丢弃，B4 教训）
                if full_response.strip() and (runtime_policy is None or runtime_policy.allow_memory_extraction):
                    # v3.0：并入技能执行摘要（产物结论定向进记忆）
                    _skill_summary = ""
                    try:
                        from app.services.tasks import snapshot_service
                        _skill_summary = await snapshot_service.collect_skill_work_lines(thread_id)
                    except Exception:  # noqa: BLE001
                        pass
                    env.spawn_bg(memory_service.extract_and_store(
                        user_id=user_id, thread_id=thread_id,
                        conversation_text=f"用户：{message}\n助手：{full_response}",
                        user_text=message,
                        model=resolved_model, api_key=newapi_key, run_id=run_id,
                        skill_summary=_skill_summary,
                    ))
                yield channel.done()
                return
            except (asyncio.CancelledError, GeneratorExit):
                # 用户停止生成（工具循环主路径，P0-9）：CancelledError 继承自
                # BaseException 而非 Exception，此前只有下面这条 except Exception 会
                # 把它漏掉——穿透到 _pump_background_run 时已流出的 out["answer"]
                # 从未落库。对齐下方直答路径（except (CancelledError, GeneratorExit)
                # 分支）用独立任务落库；处理完必须原样 raise，pump 依赖 CancelledError
                # 才能收敛出 cancelled 状态。
                if out["streamed_any"] and out["answer"].strip() and not assistant_persisted:
                    try:
                        env.spawn_partial_persist(run_id, thread_id, out["answer"])
                    except Exception:  # noqa: BLE001
                        pass
                raise
            except Exception as e:  # noqa: BLE001
                # Any execution failure belongs to the recovery owner.  Do not manufacture a
                # failed/partial Run here: that would race the Worker recovery path and make a
                # normal conversation take a second, incompatible terminal route.
                await _persist_recovery_evidence(
                    run_id=run_id,
                    thread_id=thread_id,
                    out=out,
                    assistant_persisted=assistant_persisted,
                    spawn_partial_persist=env.spawn_partial_persist,
                    record_tool_observations=task_run_service.record_tool_observations,
                    error=e,
                )
                converted = await convert_loop_failure(runtime_policy, env, e)
                if converted is not None:
                    raise converted from e
                raise
    # 深扫修复(2026-07-20 P0):此置位在 skeleton 脚本重放中因缩进不匹配被静默丢失——
    # 缺它则骨架恒 return,直答回退成死代码:普通对话在首个正文 token 前遇异常时
    # 无回答、无终态帧,Run 悬挂成僵尸。与基准 fall-through 到直答段的控制流等价。
    if runtime_policy and not runtime_policy.allow_plain_fallback:
        return
    env.fallback_plain = True
