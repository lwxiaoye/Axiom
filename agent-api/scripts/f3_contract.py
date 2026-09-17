"""F3 stopTool 语义契约：LangGraph 拓扑排除 + 发布校验放行 + 引擎 stop 检测。容器内运行。"""
import sys

sys.path.insert(0, "/app")

from app.routers.workflow import _validate_workflow_definition  # noqa: E402


def graph_with_stop_tool():
    return {
        "nodes": [
            {"nodeId": "start", "name": "开始", "flowNodeType": "workflowStart", "inputs": [], "outputs": []},
            {
                "nodeId": "tc",
                "name": "工具调用",
                "flowNodeType": "tools",
                "inputs": [{"key": "model", "value": "default"}],
                "outputs": [],
            },
            {
                "nodeId": "t1",
                "name": "文本拼接",
                "flowNodeType": "textEditor",
                "inputs": [{"key": "system_textareaInput", "value": "x"}],
                "outputs": [{"id": "system_text", "key": "system_text", "type": "static"}],
            },
            {"nodeId": "st", "name": "停止", "flowNodeType": "stopTool", "inputs": [], "outputs": []},
            {
                "nodeId": "ans",
                "name": "回复",
                "flowNodeType": "answerNode",
                "inputs": [{"key": "text", "value": "done"}],
                "outputs": [],
            },
        ],
        "edges": [
            {"source": "start", "sourceHandle": "start-source-right", "target": "tc", "targetHandle": "tc-target-left"},
            {"source": "tc", "sourceHandle": "selectedTools", "target": "t1", "targetHandle": "selectedTools"},
            # 蓝本语义：工具节点普通下游连 stopTool
            {"source": "t1", "sourceHandle": "t1-source-right", "target": "st", "targetHandle": "st-target-left"},
            {"source": "tc", "sourceHandle": "tc-source-right", "target": "ans", "targetHandle": "ans-target-left"},
        ],
        "chatConfig": {},
    }


def test_validator():
    import json

    problems = _validate_workflow_definition(json.dumps({"fastgpt": graph_with_stop_tool()}))
    assert problems == [], problems
    print("validator OK（stopTool 普通下游连线通过校验）")


def test_engine_stop_detection():
    from app.services.workflows.workflow_engine import RunContext, WorkflowEngine

    engine = WorkflowEngine(graph_with_stop_tool(), RunContext(input_text="", variables={}, token="", user_id="u", app_id="a"))
    # 复现 _run_tool_call 的检测逻辑输入：t1 的普通下游是 stopTool
    hits = [
        e for e in engine.edges
        if e.get("source") == "t1" and engine.nodes.get(e.get("target"), {}).get("flowNodeType") == "stopTool"
    ]
    assert hits, "普通边 stopTool 检测失败"
    print("engine stop 检测 OK（普通边命中）")


def test_compiler_exclusion():
    from app.services.workflow_runtime.compiler import compile_fastgpt_graph

    g = graph_with_stop_tool()
    nodes = {n["nodeId"]: n for n in g["nodes"]}
    compiled = compile_fastgpt_graph(nodes, g["edges"], checkpointer=None)
    node_names = set(compiled.get_graph().nodes.keys())
    assert "st" not in node_names, node_names
    assert "t1" not in node_names, node_names
    assert "tc" in node_names and "ans" in node_names, node_names
    print("LangGraph 拓扑 OK（stopTool/工具节点被排除，主流程保留）", sorted(node_names))


test_validator()
test_engine_stop_detection()
test_compiler_exclusion()
print("F3 ALL PASS")
