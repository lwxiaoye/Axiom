"""Harness Protocol 1 SSE event envelopes.

There is one public protocol and one event shape. ``[DONE]`` closes transport only; a terminal
Run event carries the business outcome. Historical protocol negotiation intentionally does not
live here.
"""
import json
import time
import uuid
from typing import Any, Dict, Optional
from app.services.agent_harness.event_catalog import event_definition
HARNESS_VERSION = '1'
HARNESS = 'harness/1'

REASONING_REPLAY_MAX = 2000


def compact_reasoning_summary(text: str) -> str:
    """Persist a replayable thought body for the expandable timeline step.

    The live token stream stays on ``message.reasoning.delta``. History and the
    sealed thinking step keep up to 2000 characters, snapped to a nearby
    sentence or line boundary so English bursts are not sliced mid-sentence.
    """
    collapsed = str(text or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if not collapsed:
        return ''
    if len(collapsed) <= REASONING_REPLAY_MAX:
        return collapsed
    window = collapsed[-REASONING_REPLAY_MAX:]
    boundary = -1
    for sep in ('\n', '。', '！', '？', '. ', '? ', '! '):
        idx = window.find(sep)
        if 0 <= idx < 240:
            boundary = max(boundary, idx + len(sep) - 1)
    if boundary >= 0:
        window = window[boundary + 1:].lstrip()
    return window.strip()

def negotiate(version_header: Optional[str]) -> str:
    return HARNESS if (version_header or '').strip() == HARNESS_VERSION else ''

def _sse(payload: Dict[str, Any]) -> str:
    return 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'

class SSEChannel:
    """Format the sole public Harness event envelope."""

    def __init__(self, protocol: str, thread_id: str, run_id: str, start_sequence: int=0):
        if protocol != HARNESS:
            raise ValueError('SSEChannel only supports Harness Protocol 1')
        self.protocol = HARNESS
        self.thread_id = thread_id
        self.run_id = run_id
        self._seq = int(start_sequence or 0)

    def _env(self, etype: str, data: Dict[str, Any]) -> str:
        event_definition(etype)
        self._seq += 1
        return _sse({'version': self.protocol, 'event_id': uuid.uuid4().hex, 'run_id': self.run_id, 'sequence': self._seq, 'timestamp': int(time.time() * 1000), 'schema_version': 1, 'type': etype, 'data': data})

    def run_started(
        self, agent_mode: str='standard', resume_meta: Optional[dict]=None,
        model: Optional[str]=None,
    ) -> str:
        data = {'thread_id': self.thread_id, 'agent_mode': agent_mode or 'standard'}
        if resume_meta:
            data['resume_meta'] = resume_meta
        if model:
            data['model'] = model
        return self._env('run.started', data)

    def run_accepted(self, route: str='agent') -> str:
        return self._env('run.accepted', {'route': route or 'agent'})

    def run_phase_changed(self, phase: str) -> str:
        return self._env('run.phase.changed', {'phase': str(phase or 'executing')})

    def thread(
        self, agent_mode: str='standard', resume_meta: Optional[dict]=None,
        model: Optional[str]=None,
    ) -> str:
        return self.run_started(agent_mode, resume_meta=resume_meta, model=model)

    def run_completed(self, message_id: Optional[int]=None) -> str:
        return self._env('run.completed', {'message_id': message_id})

    def run_partial(self, message_id: Optional[int]=None, reason_codes: tuple[str, ...]=()) -> str:
        return self._env('run.partial', {'message_id': message_id, 'reason_codes': list(reason_codes)})

    def run_cancelled(self, reason: str='cancelled') -> str:
        return self._env('run.cancelled', {'reason': reason})

    def run_failed(self, message: str) -> str:
        return self._env('run.failed', {'message': message})

    def input_accepted(self) -> str:
        """resume 令牌已被原子消费（P1 五批）：这是「续跑请求已被服务端接受」的**权威**
        信号——HTTP 2xx 响应头只证明连接建立，令牌实际在后台生成器里才 CAS 消费；
        前端收到本帧才允许把乐观隐藏的任务卡置为不可回滚。"""
        return self._env('input.accepted', {})

    def done(self) -> str:
        return 'data: [DONE]\n\n'

    def message_delta(self, text: str) -> str:
        return self._env('message.delta', {'text': text})

    def message_reasoning_delta(self, text: str) -> str:
        """Stream provider reasoning to the current connection only."""
        return self._env('message.reasoning.delta', {'text': text})

    def message_reasoning_completed(self, text: str = '', seconds: float = 0.0) -> str:
        """Close a reasoning burst with a compact summary and elapsed seconds."""
        summary = compact_reasoning_summary(text)
        if not summary and str(text or '').strip():
            summary = '已完成思考'
        data: Dict[str, Any] = {}
        if summary:
            data['text'] = summary[:20000]
        if seconds:
            data['seconds'] = round(float(seconds), 1)
        return self._env('message.reasoning.completed', data)

    def message_commentary(
        self,
        text: str,
        kind: str='',
        evidence_item_ids: Optional[list[str]]=None,
        next_action: str='',
    ) -> str:
        """权威过程说明（开场白/轮间衔接语），不属于最终回答。

        新协议可在任何 message.delta 之前直接发布，供前端按到达顺序渲染；历史回放仍
        兼容旧协议中「先 delta、后 commentary 分类」的事件序列。落库供轨迹重建。

        kind：可选语义标记。"plan" 是计划报告卡；其他值只能表示可公开、可持久化的
        进度说明。供应商 reasoning_content 使用独立的瞬时事件，不得混入 commentary。
        """
        payload = {'text': text}
        if kind:
            payload['kind'] = kind
        evidence = [
            str(item).strip()[:160]
            for item in (evidence_item_ids or [])
            if str(item).strip()
        ][:20]
        if evidence:
            payload['evidence_item_ids'] = list(dict.fromkeys(evidence))
        if str(next_action or '').strip():
            payload['next_action'] = str(next_action).strip()[:500]
        return self._env('message.commentary', payload)

    def model_connection(
        self,
        status: str,
        transport: str='',
        *,
        attempt: Optional[int]=None,
        max_retries: Optional[int]=None,
        delay_seconds: Optional[float]=None,
    ) -> str:
        """Expose automatic provider recovery without leaking raw upstream errors."""
        resolved = str(status or '').strip().lower()
        if resolved not in {'recovering', 'recovered', 'failed'}:
            raise ValueError(f'unsupported model connection status: {resolved}')
        payload = {'status': resolved}
        if str(transport or '').strip():
            payload['transport'] = str(transport).strip()[:80]
        if attempt is not None:
            payload['attempt'] = max(0, int(attempt))
        if max_retries is not None:
            payload['max_retries'] = max(0, int(max_retries))
        if delay_seconds is not None:
            payload['delay_seconds'] = max(0.0, float(delay_seconds))
        return self._env('model.connection', payload)

    def input_applied(self, input_id: str, content: str, scope: str='turn', revision_epoch: Optional[int]=None) -> str:
        """运行中引导已被当前轮吸收（Codex「提交，但不中断模型运行」）。

        用户在执行过程中提交的引导会在工具轮边界注入正在跑的这一轮，模型下一次请求即可见。
        本事件告诉前端「这条引导已生效」——队列卡据此收起、消息在时间线上就地定位。
        scope：turn=轮级注入。"""
        payload = {'input_id': input_id, 'content': content, 'scope': scope}
        if revision_epoch is not None:
            payload['revision_epoch'] = int(revision_epoch)
        return self._env('input.applied', payload)

    def input_rejected(self, input_id: str, content: str, reason: str) -> str:
        """运行中引导无法安全合并。与 applied 分开发布，界面不得显示“已生效”假回执。"""
        return self._env('input.rejected', {'input_id': input_id, 'content': content, 'reason': reason})

    def user_message_saved(self, message_id: int) -> str:
        """当轮用户消息落库 id（F2 编辑重发配套）：前端补 dbId 作后续编辑的截断点。"""
        return self._env('message.user_saved', {'message_id': message_id})

    def message_completed(self, text: str, message_id: Optional[int]=None) -> str:
        return self._env('message.completed', {'text': text, 'message_id': message_id})

    def plan_updated(self, items: list, round_no: int=1) -> str:
        """模型已经决定的本轮工具调用清单。

        这不是模型隐藏思维，而是即将真实执行的动作，可安全地给用户展示。每次工具轮
        追加一组，前端再由 tool.started/completed/failed 驱动各项状态。
        """
        safe_items = []
        for (index, item) in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            safe_items.append({'key': str(item.get('key') or f'round-{round_no}-{index}'), 'name': str(item.get('name') or ''), 'detail': str(item.get('detail') or '')[:200]})
        return self._env('progress.updated', {'kind': 'next_actions', 'round': max(1, int(round_no or 1)), 'items': safe_items})

    def task_plan_updated(self, steps: list) -> str:
        """模型拆解的语义任务计划（Codex 式 update_plan）：有序步骤 + 状态，整表回传。

        与 plan.updated（工具调用清单）区分：这是面向用户的高层步骤（理解需求/制作/审查/交付…），
        「任务协作」面板据此渲染——进行中转圈、完成打勾。每次调用整表替换。
        """
        safe_steps = []
        for (index, step) in enumerate(steps or []):
            if not isinstance(step, dict):
                continue
            title = str(step.get('title') or '').strip()
            if not title:
                continue
            acc = str(
                step.get('acceptance')
                or ((step.get('acceptance_criteria') or [''])[0]
                    if isinstance(step.get('acceptance_criteria'), list)
                    else step.get('acceptance_criteria') or '')
            ).strip()[:60]
            row = {
                'key': str(step.get('key') or step.get('step_key') or f'plan-{index}'),
                'title': title[:80],
                'status': str(step.get('status') or 'pending'),
                'detail': str(step.get('detail') or step.get('reason') or '')[:240],
                'reason': str(step.get('reason') or '')[:240],
                'required': bool(step.get('required', True)),
            }
            if acc:
                row['acceptance'] = acc
            if step.get('blocked'):
                row['blocked'] = True
            safe_steps.append(row)
        first = next((step for step in steps or [] if isinstance(step, dict)), {})
        payload = {
            'goal_revision': max(0, int(first.get('goal_revision') or 0)),
            'plan_version': max(0, int(first.get('plan_version') or 0)),
            'steps': safe_steps,
        }
        if first.get('goal_contract') and isinstance(first.get('goal_contract'), dict):
            payload['goal_contract'] = first['goal_contract']
        if first.get('approved_version') is not None and first.get('approved_version') != '':
            try:
                payload['approved_version'] = int(first.get('approved_version'))
            except (TypeError, ValueError):
                pass
        if first.get('diverged'):
            payload['diverged'] = True
        return self._env('plan.updated', payload)

    def capability_loaded(self, names: list[str]) -> str:
        """Capability broker audit event; UI may render it as a compact activity row."""
        return self._env('capability.loaded', {'names': [str(name) for name in names[:5]]})

    def artifact_saved(self, files: list[dict], source: str = '') -> str:
        """Persisted artifact receipts. Text previews or filenames without file IDs are not receipts."""
        safe_files = []
        for item in files[:20]:
            if not isinstance(item, dict):
                continue
            file_id = str(item.get('id') or item.get('file_id') or '').strip()
            filename = str(item.get('filename') or item.get('name') or '').strip()
            if not file_id or not filename:
                continue
            row = {'id': file_id, 'filename': filename}
            for key in (
                'size', 'mime', 'origin', 'review', 'source', 'versionNo',
                'deliverable', 'draft', 'previewOnly',
            ):
                if item.get(key) is not None:
                    row[key] = item[key]
            safe_files.append(row)
        return self._env('artifact.saved', {'source': str(source or ''), 'files': safe_files})

    def tool_started(self, name: str, args: Optional[dict]=None, call_id: str='') -> str:
        data: Dict[str, Any] = {'name': name, 'args': args or {}}
        if str(call_id or '').strip():
            data['call_id'] = str(call_id).strip()[:160]
        return self._env('tool.started', data)

    def tool_progress(self, name: str, stage: str, label: str, elapsed_ms: int=0, detail: Optional[Dict[str, Any]]=None, heartbeat: bool=False, call_id: str='') -> str:
        data: Dict[str, Any] = {'name': name, 'stage': stage, 'label': label, 'elapsed_ms': max(0, int(elapsed_ms or 0))}
        if str(call_id or '').strip():
            data['call_id'] = str(call_id).strip()[:160]
        if detail:
            data['detail'] = detail
        if heartbeat:
            data['heartbeat'] = True
        return self._env('tool.progress', data)

    def tool_completed(self, name: str, result_preview: str='', meta: Optional[Dict[str, Any]]=None, call_id: str='') -> str:
        data: Dict[str, Any] = {'name': name, 'result_preview': result_preview[:2000]}
        if str(call_id or '').strip():
            data['call_id'] = str(call_id).strip()[:160]
        if meta:
            data['meta'] = meta
        return self._env('tool.completed', data)

    def tool_failed(self, name: str, error: str, meta: Optional[Dict[str, Any]]=None, call_id: str='') -> str:
        data: Dict[str, Any] = {'name': name, 'error': error[:2000]}
        if str(call_id or '').strip():
            data['call_id'] = str(call_id).strip()[:160]
        if meta:
            data['meta'] = meta
        return self._env('tool.failed', data)

    def input_required(self, payload: Dict[str, Any]) -> str:
        return self._env('input.required', payload)

    def plan_confirmation_required(self, payload: Dict[str, Any]) -> str:
        return self._env('plan.confirmation.required', payload)

    def approval_required(self, payload: Dict[str, Any]) -> str:
        return self._env('approval.required', payload)

    def context_usage(self, tokens: int, window: int, ratio: float, kind: str='actual') -> str:
        """context.usage：本轮 prompt 上下文用量。

        kind 区分两类语义完全不同的事件（2026-07-26 真机实测修复）：
        - estimate：模型调用**前**的本地估算（发一次，用于压缩指示尽早有值）；
        - actual：模型返回的**真实** prompt_tokens，是对同一份 prompt 的**校准修正**。
        二者描述同一份上下文，差值＝估算误差而非上下文增长。前端计量行据此只用 actual
        建基线/算增量——此前不加区分，纯问答（回答「2」一个字）会显示 4.8k tokens。
        """
        data = {'tokens': tokens, 'window': window, 'ratio': ratio, 'kind': 'estimate' if kind == 'estimate' else 'actual'}
        return self._env('context.usage', data)

    def citations(self, sources: list) -> str:
        data = {'sources': sources}
        return self._env('citations', data)

    def research_team(self, team: Dict[str, Any]) -> str:
        return self._env('research.team', {'team': team})

    def research_progress(self, payload: Optional[Dict[str, Any]] = None) -> str:
        data = dict(payload or {})
        data.setdefault('stage', 'researching')
        return self._env('research.progress', data)

    def context_compacted(self, note: str='') -> str:
        """自动压缩已发生（§13）：已把较早对话整理为摘要以继续，用户无感续接。"""
        data = {'note': note or 'Context compacted'}
        return self._env('context.compacted', data)

    def context_compaction(
        self,
        status: str,
        *,
        seconds: Any = None,
        tokens_before: Any = None,
        tokens_after: Any = None,
    ) -> str:
        """Codex ContextCompaction item: started shimmer, then completed."""
        data: Dict[str, Any] = {'status': str(status or 'started')}
        if seconds is not None:
            try:
                data['seconds'] = max(0, int(seconds))
            except (TypeError, ValueError):
                pass
        if tokens_before is not None:
            data['tokens_before'] = tokens_before
        if tokens_after is not None:
            data['tokens_after'] = tokens_after
        return self._env('context.compaction', data)

    def attachments_status(self, items: list) -> str:
        """附件读取置信度（P0 附件生命周期）：本轮未完整读取的附件清单。

        items: [{filename, kind, status(partial/failed), note?, file_id?}]；
        前端据此在回答上方渲染降级提示条并提供重试入口。
        随 record_sse_payload 持久化，历史回放经 execution_trace.attachments_status 还原。"""
        data = {'items': items}
        return self._env('attachments.status', data)

    def memory_updated(self, items: list) -> str:
        """「已更新记忆」提示（§14 Phase 2，best-effort）：上一轮抽取新记的记忆摘要，
        本轮首帧下发，前端渲染可点击 chip（点开记忆管理抽屉）。items: [str]。"""
        data = {'items': [str(i) for i in items]}
        return self._env('memory.updated', data)

    def error(self, message: str) -> str:
        return self._env('run.failed', {'message': message})
