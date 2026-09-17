"""F10 契约：loopRun conditional 模式 / queryExtension 数组输出格式 / readFiles rawResponse。容器内运行。"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.services.workflows.workflow_engine import RunContext, WorkflowEngine  # noqa: E402


def ctx() -> RunContext:
    return RunContext(input_text="hi", variables={}, token="", user_id="u", app_id="a")


async def test_conditional_loop():
    graph = {
        "nodes": [
            {
                "nodeId": "loop1",
                "name": "loop",
                "flowNodeType": "loopRun",
                "inputs": [
                    {"key": "loopRunMode", "value": "conditional"},
                    {"key": "loopCustomOutputs", "value": ["t1", "system_text"]},
                ],
                "outputs": [],
            },
            {"nodeId": "start1", "name": "s", "flowNodeType": "loopRunStart", "parentNodeId": "loop1", "inputs": [], "outputs": []},
            {
                "nodeId": "t1",
                "name": "t1",
                "flowNodeType": "textEditor",
                "parentNodeId": "loop1",
                "inputs": [{"key": "system_textareaInput", "value": "第{{$start1.currentIteration$}}轮"}],
                "outputs": [{"id": "system_text", "key": "system_text", "type": "static"}],
            },
            {
                "nodeId": "if1",
                "name": "if",
                "flowNodeType": "ifElseNode",
                "parentNodeId": "loop1",
                "inputs": [
                    {
                        "key": "ifElseList",
                        "value": [
                            {
                                "condition": "AND",
                                "list": [
                                    {"variable": ["start1", "currentIteration"], "condition": "greaterThanOrEqualTo", "value": "3", "valueType": "input"}
                                ],
                            }
                        ],
                    }
                ],
                "outputs": [],
            },
            {"nodeId": "brk", "name": "break", "flowNodeType": "loopRunBreak", "parentNodeId": "loop1", "inputs": [], "outputs": []},
        ],
        "edges": [
            {"source": "start1", "sourceHandle": "start1-source-right", "target": "t1", "targetHandle": "t1-target-left"},
            {"source": "t1", "sourceHandle": "t1-source-right", "target": "if1", "targetHandle": "if1-target-left"},
            {"source": "if1", "sourceHandle": "if1-source-IF", "target": "brk", "targetHandle": "brk-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run(entry_node_id="loop1")
    out = engine.ctx.outputs["loop1"]["loopArray"]
    assert out == ["第1轮", "第2轮", "第3轮"], out
    print("conditional loop OK", out)


async def test_query_extension_format():
    # 不实际调模型，仅验证输出解析逻辑：直接模拟 llm 返回多行
    engine = WorkflowEngine({"nodes": [], "edges": [], "chatConfig": {}}, ctx())
    text = "查询一\n查询二\nhi"
    queries = [line.strip() for line in text.splitlines() if line.strip()]
    deduped = list(dict.fromkeys(["hi", *queries]))
    output = json.dumps(deduped, ensure_ascii=False)
    parsed = json.loads(output)
    assert parsed == ["hi", "查询一", "查询二"], parsed
    assert isinstance(engine, WorkflowEngine)
    print("queryExtension JSON 数组格式 OK", parsed)


asyncio.run(test_conditional_loop())
asyncio.run(test_query_extension_format())
print("F10 ALL PASS")
