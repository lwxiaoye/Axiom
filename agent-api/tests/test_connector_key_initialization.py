"""API and Worker must observe the same fully written local key."""
import multiprocessing

from cryptography.fernet import Fernet

from app.services.connectors import crypto


def _create_concurrently(path, barrier, results):
    crypto.settings.CONNECTOR_KEY_FILE = path
    original = crypto.os.urandom

    def synchronized_random(size):
        value = original(size)
        barrier.wait(timeout=15)
        return value

    crypto.os.urandom = synchronized_random
    try:
        results.put(crypto._key_from_file())
    finally:
        crypto.os.urandom = original


def test_concurrent_first_start_keeps_one_key(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(3), ctx.Queue()
    path = tmp_path / "credentials"
    processes = [ctx.Process(target=_create_concurrently, args=(str(path), barrier, results)) for _ in range(3)]
    try:
        for process in processes:
            process.start()
        keys = [results.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0
        assert len(set(keys)) == 1
        token = Fernet(keys[0]).encrypt(b"test-credential")
        assert all(Fernet(key).decrypt(token) == b"test-credential" for key in keys)
        assert path.stat().st_mode & 0o777 == 0o600
        assert list(tmp_path.iterdir()) == [path]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


def test_existing_key_is_never_replaced(tmp_path, monkeypatch):
    path = tmp_path / "credentials"
    path.write_text("existing-local-test-key")
    monkeypatch.setattr(crypto.settings, "CONNECTOR_KEY_FILE", str(path))
    assert crypto._key_from_file() == crypto._derive("existing-local-test-key")
    assert path.read_text() == "existing-local-test-key"


def test_scope_is_stable_for_shared_key_and_changes_for_other_key(monkeypatch):
    try:
        monkeypatch.setattr(crypto.settings, "CONNECTOR_SECRET_KEY", "test-key-a")
        crypto.reset_cache_for_test()
        first = crypto.credential_key_id()
        crypto.reset_cache_for_test()
        assert crypto.credential_key_id() == first
        monkeypatch.setattr(crypto.settings, "CONNECTOR_SECRET_KEY", "test-key-b")
        crypto.reset_cache_for_test()
        assert crypto.credential_key_id() != first
    finally:
        crypto.reset_cache_for_test()
