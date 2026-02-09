"""
UI routes for Brief workflow web interface.
"""

import asyncio
import os
import uuid as uuid_lib
from uuid import UUID
from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, Request, Form, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
import markdown

from src.db.session import get_db, SessionLocal
from src.db import models
from src.services.brief_generator import BriefGenerator
from src.services.generator import ArticleGenerator
from src.services.discovery import DiscoveryAgent
from src.services.writing_pipeline import PipelineRunner
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


# ============ Topics Pages ============

@router.get("/topics", response_class=HTMLResponse)
async def list_topics(request: Request, db: Session = Depends(get_db)):
    """List all topics (sites)."""
    # Get all sites with keyword and brief counts
    sites = db.query(models.Site).order_by(models.Site.created_at.desc()).all()

    topics = []
    for site in sites:
        keyword_count = db.query(models.Keyword).filter(models.Keyword.site_id == site.id).count()
        selected_count = db.query(models.Keyword).filter(
            models.Keyword.site_id == site.id,
            models.Keyword.status == 'selected'
        ).count()
        brief_count = db.query(models.Brief).filter(models.Brief.site_id == site.id).count()

        topics.append({
            "id": site.id,
            "name": site.name,
            "domain": site.domain,
            "status": site.status,
            "language": site.language,
            "country": site.country,
            "created_at": site.created_at,
            "keyword_count": keyword_count,
            "selected_count": selected_count,
            "brief_count": brief_count,
        })

    return templates.TemplateResponse("topics/list.html", {
        "request": request,
        "topics": topics,
    })


@router.get("/topics/new", response_class=HTMLResponse)
async def new_topic_form(request: Request):
    """Show form to create a new topic."""
    return templates.TemplateResponse("topics/create.html", {
        "request": request,
    })


@router.post("/topics/new", response_class=HTMLResponse)
async def create_topic(
    request: Request,
    niche: str = Form(...),
    country: str = Form("ru"),
    language: str = Form("ru"),
    db: Session = Depends(get_db),
):
    """Create a new topic by running Discovery Agent."""
    settings = get_settings()

    if not settings.serper_api_key:
        return templates.TemplateResponse("topics/create.html", {
            "request": request,
            "error": "SERPER_API_KEY not configured",
            "niche": niche,
            "country": country,
            "language": language,
        })

    if not settings.anthropic_api_key:
        return templates.TemplateResponse("topics/create.html", {
            "request": request,
            "error": "ANTHROPIC_API_KEY not configured",
            "niche": niche,
            "country": country,
            "language": language,
        })

    try:
        # Run Discovery Agent
        discovery = DiscoveryAgent(
            serper_api_key=settings.serper_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            proxy_url=settings.anthropic_proxy_url or None,
            proxy_secret=settings.anthropic_proxy_secret or None,
        )

        result = await discovery.discover(
            niche=niche,
            country=country,
            language=language,
        )

        # Create Site
        site = models.Site(
            name=niche,
            status="active",
            language=language,
            country=country.upper(),
            niche_boundaries=result.get("niche_boundaries"),
        )
        db.add(site)
        db.flush()  # Get site.id

        # Create Competitors
        for comp_data in result.get("competitors", []):
            competitor = models.Competitor(
                site_id=site.id,
                domain=comp_data.get("domain", ""),
                relevance_score=comp_data.get("relevance_score"),
                status="active",
            )
            db.add(competitor)

        # Create Keywords from seed_keywords
        for kw_text in result.get("seed_keywords", []):
            keyword = models.Keyword(
                site_id=site.id,
                keyword=kw_text,
                status="new",
            )
            db.add(keyword)

        db.commit()
        db.refresh(site)

        return RedirectResponse(
            url=f"/ui/topics/{site.id}",
            status_code=303,
        )

    except Exception as e:
        return templates.TemplateResponse("topics/create.html", {
            "request": request,
            "error": str(e),
            "niche": niche,
            "country": country,
            "language": language,
        })


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
async def topic_detail(
    request: Request,
    topic_id: UUID,
    error: str = None,
    success: str = None,
    db: Session = Depends(get_db),
):
    """Show topic details with keywords."""
    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Get keywords with their briefs
    keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == topic_id
    ).order_by(models.Keyword.status, models.Keyword.keyword).all()

    # Calculate stats
    stats = {
        "total": len(keywords),
        "new": sum(1 for kw in keywords if kw.status == "new"),
        "selected": sum(1 for kw in keywords if kw.status == "selected"),
        "rejected": sum(1 for kw in keywords if kw.status == "rejected"),
        "brief_created": sum(1 for kw in keywords if kw.status == "brief_created"),
    }

    return templates.TemplateResponse("topics/detail.html", {
        "request": request,
        "topic": topic,
        "keywords": keywords,
        "stats": stats,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/topics/{topic_id}/keywords/select", response_class=HTMLResponse)
async def select_keywords(
    request: Request,
    topic_id: UUID,
    keyword_ids: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Mark selected keywords as 'selected'."""
    if not keyword_ids:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Не выбраны ключевые слова",
            status_code=303,
        )

    count = 0
    for kw_id in keyword_ids:
        keyword = db.query(models.Keyword).filter(
            models.Keyword.id == kw_id,
            models.Keyword.site_id == topic_id,
            models.Keyword.status.in_(["new", "rejected"]),
        ).first()
        if keyword:
            keyword.status = "selected"
            count += 1

    db.commit()

    return RedirectResponse(
        url=f"/ui/topics/{topic_id}?success=Выбрано {count} ключевых слов",
        status_code=303,
    )


@router.post("/topics/{topic_id}/keywords/reject", response_class=HTMLResponse)
async def reject_keywords(
    request: Request,
    topic_id: UUID,
    keyword_ids: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Mark selected keywords as 'rejected'."""
    if not keyword_ids:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Не выбраны ключевые слова",
            status_code=303,
        )

    count = 0
    for kw_id in keyword_ids:
        keyword = db.query(models.Keyword).filter(
            models.Keyword.id == kw_id,
            models.Keyword.site_id == topic_id,
            models.Keyword.status.in_(["new", "selected"]),
        ).first()
        if keyword:
            keyword.status = "rejected"
            count += 1

    db.commit()

    return RedirectResponse(
        url=f"/ui/topics/{topic_id}?success=Отклонено {count} ключевых слов",
        status_code=303,
    )


@router.post("/topics/{topic_id}/generate-briefs", response_class=HTMLResponse)
async def generate_briefs_for_topic(
    request: Request,
    topic_id: UUID,
    db: Session = Depends(get_db),
):
    """Generate briefs for all selected keywords."""
    settings = get_settings()

    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Get selected keywords
    keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == topic_id,
        models.Keyword.status == "selected",
    ).all()

    if not keywords:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Нет выбранных ключевых слов для генерации",
            status_code=303,
        )

    try:
        generator = BriefGenerator(
            serper_api_key=settings.serper_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            proxy_url=settings.anthropic_proxy_url or None,
            proxy_secret=settings.anthropic_proxy_secret or None,
        )

        count = 0
        for keyword in keywords:
            # Generate brief
            brief_data = await generator.generate(
                topic=keyword.keyword,
                country=topic.country.lower(),
                language=topic.language,
            )

            # Create Brief with keyword_id
            db_brief = models.Brief(
                site_id=topic.id,
                keyword_id=keyword.id,
                title=brief_data.get("title", keyword.keyword),
                target_keyword=brief_data.get("target_keyword", keyword.keyword),
                secondary_keywords=brief_data.get("secondary_keywords"),
                word_count_min=brief_data.get("word_count_min", 1500),
                word_count_max=brief_data.get("word_count_max", 2500),
                structure=brief_data.get("structure"),
                competitor_urls=brief_data.get("competitor_urls"),
                serp_analysis=brief_data.get("serp_analysis"),
                status="draft",
            )
            db.add(db_brief)

            # Update keyword status
            keyword.status = "brief_created"
            count += 1

        db.commit()

        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?success=Создано {count} Briefs",
            status_code=303,
        )

    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)}",
            status_code=303,
        )


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


@router.post("/drafts/{draft_id}/validate", response_class=HTMLResponse)
async def validate_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Run validation pipeline on draft."""
    from src.services.validation_pipeline import ValidationPipeline

    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft.status not in ("generated", "validated", "validation_failed"):
        return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)

    try:
        pipeline = ValidationPipeline()
        await pipeline.run(draft.id)
    except Exception as e:
        # Error handling is done in pipeline
        pass

    return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)


@router.post("/drafts/{draft_id}/approve", response_class=HTMLResponse)
async def approve_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Approve a draft for publishing."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Allow approve from generated (skip validation) or validated status
    if draft.status in ("generated", "validated"):
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
            meta_title=draft.meta_title,
            meta_description=draft.meta_description,
        )

        if result["success"]:
            draft.status = "published"
            draft.cms_post_id = result["post"]["id"]
            db.commit()
    except Exception as e:
        pass  # Handle error silently for now

    return RedirectResponse(url=f"/ui/drafts/{draft_id}", status_code=303)


# ============ Pipeline Pages ============

def run_pipeline_sync(draft_id: str, topic: str, region: str, output_dir: str):
    """
    Run writing pipeline synchronously.
    Called from background task.
    """
    settings = get_settings()
    db = SessionLocal()

    try:
        # Get draft
        draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
        if not draft:
            return

        # Update status to running
        draft.pipeline_status = "running"
        draft.status = "generating"
        draft.pipeline_stages = {
            "intent": "pending",
            "research": "pending",
            "structure": "pending",
            "drafting": "pending",
            "editing": "pending",
            "meta": "pending",
        }
        db.commit()

        # Initialize pipeline runner
        runner = PipelineRunner(
            anthropic_api_key=settings.anthropic_api_key,
            serper_api_key=settings.serper_api_key or None,
            jina_api_key=getattr(settings, 'jina_api_key', None),
            dataforseo_login=getattr(settings, 'dataforseo_login', None),
            dataforseo_password=getattr(settings, 'dataforseo_password', None),
            proxy_url=settings.anthropic_proxy_url or None,
            proxy_secret=settings.anthropic_proxy_secret or None,
            ghost_url=settings.ghost_url or None,
            ghost_admin_key=settings.ghost_admin_key or None,
        )

        # Run pipeline (need to run async in sync context)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_with_progress():
            # Run the pipeline
            result = await runner.run(
                topic=topic,
                region=region,
                output_dir=output_dir,
                save_intermediate=True,
            )
            return result

        result = loop.run_until_complete(run_with_progress())
        loop.close()

        # Update draft with results
        draft.title = result.title
        draft.content_md = result.article_md
        draft.word_count = result.word_count
        draft.status = "generated"
        draft.pipeline_status = "completed"
        draft.pipeline_completed_at = datetime.utcnow()
        draft.pipeline_stages = {
            "intent": "completed",
            "research": "completed",
            "structure": "completed",
            "drafting": "completed",
            "editing": "completed",
            "meta": "completed",
        }

        # Save SEO metadata from Meta stage
        if result.meta:
            draft.meta_title = result.meta.meta_title
            draft.meta_description = result.meta.meta_description
            draft.slug = result.meta.slug

        # Extract sources from research
        if result.research and result.research.sources:
            draft.sources_used = [
                {
                    "url": s.url,
                    "title": s.title,
                    "publisher": s.publisher,
                }
                for s in result.research.sources
            ]

        db.commit()

    except Exception as e:
        # Update draft with error
        draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
        if draft:
            draft.pipeline_status = "failed"
            draft.pipeline_error = str(e)
            draft.status = "failed"
            draft.pipeline_completed_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


@router.get("/pipeline/new", response_class=HTMLResponse)
async def new_pipeline_form(request: Request):
    """Show form to start new article via pipeline."""
    return templates.TemplateResponse("pipeline/new.html", {
        "request": request,
    })


@router.post("/pipeline/new", response_class=HTMLResponse)
async def create_pipeline(
    request: Request,
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    region: str = Form("ru"),
    depth: str = Form("standard"),
    db: Session = Depends(get_db),
):
    """Start a new article generation via Writing Pipeline."""
    settings = get_settings()

    if not settings.anthropic_api_key:
        return templates.TemplateResponse("pipeline/new.html", {
            "request": request,
            "error": "ANTHROPIC_API_KEY not configured",
            "topic": topic,
            "region": region,
            "depth": depth,
        })

    try:
        # Generate output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in topic[:30])
        output_dir = f"/tmp/pipeline_output/{timestamp}_{safe_topic}"

        # Create draft
        draft = models.Draft(
            title=topic,  # Will be updated after pipeline
            topic=topic,
            status="generating",
            pipeline_status="pending",
            pipeline_started_at=datetime.utcnow(),
            pipeline_output_dir=output_dir,
            pipeline_stages={
                "intent": "pending",
                "research": "pending",
                "structure": "pending",
                "drafting": "pending",
                "editing": "pending",
                "meta": "pending",
            },
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        # Start background task
        background_tasks.add_task(
            run_pipeline_sync,
            str(draft.id),
            topic,
            region,
            output_dir,
        )

        return RedirectResponse(
            url=f"/ui/drafts/{draft.id}",
            status_code=303,
        )

    except Exception as e:
        return templates.TemplateResponse("pipeline/new.html", {
            "request": request,
            "error": str(e),
            "topic": topic,
            "region": region,
            "depth": depth,
        })
