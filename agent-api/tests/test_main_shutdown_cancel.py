# -*- coding: utf-8 -*-
"""agent-api 关停时主动取消后台任务（app/main.py::_cancel_background_tasks）。

背景：uvicorn 收到 SIGTERM 后由 asyncio.run 收尾去取消残余任务并**无限期**等它们退出；
只要有一个任务吞掉 CancelledError，进程就永远退不出（热重载卡在「Finished server
process」、docker stop 等到 SIGKILL）。关停钩子要做三件事，这里各钉一条：

1. 本项目自己起的、行为正常的后台任务被取消；
2. 吞掉取消的任务在限时后被**点名**进日志（协程名 + 挂起位置），而不是静默等死；
3. 不是本项目起的任务（uvicorn 的 serve/lifespan 主任务——协程定义不在 app/ 下）
   一概不碰：它们正等着关停钩子返回，取消它们等于把关停流程自己掐断。

`_ours()` 按协程代码对象的 co_filename 是否落在 app/ 目录下判定，所以「我们的」任务用
compile() 指定一个 app/ 下的假文件名来造，「uvicorn 的」任务直接在本文件里定义即可。
"""
import asyncio
import logging
import os

import pytest

from app import main as app_main


_APP_DIR = os.path.dirname(os.path.abspath(app_main.__file__))

# 在 app/ 目录下「定义」的协程：co_filename 落在 app_dir 内 → 关停钩子认作自己人。
_OURS_SRC = '''
import asyncio

async def normal_bg():
    await asyncio.sleep(60)

async def swallowing_bg(stop):
    """吞掉 CancelledError 继续跑——正是让进程退不出的那种任务。"""
    while not stop.is_set():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass
'''


def _load_ours():
    ns: dict = {}
    code = compile(_OURS_SRC, os.path.join(_APP_DIR, "_fake_background_for_test.py"), "exec")
    exec(code, ns)  # noqa: S102 - 受控源码，仅用于伪造 co_filename
    return ns["normal_bg"], ns["swallowing_bg"]


async def _uvicorn_like_serve():
    """协程定义在 tests/ 下，不在 app/ 下——模拟 uvicorn 的 serve/lifespan 主任务。"""
    await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_cancel_background_tasks_cancels_ours_names_swallowers_and_leaves_uvicorn_alone(caplog):
    normal_bg, swallowing_bg = _load_ours()
    stop = asyncio.Event()

    normal_task = asyncio.create_task(normal_bg(), name="normal-bg")
    swallow_task = asyncio.create_task(swallowing_bg(stop), name="swallow-bg")
    serve_task = asyncio.create_task(_uvicorn_like_serve(), name="uvicorn-serve")
    await asyncio.sleep(0)  # 让三个任务都跑到各自的 await 上

    try:
        with caplog.at_level(logging.WARNING, logger=app_main.__name__):
            await app_main._cancel_background_tasks(timeout=0.2)

        # 1. 正常任务被取消
        assert normal_task.cancelled(), "app/ 下的正常后台任务必须被关停钩子取消"

        # 2. 吞取消的任务：还活着，且被点名进日志（协程名 + 挂起位置）
        assert not swallow_task.done(), "吞掉 CancelledError 的任务取消不掉是前提（否则这条用例没意义）"
        named = [r for r in caplog.records
                 if r.levelno == logging.WARNING and "未响应取消" in r.getMessage()]
        assert len(named) == 1, f"限时未退出的任务应且仅应被点名一次，实际日志: {[r.getMessage() for r in caplog.records]}"
        msg = named[0].getMessage()
        assert "swallowing_bg" in msg, "日志必须带协程名，否则线上没法定位是谁卡住关停"
        assert "_fake_background_for_test.py" in msg, "日志必须带挂起位置（文件:行）"
        assert "normal_bg" not in msg, "被正常取消的任务不该出现在「退不出」名单里"

        # 3. 非 app/ 下的任务（uvicorn 主任务）不被取消
        assert not serve_task.done() and not serve_task.cancelling(), \
            "协程不在 app/ 下的任务不得被关停钩子取消——它们正等着钩子返回"
    finally:
        stop.set()
        swallow_task.cancel()   # 唤醒它，让循环看到 stop 后正常退出
        serve_task.cancel()
        await asyncio.gather(swallow_task, serve_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_background_tasks_is_a_noop_without_our_tasks(caplog):
    """没有本项目的后台任务时直接返回：不等超时、不写日志。"""
    serve_task = asyncio.create_task(_uvicorn_like_serve(), name="uvicorn-serve")
    await asyncio.sleep(0)
    try:
        loop = asyncio.get_running_loop()
        started = loop.time()
        with caplog.at_level(logging.WARNING, logger=app_main.__name__):
            await app_main._cancel_background_tasks(timeout=5.0)
        assert loop.time() - started < 1.0, "没有可取消任务时不应等满 timeout"
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not serve_task.done()
    finally:
        serve_task.cancel()
        await asyncio.gather(serve_task, return_exceptions=True)
