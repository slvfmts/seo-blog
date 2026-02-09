"""Backfill Post records from published drafts, set domain on active sites,
link keywords to posts via briefs.

Revision ID: 003_backfill
Revises: 002_monitoring
Create Date: 2026-02-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '003_backfill'
down_revision = '002_monitoring'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Create Post records for published drafts that don't have one yet
    conn.execute(sa.text("""
        INSERT INTO posts (id, site_id, draft_id, title, slug, url, cms_post_id, status, published_at, created_at)
        SELECT
            gen_random_uuid(),
            d.site_id,
            d.id,
            d.title,
            d.slug,
            COALESCE('http://95.163.230.43/' || d.slug || '/', ''),
            d.cms_post_id,
            'live',
            COALESCE(d.updated_at, d.created_at, NOW()),
            NOW()
        FROM drafts d
        WHERE d.status = 'published'
          AND d.cms_post_id IS NOT NULL
          AND d.site_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM posts p WHERE p.draft_id = d.id
          )
    """))

    # 2. Set domain on active sites that don't have one
    conn.execute(sa.text("""
        UPDATE sites
        SET domain = '95.163.230.43'
        WHERE status = 'active' AND (domain IS NULL OR domain = '')
    """))

    # 3. Link keywords to posts through briefs
    conn.execute(sa.text("""
        UPDATE keywords
        SET post_id = p.id, status = 'targeted'
        FROM briefs b
        JOIN drafts d ON d.brief_id = b.id
        JOIN posts p ON p.draft_id = d.id
        WHERE keywords.id = b.keyword_id
          AND b.keyword_id IS NOT NULL
          AND keywords.post_id IS NULL
    """))


def downgrade() -> None:
    # Data migration — downgrade just removes the backfilled data
    conn = op.get_bind()

    # Unlink keywords
    conn.execute(sa.text("""
        UPDATE keywords SET post_id = NULL, status = 'brief_created'
        WHERE status = 'targeted' AND post_id IS NOT NULL
    """))

    # Remove backfilled posts (only ones created by this migration)
    # We can't easily distinguish, so we leave them in place
    pass
