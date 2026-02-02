"""
Health check endpoints.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db.session import get_db

router = APIRouter()


@router.get("/health")
async def health_check():
    """Базовая проверка здоровья."""
    return {"status": "ok", "service": "seo-blog-api"}


@router.get("/health/db")
async def health_check_db(db: Session = Depends(get_db)):
    """Проверка подключения к БД."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": str(e)}
