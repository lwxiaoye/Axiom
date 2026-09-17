import asyncio
import os
import sys
import unittest
from unittest import mock


class WindowsEventLoopPolicyTest(unittest.TestCase):
    def test_run_script_uses_absolute_local_connector_key_file(self):
        import run

        with mock.patch.dict(os.environ, {}, clear=True):
            run.load_local_env()

            self.assertEqual(
                os.environ.get("CONNECTOR_KEY_FILE"),
                str(run.ROOT / "data" / ".connector_key"),
            )

    def test_run_script_derives_isolated_local_runtime_database(self):
        import dotenv
        import run

        with (
            mock.patch.object(dotenv, "load_dotenv", return_value=True),
            mock.patch.dict(
                os.environ,
                {
                    "CHECKPOINT_DATABASE_URL": (
                        "postgresql://langgraph:test@127.0.0.1:5432/"
                        "langgraph_checkpoints"
                    )
                },
                clear=True,
            ),
        ):
            run.load_local_env()

            self.assertEqual(
                os.environ.get("RUNTIME_DATABASE_URL"),
                "postgresql+psycopg://langgraph:test@127.0.0.1:5432/agent_runtime_local",
            )

    def test_run_script_starts_and_stops_local_worker_with_api(self):
        import run

        worker = mock.Mock()
        worker.poll.return_value = None
        with (
            mock.patch.object(run, "configure_event_loop_policy") as configure_policy,
            mock.patch.object(run, "load_local_env") as load_local_env,
            mock.patch.object(run, "start_worker_process", return_value=worker) as start_worker,
            mock.patch.object(run, "stop_worker_process") as stop_worker,
            mock.patch("uvicorn.run") as uvicorn_run,
        ):
            run.main()

        configure_policy.assert_called_once_with()
        load_local_env.assert_called_once_with()
        start_worker.assert_called_once_with()
        uvicorn_run.assert_called_once_with(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
        )
        stop_worker.assert_called_once_with(worker)

    def test_run_script_configures_selector_event_loop_on_windows(self):
        if not sys.platform.startswith("win"):
            self.skipTest("Windows-only event loop policy check")

        import run

        run.configure_event_loop_policy()
        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            self.assertNotIn("Proactor", type(loop).__name__)
        finally:
            loop.close()

    def test_app_entrypoint_uses_selector_event_loop_on_windows(self):
        if not sys.platform.startswith("win"):
            self.skipTest("Windows-only event loop policy check")

        import app.main  # noqa: F401

        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            self.assertNotIn("Proactor", type(loop).__name__)
        finally:
            loop.close()

    def test_worker_entrypoint_uses_selector_event_loop_on_windows(self):
        if not sys.platform.startswith("win"):
            self.skipTest("Windows-only event loop policy check")

        import app.worker

        async def fake_run_worker(stop):
            return None

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        with mock.patch.object(app.worker, "run_worker", side_effect=fake_run_worker):
            app.worker.main()

        loop = asyncio.get_event_loop_policy().new_event_loop()
        try:
            self.assertNotIn("Proactor", type(loop).__name__)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
