"""
API endpoints для управления сайтами.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.db.session import get_db
from src.db import models

router = APIRouter()


class SiteCreate(BaseModel):
    """Схема создания сайта."""
    name: str
    domain: str | None = None
    language: str = "ru"
    country: str = "RU"


class SiteResponse(BaseModel):
    """Схема ответа сайта."""
    id: UUID
    name: str
    domain: str | None
    status: str

    class Config:
        from_attributes = True


@router.get("/", response_model=list[SiteResponse])
async def list_sites(db: Session = Depends(get_db)):
    """Список всех сайтов."""
    sites = db.query(models.Site).all()
    return sites


@router.post("/", response_model=SiteResponse)
async def create_site(site: SiteCreate, db: Session = Depends(get_db)):
    """Создать новый сайт."""
    db_site = models.Site(
        name=site.name,
        domain=site.domain,
        status="setup",
    )
    db.add(db_site)
    db.commit()
    db.refresh(db_site)
    return db_site


@router.get("/{site_id}", response_model=SiteResponse)
async def get_site(site_id: UUID, db: Session = Depends(get_db)):
    """Получить сайт по ID."""
    site = db.query(models.Site).filter(models.Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site
