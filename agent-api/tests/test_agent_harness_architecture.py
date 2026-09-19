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

# 退役词在全仓扫描中的**唯一**合法落点。
# `run_code` 退役的是主对话那条「run_code 工具/执行路径」；`code_runner.run_code()` 是沙箱
# 执行内核（工作流「代码节点」经 workflow_engine 调它），与 test_legacy_tools_gone.py 里
# 「execute_in_sandbox 内核函数 ≠ 暴露给模型的工具名」是同一个区分。除这两处外任何
# 地方（尤其 services/chat、services/agent_harness）出现都视为回流。
_RETIRED_TERM_ALLOWED_PATHS = {
    "run_code": {
        "services/sandbox/code_runner.py",
        "services/workflows/workflow_engine.py",
    },
}


def _python_sources():
    return [path for path in APP_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_retired_architecture_vocabulary_is_absent():
    sources = _python_sources()
    for term in _RETIRED_TERMS:
        matches = {}
        allowed = _RETIRED_TERM_ALLOWED_PATHS.get(term, set())
        for path in sources:
            rel = path.relative_to(APP_ROOT).as_posix()
            if rel in allowed:
                continue
            count = path.read_text(encoding="utf-8").count(term)
            if count:
                matches[rel] = count
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
