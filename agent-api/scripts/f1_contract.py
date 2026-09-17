"""F1 协议修复契约测试：容器新键 / parallelRun 语义 / error 旧键回退。容器内运行。"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.workflows.workflow_engine import RunContext, WorkflowEngine  # noqa: E402


def ctx() -> RunContext:
    return RunContext(input_text="hi", variables={}, token="", user_id="u", app_id="a")


def text_node(node_id: str, parent: str, template: str) -> dict:
    return {
        "nodeId": node_id,
        "name": node_id,
        "flowNodeType": "textEditor",
        "parentNodeId": parent,
        "inputs": [{"key": "system_textareaInput", "value": template}],
        "outputs": [{"id": "system_text", "key": "system_text", "type": "static"}],
    }


async def test_loop_current_item():
    graph = {
        "nodes": [
            {
                "nodeId": "loop1",
                "name": "loop",
                "flowNodeType": "loopRun",
                "inputs": [
                    {"key": "loopRunInputArray", "value": ["a", "b", "c"]},
                    {"key": "loopCustomOutputs", "value": ["t1", "system_text"]},
                ],
                "outputs": [],
            },
            {
                "nodeId": "start1",
                "name": "start",
                "flowNodeType": "loopRunStart",
                "parentNodeId": "loop1",
                "inputs": [],
                "outputs": [],
            },
            # 引用蓝本新键 currentItem / currentIndex
            text_node("t1", "loop1", "第{{$start1.currentIndex$}}项:{{$start1.currentItem$}}"),
        ],
        "edges": [
            {"source": "start1", "sourceHandle": "start1-source-right", "target": "t1", "targetHandle": "t1-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run(entry_node_id="loop1")
    out = engine.ctx.outputs["loop1"]["loopArray"]
    assert out == ["第0项:a", "第1项:b", "第2项:c"], out
    print("loop currentItem/currentIndex OK", out)


async def test_loop_legacy_keys():
    graph = {
        "nodes": [
            {
                "nodeId": "loop1",
                "name": "loop",
                "flowNodeType": "loopRun",
                "inputs": [
                    {"key": "loopRunInputArray", "value": ["x", "y"]},
                    {"key": "loopCustomOutputs", "value": ["t1", "system_text"]},
                ],
                "outputs": [],
            },
            {"nodeId": "start1", "name": "s", "flowNodeType": "loopRunStart", "parentNodeId": "loop1", "inputs": [], "outputs": []},
            # 存量画布旧键仍可用
            text_node("t1", "loop1", "旧{{$start1.loopStartIndex$}}:{{$start1.loopStartInput$}}"),
        ],
        "edges": [
            {"source": "start1", "sourceHandle": "start1-source-right", "target": "t1", "targetHandle": "t1-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run(entry_node_id="loop1")
    out = engine.ctx.outputs["loop1"]["loopArray"]
    assert out == ["旧0:x", "旧1:y"], out
    print("loop legacy keys OK", out)


async def test_parallel_semantics():
    # t1 对第 2 项抛错（textEditor 不会抛错，改用 http 节点会依赖网络——用 code 节点让特定项失败）
    graph = {
        "nodes": [
            {
                "nodeId": "par1",
                "name": "par",
                "flowNodeType": "parallelRun",
                "inputs": [
                    {"key": "loopInputArray", "value": [1, 2, 3]},
                    {"key": "parallelRunMaxConcurrency", "value": 2},
                    {"key": "parallelRunMaxRetryTimes", "value": 0},
                    {"key": "loopCustomOutputs", "value": ["c1", "ok"]},
                ],
                "outputs": [],
            },
            {"nodeId": "start1", "name": "s", "flowNodeType": "loopRunStart", "parentNodeId": "par1", "inputs": [], "outputs": []},
            {
                "nodeId": "c1",
                "name": "c1",
                "flowNodeType": "code",
                "parentNodeId": "par1",
                "inputs": [
                    {
                        "key": "code",
                        "value": "def main(params):\n    item = params['item']\n    if item == 2:\n        raise ValueError('boom')\n    return {'ok': 'v%s' % item}",
                    },
                    {"key": "system_addInputParam", "value": []},
                    {"key": "item", "value": ["start1", "currentItem"], "canEdit": True},
                ],
                "outputs": [{"id": "ok", "key": "ok", "type": "dynamic"}],
            },
        ],
        "edges": [
            {"source": "start1", "sourceHandle": "start1-source-right", "target": "c1", "targetHandle": "c1-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run(entry_node_id="par1")
    out = engine.ctx.outputs["par1"]
    assert out["parallelStatus"] == "partial_success", out["parallelStatus"]
    assert out["parallelSuccessResults"] == ["v1", "v3"], out["parallelSuccessResults"]
    full = out["parallelFullResults"]
    assert len(full) == 3
    assert full[0] == {"success": True, "message": "", "data": "v1"}, full[0]
    assert full[1]["success"] is False and full[1]["data"] is None and full[1]["message"], full[1]
    print("parallel semantics OK", out["parallelStatus"], full[1]["message"][:40])


async def test_parallel_legacy_input_key():
    graph = {
        "nodes": [
            {
                "nodeId": "par1",
                "name": "par",
                "flowNodeType": "parallelRun",
                "inputs": [{"key": "loopRunInputArray", "value": ["m", "n"]}],
                "outputs": [],
            },
            {"nodeId": "start1", "name": "s", "flowNodeType": "loopRunStart", "parentNodeId": "par1", "inputs": [], "outputs": []},
            text_node("t1", "par1", "N{{$start1.currentIndex$}}"),
        ],
        "edges": [
            {"source": "start1", "sourceHandle": "start1-source-right", "target": "t1", "targetHandle": "t1-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run(entry_node_id="par1")
    out = engine.ctx.outputs["par1"]
    assert out["parallelStatus"] == "success", out
    assert out["parallelSuccessResults"] == ["m", "n"], out
    print("parallel legacy input key OK")


async def test_error_key_fallback():
    engine = WorkflowEngine({"nodes": [], "edges": [], "chatConfig": {}}, ctx())
    engine.ctx.outputs["n1"] = {"system_error_text": "some error"}
    assert engine._resolve_value(["n1", "error"]) == "some error"
    assert engine._resolve_value(["n1", "system_error_text"]) == "some error"
    print("error key fallback OK")


async def main():
    await test_loop_current_item()
    await test_loop_legacy_keys()
    await test_parallel_semantics()
    await test_parallel_legacy_input_key()
    await test_error_key_fallback()
    print("F1 ALL PASS")


asyncio.run(main())
