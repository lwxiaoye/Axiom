"""四 Tab 模板服务契约烟囱测试（无 DB 依赖的纯函数部分）。

运行：cd agent-api && python3 scripts/check_template_service.py
覆盖：系统工具目录/标签、schema->inputs、previewNode(系统工具)、工具集子项 id 解析、
manifest pythonExecutor 一致性由 check_parity_manifest.py 另行把关。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.workflows.node_template_service import (  # noqa: E402
    _schema_to_inputs,
    build_preview_node,
    list_system_tool_tags,
    list_system_tool_templates,
)
from app.services.gateway.tool_invoker import parse_toolset_child_id  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"[OK] {name}")
    else:
        FAILURES.append(name)
        print(f"[FAIL] {name} {detail}")


def main() -> int:
    tags = list_system_tool_tags()
    check("系统工具标签非空且去重", len(tags) >= 2 and len({t['id'] for t in tags}) == len(tags), str(tags))

    all_tools = list_system_tool_templates(None, None)
    check("系统工具目录返回全部 builtin", len(all_tools) >= 4, str(len(all_tools)))
    check(
        "目录项形状（tool/systemTool/isTool）",
        all(i["flowNodeType"] == "tool" and i["source"] == "systemTool" and i["isTool"] for i in all_tools),
    )
    check("searchKey 过滤生效", len(list_system_tool_templates("JSON 提取", None)) == 1)
    check("标签过滤生效", all(i["intro"] for i in list_system_tool_templates(None, ["基础"])))

    inputs = _schema_to_inputs(
        {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"},
                "count": {"type": "integer"},
                "flag": {"type": "boolean"},
                "payload": {"type": "object"},
            },
            "required": ["expression"],
        }
    )
    by_key = {i["key"]: i for i in inputs}
    check("schema->inputs 数量", len(inputs) == 4)
    check("required 对齐", by_key["expression"]["required"] and not by_key["count"]["required"])
    check("类型映射", by_key["count"]["valueType"] == "number" and by_key["flag"]["valueType"] == "boolean")
    # 蓝本 jsonSchema2NodeInput：具体控件在前、reference 在后
    check("渲染器映射", by_key["payload"]["renderTypeList"] == ["JSONEditor", "reference"])

    preview = asyncio.run(build_preview_node(None, None, template_id="builtin.timestamp"))
    check(
        "系统工具 previewNode 完整性",
        preview["flowNodeType"] == "tool"
        and preview["toolConfig"] == {"systemTool": {"toolId": "builtin.timestamp"}}
        and any(o["type"] == "error" for o in preview["outputs"])
        and preview["isTool"] is True
        and preview["pluginId"] == "builtin.timestamp",
    )

    try:
        asyncio.run(build_preview_node(None, None, template_id="builtin.nonexist"))
        check("未知系统工具拒绝", False)
    except ValueError:
        check("未知系统工具拒绝", True)

    check(
        "工具集子项 id 解析",
        parse_toolset_child_id("http-abc123/search") == ("http", "abc123", "search")
        and parse_toolset_child_id("mcp-x/y") == ("mcp", "x", "y")
        and parse_toolset_child_id("badformat") is None,
    )

    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败")
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
