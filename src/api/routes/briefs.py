"""
API endpoints для управления Brief (ТЗ на статьи).
"""

from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.db.session import get_db
from src.db import models
from src.services.generator import ArticleGenerator
from src.services.brief_generator import BriefGenerator
from src.config import get_settings

router = APIRouter()


# Schema for auto-generation
class GenerateBriefRequest(BaseModel):
    """Запрос на автогенерацию Brief из темы."""
    topic: str
    site_id: UUID | None = None
    country: str = "ru"
    language: str = "ru"


# Pydantic schemas

class BriefCreate(BaseModel):
    """Схема создания Brief."""
    title: str
    target_keyword: str
    secondary_keywords: list[str] | None = None
    site_id: UUID | None = None
    word_count_min: int = 1500
    word_count_max: int = 2500
    structure: dict | None = None  # {sections: [{heading, key_points}]}
    required_sources: list[dict] | None = None  # [{type, min_count}]
    competitor_urls: list[str] | None = None
    serp_analysis: dict | None = None


class BriefUpdate(BaseModel):
    """Схема обновления Brief."""
    title: str | None = None
    target_keyword: str | None = None
    secondary_keywords: list[str] | None = None
    word_count_min: int | None = None
    word_count_max: int | None = None
    structure: dict | None = None
    required_sources: list[dict] | None = None
    competitor_urls: list[str] | None = None
    serp_analysis: dict | None = None


class BriefResponse(BaseModel):
    """Схема ответа Brief."""
    id: UUID
    site_id: UUID | None
    title: str
    target_keyword: str
    secondary_keywords: list[str] | None
    word_count_min: int
    word_count_max: int
    structure: dict | None
    required_sources: list[dict] | None
    competitor_urls: list[str] | None
    serp_analysis: dict | None
    status: str
    created_at: datetime
    approved_at: datetime | None

    class Config:
        from_attributes = True


class GenerateDraftResponse(BaseModel):
    """Ответ на запрос генерации Draft по Brief."""
    draft_id: UUID
    brief_id: UUID
    status: str
    message: str


# Endpoints

@router.post("/generate", response_model=BriefResponse)
async def generate_brief(request: GenerateBriefRequest, db: Session = Depends(get_db)):
    """
    Автоматически генерирует Brief на основе темы/ключевого слова.

    Процесс:
    1. Получает SERP данные через Serper.dev API
    2. Claude анализирует результаты и генерирует структуру Brief
    3. Brief сохраняется в БД со статусом 'draft'

    После генерации Brief можно отредактировать через PATCH /briefs/{id}
    и затем одобрить через POST /briefs/{id}/approve
    """
    settings = get_settings()

    if not settings.serper_api_key:
        raise HTTPException(
            status_code=500,
            detail="SERPER_API_KEY not configured. Required for brief generation."
        )

    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=500,
            detail="ANTHROPIC_API_KEY not configured. Required for brief generation."
        )

    # Проверяем site_id если указан
    if request.site_id:
        site = db.query(models.Site).filter(models.Site.id == request.site_id).first()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")

    # Создаём генератор
    generator = BriefGenerator(
        serper_api_key=settings.serper_api_key,
        anthropic_api_key=settings.anthropic_api_key,
        proxy_url=settings.anthropic_proxy_url or None,
        proxy_secret=settings.anthropic_proxy_secret or None,
    )

    try:
        # Генерируем Brief
        brief_data = await generator.generate(
            topic=request.topic,
            country=request.country,
            language=request.language,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate brief: {str(e)}"
        )

    # Создаём Brief в БД
    db_brief = models.Brief(
        site_id=request.site_id,
        title=brief_data.get("title", request.topic),
        target_keyword=brief_data.get("target_keyword", request.topic),
        secondary_keywords=brief_data.get("secondary_keywords"),
        word_count_min=brief_data.get("word_count_min", 1500),
        word_count_max=brief_data.get("word_count_max", 2500),
        structure=brief_data.get("structure"),
        competitor_urls=brief_data.get("competitor_urls"),
        serp_analysis=brief_data.get("serp_analysis"),
        status="draft",
    )
    db.add(db_brief)
    db.commit()
    db.refresh(db_brief)

    return db_brief


@router.post("/", response_model=BriefResponse)
async def create_brief(brief: BriefCreate, db: Session = Depends(get_db)):
    """Создать новый Brief."""
    db_brief = models.Brief(
        site_id=brief.site_id,
        title=brief.title,
        target_keyword=brief.target_keyword,
        secondary_keywords=brief.secondary_keywords,
        word_count_min=brief.word_count_min,
        word_count_max=brief.word_count_max,
        structure=brief.structure,
        required_sources=brief.required_sources,
        competitor_urls=brief.competitor_urls,
        serp_analysis=brief.serp_analysis,
        status="draft",
    )
    db.add(db_brief)
    db.commit()
    db.refresh(db_brief)
    return db_brief


@router.get("/", response_model=list[BriefResponse])
async def list_briefs(
    status: str | None = None,
    site_id: UUID | None = None,
    db: Session = Depends(get_db),
):
    """Список Brief с фильтрацией по статусу и site_id."""
    query = db.query(models.Brief)

    if status:
        query = query.filter(models.Brief.status == status)
    if site_id:
        query = query.filter(models.Brief.site_id == site_id)

    briefs = query.order_by(models.Brief.created_at.desc()).all()
    return briefs


@router.get("/{brief_id}", response_model=BriefResponse)
async def get_brief(brief_id: UUID, db: Session = Depends(get_db)):
    """Получить Brief по ID."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")
    return brief


@router.patch("/{brief_id}", response_model=BriefResponse)
async def update_brief(
    brief_id: UUID,
    brief_update: BriefUpdate,
    db: Session = Depends(get_db),
):
    """Обновить Brief (только в статусе draft)."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Brief status is '{brief.status}', can only edit drafts"
        )

    update_data = brief_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(brief, field, value)

    db.commit()
    db.refresh(brief)
    return brief


@router.delete("/{brief_id}")
async def delete_brief(brief_id: UUID, db: Session = Depends(get_db)):
    """Удалить Brief (только в статусе draft)."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Brief status is '{brief.status}', can only delete drafts"
        )

    db.delete(brief)
    db.commit()
    return {"status": "deleted", "brief_id": str(brief_id)}


@router.post("/{brief_id}/approve", response_model=BriefResponse)
async def approve_brief(brief_id: UUID, db: Session = Depends(get_db)):
    """Одобрить Brief для генерации статьи."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"Brief status is '{brief.status}', expected 'draft'"
        )

    brief.status = "approved"
    brief.approved_at = datetime.utcnow()
    db.commit()
    db.refresh(brief)
    return brief


@router.post("/{brief_id}/generate-draft", response_model=GenerateDraftResponse)
async def generate_draft_from_brief(
    brief_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Сгенерировать Draft по Brief."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status != "approved":
        raise HTTPException(
            status_code=400,
            detail=f"Brief status is '{brief.status}', expected 'approved'"
        )

    # Обновляем статус Brief
    brief.status = "in_writing"

    # Создаём черновик
    draft = models.Draft(
        brief_id=brief.id,
        site_id=brief.site_id,
        title=brief.title,
        status="generating",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    # Формируем keywords из Brief
    keywords = [brief.target_keyword]
    if brief.secondary_keywords:
        keywords.extend(brief.secondary_keywords)

    # Запускаем генерацию в фоне
    generator = ArticleGenerator(
        api_key=settings.anthropic_api_key,
        proxy_url=settings.anthropic_proxy_url or None,
        proxy_secret=settings.anthropic_proxy_secret or None,
    )
    background_tasks.add_task(
        generator.generate_and_save_from_brief,
        draft_id=draft.id,
        brief_id=brief.id,
    )

    return GenerateDraftResponse(
        draft_id=draft.id,
        brief_id=brief.id,
        status="generating",
        message="Генерация статьи по Brief запущена в фоновом режиме",
    )
