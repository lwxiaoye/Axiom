"""LangGraph State Schema（架构文档 §10.5.10）。

P1 阶段：执行真源仍是每次运行的 WorkflowEngine 实例（RUN_REGISTRY 持有），
State 承载可序列化镜像（节点输出/边状态/全局变量），为 P2 的 checkpoint
恢复（重建 RunContext）与运行回放打底。
"""

from typing import Annotated, TypedDict


def merge_dict(left: dict | None, right: dict | None) -> dict:
    """节点级合并：每个 LangGraph 节点只写自己的键，后写覆盖。"""
    return {**(left or {}), **(right or {})}


class WFState(TypedDict, total=False):
    # nodeId -> {outputKey: value}
    outputs: Annotated[dict, merge_dict]
    # edgeKey(source|sourceHandle|target) -> waiting|active|skipped（调度真源镜像）
    edge_states: Annotated[dict, merge_dict]
    # edgeKey -> 展示轨迹状态（不被执行前的 waiting 重置抹掉，供画布回显与恢复）
    edge_display: Annotated[dict, merge_dict]
    # 全局变量（VARIABLE_NODE_ID 命名空间）
    variables: Annotated[dict, merge_dict]
    # 挂起运行归属（H2：resume 越权防护，随 checkpoint 落库；默认 LastValue reducer）
    owner_user_id: str
    owner_app_id: str
    # Provider accounting lineage survives HITL checkpoint/resume.  These values contain only
    # identifiers, never prompt/model/tool content.
    audit_run_id: str
    audit_root_run_id: str
    audit_parent_tool_call_id: str
    audit_parent_logical_call_id: str
    audit_execution_segment: str
    # 拓扑签名（M8：重发布改拓扑后旧 resumeId 不能落错节点，恢复时比对不一致则拒绝）
    topo_sig: str
