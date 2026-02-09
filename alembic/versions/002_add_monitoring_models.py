"""Add monitoring models: keyword_rankings, post_metrics, iteration_tasks + keywords.post_id

Revision ID: 002_monitoring
Revises: 001_discovery
Create Date: 2026-02-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '002_monitoring'
down_revision = '001_discovery'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add post_id FK to keywords table
    op.add_column('keywords', sa.Column('post_id', UUID(as_uuid=True), sa.ForeignKey('posts.id'), nullable=True))

    # KeywordRanking table
    op.create_table(
        'keyword_rankings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('keyword_id', UUID(as_uuid=True), sa.ForeignKey('keywords.id'), nullable=False),
        sa.Column('post_id', UUID(as_uuid=True), sa.ForeignKey('posts.id'), nullable=True),
        sa.Column('date', sa.DateTime, nullable=False),
        sa.Column('position', sa.Integer, nullable=True),
        sa.Column('url', sa.Text, nullable=True),
        sa.Column('serp_features', sa.JSON),
        sa.Column('checked_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('source', sa.String(50), server_default='dataforseo'),
        sa.UniqueConstraint('keyword_id', 'date', name='uq_keyword_date'),
    )
    op.create_index('ix_keyword_rankings_keyword_id', 'keyword_rankings', ['keyword_id'])
    op.create_index('ix_keyword_rankings_date', 'keyword_rankings', ['date'])

    # PostMetric table
    op.create_table(
        'post_metrics',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('post_id', UUID(as_uuid=True), sa.ForeignKey('posts.id'), nullable=False),
        sa.Column('date', sa.DateTime, nullable=False),
        sa.Column('impressions', sa.Integer),
        sa.Column('clicks', sa.Integer),
        sa.Column('ctr', sa.Float),
        sa.Column('avg_position', sa.Float),
        sa.Column('sessions', sa.Integer),
        sa.Column('bounce_rate', sa.Float),
        sa.Column('top_queries', sa.JSON),
        sa.Column('source', sa.String(50), server_default='gsc'),
        sa.UniqueConstraint('post_id', 'date', 'source', name='uq_post_date_source'),
    )
    op.create_index('ix_post_metrics_post_id', 'post_metrics', ['post_id'])

    # IterationTask table
    op.create_table(
        'iteration_tasks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('post_id', UUID(as_uuid=True), sa.ForeignKey('posts.id'), nullable=False),
        sa.Column('trigger_type', sa.String(50), nullable=False),
        sa.Column('trigger_data', sa.JSON),
        sa.Column('priority', sa.Integer, server_default='5'),
        sa.Column('status', sa.String(50), server_default='pending'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_iteration_tasks_post_id', 'iteration_tasks', ['post_id'])
    op.create_index('ix_iteration_tasks_status', 'iteration_tasks', ['status'])


def downgrade() -> None:
    op.drop_table('iteration_tasks')
    op.drop_table('post_metrics')
    op.drop_table('keyword_rankings')
    op.drop_column('keywords', 'post_id')
