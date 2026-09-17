"""Schema head 常量与迁移链的同步断言（P2 2026-07-26）。

`MIGRATE_ON_STARTUP=false` 的启动门禁把库里的 alembic revision 与代码里的硬编码常量逐字
比对（`app/core/runtime_db.RUNTIME_SCHEMA_HEAD` / `app/core/database.MYSQL_SCHEMA_HEAD`）。
常量与 `migrations/versions/<target>/` 之间此前无任何自动关联：新增迁移忘了改常量，正确
部署会被误判成「版本落后」而拒绝启动——故障出现在部署期、现场排查。

本测试读迁移目录自己算出真实链头，忘同步在测试期就爆掉。顺带体检整条链：单根、单头、
无悬挂 down_revision、无重复 revision、head 长度不超 Alembic 版本列的 VARCHAR(32)。
merge revision（tuple down_revision）把并行分支接到同一个 head，仍算单头。
"""
import ast
from pathlib import Path

import pytest

VERSIONS_ROOT = Path(__file__).resolve().parents[1] / "migrations" / "versions"

# Alembic 默认版本列宽（过长会让真实环境迁移整笔回滚）
ALEMBIC_VERSION_COLUMN_WIDTH = 32

_DownRev = str | None | tuple[str, ...]


def _assign_value(tree: ast.AST, name: str):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    return None


def _parent_revisions(down: _DownRev) -> tuple[str, ...]:
    if down is None:
        return ()
    if isinstance(down, str):
        return (down,)
    return tuple(down)


def _parse_chain(target: str) -> tuple[str, dict[str, _DownRev]]:
    """解析 migrations/versions/<target>/，返回 (head_revision, {revision: down_revision})。"""
    target_dir = VERSIONS_ROOT / target
    assert target_dir.is_dir(), f"迁移目录不存在：{target_dir}"

    chain: dict[str, _DownRev] = {}
    for path in sorted(target_dir.glob("*.py")):
        if path.name.startswith("__"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rev = _assign_value(tree, "revision")
        assert isinstance(rev, str) and rev, f"{path.name} 未声明 revision"
        assert rev not in chain, f"{target} 链存在重复 revision：{rev}"
        down = _assign_value(tree, "down_revision")
        assert down is None or isinstance(down, (str, tuple)), (
            f"{path.name} down_revision 类型非法：{type(down)!r}"
        )
        if isinstance(down, tuple):
            assert down and all(isinstance(item, str) and item for item in down), (
                f"{path.name} merge down_revision 必须是非空字符串元组"
            )
        chain[rev] = down

    assert chain, f"{target} 链为空——迁移文件是否被误删？"

    roots = [r for r, d in chain.items() if d is None]
    assert len(roots) == 1, f"{target} 链应恰有一个 baseline（down_revision=None），实得 {roots}"

    downs = {parent for down in chain.values() for parent in _parent_revisions(down)}
    dangling = downs - set(chain)
    assert not dangling, (
        f"{target} 链存在悬挂 down_revision（引用了不存在的 revision）：{sorted(dangling)}"
    )

    heads = set(chain) - downs
    assert len(heads) == 1, f"{target} 链应恰有一个 head（无分叉），实得 {sorted(heads)}"

    head = heads.pop()
    seen: set[str] = set()

    def walk(cur: str) -> None:
        if cur in seen:
            return
        seen.add(cur)
        for parent in _parent_revisions(chain[cur]):
            walk(parent)

    walk(head)
    assert seen == set(chain), (
        f"{target} 链不连通：head 回溯到 {len(seen)} 个节点，目录里有 {len(chain)} 个"
    )

    return head, chain


@pytest.mark.parametrize(
    "target, module_path, const_name",
    [
        ("runtime", "app.core.runtime_db", "RUNTIME_SCHEMA_HEAD"),
        ("mysql", "app.core.database", "MYSQL_SCHEMA_HEAD"),
    ],
)
def test_schema_head_constant_matches_migration_chain(target: str, module_path: str, const_name: str):
    import importlib

    head, chain = _parse_chain(target)
    const = getattr(importlib.import_module(module_path), const_name)

    assert const == head, (
        f"{const_name}={const!r} 与 migrations/versions/{target}/ 的真实链头 {head!r} 不一致。"
        f"（该目录当前 {len(chain)} 条迁移）新增迁移后必须同步改 {module_path}.{const_name}，"
        "否则 MIGRATE_ON_STARTUP=false 的环境会把正确部署误判为「版本落后」而拒绝启动。"
    )
    assert len(head) <= ALEMBIC_VERSION_COLUMN_WIDTH, (
        f"revision {head!r} 长度 {len(head)} 超过 Alembic 版本列 VARCHAR"
        f"({ALEMBIC_VERSION_COLUMN_WIDTH})，真实环境迁移会整笔回滚"
    )


def test_old_alembic_scaffolding_stays_deleted():
    """旧 alembic/ 脚手架（0001_baseline.downgrade() = Base.metadata.drop_all，按其 README 照做
    即清空整个业务库）已于 2026-07-26 删除。回归看门狗：别再把它捡回来。"""
    repo = VERSIONS_ROOT.parents[1]
    assert not (repo / "alembic").exists(), "旧 alembic/ 脚手架不应重新出现（drop_all 整库风险）"
    assert not (repo / "alembic.ini").exists(), (
        "根 alembic.ini 不应重新出现——它会让不带 -c 的裸 alembic 命令静默跑上旧链")
    assert (repo / "migrations" / "alembic.ini").is_file(), "migrations/alembic.ini 是在用配置，不得删除"


def test_mysql_interview_and_admin_audit_merge_to_one_head():
    head, chain = _parse_chain("mysql")

    assert chain["mysql_0012_interview_sessions"] == "mysql_0011_global_sub_skins"
    assert chain["mysql_0012_workflow_admin_audit"] == "mysql_0011_global_sub_skins"
    assert chain["mysql_0013_merge"] == (
        "mysql_0012_interview_sessions",
        "mysql_0012_workflow_admin_audit",
    )
    assert chain["mysql_0015_work_folders"] == "mysql_0014_conversation_logs"
    assert chain["mysql_0022_merge_work_folders"] == (
        "mysql_0021_external_session_fix",
        "mysql_0015_work_folders",
    )
    assert head == "mysql_0022_merge_work_folders"
