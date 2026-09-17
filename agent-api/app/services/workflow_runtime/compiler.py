"""FastGPTGraphCompiler：FastGPT 画布模型 -> LangGraph StateGraph（架构文档 §10.5.10）。

设计要点（与手写调度器的契约保持一致，规格见 docs/fastgpt-node-catalog.md + manifest §1）：
- 每个 FastGPT 节点编译为一个 LangGraph 节点；**无论 active 还是 skip 都会执行**——
  skip 时只做出边跳过传播（等价蓝本 skipNodeQueue 的后置传播）。因此汇聚点用
  add_edge([所有上游], target) 的 join 形式即可精确复刻「全部入边就绪、≥1 条 active 才真执行」。
- 分支语义（判断器/分类/catchError 错误边）不映射为 conditional_edges：
  由节点执行器写出边状态（active/skipped），下游节点读自己的入边状态决定真跑还是跳过。
  路由因此是数据（边状态）而非控制流，join 拓扑保持静态。
- 执行真源：每次运行一个 WorkflowEngine 实例（复用其全部节点 executor 与
  _node_state/_mark_out_edges 语义），通过 RUN_REGISTRY 以 wf_run_id 从 config 取回。
  State 同步可序列化镜像（outputs/edge_states/variables），P2 checkpoint 恢复时以镜像重建 ctx。
- 环路图暂不支持（LangGraph join 拓扑下静态环会死锁）：编译期检测并抛
  UnsupportedGraphError，由调用方回退旧引擎；循环能力按蓝本 loopRun 容器节点
  （subgraph 驱动）在 P2 实现，不做自由环。
- 发布版本编译缓存：以图拓扑签名为键（(appId, publishedVersion) 的实质等价物），
  草稿调试同样命中签名缓存（拓扑未变则复用）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.config import settings
from app.services.workflow_runtime.state import WFState

logger = logging.getLogger(__name__)


class UnsupportedGraphError(Exception):
    """当前 LangGraph 编译器不支持的图形态（如自由环路），调用方应回退旧引擎。"""


class CheckpointerRequiredError(UnsupportedGraphError):
    """交互节点需要 checkpointer 但未配置：这是明确的配置错误，不应回退 legacy
    （legacy 调度器不支持交互节点，回退只会得到误导性的「不支持节点类型」）。"""


class CheckpointAccessError(Exception):
    """恢复运行的归属校验失败（H2：resumeId 不属于当前用户/应用），应拒绝并返回 403。"""


# ---------- Checkpointer（PG sidecar，暂停恢复/运行回放的持久层） ----------

_CHECKPOINTER = None
_CHECKPOINTER_CM = None


async def get_checkpointer():
    """懒初始化全局 AsyncPostgresSaver；未配置 CHECKPOINT_DATABASE_URL 时返回 None。"""
    global _CHECKPOINTER, _CHECKPOINTER_CM
    if _CHECKPOINTER is not None:
        return _CHECKPOINTER
    url = settings.CHECKPOINT_DATABASE_URL
    if not url:
        return None
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    _CHECKPOINTER_CM = AsyncPostgresSaver.from_conn_string(url)
    _CHECKPOINTER = await _CHECKPOINTER_CM.__aenter__()
    await _CHECKPOINTER.setup()
    logger.info("LangGraph checkpointer 就绪（PG sidecar）")
    return _CHECKPOINTER


# 交互节点（蓝本 userSelect/formInput）：LangGraph interrupt 原生实现（N-5），不进旧引擎
INTERACTIVE_NODE_TYPES = {"userSelect", "formInput"}


# ---------- 运行注册表：compiled graph 与运行实例解耦 ----------


@dataclass
class RunBundle:
    engine: object  # WorkflowEngine（避免循环 import，不做类型标注）


RUN_REGISTRY: dict[str, RunBundle] = {}


def _edge_key(edge: dict) -> str:
    return f"{edge.get('source')}|{edge.get('sourceHandle') or ''}|{edge.get('target')}"


# ---------- 编译 ----------


def _topology_signature(nodes: dict, edges: list) -> str:
    payload = {
        "nodes": sorted((nid, n.get("flowNodeType", "")) for nid, n in nodes.items()),
        "edges": sorted((_edge_key(e)) for e in edges),
    }
    return hashlib.md5(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _detect_cycle(nodes: dict, edges: list) -> Optional[list[str]]:
    """DFS 三色标记；返回环路节点样本（无环返回 None）。"""
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        if e.get("source") in adj and e.get("target") in adj:
            adj[e["source"]].append(e["target"])
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in nodes}
    stack_path: list[str] = []

    def dfs(u: str) -> Optional[list[str]]:
        color[u] = GRAY
        stack_path.append(u)
        for v in adj[u]:
            if color[v] == GRAY:
                return stack_path[stack_path.index(v) :] + [v]
            if color[v] == WHITE:
                found = dfs(v)
                if found:
                    return found
        stack_path.pop()
        color[u] = BLACK
        return None

    for nid in nodes:
        if color[nid] == WHITE:
            found = dfs(nid)
            if found:
                return found
    return None


def _make_node_fn(node_id: str):
    """生成 LangGraph 节点函数：从 RUN_REGISTRY 取运行实例，复用旧引擎的节点语义。"""

    async def run_fastgpt_node(state: WFState, config) -> dict:  # noqa: ANN001
        run_id = (config or {}).get("configurable", {}).get("wf_run_id")
        bundle = RUN_REGISTRY.get(run_id or "")
        if bundle is None:
            raise RuntimeError(f"LangGraph 运行实例丢失（wf_run_id={run_id}）")
        engine = bundle.engine

        node_state = engine._node_state(node_id)  # noqa: SLF001 —— 协议兼容层有意复用内部语义
        if node_state == "wait":
            # join 拓扑下不应出现；防御性 no-op（等待触发方写入后由其分支继续）
            return {}

        node = engine.nodes[node_id]
        if node_state == "skip":
            # 蓝本 skip 传播：不执行 executor，全部出边置 skipped（错误边同样 skipped）
            engine._mark_out_edges(node_id, {"__all__": "skipped"})  # noqa: SLF001
        elif node.get("flowNodeType") in INTERACTIVE_NODE_TYPES:
            _run_interactive_node(engine, node)
        else:
            # 蓝本：执行前把自身入边重置为 waiting（自由环再入语义；DAG 下无副作用）
            for edge in engine.edges:
                if edge.get("target") == node_id:
                    edge["status"] = "waiting"
            await engine._execute_node(node_id)  # noqa: SLF001 —— 含 D-5 错误语义与 catchError 错误边路由

        # 镜像进 State（可序列化）：P2 checkpoint 恢复的数据基础
        touched = {}
        touched_display = {}
        for e in engine.edges:
            if e.get("source") == node_id or e.get("target") == node_id:
                key = _edge_key(e)
                touched[key] = e.get("status")
                if e.get("_display"):
                    touched_display[key] = e["_display"]
        updates: dict = {"edge_states": touched, "edge_display": touched_display}
        node_outputs = engine.ctx.outputs.get(node_id)
        if node_outputs is not None:
            updates["outputs"] = {node_id: node_outputs}
        if engine.ctx.variables:
            updates["variables"] = dict(engine.ctx.variables)
        return updates

    return run_fastgpt_node


def _node_input(node: dict, key: str, default=None):  # noqa: ANN001
    for item in node.get("inputs") or []:
        if item.get("key") == key:
            return item.get("value", default)
    return default


def _run_interactive_node(engine, node: dict) -> None:  # noqa: ANN001
    """蓝本 userSelect/formInput 挂起协议（dispatch/interactive/*，节点目录 §1.19/§1.20）。

    interrupt() 首次到达抛出挂起（checkpoint 落库）；恢复后本函数从头重放并拿到
    resume 值——因此 interrupt 之前不做任何写入（重放幂等）。
    """
    from app.services.workflows.workflow_engine import NodeRun

    node_id = node["nodeId"]
    node_type = node.get("flowNodeType")
    started = time.time()

    if node_type == "userSelect":
        options = _node_input(node, "userSelectOptions", []) or []
        payload = {
            "type": "userSelect",
            "params": {"description": _node_input(node, "description", ""), "userSelectOptions": options},
        }
        value = interrupt(payload)
        matched = next((o for o in options if o.get("value") == value or o.get("key") == value), None)
        if matched is None:
            # 蓝本：应答无效 -> 全部选项句柄 skip
            engine._mark_out_edges(node_id, {"__all__": "skipped"})  # noqa: SLF001
            engine.ctx.outputs[node_id] = {}
            status, output = "skipped", None
        else:
            engine.ctx.outputs[node_id] = {"selectResult": matched.get("value")}
            # 每个选项一个 source 句柄（句柄 key=选项 key），只激活命中项
            engine._mark_out_edges(node_id, {str(matched.get("key")): "active"})  # noqa: SLF001
            status, output = "success", matched.get("value")
    else:  # formInput
        forms = _node_input(node, "userInputForms", []) or []
        # 蓝本 interactive 协议：type='userInput'、params.inputForm、恢复后 submitted 标记
        payload = {
            "type": "userInput",
            "params": {
                "description": _node_input(node, "description", ""),
                "inputForm": forms,
                # 旧前端兼容字段（2.1.0 前消费 formInput/userInputForms 命名）
                "userInputForms": forms,
                "submitted": False,
            },
        }
        value = interrupt(payload)
        result = value if isinstance(value, dict) else {}
        # 默认值兜底 + 逐字段输出（蓝本：每个表单字段是独立可引用输出）
        outputs = {"formInputResult": result}
        for form in forms:
            key = form.get("key") if isinstance(form, dict) else None
            if not key:
                continue
            outputs[str(key)] = result.get(str(key), form.get("defaultValue") if isinstance(form, dict) else None)
        engine.ctx.outputs[node_id] = outputs
        engine._mark_out_edges(node_id, {"__all__": "active"})  # noqa: SLF001
        status, output = "success", result

    engine.ctx.node_runs.append(
        NodeRun(
            node_id=node_id,
            node_type=str(node_type),
            node_label=node.get("name") or str(node_type),
            status=status,
            duration_ms=int((time.time() - started) * 1000),
            input=payload["params"],
            output=output,
        )
    )


_COMPILE_CACHE: dict[str, object] = {}
_COMPILE_CACHE_MAX = 64


def compile_fastgpt_graph(nodes: dict, edges: list, checkpointer=None):  # noqa: ANN001
    """把 FastGPT 节点/边编译为可执行的 LangGraph app（带拓扑签名缓存）。"""
    cycle = _detect_cycle(nodes, edges)
    if cycle:
        raise UnsupportedGraphError(f"包含环路（{' -> '.join(cycle[:5])}），暂由旧引擎执行")

    start_nodes = [nid for nid, n in nodes.items() if n.get("flowNodeType") == "workflowStart"]
    if not start_nodes:
        raise UnsupportedGraphError("工作流缺少「流程开始」节点")

    signature = f"{_topology_signature(nodes, edges)}|ckpt={checkpointer is not None}"
    cached = _COMPILE_CACHE.get(signature)
    if cached is not None:
        return cached

    # 工具边（selectedTools）是挂载关系：不进 LangGraph 拓扑；只被工具边挂载的节点
    # （tool/stopTool/toolParams 等）由 toolCall 执行器内部调用，不作为流程节点编译
    # 容器子节点（parentNodeId）由容器执行器内部子引擎驱动，同样不进顶层拓扑
    container_children = {nid for nid, n in nodes.items() if n.get("parentNodeId")}
    tool_mounted = {e.get("target") for e in edges if e.get("sourceHandle") == "selectedTools"}
    stop_tool_ids = {nid for nid, n in nodes.items() if n.get("flowNodeType") == "stopTool"}
    flow_edges = [
        e
        for e in edges
        if e.get("sourceHandle") != "selectedTools"
        and e.get("source") not in container_children
        and e.get("target") not in container_children
        # 蓝本 stopTool：工具节点 -> stopTool 的普通连线是"终止信号"挂载关系，不进流程拓扑
        and not (e.get("source") in tool_mounted and e.get("target") in stop_tool_ids)
    ]
    flow_connected = {e.get("source") for e in flow_edges} | {e.get("target") for e in flow_edges}
    excluded = container_children | {
        nid for nid in (tool_mounted | stop_tool_ids) if nid in nodes and nid not in flow_connected
    }

    graph = StateGraph(WFState)
    for nid in nodes:
        if nid not in excluded:
            graph.add_node(nid, _make_node_fn(nid))

    # join 拓扑：按 target 聚合全部上游（去重），一次 add_edge([sources], target)
    sources_by_target: dict[str, list[str]] = {}
    targets_with_out: set[str] = set()
    for e in flow_edges:
        src, tgt = e.get("source"), e.get("target")
        if src in nodes and tgt in nodes:
            bucket = sources_by_target.setdefault(tgt, [])
            if src not in bucket:
                bucket.append(src)
            targets_with_out.add(src)

    graph.add_edge(START, start_nodes[0])
    for tgt, srcs in sources_by_target.items():
        graph.add_edge(srcs if len(srcs) > 1 else srcs[0], tgt)
    for nid in nodes:
        if nid in excluded:
            continue
        if nid not in targets_with_out:
            graph.add_edge(nid, END)

    compiled = graph.compile(checkpointer=checkpointer)
    if len(_COMPILE_CACHE) >= _COMPILE_CACHE_MAX:
        _COMPILE_CACHE.pop(next(iter(_COMPILE_CACHE)))
    _COMPILE_CACHE[signature] = compiled
    return compiled


# ---------- 运行 ----------


async def _assert_checkpoint_owner(compiled, engine, config) -> None:  # noqa: ANN001
    """H2：校验挂起运行归属当前用户/应用；不符抛 CheckpointAccessError（防 resume 越权/IDOR）。

    老 checkpoint 无归属字段时放行（向后兼容），有归属则用户与应用必须匹配。
    """
    snapshot = await compiled.aget_state(config)
    values = snapshot.values if snapshot else {}
    owner_user = values.get("owner_user_id")
    owner_app = values.get("owner_app_id")
    if owner_user and owner_user != engine.ctx.user_id:
        raise CheckpointAccessError("无权恢复该运行：运行归属其他用户")
    if owner_app and engine.ctx.app_id and owner_app != engine.ctx.app_id:
        raise CheckpointAccessError("无权恢复该运行：运行归属其他应用")
    # M8：拓扑变更（重发布改图）后旧 checkpoint 不能安全恢复到当前图，拒绝并提示重发起
    stored_sig = values.get("topo_sig")
    if stored_sig and stored_sig != _topology_signature(engine.nodes, engine.edges):
        raise CheckpointAccessError("工作流定义已变更，无法恢复此前的挂起运行，请重新发起对话")


async def _hydrate_engine_from_checkpoint(compiled, engine, config) -> None:  # noqa: ANN001
    """恢复运行：以 checkpoint 里的 State 镜像重建 ctx 输出/全局变量/边状态。"""
    snapshot = await compiled.aget_state(config)
    values = snapshot.values if snapshot else {}
    engine.ctx.audit_run_id = str(values.get("audit_run_id") or engine.ctx.audit_run_id or "")
    engine.ctx.audit_root_run_id = str(
        values.get("audit_root_run_id") or engine.ctx.audit_root_run_id or ""
    )
    engine.ctx.audit_parent_tool_call_id = str(
        values.get("audit_parent_tool_call_id")
        or engine.ctx.audit_parent_tool_call_id
        or ""
    )
    engine.ctx.audit_parent_logical_call_id = str(
        values.get("audit_parent_logical_call_id")
        or engine.ctx.audit_parent_logical_call_id
        or ""
    )
    # A resumed execution is a new segment.  Keep the explicit resume segment when supplied;
    # only legacy callers without one inherit the checkpoint value.
    engine.ctx.audit_execution_segment = str(
        engine.ctx.audit_execution_segment
        or values.get("audit_execution_segment")
        or ""
    )
    engine.ctx.outputs.update(values.get("outputs") or {})
    engine.ctx.variables.update(values.get("variables") or {})
    edge_states = values.get("edge_states") or {}
    edge_display = values.get("edge_display") or {}
    for edge in engine.edges:
        key = _edge_key(edge)
        status = edge_states.get(key)
        if status:
            edge["status"] = status
        display = edge_display.get(key)
        if display:
            edge["_display"] = display


async def run_engine_with_langgraph(
    engine,  # noqa: ANN001
    *,
    thread_id: Optional[str] = None,
    resume_value=None,  # noqa: ANN001
) -> Optional[dict]:
    """用 LangGraph 驱动一个已构建的 WorkflowEngine 实例（替代 engine.run() 的调度）。

    行为契约与 engine.run() 一致：结果写回 engine.ctx（outputs/output_parts/
    node_runs/uncaught_errors）。命中交互节点挂起时返回
    {"resumeId", "type", "params"}（蓝本 interactive 协议），否则返回 None。
    """
    checkpointer = await get_checkpointer()
    has_interactive = any(n.get("flowNodeType") in INTERACTIVE_NODE_TYPES for n in engine.nodes.values())
    if has_interactive and checkpointer is None:
        raise CheckpointerRequiredError("交互节点需要配置 CHECKPOINT_DATABASE_URL（PG sidecar）")

    compiled = compile_fastgpt_graph(engine.nodes, engine.edges, checkpointer=checkpointer)

    run_id = thread_id or uuid.uuid4().hex
    config = {"configurable": {"wf_run_id": run_id, "thread_id": run_id}, "recursion_limit": 200}
    RUN_REGISTRY[run_id] = RunBundle(engine=engine)
    try:
        if resume_value is not None:
            # 归属校验必须**先于**状态注入（2026-07-28 修正顺序）：反过来时，伪造
            # resume_id 会先把别人 checkpoint 的 outputs/variables 灌进本次 ctx，再抛
            # CheckpointAccessError。实测不外泄（output_parts 不参与 hydrate，异常也先于
            # 任何 return），属纵深防御的顺序错误——先关门再搬东西，别指望后面那道锁。
            await _assert_checkpoint_owner(compiled, engine, config)
            await _hydrate_engine_from_checkpoint(compiled, engine, config)
            result = await compiled.ainvoke(Command(resume=resume_value), config=config)
        else:
            result = await compiled.ainvoke(
                {
                    "variables": dict(engine.ctx.variables),
                    "outputs": {},
                    "edge_states": {},
                    "owner_user_id": engine.ctx.user_id,
                    "owner_app_id": engine.ctx.app_id,
                    "audit_run_id": engine.ctx.audit_run_id,
                    "audit_root_run_id": engine.ctx.audit_root_run_id,
                    "audit_parent_tool_call_id": engine.ctx.audit_parent_tool_call_id,
                    "audit_parent_logical_call_id": engine.ctx.audit_parent_logical_call_id,
                    "audit_execution_segment": engine.ctx.audit_execution_segment,
                    "topo_sig": _topology_signature(engine.nodes, engine.edges),
                },
                config=config,
            )
    finally:
        RUN_REGISTRY.pop(run_id, None)

    interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
    if interrupts:
        first = interrupts[0]
        payload = getattr(first, "value", first)
        base = payload if isinstance(payload, dict) else {"value": payload}
        return {"resumeId": run_id, **base}
    return None


def langgraph_enabled() -> bool:
    return (getattr(settings, "WORKFLOW_ENGINE", "legacy") or "legacy").lower() == "langgraph"


async def stream_engine_with_langgraph(engine):  # noqa: ANN001
    """流式驱动：逐节点产出事件（P3 SSE，架构 §10.5.9 第 8 条）。

    产出 ("node", {nodeId, nodeLabel, status}) 事件流；结束后由调用方从
    engine.ctx 汇总最终结果。交互挂起时最后产出 ("interactive", payload)。
    """
    checkpointer = await get_checkpointer()
    has_interactive = any(n.get("flowNodeType") in INTERACTIVE_NODE_TYPES for n in engine.nodes.values())
    if has_interactive and checkpointer is None:
        raise CheckpointerRequiredError("交互节点需要配置 CHECKPOINT_DATABASE_URL（PG sidecar）")

    compiled = compile_fastgpt_graph(engine.nodes, engine.edges, checkpointer=checkpointer)
    run_id = uuid.uuid4().hex
    config = {"configurable": {"wf_run_id": run_id, "thread_id": run_id}, "recursion_limit": 200}
    RUN_REGISTRY[run_id] = RunBundle(engine=engine)
    interrupted_payload = None
    try:
        seen_runs = 0
        async for chunk in compiled.astream(
            {
                "variables": dict(engine.ctx.variables),
                "outputs": {},
                "edge_states": {},
                "owner_user_id": engine.ctx.user_id,
                "owner_app_id": engine.ctx.app_id,
                "audit_run_id": engine.ctx.audit_run_id,
                "audit_root_run_id": engine.ctx.audit_root_run_id,
                "audit_parent_tool_call_id": engine.ctx.audit_parent_tool_call_id,
                "audit_parent_logical_call_id": engine.ctx.audit_parent_logical_call_id,
                "audit_execution_segment": engine.ctx.audit_execution_segment,
                "topo_sig": _topology_signature(engine.nodes, engine.edges),
            },
            config=config,
            stream_mode="updates",
        ):
            if "__interrupt__" in chunk:
                first = chunk["__interrupt__"][0]
                payload = getattr(first, "value", first)
                base = payload if isinstance(payload, dict) else {"value": payload}
                interrupted_payload = {"resumeId": run_id, **base}
                continue
            # 每个 superstep 后把新增的 NodeRun 作为节点事件吐出
            runs = engine.ctx.node_runs
            while seen_runs < len(runs):
                run = runs[seen_runs]
                seen_runs += 1
                yield "node", {"nodeId": run.node_id, "nodeLabel": run.node_label, "status": run.status}
    finally:
        RUN_REGISTRY.pop(run_id, None)
    if interrupted_payload:
        yield "interactive", interrupted_payload
