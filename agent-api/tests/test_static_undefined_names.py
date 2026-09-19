"""静态守卫：app/ 下不能有 pyflakes 判定的「undefined name」。

2026-09-19 删掉工作流编排后，orchestrator.stream_chat 里残留了两处 `agents` 引用，
单测全绿、上线后每轮主对话 NameError。这类错误 pyflakes 一秒就能抓到，放进测试集。
没装 pyflakes（生产镜像）时跳过，不影响其它用例。
"""
import subprocess
import sys
from pathlib import Path

import pytest

pyflakes = pytest.importorskip("pyflakes")

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_no_undefined_names_in_app():
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(APP_DIR)],
        capture_output=True, text=True, check=False,
    )
    undefined = [line for line in proc.stdout.splitlines() if "undefined name" in line]
    assert not undefined, "\n".join(undefined)
