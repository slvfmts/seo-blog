"""Add knowledge base tables.

Revision ID: 005_knowledge_base
Revises: 004_draft_keyword
Create Date: 2026-02-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '005_knowledge_base'
down_revision = '004_draft_keyword'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_folders',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
    )

    op.create_table(
        'knowledge_documents',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('folder_id', UUID(as_uuid=True), sa.ForeignKey('knowledge_folders.id', ondelete='CASCADE'), nullable=False),
        sa.Column('filename', sa.String(255), nullable=False),
        sa.Column('original_filename', sa.String(255), nullable=False),
        sa.Column('file_path', sa.String(500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(100), nullable=False),
        sa.Column('content_text', sa.Text(), nullable=True),
        sa.Column('word_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
    )

    op.create_table(
        'site_knowledge_folders',
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('folder_id', UUID(as_uuid=True), sa.ForeignKey('knowledge_folders.id', ondelete='CASCADE'), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table('site_knowledge_folders')
    op.drop_table('knowledge_documents')
    op.drop_table('knowledge_folders')
