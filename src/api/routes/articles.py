"""
API endpoints для генерации и публикации статей.
"""

import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.db.session import get_db
from src.db import models
from src.services.generator import ArticleGenerator
from src.services.publisher import GhostPublisher
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


class ArticleRequest(BaseModel):
    """Запрос на генерацию статьи."""
    topic: str
    keywords: list[str]
    site_id: UUID | None = None


class ArticleResponse(BaseModel):
    """Ответ с данными статьи."""
    id: UUID
    title: str
    slug: str | None = None
    status: str
    word_count: int | None = None
    content_md: str | None = None

    class Config:
        from_attributes = True


class GenerateResponse(BaseModel):
    """Ответ на запрос генерации."""
    draft_id: UUID
    status: str
    message: str


@router.post("/generate", response_model=GenerateResponse)
async def generate_article(
    request: ArticleRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Генерирует статью по теме и ключевым словам."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Создаём черновик в БД
    draft = models.Draft(
        title=request.topic,
        status="generating",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    # Запускаем генерацию в фоне
    generator = ArticleGenerator(
        api_key=settings.anthropic_api_key,
        proxy_url=settings.anthropic_proxy_url or None,
        proxy_secret=settings.anthropic_proxy_secret or None,
    )
    background_tasks.add_task(
        generator.generate_and_save,
        draft_id=draft.id,
        topic=request.topic,
        keywords=request.keywords,
    )

    return GenerateResponse(
        draft_id=draft.id,
        status="generating",
        message="Генерация запущена в фоновом режиме",
    )


@router.get("/{draft_id}", response_model=ArticleResponse)
async def get_article(draft_id: UUID, db: Session = Depends(get_db)):
    """Получить статью по ID."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.post("/{draft_id}/approve")
async def approve_article(draft_id: UUID, db: Session = Depends(get_db)):
    """Одобряет статью для публикации."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status != "generated":
        raise HTTPException(status_code=400, detail=f"Draft status is '{draft.status}', expected 'generated'")

    draft.status = "approved"
    db.commit()
    return {"status": "approved", "draft_id": str(draft.id)}


@router.post("/{draft_id}/publish")
async def publish_article(draft_id: UUID, db: Session = Depends(get_db)):
    """Публикует статью в Ghost."""
    settings = get_settings()

    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status != "approved":
        raise HTTPException(status_code=400, detail=f"Draft status is '{draft.status}', expected 'approved'")

    # Resolve Ghost creds from blog
    blog = draft.site.blog if (draft.site and draft.site.blog) else None
    ghost_url = blog.ghost_url if blog else settings.ghost_url
    ghost_admin_key = blog.ghost_admin_key if blog else settings.ghost_admin_key

    if not ghost_admin_key:
        raise HTTPException(status_code=500, detail="GHOST_ADMIN_KEY not configured")

    # Warn-only meta validation
    from src.services.validators.meta import validate_meta_before_publish
    meta_warnings = validate_meta_before_publish(draft)
    if meta_warnings:
        logger.warning("Pre-publish meta warnings for draft %s: %s", draft_id, "; ".join(meta_warnings))

    publisher = GhostPublisher(ghost_url, ghost_admin_key)
    result = publisher.publish(
        title=draft.title,
        content=draft.content_md,
        slug=draft.slug,
        meta_title=draft.meta_title,
        meta_description=draft.meta_description,
        status="published",
        og_title=draft.og_title,
        og_description=draft.og_description,
        custom_excerpt=draft.custom_excerpt,
    )

    if result["success"]:
        draft.status = "published"
        draft.cms_post_id = result["post"]["id"]
        db.commit()
        return {"status": "published", "url": result["post"]["url"]}
    else:
        raise HTTPException(status_code=500, detail=result["error"])
