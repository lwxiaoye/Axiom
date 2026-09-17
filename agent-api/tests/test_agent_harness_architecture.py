"""Final Agent Harness architecture gates."""

from __future__ import annotations

import ast
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
HARNESS_ROOT = APP_ROOT / "services" / "agent_harness"

_RETIRED_TERMS = {
    "HARNESS_V2_MODE", "TASK_DAG_MODE", "RUN_STEERING_V2",
    "runtime_v2_service", "task_graph", "run_code",
}


def _python_sources():
    return [path for path in APP_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_retired_architecture_vocabulary_is_absent():
    sources = _python_sources()
    for term in _RETIRED_TERMS:
        matches = {}
        for path in sources:
            count = path.read_text(encoding="utf-8").count(term)
            if count:
                matches[str(path.relative_to(APP_ROOT))] = count
        assert not matches, f"retired term {term} returned: {matches}"


def test_harness_package_has_no_legacy_back_dependencies():
    forbidden = {
        "app.services.agent_harness.orchestrator",
        "app.services.task_graph",
        "app.services.tasks.runtime_v2_service",
    }
    for path in HARNESS_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        bad = sorted(
            imported for imported in imports
            if any(imported == item or imported.startswith(item + ".") for item in forbidden)
        )
        assert not bad, f"{path.relative_to(APP_ROOT)} imports legacy loop modules: {bad}"


def test_harness_package_does_not_repeat_retired_vocabulary():
    text = "\n".join(
        path.read_text(encoding="utf-8") for path in HARNESS_ROOT.rglob("*.py")
    )
    for term in _RETIRED_TERMS:
        assert term not in text


def test_worker_enters_runs_through_the_single_harness_kernel():
    worker = (APP_ROOT / "worker.py").read_text(encoding="utf-8")
    assert "HarnessKernel.stream" in worker
    assert "chat_service.stream_chat" not in worker
    assert not (APP_ROOT / "services" / "model_driver.py").exists()
