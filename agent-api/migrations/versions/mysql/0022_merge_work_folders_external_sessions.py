"""Merge work-folder and external-session migration branches.

Revision ID: mysql_0022_merge_work_folders
Revises: mysql_0021_external_session_fix, mysql_0015_work_folders
Create Date: 2026-09-15
"""


revision = "mysql_0022_merge_work_folders"
down_revision = (
    "mysql_0021_external_session_fix",
    "mysql_0015_work_folders",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parent revisions have already applied their own schema changes.
    pass


def downgrade() -> None:
    pass
