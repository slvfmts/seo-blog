"""
SQLAlchemy модели для SEO Blog.

MVP-версия с базовыми сущностями.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean,
    DateTime, ForeignKey, Enum, JSON
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Brief(Base):
    """ТЗ (Brief) для статьи."""
    __tablename__ = "briefs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)

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

    # Ghost integration
    ghost_url = Column(String(500))
    ghost_admin_key = Column(String(500))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    drafts = relationship("Draft", back_populates="site")
    posts = relationship("Post", back_populates="site")
    briefs = relationship("Brief", back_populates="site")


class Draft(Base):
    """Черновик статьи."""
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)
    brief_id = Column(UUID(as_uuid=True), ForeignKey("briefs.id"), nullable=True)

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

    # Валидация
    validation_score = Column(Float)
    validation_report = Column(JSON)

    # CMS
    cms_post_id = Column(String(255))

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    site = relationship("Site", back_populates="drafts")
    brief = relationship("Brief", back_populates="drafts")


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
