"""独立交付查错冒烟。

运行（容器内）：docker exec agent-api python scripts/visual_review_smoke.py
覆盖：客观错误解析、逐项需求验收、不可用时 fail-open、代表性页面渲染、
内容提纲抽取，以及真实独立多模态审查链路。
"""
import asyncio
import sys

sys.path.insert(0, "/app")

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
results = []


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"  [{PASS if ok else FAIL}] {name}" + (f" — {detail}" if detail and not ok else ""))


async def main():
    from app.core.config import settings
    from app.services.sandbox import visual_review as vr
    from app.services.sandbox.factory import create_sandbox

    # 1 verdict 容错解析
    good = {
        "passed": True,
        "requirement_checks": [{"requirement": "包含标题", "status": "met", "evidence": "第1页"}],
        "issues": [], "next_actions": [],
    }
    import json
    v = vr._parse_verdict("```json\n" + json.dumps(good, ensure_ascii=False) + "\n```")
    check("解析围栏 JSON 且不产生分数", v and v["passed"] and not any(k.endswith("_score") for k in v))
    bad = dict(good)
    bad.update({
        "passed": False,
        "requirement_checks": [{"requirement": "标题完整", "status": "partial", "fix": "补全标题"}],
        "issues": [{"severity": "error", "category": "layout", "message": "标题被截断", "fix": "缩小字号"}],
    })
    v = vr._parse_verdict("前缀文字 " + json.dumps(bad, ensure_ascii=False) + " 后缀")
    check("解析带前后缀 + issues", v and not v["passed"] and v["issues"][0]["message"] == "标题被截断")
    check("垃圾输入 → None", vr._parse_verdict("看起来不错！") is None)
    check("代表性抽样包含首尾", vr._sample_pages(20, 6)[0] == 1 and vr._sample_pages(20, 6)[-1] == 20)

    # 2 结论并入 review 的四种形态
    e = {"name": "a.docx", "review": {"status": "passed", "summary": "3 页"}}
    vr._merge_into_review(e, good)
    check("查错通过 → summary 不含评分", e["review"]["status"] == "passed" and "未发现明确错误" in e["review"]["summary"])
    e = {"name": "b.docx", "review": {"status": "passed", "summary": ""}}
    warning = dict(good)
    warning["issues"] = [{"severity": "warn", "category": "polish", "message": "第2页略拥挤", "fix": "增加留白"}]
    vr._merge_into_review(e, warning)
    check("通过带提醒", "提醒" in e["review"]["summary"])
    e = {"name": "c.pptx", "review": {"status": "passed", "summary": "5 页"}}
    vr._merge_into_review(e, bad)
    check("视觉不通过 → 升级 failed", e["review"]["status"] == "failed" and "截断" in e["review"]["summary"])
    e = {"name": "d.pdf", "review": {"status": "passed", "summary": "2 页"}}
    vr._merge_into_review(e, None, required=True)
    check(
        # 2026-07-15 拍板：检查服务异常不得触发返工/拦截——fail-open 放行 + 如实记录
        "不可用 → 不拦截、如实记录",
        e["review"]["status"] == "passed" and "不影响交付" in e["review"]["summary"],
    )

    # 3 渲染管线真跑：docx → PDF → 页图（需 local 沙箱 + 新镜像 pdftoppm）
    sandbox = create_sandbox("local", {
        "image": settings.SKILL_SANDBOX_LOCAL_IMAGE,
        "network": settings.SKILL_SANDBOX_LOCAL_NETWORK,
        "memory": settings.SKILL_SANDBOX_LOCAL_MEMORY,
        "cpus": settings.SKILL_SANDBOX_LOCAL_CPUS,
    })
    try:
        await sandbox.create()
        from app.services.sandbox.base import ExecuteOptions
        gen = (
            "mkdir -p /workspace/outputs && python -c \""
            "import docx; d=docx.Document(); d.add_heading('视觉审查冒烟', 0); "
            "[d.add_paragraph(f'第 {i} 段正文内容') for i in range(30)]; "
            "d.save('/workspace/outputs/样例.docx')\""
        )
        res = await sandbox.execute(gen, ExecuteOptions(timeout_ms=30000))
        check("样例 docx 生成", res.ok, res.stderr[:200])
        rendered = await vr._render_pages(sandbox, "样例.docx")
        pages = rendered["pages"]
        check("渲染出代表性页图", bool(pages) and pages[0]["data"][:8] == b"\x89PNG\r\n\x1a\n", str(rendered))
        outline = await vr._extract_outline(sandbox, "样例.docx")
        check("抽取全文件内容提纲", "视觉审查冒烟" in (outline.get("outline") or ""), str(outline))

        # 4 独立多模态审查整链路（端点异常时应 fail-closed）
        outputs = [{"name": "样例.docx", "size": 1, "review": {"status": "passed", "summary": "打开正常"}}]
        overall = await vr.visual_review_outputs(
            sandbox, outputs,
            task_brief="制作一份标题为‘视觉审查冒烟’、包含 30 段正文、层级清晰且简洁美观的 Word 文档",
        )
        rv = outputs[0]["review"]
        ok_pass = overall and overall["status"] in ("passed", "warning", "failed")
        check("visual_review_outputs 整链路可跑", bool(ok_pass), str(overall))
        check(
            # 分级交付线（2026-07-15）：passed=高标准通过；warning=基本可用照常交付并
            # 如实标注；failed=明确未放行。三档都必须带可读结论。
            "结论落在 review（通过/基本可用/明确未放行）",
            (
                (rv["status"] in ("passed", "failed") and "质量审查" in (rv.get("summary") or ""))
                or (rv["status"] == "warning" and "基本可用" in (rv.get("summary") or ""))
            ),
            str(rv),
        )
        print(f"  [info] 视觉链路实际结论：{rv.get('summary')}")
    finally:
        await sandbox.delete()

    from app.core.database import engine
    await engine.dispose()  # 收口连接池：避免事件循环关闭后 aiomysql 的 __del__ 噪音
    total, ok = len(results), sum(results)
    print(f"\n{ok}/{total} passed")
    sys.exit(0 if ok == total else 1)


asyncio.run(main())
