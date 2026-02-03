"""Add Discovery Pipeline models: competitors, keywords, clusters, roadmap

Revision ID: 001_discovery
Revises:
Create Date: 2026-02-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers
revision = '001_discovery'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Competitors table
    op.create_table(
        'competitors',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id'), nullable=False),
        sa.Column('domain', sa.String(255), nullable=False),
        sa.Column('relevance_score', sa.Float),
        sa.Column('monthly_traffic', sa.Integer),
        sa.Column('top_keywords', sa.JSON),
        sa.Column('status', sa.String(50), default='active'),
        sa.Column('discovered_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_competitors_site_domain', 'competitors', ['site_id', 'domain'], unique=True)

    # Clusters table (before keywords due to FK)
    op.create_table(
        'clusters',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('primary_keyword_id', UUID(as_uuid=True), nullable=True),
        sa.Column('intent', sa.String(50)),
        sa.Column('topic_type', sa.String(50)),
        sa.Column('parent_cluster_id', UUID(as_uuid=True), sa.ForeignKey('clusters.id'), nullable=True),
        sa.Column('priority_score', sa.Float),
        sa.Column('estimated_traffic', sa.Integer),
        sa.Column('competition_level', sa.String(20)),
        sa.Column('status', sa.String(50), default='new'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_clusters_site_id', 'clusters', ['site_id'])

    # Keywords table
    op.create_table(
        'keywords',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id'), nullable=False),
        sa.Column('keyword', sa.Text, nullable=False),
        sa.Column('search_volume', sa.Integer),
        sa.Column('difficulty', sa.Float),
        sa.Column('cpc', sa.Float),
        sa.Column('intent', sa.String(50)),
        sa.Column('serp_features', sa.JSON),
        sa.Column('current_position', sa.Integer),
        sa.Column('competitor_id', UUID(as_uuid=True), sa.ForeignKey('competitors.id'), nullable=True),
        sa.Column('cluster_id', UUID(as_uuid=True), sa.ForeignKey('clusters.id'), nullable=True),
        sa.Column('status', sa.String(50), default='new'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    op.create_index('ix_keywords_site_id', 'keywords', ['site_id'])
    op.create_index('ix_keywords_cluster_id', 'keywords', ['cluster_id'])

    # Content Roadmap table
    op.create_table(
        'content_roadmap',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('site_id', UUID(as_uuid=True), sa.ForeignKey('sites.id'), nullable=False),
        sa.Column('cluster_id', UUID(as_uuid=True), sa.ForeignKey('clusters.id'), nullable=False),
        sa.Column('scheduled_week', sa.DateTime),
        sa.Column('priority', sa.Integer),
        sa.Column('reasoning', sa.Text),
        sa.Column('dependencies', sa.JSON),
        sa.Column('expected_traffic', sa.Integer),
        sa.Column('expected_time_to_rank_weeks', sa.Integer),
        sa.Column('status', sa.String(50), default='planned'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_content_roadmap_site_id', 'content_roadmap', ['site_id'])

    # Add cluster_id to briefs table
    op.add_column('briefs', sa.Column('cluster_id', UUID(as_uuid=True), sa.ForeignKey('clusters.id'), nullable=True))


def downgrade() -> None:
    op.drop_column('briefs', 'cluster_id')
    op.drop_table('content_roadmap')
    op.drop_table('keywords')
    op.drop_table('clusters')
    op.drop_table('competitors')
