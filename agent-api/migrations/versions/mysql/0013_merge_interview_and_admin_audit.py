"""Merge interview-session and workflow-admin-audit mysql 0012 branches.

Revision ID: mysql_0013_merge
Revises: mysql_0012_interview_sessions, mysql_0012_workflow_admin_audit
Create Date: 2026-09-07

Both 0012 revisions parent from mysql_0011_global_sub_skins. This empty merge
makes a single head so `upgrade head` applies whichever branch a database is
missing, then stamps the combined revision.
"""

revision = "mysql_0013_merge"
down_revision = (
    "mysql_0012_interview_sessions",
    "mysql_0012_workflow_admin_audit",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
