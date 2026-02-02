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


class Draft(Base):
    """Черновик статьи."""
    __tablename__ = "drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    site_id = Column(UUID(as_uuid=True), ForeignKey("sites.id"), nullable=True)

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
