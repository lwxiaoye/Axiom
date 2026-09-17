import pytest

from app.services.agent_harness.contracts import (
    EffectScope,
    PlanStepSnapshot,
    PlanStepStatus,
    ToolSpec,
)
from app.services.agent_harness.plan_binding import (
    PlanBindingError,
    bind_prepared_tool_calls,
    enforce_linear_cursor,
    infer_requires,
    resolve_plan_step_id,
    step_is_ready,
)
from app.services.agent_harness.model_driver import _close_incomplete_plan_steps


def _spec(name: str, *tags: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        capability="web.search" if "investigate" in tags else "files.write",
        semantic_tags=frozenset(tags),
        effect_scope=EffectScope.SCRATCH,
        idempotent=False,
        resource_locks=("workspace",),
    )


def _steps(*rows: PlanStepSnapshot) -> tuple[PlanStepSnapshot, ...]:
    return rows


def test_resolve_binds_unique_in_progress():
    steps = _steps(
        PlanStepSnapshot(step_id="a", order=0, title="调研", status=PlanStepStatus.IN_PROGRESS),
        PlanStepSnapshot(step_id="b", order=1, title="撰写"),
    )
    assert resolve_plan_step_id(steps, _spec("search_web", "investigate")) == "a"


def test_asset_collection_step_allows_search_then_real_download():
    title = "确定视觉方向并收集威少比赛照片"
    steps = (PlanStepSnapshot(
        step_id="assets", order=0, title=title,
        status=PlanStepStatus.IN_PROGRESS, requires=infer_requires(title),
    ),)
    assert resolve_plan_step_id(steps, _spec("search_web", "investigate")) == "assets"
    assert resolve_plan_step_id(steps, _spec("fetch_asset", "download")) == "assets"


def test_resolve_binds_unique_ready_when_no_in_progress():
    steps = _steps(
        PlanStepSnapshot(step_id="a", order=0, title="调研"),
        PlanStepSnapshot(step_id="b", order=1, title="撰写"),
    )
    assert resolve_plan_step_id(steps, _spec("search_web", "investigate")) == "a"
    assert step_is_ready(steps[0], steps) is True
    assert step_is_ready(steps[1], steps) is False


def test_linear_cursor_pulls_back_later_in_progress():
    steps = _steps(
        PlanStepSnapshot(step_id="outline", order=0, title="确定文档结构"),
        PlanStepSnapshot(step_id="write", order=1, title="撰写正文", status=PlanStepStatus.IN_PROGRESS),
        PlanStepSnapshot(step_id="deliver", order=2, title="质检并交付"),
    )
    next_steps = enforce_linear_cursor(steps)
    assert [step.step_id for step in next_steps] == ["outline", "write", "deliver"]
    assert [step.status for step in next_steps] == [
        PlanStepStatus.IN_PROGRESS,
        PlanStepStatus.PENDING,
        PlanStepStatus.PENDING,
    ]


def test_linear_cursor_keeps_forked_depends_on_cursor():
    steps = _steps(
        PlanStepSnapshot(step_id="root", order=0, title="澄清", status=PlanStepStatus.COMPLETED),
        PlanStepSnapshot(step_id="a", order=1, title="主题一", depends_on=("root",)),
        PlanStepSnapshot(
            step_id="b",
            order=2,
            title="主题二",
            status=PlanStepStatus.IN_PROGRESS,
            depends_on=("root",),
        ),
    )
    next_steps = enforce_linear_cursor(steps)
    assert next_steps[2].status is PlanStepStatus.IN_PROGRESS
    assert next_steps[1].status is PlanStepStatus.PENDING


def test_resolve_rejects_when_plan_has_no_unique_cursor():
    steps = _steps(
        PlanStepSnapshot(
            step_id="a",
            order=0,
            title="主题一",
            depends_on=(),
        ),
        PlanStepSnapshot(
            step_id="b",
            order=1,
            title="主题二",
            depends_on=(),
        ),
    )
    # Linear order still makes only the first step ready.
    assert resolve_plan_step_id(steps, _spec("search_web", "investigate")) == "a"

    forked = _steps(
        PlanStepSnapshot(step_id="root", order=0, title="澄清", status=PlanStepStatus.COMPLETED),
        PlanStepSnapshot(step_id="a", order=1, title="主题一", depends_on=("root",)),
        PlanStepSnapshot(step_id="b", order=2, title="主题二", depends_on=("root",)),
    )
    with pytest.raises(PlanBindingError) as exc:
        resolve_plan_step_id(forked, _spec("search_web", "investigate"))
    assert exc.value.code == "plan_step_unbound"


def test_resolve_rejects_type_mismatch():
    steps = _steps(
        PlanStepSnapshot(
            step_id="research",
            order=0,
            title="调研竞品",
            status=PlanStepStatus.IN_PROGRESS,
            requires=("investigate",),
        ),
    )
    with pytest.raises(PlanBindingError) as exc:
        resolve_plan_step_id(steps, _spec("write_file", "productive"))
    assert exc.value.code == "plan_step_type_mismatch"
    assert "in_progress" in exc.value.user_message
    assert "bash" in exc.value.user_message
    assert "write_file" in exc.value.user_message


def test_bind_prepared_calls_sets_arg_error_instead_of_executing():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    search = _spec("search_web", "investigate")
    write = _spec("write_file", "productive")
    tool_map = {
        "search_web": _Tool(search),
        "write_file": _Tool(write),
        "update_plan": _Tool(_spec("update_plan")),
    }
    plan_rows = [
        {"key": "research", "title": "调研竞品", "status": "in_progress", "requires": ["investigate"]},
        {"key": "write", "title": "撰写正文", "status": "pending"},
    ]
    calls = [
        (0, {"id": "c1"}, "write_file", {"path": "a.md"}, ""),
        (1, {"id": "c2"}, "search_web", {"query": "竞品"}, ""),
    ]
    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)
    assert bound == {1: "research"}
    assert "不匹配" in str(next_calls[0][4])
    assert next_calls[1][4] == ""


def test_fetch_ppt_asset_binds_to_investigate_photo_step():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    search = _spec("search_web", "investigate")
    fetch = _spec("fetch_ppt_asset", "investigate", "download")
    bash = _spec("bash", "productive", "artifact_producer")
    tool_map = {
        "search_web": _Tool(search),
        "fetch_ppt_asset": _Tool(fetch),
        "bash": _Tool(bash),
        "update_plan": _Tool(_spec("update_plan")),
    }
    plan_rows = [
        {"key": "step-1", "title": "理清演示结构", "status": "completed"},
        {
            "key": "step-2",
            "title": "搜索并下载库里比赛照片素材",
            "status": "in_progress",
            "requires": ["investigate"],
        },
    ]
    calls = [
        (0, {"id": "c1"}, "bash", {"command": "ls"}, ""),
        (1, {"id": "c2"}, "fetch_ppt_asset", {"url": "图1"}, ""),
        (2, {"id": "c3"}, "search_web", {"query": "库里 比赛 照片"}, ""),
    ]
    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)
    assert bound == {1: "step-2", 2: "step-2"}
    assert "不匹配" in str(next_calls[0][4])
    assert next_calls[1][4] == ""
    assert next_calls[2][4] == ""


def test_publish_ppt_artifact_is_never_blocked_by_plan_cursor():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    publish = _spec("publish_ppt_artifact", "artifact_producer")
    tool_map = {
        "publish_ppt_artifact": _Tool(publish),
        "update_plan": _Tool(_spec("update_plan")),
    }
    plan_rows = [
        {
            "key": "research",
            "title": "调研竞品",
            "status": "in_progress",
            "requires": ["investigate"],
        },
        {"key": "write", "title": "撰写正文", "status": "pending"},
    ]
    calls = [(0, {"id": "c1"}, "publish_ppt_artifact", {"filename": "a.pptx"}, "")]
    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)
    assert bound == {}
    assert next_calls[0][4] == ""


def test_publish_ppt_artifact_binds_compatible_delivery_step_without_blocking():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    publish = _spec("publish_ppt_artifact", "productive", "artifact_producer")
    tool_map = {
        "publish_ppt_artifact": _Tool(publish),
        "update_plan": _Tool(_spec("update_plan")),
    }
    plan_rows = [
        {"key": "build", "title": "制作演示文稿", "status": "completed"},
        {
            "key": "deliver",
            "title": "发布到我的文件",
            "status": "in_progress",
            "acceptance": "发布成功，PPTX 可下载",
            "requires": ["productive"],
        },
    ]
    calls = [(0, {"id": "c1"}, "publish_ppt_artifact", {"filename": "a.pptx"}, "")]

    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)

    assert bound == {0: "deliver"}
    assert next_calls[0][4] == ""


def test_publish_ppt_artifact_does_not_bind_non_delivery_build_step():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    publish = _spec("publish_ppt_artifact", "productive", "artifact_producer")
    tool_map = {
        "publish_ppt_artifact": _Tool(publish),
        "update_plan": _Tool(_spec("update_plan")),
    }
    plan_rows = [{
        "key": "build",
        "title": "制作演示文稿",
        "status": "in_progress",
        "requires": ["productive"],
    }]
    calls = [(0, {"id": "c1"}, "publish_ppt_artifact", {"filename": "a.pptx"}, "")]

    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)

    assert bound == {}
    assert next_calls[0][4] == ""


def test_bind_fails_open_when_update_plan_is_not_on_the_tool_surface():
    class _Tool:
        def __init__(self, spec):
            self.spec = spec
            self.name = spec.name

    write = _spec("write_file", "productive")
    tool_map = {"write_file": _Tool(write)}
    plan_rows = [
        {
            "key": "research",
            "title": "调研竞品",
            "status": "in_progress",
            "requires": ["investigate"],
        },
    ]
    calls = [(0, {"id": "c1"}, "write_file", {"path": "a.md"}, "")]
    next_calls, bound = bind_prepared_tool_calls(calls, tool_map, plan_rows)
    assert bound == {}
    assert next_calls[0][4] == ""


def test_close_incomplete_plan_steps_invalidates_instead_of_completing():
    closed = _close_incomplete_plan_steps([
        {"title": "整理内容", "status": "completed"},
        {"title": "写入文件", "status": "running"},
        {"title": "保存并交付", "status": "pending"},
    ])
    assert [step["status"] for step in closed] == ["completed", "invalidated", "invalidated"]
    assert closed[1]["reason"] == "run_completed_without_step_evidence"
