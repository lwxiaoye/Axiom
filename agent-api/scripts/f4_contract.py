"""F4/F7 契约：code 具名入参/旧签名兼容/codeType 拦截/动态输入默认值/HTTP 错误输出结构。容器内运行。"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app.services.workflows.workflow_engine import RunContext, WorkflowEngine, NodeRun  # noqa: E402


def ctx() -> RunContext:
    return RunContext(input_text="hi", variables={}, token="", user_id="u", app_id="a")


def code_node(code: str, inputs: list, outputs: list, code_type: str = "py") -> dict:
    return {
        "nodeId": "c1",
        "name": "code",
        "flowNodeType": "code",
        "inputs": [
            {"key": "codeType", "value": code_type},
            {"key": "code", "value": code},
            {"key": "system_addInputParam", "value": []},
            *inputs,
        ],
        "outputs": outputs,
    }


async def run_single(node: dict) -> WorkflowEngine:
    engine = WorkflowEngine({"nodes": [node], "edges": [], "chatConfig": {}}, ctx())
    await engine._run_code(node, NodeRun(node_id="c1", node_type="code", node_label="c"))
    return engine


async def test_named_args():
    node = code_node(
        'def main(data1, data2):\n    return {"result": data1, "data2": data2}',
        [
            {"key": "data1", "value": "A", "canEdit": True},
            {"key": "data2", "value": "B", "canEdit": True},
        ],
        [
            {"id": "result", "key": "result", "type": "dynamic"},
            {"id": "data2", "key": "data2", "type": "dynamic"},
        ],
    )
    engine = await run_single(node)
    out = engine.ctx.outputs["c1"]
    assert out["result"] == "A" and out["data2"] == "B", out
    assert out["system_rawResponse"] == {"result": "A", "data2": "B"}, out
    print("named args OK", out["result"], out["data2"])


async def test_legacy_params_signature():
    node = code_node(
        'def main(params):\n    return {"result": params["x"]}',
        [{"key": "x", "value": 42, "canEdit": True}],
        [{"id": "result", "key": "result", "type": "dynamic"}],
    )
    engine = await run_single(node)
    assert engine.ctx.outputs["c1"]["result"] == 42
    print("legacy main(params) OK")


async def test_js_execution():
    # 蓝本 JS_TEMPLATE 形态：单对象解构入参
    node = code_node(
        "function main({data1, data2}){\n    return { result: data1, data2 }\n}",
        [
            {"key": "data1", "value": "JA", "canEdit": True},
            {"key": "data2", "value": "JB", "canEdit": True},
        ],
        [
            {"id": "result", "key": "result", "type": "dynamic"},
            {"id": "data2", "key": "data2", "type": "dynamic"},
        ],
        code_type="js",
    )
    engine = await run_single(node)
    out = engine.ctx.outputs["c1"]
    assert out["result"] == "JA" and out["data2"] == "JB", out
    print("js 执行 OK", out["result"], out["data2"])


async def test_js_async_and_error():
    node = code_node(
        "async function main({x}){\n    const v = await Promise.resolve(x * 2);\n    return { result: v }\n}",
        [{"key": "x", "value": 21, "canEdit": True}],
        [{"id": "result", "key": "result", "type": "dynamic"}],
        code_type="js",
    )
    engine = await run_single(node)
    assert engine.ctx.outputs["c1"]["result"] == 42
    print("js async OK")

    bad = code_node("function main(){ throw new Error('js boom') }", [], [], code_type="js")
    try:
        await run_single(bad)
        raise AssertionError("js 运行错误应当抛出")
    except Exception as exc:  # noqa: BLE001
        assert "js boom" in str(exc), exc
        print("js 错误透出 OK")


async def test_default_value_fallback():
    node = code_node(
        'def main(data1):\n    return {"result": data1}',
        [{"key": "data1", "value": ["nope", "missing"], "canEdit": True, "defaultValue": "DFT"}],
        [{"id": "result", "key": "result", "type": "dynamic"}],
    )
    engine = await run_single(node)
    assert engine.ctx.outputs["c1"]["result"] == "DFT", engine.ctx.outputs["c1"]
    print("动态输入 defaultValue 回退 OK")


async def test_http_error_outputs():
    node = {
        "nodeId": "h1",
        "name": "http",
        "flowNodeType": "httpRequest468",
        "catchError": True,
        "inputs": [
            {"key": "system_httpMethod", "value": "GET"},
            {"key": "system_httpReqUrl", "value": "https://httpbin.org/status/404"},
            {"key": "system_httpTimeout", "value": 10},
        ],
        "outputs": [
            {"id": "system_httpRawError", "key": "system_httpRawError", "type": "error"},
            {"id": "error", "key": "error", "type": "error"},
        ],
    }
    graph = {
        "nodes": [
            {"nodeId": "s", "name": "s", "flowNodeType": "workflowStart", "inputs": [], "outputs": []},
            node,
        ],
        "edges": [
            {"source": "s", "sourceHandle": "s-source-right", "target": "h1", "targetHandle": "h1-target-left"},
        ],
        "chatConfig": {},
    }
    engine = WorkflowEngine(graph, ctx())
    await engine.run()
    out = engine.ctx.outputs.get("h1") or {}
    assert "system_error_text" in out and "error" in out, out
    raw = out.get("system_httpRawError")
    assert isinstance(raw, dict) and (raw.get("status") == 404 or raw.get("message")), out
    print("http 错误输出三键 OK", {k: str(v)[:40] for k, v in out.items()})


async def main():
    await test_named_args()
    await test_legacy_params_signature()
    await test_js_execution()
    await test_js_async_and_error()
    await test_default_value_fallback()
    await test_http_error_outputs()
    print("F4/F7 ALL PASS")


asyncio.run(main())
