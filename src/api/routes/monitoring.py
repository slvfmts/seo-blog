"""
API routes for position monitoring.

Supports dual-source position checks: Topvisor (primary) and Serper (fallback).
"""

import logging
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc

from src.db.session import get_db, SessionLocal
from src.db import models
from src.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/rankings")
async def list_rankings(
    site_id: UUID = None,
    db: Session = Depends(get_db),
):
    """Get current rankings for all tracked keywords."""
    query = db.query(models.Keyword).filter(
        models.Keyword.status.in_(["targeted", "achieved"]),
    )
    if site_id:
        query = query.filter(models.Keyword.site_id == site_id)

    keywords = query.order_by(models.Keyword.current_position.asc().nullslast()).all()

    return [
        {
            "id": str(kw.id),
            "keyword": kw.keyword,
            "position": kw.current_position,
            "status": kw.status,
            "site_id": str(kw.site_id),
            "post_id": str(kw.post_id) if kw.post_id else None,
        }
        for kw in keywords
    ]


@router.get("/rankings/{keyword_id}/history")
async def keyword_history(
    keyword_id: UUID,
    limit: int = 60,
    db: Session = Depends(get_db),
):
    """Get position history for a keyword."""
    keyword = db.query(models.Keyword).filter(models.Keyword.id == keyword_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")

    rankings = db.query(models.KeywordRanking).filter(
        models.KeywordRanking.keyword_id == keyword_id,
    ).order_by(desc(models.KeywordRanking.date)).limit(limit).all()

    return {
        "keyword": keyword.keyword,
        "keyword_id": str(keyword.id),
        "current_position": keyword.current_position,
        "history": [
            {
                "date": r.date.isoformat(),
                "position": r.position,
                "url": r.url,
                "serp_features": r.serp_features,
                "source": r.source,
            }
            for r in rankings
        ],
    }


@router.post("/check")
async def trigger_check(
    site_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Manually trigger position check for a site (Serper)."""
    settings = get_settings()

    site = db.query(models.Site).filter(models.Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    # Resolve serper key from blog
    blog = site.blog if site.blog_id else None
    serper_key = (blog.serper_api_key if blog and blog.serper_api_key else None) or settings.serper_api_key

    if not serper_key:
        raise HTTPException(status_code=400, detail="SERPER_API_KEY not configured")

    from src.services.monitoring.position_tracker import PositionTracker

    tracker = PositionTracker(
        db_session_factory=SessionLocal,
        serper_api_key=serper_key,
    )

    summary = await tracker.run_daily_check(site_id)
    signals = await tracker.detect_decay(site_id)

    return {
        "check_summary": summary,
        "decay_signals": [
            {
                "keyword_id": s.keyword_id,
                "signal_type": s.signal_type,
                "severity": s.severity,
                "details": s.details,
                "suggested_action": s.suggested_action,
            }
            for s in signals
        ],
    }


@router.post("/check/topvisor")
async def trigger_topvisor_check(
    site_id: UUID,
    db: Session = Depends(get_db),
):
    """
    Manually trigger Topvisor position check for a site.

    Triggers async check → polls for results → saves to keyword_rankings.
    """
    from src.services.monitoring import make_topvisor_client
    from src.services.monitoring.topvisor_positions import TopvisorPositionTracker

    site = db.query(models.Site).filter(models.Site.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")

    blog = site.blog if site.blog_id else None
    settings = get_settings()
    bs = {
        "topvisor_user_id": (blog.topvisor_user_id if blog and blog.topvisor_user_id else None) or settings.topvisor_user_id,
        "topvisor_access_token": (blog.topvisor_access_token if blog and blog.topvisor_access_token else None) or settings.topvisor_access_token,
        "topvisor_project_id": (blog.topvisor_project_id if blog and blog.topvisor_project_id else None) or settings.topvisor_project_id,
    }

    tv_client = make_topvisor_client(bs)
    if not tv_client:
        raise HTTPException(status_code=400, detail="Topvisor not configured for this site")

    tracker = TopvisorPositionTracker(tv_client)

    logger.info("Manual Topvisor position check triggered for site %s", site.name)

    result = await tracker.run_daily_check(site.id, db)

    return {
        "source": "topvisor",
        "check_result": result,
    }


@router.get("/decay")
async def list_decay_signals(
    site_id: UUID = None,
    db: Session = Depends(get_db),
):
    """Get pending iteration tasks (decay signals)."""
    query = db.query(models.IterationTask).filter(
        models.IterationTask.status.in_(["pending", "in_progress"]),
    )

    if site_id:
        query = query.join(models.Post).filter(models.Post.site_id == site_id)

    tasks = query.order_by(models.IterationTask.priority.asc()).all()

    return [
        {
            "id": str(t.id),
            "post_id": str(t.post_id),
            "trigger_type": t.trigger_type,
            "trigger_data": t.trigger_data,
            "priority": t.priority,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in tasks
    ]


@router.get("/summary")
async def monitoring_summary(
    site_id: UUID,
    db: Session = Depends(get_db),
):
    """Get monitoring summary for a site."""
    keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == site_id,
        models.Keyword.status.in_(["targeted", "achieved"]),
    ).all()

    total = len(keywords)
    in_top_3 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 3)
    in_top_10 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 10)
    in_top_20 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 20)
    not_ranking = sum(1 for kw in keywords if kw.current_position is None)
    positions = [kw.current_position for kw in keywords if kw.current_position is not None]
    avg_position = round(sum(positions) / len(positions), 1) if positions else None

    alert_count = db.query(models.IterationTask).filter(
        models.IterationTask.status == "pending",
        models.IterationTask.post_id.in_(
            db.query(models.Post.id).filter(models.Post.site_id == site_id)
        ),
    ).count()

    return {
        "total_tracked": total,
        "in_top_3": in_top_3,
        "in_top_10": in_top_10,
        "in_top_20": in_top_20,
        "not_ranking": not_ranking,
        "avg_position": avg_position,
        "alerts": alert_count,
    }
