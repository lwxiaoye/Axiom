"""双引擎契约测试（任务 #29，规格=docs/fastgpt-node-catalog.md 各节点「运行时」小节 + manifest §1 + 架构 §10.5.9）。

同一批工作流分别用 legacy（手写调度器）与 langgraph（P1 编译器）执行，
比较：整体 status / 每节点 (nodeId,status) 集合 / 每节点 outputs / 答案输出
（并行分支的 answer 拼接顺序本就非确定，用例可声明 ordered=False 改比多重集）。

运行：docker exec agent-api python scripts/dual_engine_contract.py
"""

import asyncio
import json
import sys
from collections import Counter

from app.services.workflows.workflow_engine import execute_workflow

START_ID = "workflowStartNodeId"


def _start_node():
    return {"nodeId": START_ID, "name": "流程开始", "flowNodeType": "workflowStart", "inputs": [], "outputs": []}


def _answer(nid: str, text) -> dict:
    return {"nodeId": nid, "name": f"回复{nid}", "flowNodeType": "answerNode",
            "inputs": [{"key": "text", "value": text}], "outputs": []}


def _edge(src: str, tgt: str, handle_key: str = "right", catch: bool = False) -> dict:
    handle = f"{src}-source_catch-right" if catch else f"{src}-source-{handle_key}"
    return {"source": src, "sourceHandle": handle, "target": tgt, "targetHandle": f"{tgt}-target-left"}


def _wf(nodes: list, edges: list) -> str:
    return json.dumps({"version": "0.1", "nodes": [], "edges": [], "fastgpt": {"nodes": nodes, "edges": edges}})


CASES = [
    {
        "name": "D-9 顺序拼接（answer 前导\\n 无分隔符）",
        "ordered": True,
        "input": "hi",
        "wf": _wf(
            [_start_node(), _answer("a1", "你好"), _answer("a2", [START_ID, "userChatInput"])],
            [_edge(START_ID, "a1"), _edge("a1", "a2")],
        ),
    },
    {
        "name": "分支跳过后汇聚继续（D-5 skip 传播 + join）",
        "ordered": True,
        "input": "问题不为空",
        "wf": _wf(
            [
                _start_node(),
                {
                    "nodeId": "if1", "name": "判断器", "flowNodeType": "ifElseNode",
                    "inputs": [{"key": "ifElseList", "value": [{
                        "condition": "AND",
                        "list": [{"variable": [START_ID, "userChatInput"], "condition": "isEmpty",
                                  "value": "", "valueType": "input"}],
                    }]}],
                    "outputs": [],
                },
                _answer("aIF", "输入为空"),
                _answer("aELSE", "输入非空"),
                _answer("aJoin", "汇聚点"),
            ],
            [
                _edge(START_ID, "if1"),
                _edge("if1", "aIF", "IF"),
                _edge("if1", "aELSE", "ELSE"),
                _edge("aIF", "aJoin"),
                _edge("aELSE", "aJoin"),
            ],
        ),
    },
    {
        "name": "catchError 错误边（SSRF 失败 -> 错误分支引用 system_error_text）",
        "ordered": True,
        "input": "x",
        "wf": _wf(
            [
                _start_node(),
                {
                    "nodeId": "h1", "name": "HTTP", "flowNodeType": "httpRequest468", "catchError": True,
                    "inputs": [
                        {"key": "system_httpReqUrl", "value": "http://169.254.169.254/meta"},
                        {"key": "system_httpMethod", "value": "GET"},
                        {"key": "system_httpTimeout", "value": 5},
                    ],
                    "outputs": [],
                },
                _answer("aOK", "请求成功了"),
                _answer("aErr", ["h1", "system_error_text"]),
            ],
            [_edge(START_ID, "h1"), _edge("h1", "aOK"), _edge("h1", "aErr", catch=True)],
        ),
    },
    {
        "name": "变量更新写全局 + 文本拼接插值",
        "ordered": True,
        "input": "x",
        "wf": _wf(
            [
                _start_node(),
                {
                    "nodeId": "v1", "name": "变量更新", "flowNodeType": "variableUpdate",
                    "inputs": [{"key": "updateList", "value": [
                        {"variable": ["VARIABLE_NODE_ID", "foo"], "value": "bar", "renderType": "input"},
                    ]}],
                    "outputs": [],
                },
                {
                    "nodeId": "t1", "name": "文本拼接", "flowNodeType": "textEditor",
                    "inputs": [{"key": "system_textareaInput", "value": "值:{{foo}}"}],
                    "outputs": [],
                },
                _answer("a1", ["t1", "system_text"]),
            ],
            [_edge(START_ID, "v1"), _edge("v1", "t1"), _edge("t1", "a1")],
        ),
    },
    {
        "name": "并行分支 + 双入边汇聚",
        "ordered": False,  # 并行 answer 拼接顺序不保证
        "input": "x",
        "wf": _wf(
            [_start_node(), _answer("pa", "分支A"), _answer("pb", "分支B"), _answer("pc", "汇聚C")],
            [_edge(START_ID, "pa"), _edge(START_ID, "pb"), _edge("pa", "pc"), _edge("pb", "pc")],
        ),
    },
]


async def _run(case: dict, mode: str) -> dict:
    return await execute_workflow(
        case["wf"], case["input"], {},
        token="", user_id="contract", app_id="contract", llm_api_key="dummy",
        default_model="dummy", engine_mode=mode,
    )


def _node_status_set(result: dict) -> set:
    return {(r["nodeId"], r["status"]) for r in result["nodeRuns"]}


def _compare(case: dict, legacy: dict, lg: dict) -> list[str]:
    problems = []
    if legacy["status"] != lg["status"]:
        problems.append(f"status: legacy={legacy['status']} langgraph={lg['status']}")
    if _node_status_set(legacy) != _node_status_set(lg):
        problems.append(f"nodeRuns: legacy={_node_status_set(legacy)} langgraph={_node_status_set(lg)}")
    if case["ordered"]:
        if legacy["output"] != lg["output"]:
            problems.append(f"output: legacy={legacy['output']!r} langgraph={lg['output']!r}")
    else:
        seg = lambda out: Counter(p for p in out.split("\n") if p)  # noqa: E731
        if seg(legacy["output"]) != seg(lg["output"]):
            problems.append(f"output(multiset): legacy={legacy['output']!r} langgraph={lg['output']!r}")
    if (legacy["errorMessage"] or "") != (lg["errorMessage"] or ""):
        problems.append(f"errorMessage: legacy={legacy['errorMessage']!r} langgraph={lg['errorMessage']!r}")
    return problems


async def main() -> None:
    failed = 0
    for case in CASES:
        legacy = await _run(case, "legacy")
        lg = await _run(case, "langgraph")
        problems = _compare(case, legacy, lg)
        if problems:
            failed += 1
            print(f"FAIL  {case['name']}")
            for p in problems:
                print(f"      {p}")
        else:
            print(f"PASS  {case['name']}  status={lg['status']} output={lg['output']!r}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} 用例契约一致")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
