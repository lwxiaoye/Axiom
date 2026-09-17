"""本地沙箱 sdk 后端输出上限早停（深度扫描 P1：`while True: print(...)` 打爆宿主内存）。

CLI 后端早有 `_read_capped` 边读边判、超限即 kill；sdk 后端（容器内 docker-py，本机实际
选中的后端）此前把 max_bytes 丢在门口，读完才 `_cap()` 截断。这里用假的 docker api 验：
- 无穷输出流会在 max_bytes 处停读并断流（不读到天荒地老）；
- 结果标注 truncated；退出码尽力取真实值——「输出超限但已跑完」的成功脚本不得误判失败
  （2026-07-26 收尾修订），只有确实还在跑的失控进程才归一为 1；
- 正常有限输出与流式 on_stdout 回调（逐页产物直播）不回归。
"""

import pytest

from app.services.sandbox.base import ExecuteOptions
from app.services.sandbox.local_adapter import LocalDockerAdapter


class _FakeExecApi:
    """docker-py 低层 api 的替身：exec_create/exec_start(stream, demux)/exec_inspect。"""

    def __init__(self, frames=None, infinite_chunk: bytes = b"", exit_code: int = 0):
        self._frames = frames
        self._infinite_chunk = infinite_chunk
        self.exit_code = exit_code
        self.yielded = 0
        self.closed = False
        self.inspected = 0

    def exec_create(self, container_id, cmd, **kwargs):
        self.container_id = container_id
        self.cmd = cmd
        return {"Id": "exec-1"}

    def exec_start(self, exec_id, stream=False, demux=False):
        def _gen():
            try:
                if self._frames is not None:
                    for frame in self._frames:
                        self.yielded += 1
                        yield frame
                else:
                    while True:  # 死循环 print 的替身
                        self.yielded += 1
                        yield (self._infinite_chunk, None)
            finally:
                self.closed = True

        return _gen()

    def exec_inspect(self, exec_id):
        self.inspected += 1
        return {"ExitCode": self.exit_code}


def _adapter(api) -> LocalDockerAdapter:
    adapter = LocalDockerAdapter()
    adapter._backend = "sdk"
    adapter._started = True
    adapter._sdk_container = type("C", (), {"id": "container-1"})()
    adapter._client = type("Client", (), {"api": api})()
    return adapter


@pytest.mark.asyncio
async def test_sdk_backend_stops_reading_at_max_bytes():
    chunk = b"x" * 1000
    api = _FakeExecApi(infinite_chunk=chunk)
    adapter = _adapter(api)

    result = await adapter.execute(
        "python -c \"while True: print('x' * 1000000)\"",
        ExecuteOptions(max_output_bytes=5000, timeout_ms=60_000),
    )

    # 早停：读到刚过 5000 字节就断，而不是把无穷流吃完（不早停这里根本不会返回）
    assert api.yielded <= 8, f"读了 {api.yielded} 块，没有在上限处停"
    assert api.closed is True, "提前止读后必须断流"
    assert result.truncated is True
    assert len(result.stdout) == 5000
    # 替身 exec_inspect 报 ExitCode=0 且未在跑：截断但已跑完的脚本保留真实退出码
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_sdk_backend_capped_and_still_running_maps_to_failure():
    """截断且 exec_inspect 一直报 Running：拿不到退出码，归一为 1（失控进程口径）。"""
    api = _FakeExecApi(infinite_chunk=b"x" * 1000)
    api.exec_inspect = lambda exec_id: {"ExitCode": None, "Running": True}
    adapter = _adapter(api)

    result = await adapter.execute(
        "cmd", ExecuteOptions(max_output_bytes=5000, timeout_ms=60_000),
    )
    assert result.truncated is True
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_sdk_backend_caps_stderr_too():
    api = _FakeExecApi(infinite_chunk=b"e" * 1000)

    def _stderr_only(exec_id, stream=False, demux=False):
        def _gen():
            try:
                while True:
                    api.yielded += 1
                    yield (None, b"e" * 1000)
            finally:
                api.closed = True

        return _gen()

    api.exec_start = _stderr_only
    adapter = _adapter(api)

    result = await adapter.execute("cmd", ExecuteOptions(max_output_bytes=4000, timeout_ms=60_000))
    assert api.yielded <= 8
    assert result.truncated is True
    assert len(result.stderr) == 4000


@pytest.mark.asyncio
async def test_sdk_backend_normal_output_not_truncated():
    api = _FakeExecApi(frames=[(b"hello ", None), (None, b"warn"), (b"world", None)], exit_code=0)
    adapter = _adapter(api)

    result = await adapter.execute("echo hello", ExecuteOptions(max_output_bytes=65536, timeout_ms=60_000))

    assert result.stdout == "hello world"
    assert result.stderr == "warn"
    assert result.exit_code == 0
    assert result.truncated is False
    assert api.inspected == 1  # 正常结束才去取退出码


@pytest.mark.asyncio
async def test_sdk_backend_streams_stdout_callback():
    """回归：逐页产物直播的 on_stdout 逐块回调在统一到低层 API 后仍然工作。"""
    api = _FakeExecApi(frames=[(b"page1", None), (b"page2", None)], exit_code=0)
    adapter = _adapter(api)
    seen: list[bytes] = []

    result = await adapter.execute(
        "run", ExecuteOptions(max_output_bytes=65536, timeout_ms=60_000, on_stdout=seen.append)
    )

    assert seen == [b"page1", b"page2"]
    assert result.stdout == "page1page2"


@pytest.mark.asyncio
async def test_sdk_backend_callback_failure_does_not_break_execution():
    api = _FakeExecApi(frames=[(b"a", None), (b"b", None)], exit_code=0)
    adapter = _adapter(api)

    def _boom(_data):
        raise RuntimeError("回调炸了")

    result = await adapter.execute(
        "run", ExecuteOptions(max_output_bytes=65536, timeout_ms=60_000, on_stdout=_boom)
    )
    assert result.stdout == "ab"
    assert result.exit_code == 0
