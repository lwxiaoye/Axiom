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
    # 仍保留有意义的闸
    assert "product_block_after_deliverable" in src
    assert "product_block_search_no_research" in src
    assert "product_block_bash_verify_thrash" in src


def test_search_thrash_floor_only_extreme():
    """仅极多检索仍不写盘时 net_product_force_write（死循环地板，非业务配额）。"""
    src = _src()
    assert "net_product_force_write" in src
    assert "_PRODUCT_SEARCH_THRASH_N = 8" in src


def test_python_docx_path_still_mentioned():
    src = _src()
    assert "python-docx" in src
