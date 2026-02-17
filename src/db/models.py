"""
SQLAlchemy модели для SEO Blog.

MVP-версия с базовыми сущностями.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, Enum, JSON, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Brief(Base):
    """ТЗ (Brief) для статьи."""
    __tablename__ = "briefs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=True)
    keyword_id = Column(UUID(as_uuid=True), ForeignKey("keywords.id"), nullable=True)

    # Основное
    title = Column(String(500), nullable=False)
    target_keyword = Column(String(500), nullable=False)
    secondary_keywords = Column(JSON)  # ["keyword1", "keyword2"]

    # Объём
    word_count_min = Column(Integer, default=1500)
    word_count_max = Column(Integer, default=2500)

    # Структура
    structure = Column(JSON)  # {sections: [{heading, key_points}]}
    required_sources = Column(JSON)  # [{type: "statistic", min_count: 2}]
    competitor_urls = Column(JSON)  # ["url1", "url2"]

    # SEO
    serp_analysis = Column(JSON)  # {paa_questions: [], featured_snippet_target: bool}

    # Статус
    status = Column(String(50), default="draft")  # draft → approved → in_writing → completed

    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime)

    # Relationships
    site = relationship("Site", back_populates="briefs")
    cluster = relationship("Cluster", back_populates="briefs")
    keyword = relationship("Keyword", back_populates="briefs")
    drafts = relationship("Draft", back_populates="brief")


class Site(Base):
    """Сайт/проект."""
    __tablename__ = "sites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    domain = Column(String(255))
    status = Column(String(50), default="setup")  # setup | active | paused
    language = Column(String(10), default="ru")
    country = Column(String(2), default="RU")

    # Discovery
    niche_boundaries = Column(JSON, nullable=True)  # {include: [], exclude: [], target_audience: ""}

    # Ghost integration
    ghost_url = Column(String(500))
    ghost_admin_key = Column(String(500))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    drafts = relationship("Draft", back_populates="site")
    posts = relationship("Post", back_populates="site")
    briefs = relationship("Brief", back_populates="site")
    competitors = relationship("Competitor", back_populates="site")
    keywords = relationship("Keyword", back_populates="site")
    clusters = relationship("Cluster", back_populates="site")
    roadmap = relationship("ContentRoadmap", back_populates="site")


class Draft(Base):
    """Черновик статьи."""
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)
    brief_id = Column(UUID(as_uuid=True), ForeignKey("briefs.id"), nullable=True)
    keyword_id = Column(UUID(as_uuid=True), ForeignKey("keywords.id"), nullable=True)

    title = Column(String(500), nullable=False)
    slug = Column(String(255))
    content_md = Column(Text)  # Markdown
    word_count = Column(Integer)

    meta_title = Column(String(70))
    meta_description = Column(String(160))

    # Генерация
    topic = Column(String(500))
    keywords = Column(JSON)  # list[str]
    sources_used = Column(JSON)  # list[{url, title, quote}]

    # Статус
    status = Column(String(50), default="draft")
    # draft | generating | generated | validating | approved | published | rejected
    # pipeline_running | pipeline_completed | pipeline_failed

    # Валидация
    validation_score = Column(Float)
    validation_report = Column(JSON)

    # Writing Pipeline fields
    pipeline_status = Column(String(50))  # None | running | completed | failed
    pipeline_stages = Column(JSON)  # {"intent": "completed", "research": "running", ...}
    pipeline_started_at = Column(DateTime)
    pipeline_completed_at = Column(DateTime)
    pipeline_error = Column(Text)
    pipeline_output_dir = Column(String(500))  # Path to intermediate files

    # CMS
    cms_post_id = Column(String(255))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="drafts")
    brief = relationship("Brief", back_populates="drafts")
    keyword = relationship("Keyword", back_populates="drafts", foreign_keys=[keyword_id])


class Post(Base):
    """Опубликованная статья."""
    __tablename__ = "posts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    draft_id = Column(UUID(as_uuid=True), ForeignKey("drafts.id"))

    title = Column(String(500), nullable=False)
    slug = Column(String(255))
    url = Column(Text)
    cms_post_id = Column(String(255))

    status = Column(String(50), default="live")  # live | updated | unpublished

    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="posts")
    keywords = relationship("Keyword", back_populates="post")
    rankings = relationship("KeywordRanking", back_populates="post")
    metrics = relationship("PostMetric", back_populates="post")
    iteration_tasks = relationship("IterationTask", back_populates="post")


# ============ Discovery Pipeline Models ============

class Competitor(Base):
    """Конкурент сайта."""
    __tablename__ = "competitors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    domain = Column(String(255), nullable=False)
    relevance_score = Column(Float)  # 0.0 - 1.0
    monthly_traffic = Column(Integer)
    top_keywords = Column(JSON)  # [{keyword, position, volume}]

    status = Column(String(50), default="active")  # active | ignored | analyzed

    discovered_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="competitors")
    keywords = relationship("Keyword", back_populates="source_competitor")


class Keyword(Base):
    """Ключевое слово."""
    __tablename__ = "keywords"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    keyword = Column(Text, nullable=False)
    search_volume = Column(Integer)
    difficulty = Column(Float)  # 0-100
    cpc = Column(Float)

    # Intent: informational | transactional | navigational | commercial
    intent = Column(String(50))

    # SERP features
    serp_features = Column(JSON)  # ["featured_snippet", "paa", "video"]
    current_position = Column(Integer)  # наша позиция (null если не ранжируемся)

    # Связи
    competitor_id = Column(UUID(as_uuid=True), ForeignKey("competitors.id"), nullable=True)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=True)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=True)

    status = Column(String(50), default="new")  # new | clustered | targeted | achieved | abandoned

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="keywords")
    source_competitor = relationship("Competitor", back_populates="keywords")
    cluster = relationship("Cluster", back_populates="keywords")
    briefs = relationship("Brief", back_populates="keyword")
    drafts = relationship("Draft", back_populates="keyword", foreign_keys="Draft.keyword_id")
    post = relationship("Post", back_populates="keywords")
    rankings = relationship("KeywordRanking", back_populates="keyword", order_by="KeywordRanking.date.desc()")


class Cluster(Base):
    """Кластер (тематическая группа ключевых слов)."""
    __tablename__ = "clusters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)

    name = Column(String(255), nullable=False)
    primary_keyword_id = Column(UUID(as_uuid=True), nullable=True)  # FK добавим позже

    # Intent: informational | transactional | navigational | commercial
    intent = Column(String(50))

    # Topic type: pillar | cluster | supporting
    topic_type = Column(String(50))

    # Иерархия
    parent_cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=True)

    # Метрики
    priority_score = Column(Float)  # 0-100, рассчитывается Strategy
    estimated_traffic = Column(Integer)
    competition_level = Column(String(20))  # low | medium | high

    status = Column(String(50), default="new")  # new | planned | in_progress | published | monitoring

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="clusters")
    keywords = relationship("Keyword", back_populates="cluster")
    parent = relationship("Cluster", remote_side=[id], backref="children")
    roadmap_items = relationship("ContentRoadmap", back_populates="cluster")
    briefs = relationship("Brief", back_populates="cluster")


class ContentRoadmap(Base):
    """План публикаций (roadmap)."""
    __tablename__ = "content_roadmap"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=False)
    cluster_id = Column(UUID(as_uuid=True), ForeignKey("clusters.id"), nullable=False)

    scheduled_week = Column(DateTime)  # начало недели
    priority = Column(Integer)  # 1 = highest

    reasoning = Column(Text)  # почему такой приоритет
    dependencies = Column(JSON)  # [cluster_id, ...] - clusters that should be published first

    expected_traffic = Column(Integer)
    expected_time_to_rank_weeks = Column(Integer)

    status = Column(String(50), default="planned")  # planned | in_progress | completed | skipped

    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="roadmap")
    cluster = relationship("Cluster", back_populates="roadmap_items")


# ============ Internal Linking Models ============

class ArticleKeyword(Base):
    """Keyword-article mapping for internal linking."""
    __tablename__ = "article_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    site_id = Column(String(255), nullable=True)
    post_url = Column(Text, nullable=False)
    post_title = Column(Text, nullable=False)
    cms_post_id = Column(String(255), nullable=True)
    content_md = Column(Text, nullable=True)
    keyword = Column(Text, nullable=False)
    keyword_type = Column(String(20), default="secondary")  # primary | secondary
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('post_url', 'keyword', name='uq_article_keyword'),
    )


# ============ Monitoring & Iteration Models ============

class KeywordRanking(Base):
    """Ежедневный снимок позиции keyword в SERP."""
    __tablename__ = "keyword_rankings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    keyword_id = Column(UUID(as_uuid=True), ForeignKey("keywords.id"), nullable=False)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=True)

    date = Column(DateTime, nullable=False)
    position = Column(Integer, nullable=True)  # None = не в топ-100
    url = Column(Text, nullable=True)  # какой URL ранжируется
    serp_features = Column(JSON)  # ["featured_snippet", "paa", ...]
    checked_at = Column(DateTime, default=datetime.utcnow)
    source = Column(String(50), default="dataforseo")  # dataforseo | manual

    # Relationships
    keyword = relationship("Keyword", back_populates="rankings")
    post = relationship("Post", back_populates="rankings")

    __table_args__ = (
        UniqueConstraint('keyword_id', 'date', name='uq_keyword_date'),
    )


class PostMetric(Base):
    """Метрики поста (подготовка для GSC/GA4)."""
    __tablename__ = "post_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)

    date = Column(DateTime, nullable=False)
    impressions = Column(Integer)
    clicks = Column(Integer)
    ctr = Column(Float)
    avg_position = Column(Float)
    sessions = Column(Integer)
    bounce_rate = Column(Float)
    top_queries = Column(JSON)  # [{query, impressions, clicks, position}]
    source = Column(String(50), default="gsc")  # gsc | ga4 | manual

    # Relationships
    post = relationship("Post", back_populates="metrics")

    __table_args__ = (
        UniqueConstraint('post_id', 'date', 'source', name='uq_post_date_source'),
    )


class IterationTask(Base):
    """Задача на обновление контента."""
    __tablename__ = "iteration_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    post_id = Column(UUID(as_uuid=True), ForeignKey("posts.id"), nullable=False)

    trigger_type = Column(String(50), nullable=False)  # decay | freshness | opportunity | manual
    trigger_data = Column(JSON)  # детали триггера
    priority = Column(Integer, default=5)  # 1 = highest
    status = Column(String(50), default="pending")  # pending | in_progress | completed | skipped

    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

    # Relationships
    post = relationship("Post", back_populates="iteration_tasks")
