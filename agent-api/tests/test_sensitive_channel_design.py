# -*- coding: utf-8 -*-
"""敏感审批通道的设计锁定（2026-07-28）。

现状（有意的设计，不是缺口）：敏感判定走**动态**通道——danger.assess() 按工具名+参数
判「真正不可逆或对外有副作用」的调用（rm -rf、DROP TABLE、覆盖删除类），命中才进
审批网关 pending_approval；工具对象上的**静态** sensitive 标记全量为 False。

为什么锁死「静态零挂载」：静态标记是调用级一刀切——标了 sensitive 的工具**每一次**
调用都要人工审批。bash 标上=每条 ls 都要审批；browser_act 标上=有状态浏览每次点击
都要审批，产品直接不可用。按参数判危险（danger.assess）才是正确粒度。
未来真要给某个工具挂静态 sensitive，先回答：它是否每一次调用都不可逆？若不是，
去 danger.py 加参数判据，不要碰这个标记。
"""
import pytest

from app.services.chat.tools import build_tools


@pytest.mark.asyncio
async def test_no_tool_is_statically_sensitive():
    tools = await build_tools(
        token="t", knowledge_ids=None, web_enabled=True, user_id="u-sens",
        thread_id="th-sens", newapi_key="k", run_id="r-sens",
        user_message="做一份文档", turn_intent="execution", action_authority="mutate",
    )
    assert tools, "工具集不该为空——空集说明夹具参数已过时，先修夹具"
    statically_sensitive = [t.name for t in tools if getattr(t, "sensitive", False)]
    assert not statically_sensitive, (
        f"这些工具被标了静态 sensitive={statically_sensitive}——每一次调用都会进审批网关。"
        "若确属每次调用皆不可逆请更新本测试并说明；否则请改在 danger.py 加参数级判据。"
    )


def test_danger_assess_is_the_live_channel():
    """动态通道必须真的在：删除类 bash 命令要能被参数判据点名。"""
    from app.services.chat.tools import danger

    reason = danger.assess("bash", {"command": "rm -rf /workspace/files"})
    assert reason, "danger.assess 对 rm -rf 必须给出拦截理由——动态敏感通道是唯一的闸，它空了整个审批层就真空了"
