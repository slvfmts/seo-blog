"""
API routes for content iteration tasks.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime

from src.db.session import get_db
from src.db import models

router = APIRouter()


@router.get("")
async def list_iterations(
    site_id: UUID = None,
    status: str = None,
    db: Session = Depends(get_db),
):
    """List iteration tasks."""
    query = db.query(models.IterationTask)

    if site_id:
        query = query.join(models.Post).filter(models.Post.site_id == site_id)

    if status:
        query = query.filter(models.IterationTask.status == status)

    tasks = query.order_by(
        models.IterationTask.priority.asc(),
        models.IterationTask.created_at.desc(),
    ).all()

    result = []
    for t in tasks:
        post = db.query(models.Post).filter(models.Post.id == t.post_id).first()
        result.append({
            "id": str(t.id),
            "post_id": str(t.post_id),
            "post_title": post.title if post else "Unknown",
            "post_url": post.url if post else None,
            "trigger_type": t.trigger_type,
            "trigger_data": t.trigger_data,
            "priority": t.priority,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        })

    return result


@router.get("/{task_id}")
async def get_iteration(
    task_id: UUID,
    db: Session = Depends(get_db),
):
    """Get iteration task details."""
    task = db.query(models.IterationTask).filter(models.IterationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Iteration task not found")

    post = db.query(models.Post).filter(models.Post.id == task.post_id).first()

    return {
        "id": str(task.id),
        "post_id": str(task.post_id),
        "post_title": post.title if post else "Unknown",
        "post_url": post.url if post else None,
        "trigger_type": task.trigger_type,
        "trigger_data": task.trigger_data,
        "priority": task.priority,
        "status": task.status,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


@router.post("/{task_id}/skip")
async def skip_iteration(
    task_id: UUID,
    db: Session = Depends(get_db),
):
    """Skip an iteration task."""
    task = db.query(models.IterationTask).filter(models.IterationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Iteration task not found")

    if task.status in ("pending", "in_progress"):
        task.status = "skipped"
        task.completed_at = datetime.utcnow()
        db.commit()

    return {"status": "skipped", "id": str(task.id)}
