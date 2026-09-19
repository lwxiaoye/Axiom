"""v2.95/v2.96: no hard search quota; model decides when info is enough."""
from pathlib import Path


def _src() -> str:
    return Path(__file__).resolve().parents[1].joinpath("app/services/agent_harness/model_driver.py").read_text(encoding="utf-8")


def test_no_hard_search_quota_v296():
    src = _src()
    # 产物路径：禁止 1 次检索硬闸
    assert "product_block_extra_search" not in src
    assert "产物任务检索额度已用完" not in src
    assert "最多 1 次 search_web" not in src
    assert "最多 search_web 一次取证" not in src
    # 对话 lookup：禁止「检索已完成/不要继续换词重搜」硬闸与强制收口
    assert "chat_lookup_block_extra_search" not in src
    assert "net_chat_lookup_converge" not in src
    assert "chat_lookup_done" not in src
    assert "本轮对话检索已完成" not in src
    # 产物路径上按关键词/计数物理拦工具的三道闸也已整体退役（Harness 规范：拦截是
    # observation，不是隐藏工具；死循环由 tool_loop_circuit_breakers 的同形重复判定兜底）
    assert "product_block_after_deliverable" not in src
    assert "product_block_search_no_research" not in src
    assert "product_block_bash_verify_thrash" not in src


def test_search_thrash_floor_retired():
    """「检索 N 次仍不写盘就强制 write」的地板闸已退役：写不写盘由模型和 CompletionVerifier
    决定，同形空转只记指标（见 loop_guard 系列用例），不再有 8 次的硬地板。"""
    src = _src()
    assert "net_product_force_write" not in src
    assert "_PRODUCT_SEARCH_THRASH_N" not in src


def test_python_docx_path_still_mentioned():
    src = _src()
    assert "python-docx" in src
