# -*- coding: utf-8 -*-
"""循环护栏之间的交互缺陷回归（2026-07-29 深扫 P0 ×2）。

两条都由当天新加的护栏引入，单看各自逻辑都对，串起来才坏：

① 软提醒破坏门禁末尾锚定：单工具广撒网提醒原先直接追加在工具回执尾部，而
   artifact_validity_gate 标记是**末尾锚定**（base.py 的 _GATE_RE 以 \\]\\s*$ 收尾）。
   在它后面加一个字，_pop_validity_gate 就解析成 None → 质检结论整条丢弃
   （failed 不返工 / passed 不清 pending），且带防伪 nonce 的原始标记随回执流进
   模型上下文与 SSE。nonce 是进程级常量，泄漏一次该进程内任何 Run 都能伪造
   [artifact_validity_gate=passed:<nonce>] 让坏产物过检。

② arg_error 计入物理停用：参数残缺的处方是"重发并给出完整参数"，不是"工具坏了"。
   arg_error 文案是固定串、归一指纹逐字相同，一条被截断的模型消息（含 5 个残缺
   调用）即可把 bash（唯一执行器）本 Run 物理停用，且无解锁路径。
"""
from app.services.chat.tools.base import _pop_validity_gate, _with_validity_gate
from app.services.agent_harness.model_driver import LoopState, _error_fingerprint


def test_gate_parses_when_nudge_appended_after_pop():
    """正确顺序：先剪门禁、再追加提醒。"""
    raw = _with_validity_gate("PPT 已生成 3 页", "failed")
    text, status = _pop_validity_gate(raw)
    assert status == "failed", "阳性对照：不加任何东西时门禁必须解析出来"
    final = text + "\n[提示：本轮已调用 bash 15 次。]"
    assert "artifact_validity_gate" not in final, "剪切后再追加，nonce 不会泄漏"


def test_appending_before_pop_breaks_gate_and_leaks_nonce():
    """反向断言：锁住"为什么必须先剪后加"。这是 2026-07-29 的真实缺陷形态。"""
    raw = _with_validity_gate("PPT 已生成 3 页", "failed")
    broken = raw + "\n[提示：本轮已调用 bash 15 次。]"
    text, status = _pop_validity_gate(broken)
    assert status is None, "顺序反了会让门禁静默失效——这条断言在提醒实现回退到旧顺序时会失败"
    assert "artifact_validity_gate" in text, "且防伪 nonce 会留在正文里流向模型/SSE"


def test_arg_error_fingerprints_are_identical_across_calls():
    """arg_error 文案固定 → 指纹恒等：这是它能在 5 次内顶满停用阈值的根因。"""
    msg = ("（本次调用未执行：arguments 不是合法 JSON。请重新发起这次调用并给出完整合法的"
           "参数；如果参数本身很长（长代码、长文本），把它拆小分多次写入。）")
    assert _error_fingerprint("bash", msg) == _error_fingerprint("bash", msg)
    assert LoopState.SAME_ERROR_HARD_MAX <= 5, (
        "阈值越小，arg_error 顶满停用越快——本测试的前提是它确实低到一条截断消息就能顶满"
    )


def test_arg_error_branch_precedes_disable_in_source():
    """结构断言：arg_error 分支必须排在物理停用分支之前（源码顺序即行为）。

    用源码顺序而非行为断言，是因为触发真实停用需要跑完整工具循环（要真模型）。
    这条能抓住"有人把 if arg_error 挪到 elif 后面/删掉"的回退。
    """
    import inspect

    from app.services.agent_harness import model_driver
    src = inspect.getsource(model_driver.drive_model)
    # 参数残缺只生成模型可见的结构化软回执；本轮不再存在“物理停用工具”分支，
    # 因而不会因为一条截断调用永久锁死唯一执行器。
    assert "bad_tool_args" in src
    assert "tool_guard_rejected" in src
    assert "st.disabled_tools.setdefault" not in src
