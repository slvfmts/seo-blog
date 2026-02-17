"""Add keyword_id to drafts table.

Revision ID: 004_draft_keyword
Revises: 003_backfill
Create Date: 2026-02-17
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '004_draft_keyword'
down_revision = '003_backfill'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drafts', sa.Column('keyword_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        'fk_drafts_keyword_id',
        'drafts', 'keywords',
        ['keyword_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_drafts_keyword_id', 'drafts', type_='foreignkey')
    op.drop_column('drafts', 'keyword_id')
