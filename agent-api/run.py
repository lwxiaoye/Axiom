import asyncio
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parent
LOCAL_ENV_FILE = ROOT / ".env.dev"
LOCAL_CONNECTOR_KEY_FILE = ROOT / "data" / ".connector_key"
LOCAL_RUNTIME_DB_NAME = "agent_runtime"


def configure_event_loop_policy() -> None:
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def load_local_env() -> None:
    """Load the same local overrides for both the API process and the worker."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    load_dotenv(ROOT / ".env", override=False)
    if LOCAL_ENV_FILE.exists():
        load_dotenv(LOCAL_ENV_FILE, override=True)
    os.environ.setdefault("CONNECTOR_KEY_FILE", str(LOCAL_CONNECTOR_KEY_FILE))
    if not os.environ.get("RUNTIME_DATABASE_URL"):
        runtime_url = derive_local_runtime_database_url(
            os.environ.get("CHECKPOINT_DATABASE_URL", ""),
        )
        if runtime_url:
            os.environ["RUNTIME_DATABASE_URL"] = runtime_url


def derive_local_runtime_database_url(checkpoint_url: str) -> str:
    url = str(checkpoint_url or "").strip()
    if not url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return ""
    parsed = urlsplit(url)
    scheme = "postgresql+psycopg"
    return urlunsplit((scheme, parsed.netloc, f"/{LOCAL_RUNTIME_DB_NAME}", "", ""))


def start_worker_process() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "app.worker"],
        cwd=str(ROOT),
        env=os.environ.copy(),
    )


def stop_worker_process(worker: subprocess.Popen) -> None:
    if worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=10)


class WorkerSupervisor:
    """Replace only this entrypoint's exited child; never restart a live worker."""

    def __init__(self) -> None:
        self.worker: subprocess.Popen | None = None
        self.stop_event = threading.Event()
        self.monitor = threading.Thread(target=self._watch, name="worker-supervisor", daemon=True)

    def start(self) -> None:
        from app.core.local_worker import track_worker

        self.worker = start_worker_process()
        track_worker(self.worker)
        self.monitor.start()

    def _watch(self) -> None:
        from app.core.local_worker import track_worker

        logger = logging.getLogger(__name__)
        backoff = 1.0
        started_at = time.monotonic()
        while not self.stop_event.wait(0.5):
            if self.worker is not None and self.worker.poll() is None:
                continue
            if time.monotonic() - started_at >= 60:
                backoff = 1.0
            code = self.worker.returncode if self.worker is not None else None
            track_worker(None)
            logger.error("Local worker exited code=%s; retrying in %.1fs", code, backoff)
            if self.stop_event.wait(backoff):
                return
            try:
                self.worker = start_worker_process()
            except OSError:
                self.worker = None
                logger.exception("Local worker restart failed")
            else:
                track_worker(self.worker)
            started_at = time.monotonic()
            backoff = min(30.0, backoff * 2)

    def stop(self) -> None:
        from app.core.local_worker import track_worker

        self.stop_event.set()
        if self.monitor.is_alive():
            self.monitor.join()
        if self.worker is not None:
            stop_worker_process(self.worker)
        track_worker(None, managed=False)


def main() -> None:
    configure_event_loop_policy()
    load_local_env()

    from app.services.connectors.crypto import initialize_credentials
    initialize_credentials()

    import uvicorn

    supervisor = WorkerSupervisor()
    supervisor.start()

    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
        )
    finally:
        supervisor.stop()


if __name__ == "__main__":
    main()
