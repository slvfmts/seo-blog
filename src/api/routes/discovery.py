"""
API endpoints для Discovery — анализ ниши и поиск конкурентов.
"""

from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.db.session import get_db
from src.db import models
from src.services.discovery import DiscoveryAgent
from src.config import get_settings

router = APIRouter()


# ============ Schemas ============

class DiscoverRequest(BaseModel):
    """Запрос на анализ ниши."""
    niche: str
    site_id: UUID | None = None
    country: str = "ru"
    language: str = "ru"
    seed_queries: list[str] | None = None


class CompetitorResponse(BaseModel):
    """Конкурент."""
    id: UUID
    domain: str
    relevance_score: float | None
    monthly_traffic: int | None
    status: str
    discovered_at: datetime

    class Config:
        from_attributes = True


class DiscoverResponse(BaseModel):
    """Результат анализа ниши."""
    site_id: UUID | None
    competitors_found: int
    seed_keywords_count: int
    niche_boundaries: dict | None
    content_gaps: list[str] | None


class CompetitorListResponse(BaseModel):
    """Список конкурентов."""
    competitors: list[CompetitorResponse]
    total: int


# ============ Endpoints ============

@router.post("/discover", response_model=DiscoverResponse)
async def discover_niche(
    request: DiscoverRequest,
    db: Session = Depends(get_db),
):
    """
    Анализирует нишу и находит конкурентов.

    Сохраняет найденных конкурентов и seed keywords в БД.
    """
    settings = get_settings()

    # Resolve keys from blog if site_id provided
    site = None
    blog = None
    if request.site_id:
        site = db.query(models.Site).filter(models.Site.id == request.site_id).first()
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")
        blog = site.blog

    serper_key = (blog.serper_api_key if blog and blog.serper_api_key else None) or settings.serper_api_key
    anthropic_key = (blog.anthropic_api_key if blog and blog.anthropic_api_key else None) or settings.anthropic_api_key
    proxy_url = (blog.anthropic_proxy_url if blog and blog.anthropic_proxy_url else None) or settings.anthropic_proxy_url
    proxy_secret = (blog.anthropic_proxy_secret if blog and blog.anthropic_proxy_secret else None) or settings.anthropic_proxy_secret

    if not serper_key:
        raise HTTPException(status_code=500, detail="SERPER_API_KEY not configured")

    if not anthropic_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")

    # Запускаем Discovery
    agent = DiscoveryAgent(
        serper_api_key=serper_key,
        anthropic_api_key=anthropic_key,
        proxy_url=proxy_url or None,
        proxy_secret=proxy_secret or None,
    )

    try:
        result = await agent.discover(
            niche=request.niche,
            country=request.country,
            language=request.language,
            seed_queries=request.seed_queries,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery failed: {str(e)}")

    # Если site_id не указан, создаём новый site
    if not site:
        site = models.Site(
            name=request.niche[:100],
            language=request.language,
            country=request.country.upper(),
            status="setup",
        )
        db.add(site)
        db.commit()
        db.refresh(site)

    # Сохраняем конкурентов
    competitors_added = 0
    for comp_data in result.get("competitors", []):
        domain = comp_data.get("domain", "")
        if not domain:
            continue

        # Проверяем, есть ли уже такой конкурент
        existing = db.query(models.Competitor).filter(
            models.Competitor.site_id == site.id,
            models.Competitor.domain == domain,
        ).first()

        if not existing:
            competitor = models.Competitor(
                site_id=site.id,
                domain=domain,
                relevance_score=comp_data.get("relevance_score"),
                top_keywords=comp_data.get("top_content_types"),
                status="active",
            )
            db.add(competitor)
            competitors_added += 1

    # Сохраняем seed keywords
    keywords_added = 0
    for kw in result.get("seed_keywords", []):
        if not kw:
            continue

        existing = db.query(models.Keyword).filter(
            models.Keyword.site_id == site.id,
            models.Keyword.keyword == kw,
        ).first()

        if not existing:
            keyword = models.Keyword(
                site_id=site.id,
                keyword=kw,
                status="new",
            )
            db.add(keyword)
            keywords_added += 1

    db.commit()

    return DiscoverResponse(
        site_id=site.id,
        competitors_found=competitors_added,
        seed_keywords_count=keywords_added,
        niche_boundaries=result.get("niche_boundaries"),
        content_gaps=result.get("content_gaps"),
    )


@router.get("/competitors", response_model=CompetitorListResponse)
async def list_competitors(
    site_id: UUID | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    """Список конкурентов с фильтрацией."""
    query = db.query(models.Competitor)

    if site_id:
        query = query.filter(models.Competitor.site_id == site_id)
    if status:
        query = query.filter(models.Competitor.status == status)

    query = query.order_by(models.Competitor.relevance_score.desc().nullslast())
    competitors = query.all()

    return CompetitorListResponse(
        competitors=competitors,
        total=len(competitors),
    )


@router.get("/competitors/{competitor_id}", response_model=CompetitorResponse)
async def get_competitor(competitor_id: UUID, db: Session = Depends(get_db)):
    """Получить конкурента по ID."""
    competitor = db.query(models.Competitor).filter(
        models.Competitor.id == competitor_id
    ).first()

    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    return competitor


@router.delete("/competitors/{competitor_id}")
async def delete_competitor(competitor_id: UUID, db: Session = Depends(get_db)):
    """Удалить конкурента."""
    competitor = db.query(models.Competitor).filter(
        models.Competitor.id == competitor_id
    ).first()

    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    db.delete(competitor)
    db.commit()

    return {"status": "deleted", "competitor_id": str(competitor_id)}


@router.patch("/competitors/{competitor_id}/ignore")
async def ignore_competitor(competitor_id: UUID, db: Session = Depends(get_db)):
    """Пометить конкурента как ignored."""
    competitor = db.query(models.Competitor).filter(
        models.Competitor.id == competitor_id
    ).first()

    if not competitor:
        raise HTTPException(status_code=404, detail="Competitor not found")

    competitor.status = "ignored"
    db.commit()

    return {"status": "ignored", "competitor_id": str(competitor_id)}
