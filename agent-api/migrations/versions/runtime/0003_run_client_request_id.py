"""agent_runs 可靠握手幂等键（N-02，2026-07-22）。

client_request_id：客户端每次「发送」生成一次的请求 id。
(user_id, client_request_id) 部分唯一索引保证同键只建一个 Run——并发重复 POST 的
第二次 INSERT 在 PG 层冲突（DuplicateRequestConflict），兜住路由层预检的 TOCTOU 窗口；
首帧丢失后客户端凭它 GET /chat/requests/{id}/run 发现已建 Run，订阅续接而不是重发第二轮。

与 app/core/runtime_db.init_runtime_tables 的启动期幂等 DDL 同源，两边都用
IF NOT EXISTS，先跑哪个都无害。

Revision ID: runtime_0003_client_request
Revises: runtime_0002_instr_scope
Create Date: 2026-07-22
"""
from alembic import op

revision = "runtime_0003_client_request"
down_revision = "runtime_0002_instr_scope"
branch_labels = None
depends_on = None

_UPGRADE_DDL = (
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_client_request "
    "ON agent_runs (user_id, client_request_id) "
    "WHERE client_request_id IS NOT NULL",
)

_DOWNGRADE_DDL = (
    "DROP INDEX IF EXISTS uq_agent_runs_client_request",
    "ALTER TABLE agent_runs DROP COLUMN IF EXISTS client_request_id",
)


def upgrade() -> None:
    for stmt in _UPGRADE_DDL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWNGRADE_DDL:
        op.execute(stmt)
