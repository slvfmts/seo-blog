"""Add og_title, og_description, custom_excerpt to drafts.

Revision ID: 009_add_draft_og_fields
Revises: 008_add_blog_api_keys
Create Date: 2026-03-01
"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_draft_og_fields'
down_revision = '008_add_blog_api_keys'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drafts', sa.Column('og_title', sa.String(95), nullable=True))
    op.add_column('drafts', sa.Column('og_description', sa.String(200), nullable=True))
    op.add_column('drafts', sa.Column('custom_excerpt', sa.String(300), nullable=True))


def downgrade() -> None:
    op.drop_column('drafts', 'custom_excerpt')
    op.drop_column('drafts', 'og_description')
    op.drop_column('drafts', 'og_title')
