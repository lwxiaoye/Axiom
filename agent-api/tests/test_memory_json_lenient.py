"""记忆抽取的模型输出经常带围栏/前言/附注：宽松解析只截取 JSON 片段，纯文本才抛。"""
import pytest

from app.services.memory.memory_service import _loads_json_lenient


def test_plain_and_fenced_json_parse():
    assert _loads_json_lenient('[{"a": 1}]') == [{"a": 1}]
    assert _loads_json_lenient('```json\n[1, 2]\n```') == [1, 2]
    assert _loads_json_lenient('```\n{"k": "v"}\n```') == {"k": "v"}


def test_prose_around_json_is_ignored():
    assert _loads_json_lenient('以下是抽取结果：\n[{"k": "v"}]\n（完）') == [{"k": "v"}]
    assert _loads_json_lenient('好的。{"k": [1, 2]} 请查收') == {"k": [1, 2]}


def test_non_json_raises_with_preview():
    with pytest.raises(ValueError) as error:
        _loads_json_lenient('抱歉，没有可记的内容。')
    assert '抱歉' in str(error.value)
    with pytest.raises(ValueError):
        _loads_json_lenient('')
