"""OpenSandbox provider smoke test.

Run inside the agent-api container after applying the OpenSandbox compose override:
    docker exec agent-api python scripts/sandbox_opensandbox_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sandbox import create_configured_sandbox
from app.services.sandbox.base import ExecuteOptions, FileWriteEntry
from app.services.sandbox.sandbox_executor import execute_in_sandbox


async def main() -> None:
    sandbox = create_configured_sandbox("opensandbox-smoke")
    if not await sandbox.ping():
        raise SystemExit("OpenSandbox health check failed")
    await sandbox.create()
    try:
        await sandbox.write_files([FileWriteEntry(path="smoke.txt", data=b"opensandbox-ok\n")])
        result = await sandbox.execute(
            "python -c 'print(open(\"smoke.txt\").read().strip())'",
            ExecuteOptions(timeout_ms=30_000),
        )
        if not result.ok or "opensandbox-ok" not in result.stdout:
            raise SystemExit(f"command failed: {result}")
        isolation = await sandbox.execute(
            "if getent hosts checkpoint-postgres >/dev/null 2>&1; "
            "then echo reachable; exit 1; else echo isolated; fi",
            ExecuteOptions(timeout_ms=10_000),
        )
        if not isolation.ok or "isolated" not in isolation.stdout:
            raise SystemExit(f"sandbox can resolve an internal database service: {isolation}")
        files = await sandbox.read_files(["smoke.txt"])
        if not files or not files[0].ok or files[0].data != b"opensandbox-ok\n":
            raise SystemExit(f"file round-trip failed: {files}")
        print("OpenSandbox smoke: create/write/execute/read passed")
    finally:
        await sandbox.delete()
        print("OpenSandbox smoke: destroy requested")

    result = await execute_in_sandbox(
        "from pathlib import Path\n"
        "Path('/workspace/files').mkdir(parents=True, exist_ok=True)\n"
        "Path('/workspace/files/sandbox-executor.txt').write_text('sandbox-executor-ok\\n')\n"
        "print('sandbox-executor-ok')\n",
        collect_outputs=False,
        collect_workspace=True,
        timeout_ms=30_000,
    )
    output = next(
        (item for item in result.workspace_changes if item.get("path") == "sandbox-executor.txt"),
        None,
    )
    if not result.ok or "sandbox-executor-ok" not in result.stdout:
        raise SystemExit(f"sandbox_executor failed: {result.to_tool_text()}")
    if not output or output.get("data") != b"sandbox-executor-ok\n":
        raise SystemExit(
            f"sandbox_executor output round-trip failed: {result.workspace_changes}"
        )
    print("OpenSandbox smoke: sandbox_executor pipeline passed")


if __name__ == "__main__":
    asyncio.run(main())
