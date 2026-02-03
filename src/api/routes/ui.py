"""
UI routes for Brief workflow web interface.
"""

from uuid import UUID
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import markdown

from src.db.session import get_db
from src.db import models
from src.services.brief_generator import BriefGenerator
from src.services.generator import ArticleGenerator
from src.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def md_to_html(text: str) -> str:
    """Convert markdown to HTML."""
    if not text:
        return ""
    return markdown.markdown(text, extensions=['tables', 'fenced_code'])


# Register custom filter
templates.env.filters['markdown'] = md_to_html


# ============ Briefs Pages ============

@router.get("/briefs", response_class=HTMLResponse)
async def list_briefs(request: Request, db: Session = Depends(get_db)):
    """List all briefs."""
    briefs = db.query(models.Brief).order_by(models.Brief.created_at.desc()).all()
    return templates.TemplateResponse("briefs/list.html", {
        "request": request,
        "briefs": briefs,
    })


@router.get("/briefs/new", response_class=HTMLResponse)
async def new_brief_form(request: Request):
    """Show form to create a new brief."""
    return templates.TemplateResponse("briefs/create.html", {
        "request": request,
    })


@router.post("/briefs/new", response_class=HTMLResponse)
async def create_brief(
    request: Request,
    topic: str = Form(...),
    country: str = Form("ru"),
    language: str = Form("ru"),
    db: Session = Depends(get_db),
):
    """Generate a new brief from topic."""
    settings = get_settings()

    if not settings.serper_api_key:
        return templates.TemplateResponse("briefs/create.html", {
            "request": request,
            "error": "SERPER_API_KEY not configured",
            "topic": topic,
            "country": country,
            "language": language,
        })

    if not settings.anthropic_api_key:
        return templates.TemplateResponse("briefs/create.html", {
            "request": request,
            "error": "ANTHROPIC_API_KEY not configured",
            "topic": topic,
            "country": country,
            "language": language,
        })

    try:
        generator = BriefGenerator(
            serper_api_key=settings.serper_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            proxy_url=settings.anthropic_proxy_url or None,
            proxy_secret=settings.anthropic_proxy_secret or None,
        )

        brief_data = await generator.generate(
            topic=topic,
            country=country,
            language=language,
        )

        db_brief = models.Brief(
            title=brief_data.get("title", topic),
            target_keyword=brief_data.get("target_keyword", topic),
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

        return RedirectResponse(
            url=f"/ui/briefs/{db_brief.id}",
            status_code=303,
        )

    except Exception as e:
        return templates.TemplateResponse("briefs/create.html", {
            "request": request,
            "error": str(e),
            "topic": topic,
            "country": country,
            "language": language,
        })


@router.get("/briefs/{brief_id}", response_class=HTMLResponse)
async def brief_detail(request: Request, brief_id: UUID, db: Session = Depends(get_db)):
    """Show brief details."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    # Get associated draft if exists
    draft = db.query(models.Draft).filter(models.Draft.brief_id == brief_id).first()

    return templates.TemplateResponse("briefs/detail.html", {
        "request": request,
        "brief": brief,
        "draft": draft,
    })


@router.post("/briefs/{brief_id}/approve", response_class=HTMLResponse)
async def approve_brief(request: Request, brief_id: UUID, db: Session = Depends(get_db)):
    """Approve a brief."""
    from datetime import datetime

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status == "draft":
        brief.status = "approved"
        brief.approved_at = datetime.utcnow()
        db.commit()

    return RedirectResponse(url=f"/ui/briefs/{brief_id}", status_code=303)


@router.post("/briefs/{brief_id}/generate-draft", response_class=HTMLResponse)
async def generate_draft(request: Request, brief_id: UUID, db: Session = Depends(get_db)):
    """Generate draft from brief."""
    settings = get_settings()

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    if brief.status != "approved":
        return RedirectResponse(url=f"/ui/briefs/{brief_id}", status_code=303)

    # Update brief status
    brief.status = "in_writing"

    # Create draft
    draft = models.Draft(
        brief_id=brief.id,
        site_id=brief.site_id,
        title=brief.title,
        status="generating",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    # Run generation synchronously
    generator = ArticleGenerator(
        api_key=settings.anthropic_api_key,
        proxy_url=settings.anthropic_proxy_url or None,
        proxy_secret=settings.anthropic_proxy_secret or None,
    )

    try:
        generator.generate_and_save_from_brief(
            draft_id=draft.id,
            brief_id=brief.id,
        )
    except Exception as e:
        draft.status = "failed"
        db.commit()

    return RedirectResponse(url=f"/ui/briefs/{brief_id}", status_code=303)


# ============ Drafts Pages ============

@router.get("/drafts", response_class=HTMLResponse)
async def list_drafts(request: Request, db: Session = Depends(get_db)):
    """List all drafts."""
    drafts = db.query(models.Draft).order_by(models.Draft.created_at.desc()).all()
    return templates.TemplateResponse("drafts/list.html", {
        "request": request,
        "drafts": drafts,
    })


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
async def draft_detail(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Show draft details."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    return templates.TemplateResponse("drafts/detail.html", {
        "request": request,
        "draft": draft,
    })


@router.post("/drafts/{draft_id}/approve", response_class=HTMLResponse)
async def approve_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Approve a draft for publishing."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status == "generated":
        draft.status = "approved"
        db.commit()

    return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)


@router.post("/drafts/{draft_id}/publish", response_class=HTMLResponse)
async def publish_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Publish draft to Ghost."""
    from src.services.publisher import GhostPublisher
    settings = get_settings()

    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status != "approved":
        return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)

    try:
        publisher = GhostPublisher(settings.ghost_url, settings.ghost_admin_key)
        result = publisher.publish(
            title=draft.title,
            content=draft.content_md,
            slug=draft.slug,
            meta_description=draft.meta_description,
        )

        if result["success"]:
            draft.status = "published"
            draft.cms_post_id = result["post"]["id"]
            db.commit()
    except Exception as e:
        pass  # Handle error silently for now

    return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)
