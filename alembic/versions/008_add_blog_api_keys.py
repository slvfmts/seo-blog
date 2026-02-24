"""Add per-blog API key columns for full blog isolation.

Revision ID: 008_add_blog_api_keys
Revises: 007_add_blog_id_to_kb
Create Date: 2026-02-24
"""
import os
from alembic import op
import sqlalchemy as sa

revision = '008_add_blog_api_keys'
down_revision = '007_add_blog_id_to_kb'
branch_labels = None
depends_on = None

# Columns to add (name, env_var for backfill)
API_KEY_COLUMNS = [
    ("anthropic_api_key", "ANTHROPIC_API_KEY"),
    ("anthropic_proxy_url", "ANTHROPIC_PROXY_URL"),
    ("anthropic_proxy_secret", "ANTHROPIC_PROXY_SECRET"),
    ("serper_api_key", "SERPER_API_KEY"),
    ("jina_api_key", "JINA_API_KEY"),
    ("yandex_wordstat_api_key", "YANDEX_WORDSTAT_API_KEY"),
    ("yandex_cloud_folder_id", "YANDEX_CLOUD_FOLDER_ID"),
    ("rush_analytics_api_key", "RUSH_ANALYTICS_API_KEY"),
    ("openai_api_key", "OPENAI_API_KEY"),
    ("openai_proxy_url", "OPENAI_PROXY_URL"),
    ("residential_proxy_url", "RESIDENTIAL_PROXY_URL"),
]


def upgrade() -> None:
    for col_name, _ in API_KEY_COLUMNS:
        op.add_column('blogs', sa.Column(col_name, sa.String(500), nullable=True))

    # Backfill first blog (main) with env vars so it keeps working
    for col_name, env_var in API_KEY_COLUMNS:
        value = os.environ.get(env_var, "")
        if value:
            op.execute(
                sa.text(
                    f"UPDATE blogs SET {col_name} = :val "
                    "WHERE id = (SELECT id FROM blogs ORDER BY created_at ASC LIMIT 1)"
                ).bindparams(val=value)
            )


def downgrade() -> None:
    for col_name, _ in reversed(API_KEY_COLUMNS):
        op.drop_column('blogs', col_name)
