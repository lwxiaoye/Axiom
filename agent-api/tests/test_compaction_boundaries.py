# -*- coding: utf-8 -*-
"""compaction 三条边界缺陷回归（2026-07-29 深扫 P0×3）。

① **已作废分支复活**：delete_for_thread（编辑重发截断）不持锁，在途压缩正等 LLM 返回时
   摘要行被删；LLM 返回后 get() 拿到 None 于是新插一行，把已 archived 分支的内容以摘要
   散文永久固化（原文被排除、结论却留下），全程无异常无日志。修法=世代号，delete 递增、
   压缩写回前比对，不一致即丢弃结果（不持锁，避免卡在用户路径上等 600s 后台压缩）。
② **消息无声蒸发**：transcript 原先整体 [:24000] 切尾，而 covered 仍推进到 to_compact[-1]
   ——被切掉的消息既没进摘要（LLM 没看到）又被 id ≤ covered 永久排除在原文外。
   修法=逐条累加，装不下不收，covered 只推进到真正进 prompt 的那条。
③ **残缺摘要被当完整基线固化**：8000 硬截断按文档顺序切，专杀排在最后的
   【进行中/待办】【关键决定】；下一轮 update_rule 又要求"必须保留旧摘要"。
   修法=上限放宽 + 截断显式标记 + 下一轮读到标记时告知模型基线不完整。
"""
import pytest

from app.services.memory import context_service as cs


def _code_only(src: str) -> str:
    """取**真代码体**做文本判据：剥 `#` 注释**和 docstring**，用 AST 而不是逐行切。

    2026-07-29 升级(调度员在核另一处改动时当场踩了两次同款):
    ①`grep -c` 数到的可能全在注释里;②"去掉以 `#` 开头的行"**剥不掉多行 docstring**
    ——docstring 正文既不以 `#` 开头也不含三引号,整段活下来,而它里面往往逐字引用了
    被禁的符号名。文本搜索分不开代码/注释/docstring/字符串字面量,这四样在源码里长得
    一模一样,所以只能走 AST。
    带两条自证:剥完必须还有函数签名(防静默空串),且必须真的短了(防剥了个空)。
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(src))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body.pop(0)  # 删 docstring 节点
    out = ast.unparse(tree)  # unparse 天然不含 # 注释
    assert "def " in out, "剥完连函数签名都没了，说明这个辅助自己坏了"
    assert len(out) < len(src), "剥完没变短——docstring/注释一个都没去掉，判据此刻是瞎的"
    return out


# ---------- ① 世代号 ----------

def test_delete_bumps_epoch_so_inflight_compaction_can_self_invalidate():
    tid = "th-epoch-1"
    cs._summary_epochs.pop(tid, None)
    start = cs._epoch_of(tid)
    cs._bump_epoch(tid)
    assert cs._epoch_of(tid) != start, "编辑重发必须让在途压缩的世代失效"


@pytest.mark.asyncio
async def test_delete_for_thread_bumps_epoch_even_without_runtime_db():
    """Runtime 域库未配置时 delete 会提前 return——世代号必须在那之前递增。"""
    tid = "th-epoch-2"
    cs._summary_epochs.pop(tid, None)
    before = cs._epoch_of(tid)
    await cs.delete_for_thread(tid)
    assert cs._epoch_of(tid) > before, (
        "递增必须早于任何 return/删除动作，否则存在「压缩在删除后、递增前完成」的窄窗"
    )


# ---------- ② transcript 逐条累加 ----------

def test_transcript_cap_is_per_message_not_whole_slice():
    """锁住常量与算法形态：逐条累加 + covered 截到 _fitted。

    真实压缩要跑 LLM，这里用与实现同构的累加逻辑证明"装不下的不收"，
    并断言实现里确实存在 to_compact 截断这一步（防回退成整体切片）。
    """
    import inspect
    src = _code_only(inspect.getsource(cs.maybe_compact))
    assert "_TRANSCRIPT_CHAR_CAP" in src and "to_compact = to_compact[:_fitted]" in src, (
        "transcript 必须逐条累加并把 covered 截到真正进 prompt 的那条"
    )
    assert "[:24000]" not in src, "整体切片会让消息无声蒸发（注释里的历史说明不算）"

    # 同构演算：cap=100 时只有前两条能装下，第三条必须留给下一片
    cap = 100
    lines = ["用户：" + "字" * 40, "助手：" + "字" * 40, "用户：" + "字" * 40]
    used, fitted = 0, 0
    for line in lines:
        if fitted and used + len(line) + 1 > cap:
            break
        used += len(line) + 1
        fitted += 1
    assert fitted == 2, "第三条装不下就不该被算进已覆盖"


# ---------- ③ 截断标记 ----------

def test_legacy_incomplete_summary_is_treated_as_incomplete():
    from app.services.agent_harness.conversation_compact import SUMMARIZATION_PROMPT
    assert "旧摘要标注不完整时，不得猜补缺失事实" in SUMMARIZATION_PROMPT


def test_soft_char_target_no_longer_contradicts_preserve_rule():
    """800 字与"必须保留旧结论"互斥；软目标必须放宽且给出取舍优先级。"""
    from app.services.agent_harness.conversation_compact import SUMMARIZATION_PROMPT
    src = SUMMARIZATION_PROMPT
    assert "800 字以内" not in src, "旧的互斥口径必须消失（注释里的历史说明不算）"
    assert "优先完整保留【关键决定】【进行中/待办】" in src, "装不下时的取舍必须写明"
