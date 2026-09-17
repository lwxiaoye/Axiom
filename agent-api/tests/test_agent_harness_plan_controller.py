from datetime import datetime

import pytest

from app.services.agent_harness.contracts import (
    EffectScope,
    ObservationStatus,
    PlanSnapshot,
    PlanStepSnapshot,
    PlanStepStatus,
    ToolObservation,
    ToolSpec,
)
from app.services.agent_harness.plan_controller import project_observation
from app.services.agent_harness.plan_controller import constrain_model_step_status
from app.services.agent_harness import model_driver
from app.services.chat.tools.base import MainTool
from app.services.tasks import plan_service


def _spec(
    name: str,
    *,
    capability: str,
    tags: tuple[str, ...],
    effect_scope: EffectScope = EffectScope.SCRATCH,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        capability=capability,
        semantic_tags=frozenset(tags),
        effect_scope=effect_scope,
        idempotent=False,
        resource_locks=("workspace",),
    )


def _observation(
    name: str,
    call_id: str,
    *,
    succeeded: bool = True,
    artifacts: list[dict] | None = None,
    receipts: list[dict] | None = None,
    plan_step_id: str | None = None,
) -> ToolObservation:
    return ToolObservation(
        call_id=call_id,
        tool_name=name,
        status=ObservationStatus.SUCCEEDED if succeeded else ObservationStatus.FAILED,
        summary="ok" if succeeded else "failed",
        artifact_refs=list(artifacts or []),
        receipts=list(receipts or []),
        error_code=None if succeeded else "quality_check_failed",
        plan_step_id=plan_step_id,
    )


def _plan(steps: tuple[PlanStepSnapshot, ...], version: int = 1) -> PlanSnapshot:
    return PlanSnapshot(
        run_id="run-plan-controller",
        goal_revision=0,
        plan_version=version,
        goal="制作并发布演示文稿",
        steps=steps,
        updated_at=datetime.utcnow(),
    )


def _next(plan: PlanSnapshot, spec: ToolSpec, observation: ToolObservation) -> PlanSnapshot:
    return plan.model_copy(update={
        "plan_version": plan.plan_version + 1,
        "steps": project_observation(plan, spec, observation),
    })


def _ppt_progress(*stages, checked=True):
    return [{"kind": "artifact_progress", "artifact_type": "pptx", "checked": checked, "stages": list(stages)}]


@pytest.mark.parametrize(
    ("title", "tags", "capability"),
    [
        ("设计页面结构与版式骨架", ("download",), "workspace.scratch"),
        ("导出 PPTX 并校验", ("productive", "artifact_producer"), "files.write"),
        ("确定视觉方向并收集威少比赛照片", ("investigate",), "web.search"),
    ],
)
def test_partial_tool_success_cannot_complete_an_unrelated_ppt_stage(title, tags, capability):
    step = PlanStepSnapshot(step_id="active", order=0, title=title, status=PlanStepStatus.IN_PROGRESS)
    tool = _spec("custom_tool", capability=capability, tags=tags)
    result = _next(_plan((step,)), tool, _observation(tool.name, "partial", plan_step_id="active"))
    assert result.steps[0].status is PlanStepStatus.IN_PROGRESS
    assert constrain_model_step_status(result.steps[0], PlanStepStatus.COMPLETED) is PlanStepStatus.IN_PROGRESS


def test_ppt_plan_tracks_design_all_pages_export_and_publish_separately():
    plan = _plan(tuple(
        PlanStepSnapshot(step_id=key, order=i, title=title, status=PlanStepStatus.IN_PROGRESS if i == 0 else PlanStepStatus.PENDING)
        for i, (key, title) in enumerate([
            ("design", "设计页面结构与版式骨架"),
            ("source", "按页写入内容并嵌入照片"),
            ("export", "导出 PPTX 并校验"),
            ("publish", "发布到我的文件"),
        ])
    ))
    write = _spec("custom_writer", capability="workspace.scratch", tags=("productive",))
    download = _spec("custom_fetch", capability="workspace.scratch", tags=("download",))
    for tool, stages in [(download, ()), (write, ())]:
        plan = _next(plan, tool, _observation(tool.name, "partial-design", plan_step_id="design", receipts=_ppt_progress(*stages)))
        assert plan.steps[0].status is PlanStepStatus.IN_PROGRESS

    plan = _next(plan, write, _observation(write.name, "manifest", plan_step_id="design", receipts=_ppt_progress("design")))
    assert plan.steps[0].status is PlanStepStatus.COMPLETED
    assert plan.steps[1].status is PlanStepStatus.IN_PROGRESS

    plan = _next(plan, write, _observation(write.name, "cover-only", plan_step_id="source", receipts=_ppt_progress("design")))
    assert plan.steps[1].status is PlanStepStatus.IN_PROGRESS
    plan = _next(plan, write, _observation(write.name, "all-pages", plan_step_id="source", receipts=_ppt_progress("design", "source")))
    assert plan.steps[1].status is PlanStepStatus.COMPLETED
    assert plan.steps[2].status is PlanStepStatus.IN_PROGRESS

    for call_id, succeeded, stages in [
        ("page-edit", True, ("design", "source")),
        ("export-failed", False, ("design", "source", "export", "validation")),
        ("exported-with-layout-errors", True, ("design", "source", "export")),
    ]:
        plan = _next(plan, write, _observation(write.name, call_id, plan_step_id="export", succeeded=succeeded, receipts=_ppt_progress(*stages)))
        assert plan.steps[2].status is PlanStepStatus.IN_PROGRESS
    plan = _next(plan, write, _observation(write.name, "real-export", plan_step_id="export", receipts=_ppt_progress("design", "source", "export", "validation")))
    assert plan.steps[2].status is PlanStepStatus.COMPLETED
    assert plan.steps[3].status is PlanStepStatus.IN_PROGRESS
    plan = _next(plan, write, _observation(write.name, "still-scratch", plan_step_id="publish", receipts=_ppt_progress("design", "source", "export", "validation")))
    assert plan.steps[3].status is PlanStepStatus.IN_PROGRESS


def test_unavailable_ppt_snapshot_never_falls_back_to_generic_write_success():
    step = PlanStepSnapshot(step_id="source", order=0, title="按页写入内容并嵌入照片", status=PlanStepStatus.IN_PROGRESS)
    tool = _spec("writer", capability="workspace.scratch", tags=("productive",))
    result = _next(_plan((step,)), tool, _observation(tool.name, "unverified", plan_step_id="source", receipts=_ppt_progress(checked=False)))
    assert result.steps[0].status is PlanStepStatus.IN_PROGRESS


def test_stale_ppt_export_receipt_cannot_complete_newer_source_revision():
    step = PlanStepSnapshot(step_id="export", order=0, title="导出 PPTX 并校验", status=PlanStepStatus.IN_PROGRESS, evidence_refs=[
        {"status": "succeeded", "milestone": "artifact_build", "receipts": _ppt_progress("design", "source", "export", "validation")},
        {"status": "succeeded", "milestone": "artifact_build", "receipts": _ppt_progress("design", "source")},
    ])
    assert constrain_model_step_status(step, PlanStepStatus.COMPLETED) is PlanStepStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_model_driver_auto_projects_receipt_without_blocking_on_update_plan(monkeypatch):
    current = _plan((
        PlanStepSnapshot(
            step_id="write",
            order=0,
            title="生成文档",
            status=PlanStepStatus.IN_PROGRESS,
        ),
    ))
    projected_rows = [{"key": "write", "title": "生成文档", "status": "completed"}]
    recorded: list[ToolObservation] = []

    async def get_snapshot(_run_id):
        return current

    async def record(_run_id, _spec_value, observation):
        recorded.append(observation)
        return current.model_copy(update={"plan_version": 2})

    async def get_projection(_run_id):
        return projected_rows

    from app.services.agent_harness import plan_controller, plan_store

    monkeypatch.setattr(plan_store, "get_plan_snapshot", get_snapshot)
    monkeypatch.setattr(plan_controller, "record_tool_observation", record)
    monkeypatch.setattr(plan_service, "get_current_plan", get_projection)

    async def execute(_args):
        return "ok"

    tool = MainTool(name="bash", description="bash", parameters={}, execute=execute)
    rows = await model_driver._advance_plan_from_tool_observation(
        gateway={"run_id": "run-plan-controller"},
        tool=tool,
        observation=_observation(
            "bash", "bash-1", plan_step_id="write",
        ).model_dump(mode="json"),
    )

    assert rows == projected_rows
    assert len(recorded) == 1
    assert recorded[0].plan_step_id == "write"


def test_tool_observations_advance_bound_plan_steps_only():
    plan = _plan((
        PlanStepSnapshot(step_id="research", order=0, title="调研", status=PlanStepStatus.COMPLETED),
        PlanStepSnapshot(step_id="assets", order=1, title="收集素材"),
        PlanStepSnapshot(step_id="build", order=2, title="制作演示文稿"),
        PlanStepSnapshot(step_id="verify", order=3, title="校验并修复"),
        PlanStepSnapshot(step_id="publish", order=4, title="发布文件"),
    ))
    download = _spec("fetch_asset", capability="asset.read", tags=("investigate", "download"))
    build = _spec("workspace_mutation", capability="files.write", tags=("productive", "revision_mutation"))
    publish = _spec(
        "artifact_publish",
        capability="artifact.export",
        tags=("productive", "revision_mutation", "artifact_producer"),
        effect_scope=EffectScope.USER_FILES,
    )

    unbound = _next(plan, download, _observation(download.name, "asset-unbound"))
    assert [step.status for step in unbound.steps] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.PENDING,
        PlanStepStatus.PENDING,
        PlanStepStatus.PENDING,
        PlanStepStatus.PENDING,
    ]

    plan = _next(plan, download, _observation(download.name, "asset-1", plan_step_id="assets"))
    assert [step.status for step in plan.steps] == [
        PlanStepStatus.COMPLETED,
        PlanStepStatus.COMPLETED,
        PlanStepStatus.IN_PROGRESS,
        PlanStepStatus.PENDING,
        PlanStepStatus.PENDING,
    ]

    plan = _next(plan, build, _observation(build.name, "build-1", plan_step_id="build"))
    assert plan.steps[2].status is PlanStepStatus.COMPLETED
    assert plan.steps[3].status is PlanStepStatus.IN_PROGRESS

    # A receipt bound to verify cannot complete publish.
    plan = _next(plan, build, _observation(build.name, "build-2", plan_step_id="verify"))
    assert plan.steps[3].status is PlanStepStatus.COMPLETED
    assert plan.steps[4].status is PlanStepStatus.IN_PROGRESS

    plan = _next(plan, publish, _observation(publish.name, "publish-failed", succeeded=False, plan_step_id="publish"))
    assert plan.steps[4].status is PlanStepStatus.IN_PROGRESS

    plan = _next(
        plan,
        publish,
        _observation(
            publish.name,
            "publish-ok",
            artifacts=[{"id": "file-1", "filename": "deck.pptx"}],
            plan_step_id="publish",
        ),
    )
    assert all(step.status is PlanStepStatus.COMPLETED for step in plan.steps)


def test_failed_observation_starts_work_but_never_claims_step_completion():
    plan = _plan((PlanStepSnapshot(step_id="one", order=0, title="执行"),))
    spec = _spec("external_action", capability="external.write", tags=("mutate",))

    projected = project_observation(
        plan,
        spec,
        _observation(spec.name, "failed-1", succeeded=False, plan_step_id="one"),
    )

    assert projected[0].status is PlanStepStatus.IN_PROGRESS
    assert projected[0].evidence_refs[0]["status"] == "failed"


def test_delivery_step_requires_persisted_file_receipt_for_completion():
    delivery = PlanStepSnapshot(
        step_id="deliver",
        order=0,
        title="校验并发布文件",
        detail="检查结构、渲染与发布产物",
    )
    assert constrain_model_step_status(
        delivery,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.IN_PROGRESS

    delivered = delivery.model_copy(update={
        "evidence_refs": [{
            "status": "succeeded",
            "milestone": "artifact_delivery",
            "artifact_refs": [{"id": "file-1", "filename": "deck.pptx"}],
        }],
    })
    assert constrain_model_step_status(
        delivered,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.COMPLETED


def test_bash_file_receipt_completes_word_delivery_step():
    delivery = PlanStepSnapshot(
        step_id="deliver",
        order=0,
        title="撰写Agent开发笔记并生成Word文档",
        acceptance_criteria=["Word文档已存入我的文件，可下载"],
        evidence_refs=[{
            "status": "succeeded",
            "milestone": "artifact_build",
            "artifact_refs": [{"file_id": "file-1", "filename": "笔记.docx"}],
        }],
    )
    assert constrain_model_step_status(
        delivery,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.COMPLETED


def test_ppt_publish_still_requires_export_milestone():
    publish = PlanStepSnapshot(
        step_id="publish",
        order=0,
        title="发布演示文稿",
        acceptance_criteria=["pptx 已写入我的文件"],
        evidence_refs=[{
            "status": "succeeded",
            "milestone": "artifact_build",
            "artifact_refs": [{"file_id": "file-1", "filename": "deck.pptx"}],
        }],
    )
    assert constrain_model_step_status(
        publish,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.IN_PROGRESS
    delivered = publish.model_copy(update={
        "evidence_refs": [{
            "status": "succeeded",
            "milestone": "artifact_delivery",
            "artifact_refs": [{"file_id": "file-1", "filename": "deck.pptx"}],
        }],
    })
    assert constrain_model_step_status(
        delivered,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.COMPLETED


def test_sibling_bash_receipt_allows_completing_word_delivery():
    outline = PlanStepSnapshot(
        step_id="step-1",
        order=0,
        title="规划文档大纲",
        status=PlanStepStatus.COMPLETED,
        evidence_refs=[{
            "status": "succeeded",
            "milestone": "artifact_build",
            "artifact_refs": [{
                "file_id": "568acbcd1cfa4beeb6829ad59e99c8ce",
                "filename": "Agent开发笔记.docx",
            }],
        }],
    )
    delivery = PlanStepSnapshot(
        step_id="step-2",
        order=1,
        title="撰写Agent开发笔记全文并生成Word文档交付",
        acceptance_criteria=["Word文档已存入我的文件，可下载"],
        status=PlanStepStatus.IN_PROGRESS,
    )
    assert constrain_model_step_status(
        delivery,
        PlanStepStatus.COMPLETED,
        plan_steps=(outline, delivery),
    ) is PlanStepStatus.COMPLETED
    assert constrain_model_step_status(
        delivery,
        PlanStepStatus.COMPLETED,
        plan_steps=(delivery,),
    ) is PlanStepStatus.IN_PROGRESS


def test_sibling_file_does_not_complete_unrelated_empty_step():
    outline = PlanStepSnapshot(
        step_id="step-1",
        order=0,
        title="规划文档大纲",
        status=PlanStepStatus.COMPLETED,
        evidence_refs=[{
            "status": "succeeded",
            "milestone": "artifact_build",
            "artifact_refs": [{"file_id": "file-1", "filename": "notes.docx"}],
        }],
    )
    understand = PlanStepSnapshot(
        step_id="understand",
        order=1,
        title="理解需求",
    )
    assert constrain_model_step_status(
        understand,
        PlanStepStatus.COMPLETED,
        plan_steps=(outline, understand),
    ) is PlanStepStatus.IN_PROGRESS


def test_completion_gap_ack_names_demoted_steps_and_existing_file():
    from app.services.agent_harness.plan_controller import completion_gap_ack

    ack = completion_gap_ack(
        [
            {"key": "step-1", "title": "规划大纲", "status": "completed"},
            {"key": "step-2", "title": "撰写并交付Word", "status": "completed"},
        ],
        [
            {"key": "step-1", "title": "规划大纲", "status": "completed", "evidence": [{
                "status": "succeeded",
                "milestone": "artifact_build",
                "artifact_refs": [{"file_id": "f1", "filename": "笔记.docx"}],
            }]},
            {"key": "step-2", "title": "撰写并交付Word", "status": "running"},
        ],
    )
    assert "撰写并交付Word" in ack
    assert "空口 completed 无效" in ack
    assert "笔记.docx" in ack
    assert "不要再反复 update_plan" in ack
    assert completion_gap_ack(
        [{"key": "a", "title": "调研", "status": "completed"}],
        [{"key": "a", "title": "调研", "status": "completed"}],
    ) == "计划已更新。"
    empty_ack = completion_gap_ack(
        [{"key": "b", "title": "撰写并交付Word", "status": "completed"}],
        [{"key": "b", "title": "撰写并交付Word", "status": "in_progress"}],
    )
    assert "bash" in empty_ack
    assert "write_file" in empty_ack
    assert "写入我的文件的工具" not in empty_ack


def test_investigation_step_with_acceptance_needs_two_successes():
    step = PlanStepSnapshot(
        step_id="research",
        order=0,
        title="调研竞品",
        detail="对照主流产品",
        acceptance_criteria=["至少两份可核验来源"],
        evidence_refs=[{
            "status": "succeeded",
            "milestone": "investigation",
            "tool_name": "search_web",
        }],
    )
    assert constrain_model_step_status(
        step,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.IN_PROGRESS

    two = step.model_copy(update={
        "evidence_refs": [
            *step.evidence_refs,
            {"status": "succeeded", "milestone": "investigation", "tool_name": "browser_fetch"},
        ],
    })
    assert constrain_model_step_status(
        two,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.COMPLETED


def test_research_mode_investigation_needs_two_source_urls():
    step = PlanStepSnapshot(
        step_id="research",
        order=0,
        title="调研竞品",
        evidence_refs=[
            {"status": "succeeded", "milestone": "investigation", "tool_name": "search_web"},
            {"status": "succeeded", "milestone": "investigation", "tool_name": "search_web"},
        ],
    )
    assert constrain_model_step_status(
        step,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.COMPLETED
    assert constrain_model_step_status(
        step,
        PlanStepStatus.COMPLETED,
        research_mode=True,
    ) is PlanStepStatus.IN_PROGRESS

    with_urls = step.model_copy(update={
        "evidence_refs": [
            {
                "status": "succeeded",
                "milestone": "investigation",
                "tool_name": "search_web",
                "urls": ["https://a.example.com/one"],
            },
            {
                "status": "succeeded",
                "milestone": "investigation",
                "tool_name": "deep_read",
                "urls": ["https://b.example.com/two"],
            },
        ],
    })
    assert constrain_model_step_status(
        with_urls,
        PlanStepStatus.COMPLETED,
        research_mode=True,
    ) is PlanStepStatus.COMPLETED


def test_plain_step_without_acceptance_cannot_complete_without_evidence():
    step = PlanStepSnapshot(
        step_id="understand",
        order=0,
        title="理解需求",
    )
    assert constrain_model_step_status(
        step,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.IN_PROGRESS
    conversational = PlanStepSnapshot(
        step_id="reply",
        order=1,
        title="对话答复",
    )
    assert constrain_model_step_status(
        conversational,
        PlanStepStatus.COMPLETED,
    ) is PlanStepStatus.IN_PROGRESS


def test_bash_file_on_word_delivery_step_completes():
    plan = _plan((
        PlanStepSnapshot(
            step_id="deliver",
            order=0,
            title="撰写笔记并生成Word文档",
            detail="Word文档已存入我的文件",
            status=PlanStepStatus.IN_PROGRESS,
            acceptance_criteria=["Word文档已存入我的文件，可下载"],
        ),
    ))
    bash = _spec(
        "bash",
        capability="files.write",
        tags=("productive", "artifact_producer"),
        effect_scope=EffectScope.USER_FILES,
    )
    projected = project_observation(
        plan,
        bash,
        _observation(
            bash.name,
            "bash-1",
            artifacts=[{"file_id": "docx-1", "filename": "笔记.docx"}],
            plan_step_id="deliver",
        ),
    )
    assert projected[0].status is PlanStepStatus.COMPLETED
    assert projected[0].evidence_refs[-1]["milestone"] == "artifact_build"
    plan = _plan((
        PlanStepSnapshot(
            step_id="build",
            order=0,
            title="写入工程与页面文件",
            status=PlanStepStatus.COMPLETED,
        ),
        PlanStepSnapshot(
            step_id="qa",
            order=1,
            title="导出并检查成品",
            status=PlanStepStatus.IN_PROGRESS,
        ),
        PlanStepSnapshot(
            step_id="deliver",
            order=2,
            title="交付最终文件",
        ),
    ))
    publish = _spec(
        "publish_ppt_artifact",
        capability="artifact.export",
        tags=("productive", "artifact_producer"),
        effect_scope=EffectScope.USER_FILES,
    )

    projected = project_observation(
        plan,
        publish,
        _observation(
            publish.name,
            "publish-ok",
            artifacts=[
                {"file_id": "pptx-1", "filename": "deck.pptx"},
            ],
            plan_step_id="deliver",
        ),
    )

    assert projected[1].status is PlanStepStatus.IN_PROGRESS
    assert projected[2].status is PlanStepStatus.COMPLETED
    assert projected[2].evidence_refs[-1]["milestone"] == "artifact_delivery"


def test_later_milestone_does_not_close_unbound_earlier_step():
    plan = _plan((
        PlanStepSnapshot(step_id="understand", order=0, title="理解需求"),
        PlanStepSnapshot(
            step_id="research",
            order=1,
            title="调研竞品",
            status=PlanStepStatus.IN_PROGRESS,
        ),
        PlanStepSnapshot(step_id="reply", order=2, title="整理结论并回复"),
    ))
    search = _spec("search_web", capability="web.search", tags=("investigate",))
    projected = project_observation(
        plan,
        search,
        _observation(search.name, "search-1", plan_step_id="research"),
    )
    assert projected[0].status is PlanStepStatus.IN_PROGRESS
    assert projected[1].status is PlanStepStatus.COMPLETED
    assert projected[2].status is PlanStepStatus.PENDING


def test_model_may_skip_without_evidence():
    step = PlanStepSnapshot(step_id="old", order=0, title="理解需求")
    assert constrain_model_step_status(step, PlanStepStatus.SKIPPED) is PlanStepStatus.SKIPPED
    assert constrain_model_step_status(step, PlanStepStatus.IN_PROGRESS) is PlanStepStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_upsert_plan_normalizes_multiple_in_progress_steps(monkeypatch):
    async def _run_state(_run_id):
        return {"state": {"plan_version": 0, "goal_revision": 0}}

    async def _no_existing(_run_id):
        return None

    async def _goal(_run_id, _fallback=""):
        return "完成演示文稿"

    async def _update(run_id, **kwargs):
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=kwargs["expected_goal_revision"],
            plan_version=kwargs["expected_plan_version"] + 1,
            goal=kwargs["goal"],
            steps=kwargs["steps"],
            updated_at=datetime.utcnow(),
        )

    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _no_existing)
    monkeypatch.setattr(plan_service, "_goal_for_run", _goal)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    projected = await plan_service.upsert_plan("run-multi-active", [
        {"key": "build", "title": "制作", "status": "in_progress"},
        {"key": "deliver", "title": "交付", "status": "in_progress"},
    ])

    assert [step["status"] for step in projected] == ["running", "pending"]


@pytest.mark.asyncio
async def test_upsert_plan_preserves_model_selected_cursor(monkeypatch):
    async def _run_state(_run_id):
        return {"state": {"plan_version": 0, "goal_revision": 0}}

    async def _no_existing(_run_id):
        return None

    async def _goal(_run_id, _fallback=""):
        return "写一份公司介绍"

    async def _update(run_id, **kwargs):
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=kwargs["expected_goal_revision"],
            plan_version=kwargs["expected_plan_version"] + 1,
            goal=kwargs["goal"],
            steps=kwargs["steps"],
            updated_at=datetime.utcnow(),
        )

    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _no_existing)
    monkeypatch.setattr(plan_service, "_goal_for_run", _goal)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    projected = await plan_service.upsert_plan("run-linear-cursor", [
        {"key": "outline", "title": "确定文档结构", "status": "pending"},
        {"key": "write", "title": "撰写正文", "status": "in_progress"},
        {"key": "deliver", "title": "质检并交付", "status": "pending"},
    ])

    assert [step["title"] for step in projected] == ["确定文档结构", "撰写正文", "质检并交付"]
    assert [step["status"] for step in projected] == ["pending", "running", "pending"]


@pytest.mark.asyncio
async def test_upsert_plan_keeps_receipt_backed_completion_when_model_is_stale(monkeypatch):
    existing = PlanSnapshot(
        run_id="run-keep-done",
        goal_revision=0,
        plan_version=2,
        goal="制作并发布演示文稿",
        steps=(
            PlanStepSnapshot(
                step_id="research",
                order=0,
                title="调研竞品",
                status=PlanStepStatus.COMPLETED,
                evidence_refs=[{
                    "status": "succeeded",
                    "milestone": "investigation",
                    "tool_name": "search_web",
                }],
            ),
            PlanStepSnapshot(
                step_id="write",
                order=1,
                title="撰写正文",
                status=PlanStepStatus.IN_PROGRESS,
            ),
        ),
        updated_at=datetime.utcnow(),
    )

    async def _run_state(_run_id):
        return {"state": {"plan_version": 2, "goal_revision": 0}}

    async def _existing(_run_id):
        return existing

    async def _goal(_run_id, _fallback=""):
        return existing.goal

    captured = {}

    async def _update(run_id, **kwargs):
        captured["steps"] = kwargs["steps"]
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=kwargs["expected_goal_revision"],
            plan_version=kwargs["expected_plan_version"] + 1,
            goal=kwargs["goal"],
            steps=kwargs["steps"],
            updated_at=datetime.utcnow(),
        )

    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _existing)
    monkeypatch.setattr(plan_service, "_goal_for_run", _goal)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    projected = await plan_service.upsert_plan("run-keep-done", [
        {"key": "research", "title": "调研竞品", "status": "pending"},
        {"key": "write", "title": "撰写正文", "status": "in_progress"},
    ])
    assert [step["status"] for step in projected] == ["completed", "running"]
    assert captured["steps"][0].status is PlanStepStatus.COMPLETED
    assert captured["steps"][0].evidence_refs


@pytest.mark.asyncio
async def test_upsert_plan_drops_conversational_empty_steps(monkeypatch):
    captured = {}

    async def _run_state(_run_id):
        return {"state": {"plan_version": 0, "goal_revision": 0}}

    async def _no_existing(_run_id):
        return None

    async def _goal(_run_id, _fallback=""):
        return "调研竞品"

    async def _update(run_id, **kwargs):
        captured["steps"] = kwargs["steps"]
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=kwargs["expected_goal_revision"],
            plan_version=kwargs["expected_plan_version"] + 1,
            goal=kwargs["goal"],
            steps=kwargs["steps"],
            updated_at=datetime.utcnow(),
        )

    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _no_existing)
    monkeypatch.setattr(plan_service, "_goal_for_run", _goal)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    projected = await plan_service.upsert_plan("run-drop-preamble", [
        {"key": "understand", "title": "理解需求", "status": "in_progress"},
        {"key": "research", "title": "调研竞品", "status": "pending"},
    ])
    assert [step["title"] for step in projected] == ["调研竞品"]
    assert captured["steps"][0].step_id == "research"


@pytest.mark.asyncio
async def test_bare_resume_inherits_authoritative_plan_and_evidence(monkeypatch):
    source = PlanSnapshot(
        run_id="source-run",
        goal_revision=3,
        plan_version=8,
        goal="制作并发布演示文稿",
        steps=(PlanStepSnapshot(
            step_id="publish",
            order=0,
            title="发布文件",
            status=PlanStepStatus.COMPLETED,
            evidence_refs=[{
                "status": "succeeded",
                "artifact_refs": [{"id": "file-1", "filename": "deck.pptx"}],
            }],
        ),),
        updated_at=datetime.utcnow(),
    )

    async def _no_target(run_id):
        return None

    async def _latest(thread_id, *, before_run_id, require_complete=False):
        assert thread_id == "thread-1"
        assert before_run_id == "target-run"
        assert require_complete is True
        return source

    async def _run_state(run_id):
        return {"state": {"plan_version": 0, "goal_revision": 0}}

    async def _update(run_id, **kwargs):
        assert run_id == "target-run"
        assert kwargs["goal"] == source.goal
        assert kwargs["steps"][0].evidence_refs == source.steps[0].evidence_refs
        return source.model_copy(update={
            "run_id": run_id,
            "goal_revision": 0,
            "plan_version": 1,
        })

    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _no_target)
    monkeypatch.setattr(plan_service.plan_store, "get_latest_prior_plan_snapshot", _latest)
    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    inherited = await plan_service.inherit_latest_thread_plan(
        "target-run",
        "thread-1",
        require_complete=True,
    )
    assert inherited == [{
        "key": "publish",
        "title": "发布文件",
        "detail": None,
        "status": "completed",
        "required": True,
        "verified": True,
        "evidence": source.steps[0].evidence_refs,
        "acceptance_criteria": [],
        "reason": None,
        "goal_revision": 0,
        "plan_version": 1,
    }]


@pytest.mark.asyncio
async def test_upsert_plan_completes_word_delivery_from_sibling_receipt(monkeypatch):
    existing = PlanSnapshot(
        run_id="run-word-loop",
        goal_revision=0,
        plan_version=3,
        goal="随便写一份agent开发的笔记，给我word文档",
        steps=(
            PlanStepSnapshot(
                step_id="step-1",
                order=0,
                title="规划Agent开发笔记大纲",
                status=PlanStepStatus.COMPLETED,
                evidence_refs=[{
                    "status": "succeeded",
                    "milestone": "artifact_build",
                    "artifact_refs": [{
                        "file_id": "568acbcd1cfa4beeb6829ad59e99c8ce",
                        "filename": "Agent开发笔记.docx",
                    }],
                }],
            ),
            PlanStepSnapshot(
                step_id="step-2",
                order=1,
                title="撰写Agent开发笔记全文并生成Word文档交付",
                status=PlanStepStatus.IN_PROGRESS,
                acceptance_criteria=["Word文档已存入我的文件，可下载"],
            ),
        ),
        updated_at=datetime.utcnow(),
    )
    captured = {}

    async def _run_state(_run_id):
        return {"state": {"plan_version": 3, "goal_revision": 0}}

    async def _existing(_run_id):
        return existing

    async def _goal(_run_id, _fallback=""):
        return existing.goal

    async def _update(run_id, **kwargs):
        captured["steps"] = kwargs["steps"]
        return PlanSnapshot(
            run_id=run_id,
            goal_revision=kwargs["expected_goal_revision"],
            plan_version=kwargs["expected_plan_version"] + 1,
            goal=kwargs["goal"],
            steps=kwargs["steps"],
            updated_at=datetime.utcnow(),
        )

    monkeypatch.setattr(plan_service.run_store, "get_run_state", _run_state)
    monkeypatch.setattr(plan_service.plan_store, "get_plan_snapshot", _existing)
    monkeypatch.setattr(plan_service, "_goal_for_run", _goal)
    monkeypatch.setattr(plan_service.plan_store, "update_plan", _update)

    projected = await plan_service.upsert_plan("run-word-loop", [
        {
            "key": "step-1",
            "title": "规划Agent开发笔记大纲",
            "status": "completed",
        },
        {
            "key": "step-2",
            "title": "撰写Agent开发笔记全文并生成Word文档交付",
            "status": "completed",
            "acceptance": "Word文档已存入我的文件，可下载",
        },
    ])
    assert [step["status"] for step in projected] == ["completed", "completed"]
    assert captured["steps"][1].status is PlanStepStatus.COMPLETED
