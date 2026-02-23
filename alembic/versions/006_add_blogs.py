"""Add blogs table and blog_id FK to sites.

Revision ID: 006_add_blogs
Revises: 005_knowledge_base
Create Date: 2026-02-23
"""
import os
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '006_add_blogs'
down_revision = '005_knowledge_base'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create blogs table
    op.create_table(
        'blogs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(255), nullable=False, unique=True),
        sa.Column('domain', sa.String(255), nullable=True),
        sa.Column('ghost_url', sa.String(500), nullable=False),
        sa.Column('ghost_admin_key', sa.String(500), nullable=False),
        sa.Column('status', sa.String(50), server_default='active'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()')),
    )

    # Add blog_id column to sites
    op.add_column('sites', sa.Column('blog_id', UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_sites_blog_id', 'sites', 'blogs', ['blog_id'], ['id'])
    op.create_index('ix_sites_blog_id', 'sites', ['blog_id'])

    # Insert default blog from env vars
    ghost_url = os.environ.get('GHOST_URL', 'http://ghost:2368')
    ghost_admin_key = os.environ.get('GHOST_ADMIN_KEY', '')

    op.execute(
        sa.text(
            "INSERT INTO blogs (id, name, slug, ghost_url, ghost_admin_key, status) "
            "VALUES (gen_random_uuid(), 'Main Blog', 'main-blog', :ghost_url, :ghost_admin_key, 'active')"
        ).bindparams(ghost_url=ghost_url, ghost_admin_key=ghost_admin_key)
    )

    # Bind all existing sites to the default blog
    op.execute(
        sa.text(
            "UPDATE sites SET blog_id = (SELECT id FROM blogs LIMIT 1) WHERE blog_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index('ix_sites_blog_id', table_name='sites')
    op.drop_constraint('fk_sites_blog_id', 'sites', type_='foreignkey')
    op.drop_column('sites', 'blog_id')
    op.drop_table('blogs')
