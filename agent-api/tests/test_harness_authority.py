"""Agent Harness 的授权与原位修改硬门禁。"""

import pytest
from types import SimpleNamespace

from app.services.agent_harness import model_driver
from app.services.chat.turn_decision import decide_turn
from app.services.tasks.run_input_service import classify_input


@pytest.fixture(autouse=True)
def _no_live_connector_lookup(monkeypatch):
    from app.services.chat import tools as chat_tools

    async def _none(**_kwargs):
        return []

    monkeypatch.setattr(chat_tools._connectors, "build_connector_tools", _none)


def _visible_names(tools, capability_scope: str) -> set[str]:
    from app.services.agent_harness.contracts import RunSnapshot
    from app.services.agent_harness.policy import HarnessPolicy

    run = RunSnapshot(
        run_id="run-policy",
        thread_id="thread-policy",
        user_id="user-policy",
        agent_mode="standard",
        phase="executing",
        capability_scope=capability_scope,
        state_version=1,
        goal_revision=0,
        plan_version=0,
        event_cursor=0,
        execution_profile={"id": "interactive"},
    )
    policy = HarnessPolicy()
    return {tool.name for tool in tools if policy.evaluate(tool.spec, run).allowed}


# 一个对话框、能力常驻：正常对话可读写；只有用户显式要求“不修改”才剥离写工具。
@pytest.mark.parametrize(
    ("message", "intent", "authority"),
    [
        ("我觉得这里太挤", "feedback", "mutate"),
        ("我觉得这里需要优化", "feedback", "mutate"),
        ("report.md 的排版感觉有点乱。", "feedback", "mutate"),
        ("这里太挤，帮我改一下", "execute", "mutate"),
        ("如何修改这个页面", "conversation", "mutate"),
        ("修改刚才那个文件", "revise", "mutate"),
        ("重新做一份报告", "execute", "mutate"),
        ("帮我对比三份报告并总结差异", "execute", "mutate"),
        ("分析一下这个系统架构", "execute", "mutate"),
        ("帮我检查现有项目有没有 bug", "execute", "mutate"),
        ("帮我分析代码，直接执行，不用确认", "execute", "mutate"),
        ("读取现有代码并给出优化方案，不要修改任何文件，直接执行", "execute", "inspect"),
        ("给出改进建议，不要修改任何文件", "feedback", "inspect"),
        ("只分析一下这个文件", "conversation", "inspect"),
        ("只查看一下，不要动", "conversation", "inspect"),
        ("继续改一下刚才那个文件", "revise", "mutate"),
        ("把标题字号调大点", "execute", "mutate"),
        ("给这份文件的颜色换成蓝白色调", "revise", "mutate"),
        ("直接执行部署", "execute", "mutate"),
    ],
)
def test_turn_decision_three_tier_authority(message, intent, authority):
    decision = decide_turn(message)
    assert decision.intent == intent
    assert decision.authority == authority


def test_revision_defaults_to_existing_target_not_new_artifact():
    revise = decide_turn("修改刚才那个文件")
    recreate = decide_turn("重新做一份报告")
    assert revise.revision is True
    assert revise.allow_create is False
    assert recreate.allow_create is True


def test_feedback_keeps_write_capability_and_revises_existing_target():
    """意见不再触发“只读模式”；存在明确产物时做最小可逆改进。"""
    d = decide_turn("report.md 的排版感觉有点乱。")
    assert d.intent == "feedback"
    assert d.authority == "mutate"
    assert d.revision is True
    assert "最小、可逆的改进" in d.prompt_block()

    explicit = decide_turn("report.md 有点乱，帮我调整一下")
    assert explicit.intent == "revise"
    assert explicit.authority == "mutate"
    assert explicit.revision is True


def test_external_actions_require_confirmation():
    """确认后执行层（V3 §九）：发送/发布/删除/提交等外部动作立确认标记。"""
    send = decide_turn("帮我把这份报告提交到系统里")
    assert send.authority == "mutate"
    assert send.requires_confirmation is True
    assert "必须先向用户确认" in send.prompt_block()

    delete = decide_turn("删除刚才那个文件")
    assert delete.requires_confirmation is True

    plain = decide_turn("把标题字号调大点")
    assert plain.requires_confirmation is False

    analysis = decide_turn("分析一下这个系统架构")
    assert analysis.authority == "mutate"
    assert analysis.requires_confirmation is False

    no_write = decide_turn("只分析这个系统，不要修改任何文件")
    assert no_write.authority == "inspect"
    assert "不要把这称为某种会话模式" in no_write.prompt_block()

    inspect_request = decide_turn("帮我检查现有项目有没有 bug")
    assert inspect_request.authority == "mutate"
    assert inspect_request.reason_code == "explicit_inspection"
    assert "本轮目标不是修改" in inspect_request.prompt_block()


def test_input_type_is_deterministic_and_auditable():
    assert classify_input("先做验证，再做排版") == "reprioritize"
    assert classify_input("新增一个 PDF 交付物") == "scope_change"
    assert classify_input("另外一个新任务：做预算表") == "new_task"
    assert classify_input("颜色用深蓝色即可") == "guidance"


@pytest.mark.asyncio
async def test_no_write_authority_removes_all_mutating_tools():
    """显式「不要修改」→ inspect：写工具仍被物理剥离（先做哲学下唯一的硬门禁）。"""
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-harness",
        action_authority="inspect",
    )
    names = _visible_names(tools, "inspect")
    assert not names.intersection(
        {"edit_file", "update_file", "create_file", "execute_in_sandbox", "remember_fact", "forget_memory"}
    )


@pytest.mark.asyncio
async def test_ambiguous_revision_keeps_read_and_ask_path():
    """2026-08-05 harness 硬闸：目标未解析时只留 read/glob，写/执行物理收起。"""
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-harness",
        action_authority="mutate",
        revision_mode=True,
        allow_create=False,
        revision_target=None,
    )
    names = _visible_names(tools, "revision_pending")
    assert "read_file" in names
    assert names.intersection({"list_files", "glob"}), "必须仍能列出文件供用户确认"
    assert "download_url" not in names, "目标未解析时 download_url 可静默覆盖任意文件，仍应收掉"
    # Claude Code 式硬闸：未确认前不给写面（与 prompt「先问」同口径，避免互相打架）
    for banned in ("bash", "write_file", "edit_file"):
        assert banned not in names, f"ambiguous revision must hard-gate {banned}"


@pytest.mark.asyncio
async def test_revision_tool_rejects_writing_another_file_before_io(monkeypatch):
    from app.services.files import user_file_service

    async def fake_list_files(_user_id, folder_id=None):
        return {"files": [
            {"id": "target-1", "filename": "原稿.md", "size": 9, "createdAt": "2026-07-27T00:00:00"},
            {"id": "other-file", "filename": "别的文件.md", "size": 5, "createdAt": "2026-07-27T00:00:00"},
        ]}

    monkeypatch.setattr(user_file_service, "list_files", fake_list_files)
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-harness",
        action_authority="mutate",
        revision_mode=True,
        allow_create=False,
        revision_target={"file_id": "target-1", "filename": "原稿.md"},
    )
    names = _visible_names(tools, "revision")
    assert "create_file" not in names
    # 2026-07-27：edit_file 已从 file_id 寻址换成路径寻址（遗留版退休），**断言不变**——
    # 「本轮只授权原位修改某个文件」这条语义已移植进 paths.py，仍必须拦住改别的文件。
    edit = next(tool for tool in tools if tool.name == "edit_file")
    with pytest.raises(model_driver.ToolSoftError, match="只授权原位修改"):
        await edit.execute(
            {
                "path": "别的文件.md",
                "old_string": "旧",
                "new_string": "新",
            }
        )


def _revision_fs(monkeypatch, *, changes):
    """把「原位修改轮 + bash 在 files/ 里产生了 changes」这套场景的 IO 全部打桩。

    返回 saved_calls：落库层真正被调用的动作。断言它才算数——回执文字可以骗人，
    这个列表就是「用户的文件区到底变没变」。
    """
    from types import SimpleNamespace as _NS

    from app.core.config import settings
    from app.services.files import user_file_service as ufs
    from app.services.sandbox import sandbox_executor


    async def fake_list_files(_uid, folder_id=None):
        return {"files": [{"id": "target-1", "filename": "季度汇报.pptx", "size": 4,
                           "createdAt": "2026-07-27T00:00:00"}]}

    async def fake_read_many(_uid, fids):
        return {fid: b"PK\x03\x04" for fid in fids}

    saved_calls: list = []

    async def fake_save(_uid, name, data, **_kw):
        saved_calls.append(("save", name))
        return {"id": f"new-{name}", "filename": name, "size": len(data)}

    async def fake_overwrite(_uid, fid, data, **kw):
        saved_calls.append(("overwrite", fid))
        return {"id": fid, "filename": kw.get("filename"), "size": len(data)}

    async def fake_execute_in_sandbox(_command, **kwargs):
        # 镜像加载器现在由**内核**在拿到会话锁之后回调（增量同步，2026-07-28）。
        # 桩里必须照做：`_path_to_id` 是在那一步填上的，不调用它 persist() 就分不清
        # 「覆盖已有」和「新建」，原位修改会被自己的授权闸挡下来。
        loader = kwargs.get("workspace_loader")
        if loader is not None:
            await loader({})
        return _NS(ok=True, stdout="", stderr="", exit_code=0, error=None, truncated=False,
                   review=None, workspace_oversized=[], workspace_deleted=[],
                   outputs_migrated=[], outputs_conflicts=[],
                   workspace_changes=changes)

    monkeypatch.setattr(ufs, "list_files", fake_list_files)
    monkeypatch.setattr(ufs, "read_many_bytes", fake_read_many)
    monkeypatch.setattr(ufs, "save_file", fake_save)
    monkeypatch.setattr(ufs, "overwrite_file", fake_overwrite)
    monkeypatch.setattr(sandbox_executor, "execute_in_sandbox", fake_execute_in_sandbox)
    return saved_calls


async def _revision_bash():
    tools = await model_driver.build_tools(
        token="", knowledge_ids=None, web_enabled=False,
        user_id="u-harness", thread_id="th-1", run_id="run-1",
        action_authority="mutate", revision_mode=True, allow_create=False,
        revision_target={"file_id": "target-1", "filename": "季度汇报.pptx"},
    )
    names = _visible_names(tools, "revision")
    # bash **必须还在**：PPT 修改就是靠它跑技能脚本，摘掉等于把主用例打死。
    # 所以它的门禁只能下沉到落库层，见下面两条断言。
    assert "bash" in names
    assert "write_file" not in names and "create_file" not in names
    return next(tool for tool in tools if tool.name == "bash")


@pytest.mark.asyncio
async def test_revision_bash_cannot_persist_a_replacement_file(monkeypatch):
    """原位修改轮里 bash 另建的替代品不得进用户文件区（2026-07-27 复核发现的缺口）。

    当时 write_file 已被摘、edit_file/download_url 被 `_require_target()` 挡住，
    **唯独 bash 什么校验都没有**——一条 `cp` 就能另起一份，且照常落库。
    """
    saved_calls = _revision_fs(
        monkeypatch, changes=[{"path": "季度汇报_v2.pptx", "data": b"PK\x03\x04new"}])
    bash = await _revision_bash()
    text = (await bash.execute({
        "command": "cp /workspace/files/季度汇报.pptx /workspace/files/季度汇报_v2.pptx"})
    ).model_content

    assert saved_calls == [], "另建的替代品一个字节都不能进「我的文件」"
    # 挡下来还必须**说出来**：不说的话模型以为交付完成，转头向用户宣布"新版已生成"
    assert "只授权原位修改" in text and "季度汇报_v2.pptx" in text
    assert "本轮**只授权原位修改" in bash.description, "边界要对模型可见，别让它做完才发现白做"


@pytest.mark.asyncio
async def test_revision_bash_still_writes_back_the_target(monkeypatch):
    """反向边界：改目标文件本身必须照常落库——PPT 修改的主用例走的就是这条路。"""
    saved_calls = _revision_fs(
        monkeypatch, changes=[{"path": "季度汇报.pptx", "data": b"PK\x03\x04updated"}])
    bash = await _revision_bash()
    text = await bash.execute({"command": "python3 /workspace/skills/ppt-studio/build.py"})

    assert saved_calls == [("overwrite", "target-1")], "改原文件要覆盖原 file_id（进版本历史）"
    assert "只授权原位修改" not in text


@pytest.mark.asyncio
async def test_revision_tool_passes_snapshot_hash_to_atomic_write(monkeypatch):
    from app.services.files import user_file_service

    captured = {}

    async def fake_read_bytes(_user_id, _file_id):
        return SimpleNamespace(filename="原稿.md"), "旧内容".encode()

    async def fake_list_files(_user_id, folder_id=None):
        # 路径寻址靠文件名解析到 file_id（遗留版是模型直接给 file_id）
        return {"files": [{"id": "target-1", "filename": "原稿.md", "size": 9,
                           "createdAt": "2026-07-27T00:00:00"}]}

    async def fake_update(_user_id, _file_id, content, **kwargs):
        captured.update(kwargs)
        return {
            "id": "target-1", "filename": "原稿.md",
            "size": len(content.encode()), "versionNo": 2,
        }

    monkeypatch.setattr(user_file_service, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(user_file_service, "update_file_content", fake_update)
    monkeypatch.setattr(user_file_service, "list_files", fake_list_files)
    tools = await model_driver.build_tools(
        token="",
        knowledge_ids=None,
        web_enabled=False,
        user_id="u-harness",
        action_authority="mutate",
        revision_mode=True,
        allow_create=False,
        revision_target={
            "file_id": "target-1",
            "filename": "原稿.md",
            "sha256": "abc123",
        },
    )
    edit = next(tool for tool in tools if tool.name == "edit_file")
    # 同上：改成路径寻址，但「快照 hash 必须传给原子写」这条断言原样保留
    await edit.execute({
        "path": "原稿.md",
        "old_string": "旧",
        "new_string": "新",
    })
    assert captured["expected_sha256"] == "abc123"


def test_non_mutating_turn_cannot_plan_or_call_subagent():
    import inspect
    from app.services.chat import main_tool_turn

    source = inspect.getsource(main_tool_turn.run_agent_turn)
    assert 'if env.action_authority != "mutate"' in source
    assert 'subagent_candidates = []' in source


def test_mysql_lock_timeout_and_deadlock_are_both_retryable():
    from app.services.chat.turn_finalizer import is_mysql_deadlock, is_mysql_retryable_lock

    timeout = SimpleNamespace(orig=SimpleNamespace(args=(1205, "lock wait timeout")))
    deadlock = SimpleNamespace(orig=SimpleNamespace(args=(1213, "deadlock")))
    other = SimpleNamespace(orig=SimpleNamespace(args=(1062, "duplicate")))

    assert is_mysql_deadlock(timeout) is False
    assert is_mysql_deadlock(deadlock) is True
    assert is_mysql_retryable_lock(timeout) is True
    assert is_mysql_retryable_lock(deadlock) is True
    assert is_mysql_retryable_lock(other) is False


def test_runtime_schema_head_fits_postgres_alembic_column():
    """Alembic 默认版本列是 VARCHAR(32)，过长会让真实环境迁移整笔回滚。

    此处不再钉死具体 revision 字面量（钉死会让「正确新增迁移并同步常量」也失败）；
    常量与 migrations/versions/runtime/ 真实链头的一致性由 tests/test_schema_head_sync.py 断言。
    """
    from app.core.runtime_db import RUNTIME_SCHEMA_HEAD

    assert RUNTIME_SCHEMA_HEAD.startswith("runtime_")
    assert len(RUNTIME_SCHEMA_HEAD) <= 32


def test_running_instruction_keeps_exact_attachment_identity():
    formatted = model_driver._format_run_input(
        "继续修改这份文件",
        [{
            "filename": "同名报告.xlsx",
            "file_id": "file-new-upload",
            "sha256": "exact-sha",
        }],
    )

    assert "同名报告.xlsx（file_id=file-new-upload）" in formatted
    assert "不得按文件名改用文件区里的同名或近似文件" in formatted
    assert "不要推翻重来" in formatted


def test_steering_deliver_now_forbids_rebuild():
    formatted = model_driver._format_run_input("直接交付ppt", [])
    # Standard 只提供用户事实与约束；是否复用、核对、重做或发布由模型结合
    # ToolSpec/回执决定，不再由关键词生成“禁止重做”或固定工具处方。
    assert "禁止从零重做" not in formatted
    assert "模型自行判断" in formatted
