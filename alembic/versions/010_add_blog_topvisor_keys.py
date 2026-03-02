"""Add per-blog Topvisor columns for project isolation.

Revision ID: 010_add_blog_topvisor_keys
Revises: 009_add_draft_og_fields
Create Date: 2026-03-02
"""
import os
from alembic import op
import sqlalchemy as sa

revision = '010_add_blog_topvisor_keys'
down_revision = '009_add_draft_og_fields'
branch_labels = None
depends_on = None

TOPVISOR_COLUMNS = [
    ("topvisor_user_id", "TOPVISOR_USER_ID", sa.String(255)),
    ("topvisor_access_token", "TOPVISOR_ACCESS_TOKEN", sa.String(500)),
    ("topvisor_project_id", "TOPVISOR_PROJECT_ID", sa.Integer()),
]


def upgrade() -> None:
    for col_name, _, col_type in TOPVISOR_COLUMNS:
        op.add_column('blogs', sa.Column(col_name, col_type, nullable=True))

    # Backfill first blog with env vars
    for col_name, env_var, col_type in TOPVISOR_COLUMNS:
        value = os.environ.get(env_var, "")
        if value:
            if isinstance(col_type, sa.Integer):
                op.execute(
                    sa.text(
                        f"UPDATE blogs SET {col_name} = :val "
                        "WHERE id = (SELECT id FROM blogs ORDER BY created_at ASC LIMIT 1)"
                    ).bindparams(val=int(value))
                )
            else:
                op.execute(
                    sa.text(
                        f"UPDATE blogs SET {col_name} = :val "
                        "WHERE id = (SELECT id FROM blogs ORDER BY created_at ASC LIMIT 1)"
                    ).bindparams(val=value)
                )


def downgrade() -> None:
    for col_name, _, _ in reversed(TOPVISOR_COLUMNS):
        op.drop_column('blogs', col_name)
