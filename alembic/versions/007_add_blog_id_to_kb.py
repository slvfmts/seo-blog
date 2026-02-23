"""Add blog_id to knowledge_folders for multi-blog scoping.

Revision ID: 007_add_blog_id_to_kb
Revises: 006_add_blogs
Create Date: 2026-02-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '007_add_blog_id_to_kb'
down_revision = '006_add_blogs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('knowledge_folders', sa.Column('blog_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_knowledge_folders_blog_id', 'knowledge_folders', 'blogs', ['blog_id'], ['id'])
    op.create_index('ix_knowledge_folders_blog_id', 'knowledge_folders', ['blog_id'])

    # Bind existing folders to the first blog
    op.execute(
        sa.text(
            "UPDATE knowledge_folders SET blog_id = (SELECT id FROM blogs LIMIT 1) WHERE blog_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index('ix_knowledge_folders_blog_id', table_name='knowledge_folders')
    op.drop_constraint('fk_knowledge_folders_blog_id', 'knowledge_folders', type_='foreignkey')
    op.drop_column('knowledge_folders', 'blog_id')
