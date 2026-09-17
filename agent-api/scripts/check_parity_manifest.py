"""FastGPT 对齐门禁（gap-audit §11-P0-1）后端侧。

校验 parity.manifest.json 的 pythonExecutor 列与 workflow_engine 实际支持集合
（EXECUTOR_METHODS ∪ 交互节点）完全一致：新增执行器没登记 manifest、或 manifest
声称支持但执行器缺失，都会失败。

运行：cd agent-api && python3 scripts/check_parity_manifest.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.workflows.workflow_engine import SUPPORTED_NODE_TYPES  # noqa: E402

# 容器内（仅挂载 agent-api）经 PARITY_MANIFEST 指定拷贝路径；宿主机默认取仓库相对路径
import os  # noqa: E402

MANIFEST_PATH = Path(
    os.environ.get("PARITY_MANIFEST")
    or Path(__file__).resolve().parents[2] / "src" / "views" / "workflow" / "core" / "parity.manifest.json"
)


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    declared = {n["flowNodeType"] for n in manifest["nodes"] if n.get("pythonExecutor")}
    actual = set(SUPPORTED_NODE_TYPES)

    missing_impl = declared - actual  # manifest 声称支持但执行器不存在
    unregistered = actual - declared  # 执行器存在但没登记 manifest

    if missing_impl or unregistered:
        if missing_impl:
            print(f"[FAIL] manifest 声明 pythonExecutor 但运行时不支持: {sorted(missing_impl)}")
        if unregistered:
            print(f"[FAIL] 运行时支持但未登记 manifest: {sorted(unregistered)}")
        return 1

    print(f"[OK] pythonExecutor 列与运行时支持集合一致（{len(actual)} 类）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
