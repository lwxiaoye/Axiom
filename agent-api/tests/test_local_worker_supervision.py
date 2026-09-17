"""A live API must not silently strand accepted Runs after its local worker exits."""
import json
import threading
import time
from unittest.mock import Mock

import pytest

import run
from app.core import local_worker


class Child:
    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


@pytest.fixture
def supervisor(monkeypatch):
    owner = run.WorkerSupervisor()
    monkeypatch.setattr(run, "stop_worker_process", Mock())
    yield owner
    owner.stop()


def wait_running(timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if local_worker.worker_state() == "running":
            return
        time.sleep(0.01)
    pytest.fail("replacement worker was not registered")


def test_exited_worker_is_replaced_without_restarting_api(supervisor, monkeypatch):
    dead, replacement = Child(1), Child()
    spawn = Mock(side_effect=[dead, replacement])
    monkeypatch.setattr(run, "start_worker_process", spawn)
    supervisor.start()
    assert local_worker.worker_state() == "unavailable"
    wait_running()
    assert spawn.call_count == 2
    supervisor.stop()
    run.stop_worker_process.assert_called_with(replacement)


def test_live_worker_is_not_replaced(supervisor, monkeypatch):
    child = Child()
    spawn = Mock(return_value=child)
    monkeypatch.setattr(run, "start_worker_process", spawn)
    supervisor.start()
    assert local_worker.worker_state() == "running"
    threading.Event().wait(0.65)
    spawn.assert_called_once_with()


def test_shutdown_during_backoff_does_not_launch_another_worker(supervisor, monkeypatch):
    spawn = Mock(return_value=Child(137))
    monkeypatch.setattr(run, "start_worker_process", spawn)
    supervisor.start()
    threading.Event().wait(0.65)
    supervisor.stop()
    assert not supervisor.monitor.is_alive()
    spawn.assert_called_once_with()
    assert local_worker.worker_state() == "external"


def test_spawn_failure_remains_unready_and_recovers_after_backoff(supervisor, monkeypatch):
    failed = threading.Event()
    attempts = []

    def spawn():
        attempts.append(1)
        if len(attempts) == 1:
            return Child(1)
        if len(attempts) == 2:
            failed.set()
            raise OSError("test process creation failure")
        return Child()

    monkeypatch.setattr(run, "start_worker_process", spawn)
    supervisor.start()
    assert failed.wait(3)
    assert local_worker.worker_state() == "unavailable"
    wait_running()
    assert len(attempts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("managed,code,ready", [(True, 1, False), (True, None, True), (False, None, True)])
async def test_readiness_checks_only_the_owned_worker(monkeypatch, managed, code, ready):
    from app import main

    async def database_ok():
        return "ok"

    monkeypatch.setattr(main, "_runtime_db_state", database_ok)
    local_worker.track_worker(Child(code), managed=managed)
    try:
        result = await main.health_ready()
        if ready:
            assert result["status"] == "ready"
        else:
            assert result.status_code == 503
            assert json.loads(result.body)["local_worker"] == "unavailable"
    finally:
        local_worker.track_worker(None, managed=False)
