"""Merge publisher API and unified audit event migration branches.

Revision ID: mysql_0016_merge_publisher_audit
Revises: mysql_0015_publisher_agent_api, mysql_0015_audit_events
Create Date: 2026-09-10
"""


revision = "mysql_0016_merge_publisher_audit"
down_revision = (
    "mysql_0015_publisher_agent_api",
    "mysql_0015_audit_events",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
