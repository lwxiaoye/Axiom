# -*- coding: utf-8 -*-
"""write_file 富格式前置闸（2026-07-28）。

write_file 只写 UTF-8 文本：以 .docx/.pptx/.xlsx/.pdf/图片为扩展名的写入必然产出
打不开的坏文件（不是 zip/二进制容器）。bash 产物有 review_file 硬校验兜底，这条
通道此前是质检盲区——修法是写之前就拦掉并指路 bash+对应库，省一轮
「写坏→质检 fail→返工」。文本格式（.md/.txt/.csv）不受影响。
"""
import pytest

from app.services.chat.tools.base import ToolSoftError
from app.services.chat.tools.paths import build_path_tools


def _write_tool():
    tools = build_path_tools(
        user_id="u-gate", thread_id="th-gate", run_id="r-gate",
        tool_meta_sink={}, newapi_key="k")
    return next(t for t in tools if t.name == "write_file")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["报告.docx", "slides.pptx", "数据.xlsx", "扫描.pdf", "图.png"])
async def test_rich_format_write_is_blocked_with_guidance(path):
    tool = _write_tool()
    with pytest.raises(ToolSoftError) as ei:
        await tool.execute({"path": path, "content": "# 假装是文档"})
    msg = str(ei.value)
    assert "bash" in msg, "报错必须指路正确通道（bash+对应库），不能只说不行"
    assert "坏文件" in msg


def test_office_task_still_allows_process_script_write():
    """办公交付不得再拦 write_file 过程脚本；脚本不是产物卡，但模型要靠它导出。"""
    src = open("app/services/chat/tools/paths.py", encoding="utf-8").read()
    assert "_office_product_goal" not in src
    assert "不要用 write_file 写过程脚本" not in src


@pytest.mark.asyncio
async def test_csv_stays_writable_as_text():
    """csv 是文本，明确不拦（与 _UNEDITABLE_EXTS 排除 csv 的口径一致）。

    只断言不被富格式闸拦住——真实写入走 DB，这里不建库；任何非富格式闸的
    失败（如无库连接）都可接受，唯独不能是「富格式坏文件」这个拒绝理由。
    """
    tool = _write_tool()
    try:
        await tool.execute({"path": "表.csv", "content": "a,b\n1,2"})
    except ToolSoftError as e:
        assert "坏文件" not in str(e), "csv 不该被富格式闸拦"
    except Exception:
        pass
