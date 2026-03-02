"""
UI routes for Brief workflow web interface.
"""

import asyncio
import json
import logging
import os
import re
import traceback
import uuid as uuid_lib
from uuid import UUID
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)
from fastapi import APIRouter, Depends, Request, Form, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import markdown

from src.db.session import get_db, SessionLocal
from src.db import models
from src.services.brief_generator import BriefGenerator
from src.services.generator import ArticleGenerator
from src.services.discovery import DiscoveryAgent
from src.services.writing_pipeline import PipelineRunner
from src.services.monitoring.position_tracker import PositionTracker
from src.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def md_to_html(text: str) -> str:
    """Convert markdown to HTML."""
    if not text:
        return ""
    return markdown.markdown(text, extensions=['tables', 'fenced_code'])


def strip_first_h1(text: str) -> str:
    """Remove first H1 heading from markdown (template already shows title)."""
    if not text:
        return ""
    return re.sub(r'^#\s+.+\n?', '', text, count=1)


# Register custom filters
templates.env.filters['markdown'] = md_to_html
templates.env.filters['strip_first_h1'] = strip_first_h1


# ============ Auth Pages ============

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Show login form."""
    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "error": request.query_params.get("error"),
    })


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
):
    """Verify credentials and set session."""
    import secrets
    import bcrypt
    settings = get_settings()

    email_ok = secrets.compare_digest(email.lower().strip(), settings.auth_email.lower())
    password_ok = False
    if settings.auth_password_hash:
        try:
            password_ok = bcrypt.checkpw(
                password.encode("utf-8"),
                settings.auth_password_hash.encode("utf-8"),
            )
        except Exception:
            password_ok = False

    if email_ok and password_ok:
        request.session["user"] = email.lower().strip()
        return RedirectResponse(url="/ui/blogs", status_code=303)

    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "error": "Неверный email или пароль",
        "email": email,
    })


@router.get("/logout")
async def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/ui/login", status_code=302)


# ============ Blog helpers ============

def get_current_blog(request: Request, db: Session):
    """Get the currently selected blog from session."""
    blog_id = request.session.get("blog_id")
    if not blog_id:
        return None
    blog = db.query(models.Blog).filter(
        models.Blog.id == blog_id,
        models.Blog.status == "active",
    ).first()
    return blog


def get_blog_site_ids(db: Session, blog_id) -> list:
    """Get all site IDs belonging to a blog."""
    return [s.id for s in db.query(models.Site.id).filter(models.Site.blog_id == blog_id).all()]


def get_blog_kb_folders(db: Session, current_blog):
    """Get KB folders scoped by blog."""
    query = db.query(models.KnowledgeFolder).order_by(models.KnowledgeFolder.name)
    if current_blog:
        query = query.filter(models.KnowledgeFolder.blog_id == current_blog.id)
    return query.all()


def resolve_blog_settings(blog, settings) -> dict:
    """Return effective settings for a blog. Blog values override env vars."""
    return {
        "anthropic_api_key": (blog.anthropic_api_key if blog and blog.anthropic_api_key else None) or settings.anthropic_api_key,
        "anthropic_proxy_url": (blog.anthropic_proxy_url if blog and blog.anthropic_proxy_url else None) or settings.anthropic_proxy_url,
        "anthropic_proxy_secret": (blog.anthropic_proxy_secret if blog and blog.anthropic_proxy_secret else None) or settings.anthropic_proxy_secret,
        "serper_api_key": (blog.serper_api_key if blog and blog.serper_api_key else None) or settings.serper_api_key,
        "jina_api_key": (blog.jina_api_key if blog and blog.jina_api_key else None) or settings.jina_api_key,
        "yandex_wordstat_api_key": (blog.yandex_wordstat_api_key if blog and blog.yandex_wordstat_api_key else None) or settings.yandex_wordstat_api_key,
        "yandex_cloud_folder_id": (blog.yandex_cloud_folder_id if blog and blog.yandex_cloud_folder_id else None) or settings.yandex_cloud_folder_id,
        "rush_analytics_api_key": (blog.rush_analytics_api_key if blog and blog.rush_analytics_api_key else None) or settings.rush_analytics_api_key,
        "topvisor_user_id": (blog.topvisor_user_id if blog and blog.topvisor_user_id else None) or settings.topvisor_user_id,
        "topvisor_access_token": (blog.topvisor_access_token if blog and blog.topvisor_access_token else None) or settings.topvisor_access_token,
        "topvisor_project_id": (blog.topvisor_project_id if blog and blog.topvisor_project_id else None) or settings.topvisor_project_id,
        "openai_api_key": (blog.openai_api_key if blog and blog.openai_api_key else None) or settings.openai_api_key,
        "openai_proxy_url": (blog.openai_proxy_url if blog and blog.openai_proxy_url else None) or settings.openai_proxy_url,
        "residential_proxy_url": (blog.residential_proxy_url if blog and blog.residential_proxy_url else None) or settings.residential_proxy_url,
        "ghost_url": blog.ghost_url if blog else settings.ghost_url,
        "ghost_admin_key": blog.ghost_admin_key if blog else settings.ghost_admin_key,
        "database_url": settings.database_url,
    }


def _resolve_blog(request, db, draft=None, site=None, cluster=None):
    """Resolve blog from draft/site/cluster chain, fall back to session."""
    blog = None
    if draft and draft.site and draft.site.blog:
        blog = draft.site.blog
    elif site and site.blog:
        blog = site.blog
    elif cluster and cluster.site and cluster.site.blog:
        blog = cluster.site.blog
    if not blog:
        blog = get_current_blog(request, db)
    return blog


def _check_blog_ownership(request, db, site=None, draft=None, cluster=None, brief=None):
    """Raise 404 if entity belongs to a different blog than the session blog."""
    current_blog = get_current_blog(request, db)
    if not current_blog:
        return
    # Resolve entity's blog_id
    entity_blog_id = None
    if site:
        entity_blog_id = site.blog_id
    elif draft and draft.site:
        entity_blog_id = draft.site.blog_id
    elif cluster and cluster.site:
        entity_blog_id = cluster.site.blog_id
    elif brief and brief.site:
        entity_blog_id = brief.site.blog_id
    if entity_blog_id and entity_blog_id != current_blog.id:
        raise HTTPException(status_code=404)


def _make_anthropic_client(bs: dict):
    """Create an Anthropic client from resolved blog settings."""
    import anthropic
    if bs["anthropic_proxy_url"] and bs["anthropic_proxy_secret"]:
        return anthropic.Anthropic(
            api_key=bs["anthropic_api_key"],
            base_url=bs["anthropic_proxy_url"],
            default_headers={"x-proxy-token": bs["anthropic_proxy_secret"]},
        )
    return anthropic.Anthropic(api_key=bs["anthropic_api_key"])


def _make_volume_provider(bs: dict, region: str = "ru"):
    """Create a volume provider from resolved blog settings."""
    from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider

    class _S:
        pass

    s = _S()
    s.yandex_wordstat_api_key = bs.get("yandex_wordstat_api_key", "")
    s.yandex_cloud_folder_id = bs.get("yandex_cloud_folder_id", "")
    s.rush_analytics_api_key = bs.get("rush_analytics_api_key", "")
    s.topvisor_user_id = bs.get("topvisor_user_id", "")
    s.topvisor_access_token = bs.get("topvisor_access_token", "")
    s.topvisor_project_id = bs.get("topvisor_project_id", 0)
    return get_volume_provider(region, s)


def _make_topvisor_client(bs: dict = None):
    """Create a TopvisorClient from blog settings (with global fallback), or None."""
    if bs:
        token = bs.get("topvisor_access_token", "")
        user_id = bs.get("topvisor_user_id", "")
        project_id = bs.get("topvisor_project_id", 0)
    else:
        from src.config.settings import get_settings
        settings = get_settings()
        token = settings.topvisor_access_token
        user_id = settings.topvisor_user_id
        project_id = settings.topvisor_project_id
    if token and user_id and project_id:
        from src.services.writing_pipeline.data_sources.topvisor_client import TopvisorClient
        return TopvisorClient(
            user_id=user_id,
            access_token=token,
            project_id=project_id,
        )
    return None


def _render(request: Request, db: Session, template: str, ctx: dict):
    """Render template with blog context injected."""
    blog_id = request.session.get("blog_id")
    ctx["current_blog"] = db.query(models.Blog).get(blog_id) if blog_id else None
    ctx["all_blogs"] = db.query(models.Blog).filter(models.Blog.status == "active").all()
    ctx["request"] = request
    return templates.TemplateResponse(template, ctx)


# ============ Blog Pages ============

@router.get("/blogs", response_class=HTMLResponse)
async def list_blogs(request: Request, db: Session = Depends(get_db)):
    """List all blogs. Auto-select on first visit if only 1 blog."""
    active_blogs = db.query(models.Blog).filter(models.Blog.status == "active").all()

    # Auto-select only on first visit (no blog selected yet)
    if len(active_blogs) == 1 and not request.session.get("blog_id"):
        request.session["blog_id"] = str(active_blogs[0].id)
        return RedirectResponse(url="/ui/articles", status_code=302)

    blogs = []
    for blog in active_blogs:
        site_ids = get_blog_site_ids(db, blog.id)
        article_count = db.query(models.Draft).filter(
            models.Draft.site_id.in_(site_ids)
        ).count() if site_ids else 0

        blogs.append({
            "id": blog.id,
            "name": blog.name,
            "slug": blog.slug,
            "domain": blog.domain,
            "status": blog.status,
            "site_count": len(site_ids),
            "article_count": article_count,
        })

    return _render(request, db, "blogs/list.html", {
        "blogs": blogs,
        "error": request.query_params.get("error"),
    })


@router.get("/blogs/{blog_id}/select")
async def select_blog(request: Request, blog_id: UUID, db: Session = Depends(get_db)):
    """Set active blog in session."""
    blog = db.query(models.Blog).filter(models.Blog.id == blog_id).first()
    if not blog:
        return RedirectResponse(url="/ui/blogs?error=Blog not found", status_code=302)
    request.session["blog_id"] = str(blog.id)
    return RedirectResponse(url="/ui/articles", status_code=302)


@router.get("/blogs/new", response_class=HTMLResponse)
async def new_blog_form(request: Request, db: Session = Depends(get_db)):
    """Show form to create a new blog."""
    return _render(request, db, "blogs/create.html", {})


@router.post("/blogs/new", response_class=HTMLResponse)
async def create_blog(
    request: Request,
    name: str = Form(...),
    slug: str = Form(...),
    domain: str = Form(""),
    ghost_url: str = Form(...),
    ghost_admin_key: str = Form(...),
    anthropic_api_key: str = Form(""),
    anthropic_proxy_url: str = Form(""),
    anthropic_proxy_secret: str = Form(""),
    serper_api_key: str = Form(""),
    jina_api_key: str = Form(""),
    yandex_wordstat_api_key: str = Form(""),
    yandex_cloud_folder_id: str = Form(""),
    rush_analytics_api_key: str = Form(""),
    topvisor_user_id: str = Form(""),
    topvisor_access_token: str = Form(""),
    topvisor_project_id: int = Form(0),
    openai_api_key: str = Form(""),
    openai_proxy_url: str = Form(""),
    residential_proxy_url: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a new blog."""
    # Strip /ghost suffix from ghost_url (common mistake)
    ghost_url = ghost_url.strip().rstrip("/")
    if ghost_url.endswith("/ghost"):
        ghost_url = ghost_url[:-6]

    # Validate slug uniqueness
    existing = db.query(models.Blog).filter(models.Blog.slug == slug.strip().lower()).first()
    if existing:
        return _render(request, db, "blogs/create.html", {
            "error": f"Slug '{slug}' already exists",
            "name": name, "slug": slug, "domain": domain,
            "ghost_url": ghost_url, "ghost_admin_key": ghost_admin_key,
        })

    blog = models.Blog(
        name=name.strip(),
        slug=slug.strip().lower(),
        domain=domain.strip() or None,
        ghost_url=ghost_url,
        ghost_admin_key=ghost_admin_key.strip(),
        anthropic_api_key=anthropic_api_key.strip() or None,
        anthropic_proxy_url=anthropic_proxy_url.strip() or None,
        anthropic_proxy_secret=anthropic_proxy_secret.strip() or None,
        serper_api_key=serper_api_key.strip() or None,
        jina_api_key=jina_api_key.strip() or None,
        yandex_wordstat_api_key=yandex_wordstat_api_key.strip() or None,
        yandex_cloud_folder_id=yandex_cloud_folder_id.strip() or None,
        rush_analytics_api_key=rush_analytics_api_key.strip() or None,
        topvisor_user_id=topvisor_user_id.strip() or None,
        topvisor_access_token=topvisor_access_token.strip() or None,
        topvisor_project_id=topvisor_project_id or None,
        openai_api_key=openai_api_key.strip() or None,
        openai_proxy_url=openai_proxy_url.strip() or None,
        residential_proxy_url=residential_proxy_url.strip() or None,
    )
    db.add(blog)
    db.commit()
    db.refresh(blog)

    # Auto-select the new blog
    request.session["blog_id"] = str(blog.id)
    return RedirectResponse(url="/ui/blogs", status_code=303)


@router.get("/blogs/{blog_id}/edit", response_class=HTMLResponse)
async def edit_blog_form(request: Request, blog_id: UUID, db: Session = Depends(get_db)):
    """Show edit form for a blog."""
    blog = db.query(models.Blog).filter(models.Blog.id == blog_id).first()
    if not blog:
        return RedirectResponse(url="/ui/blogs?error=Blog not found", status_code=302)
    return _render(request, db, "blogs/edit.html", {"blog": blog})


@router.post("/blogs/{blog_id}/edit", response_class=HTMLResponse)
async def update_blog(
    request: Request,
    blog_id: UUID,
    name: str = Form(...),
    slug: str = Form(...),
    domain: str = Form(""),
    ghost_url: str = Form(...),
    ghost_admin_key: str = Form(...),
    status: str = Form("active"),
    anthropic_api_key: str = Form(""),
    anthropic_proxy_url: str = Form(""),
    anthropic_proxy_secret: str = Form(""),
    serper_api_key: str = Form(""),
    jina_api_key: str = Form(""),
    yandex_wordstat_api_key: str = Form(""),
    yandex_cloud_folder_id: str = Form(""),
    rush_analytics_api_key: str = Form(""),
    topvisor_user_id: str = Form(""),
    topvisor_access_token: str = Form(""),
    topvisor_project_id: int = Form(0),
    openai_api_key: str = Form(""),
    openai_proxy_url: str = Form(""),
    residential_proxy_url: str = Form(""),
    db: Session = Depends(get_db),
):
    """Update a blog."""
    blog = db.query(models.Blog).filter(models.Blog.id == blog_id).first()
    if not blog:
        return RedirectResponse(url="/ui/blogs?error=Blog not found", status_code=302)

    # Strip /ghost suffix from ghost_url (common mistake)
    ghost_url = ghost_url.strip().rstrip("/")
    if ghost_url.endswith("/ghost"):
        ghost_url = ghost_url[:-6]

    # Validate slug uniqueness (excluding current)
    existing = db.query(models.Blog).filter(
        models.Blog.slug == slug.strip().lower(),
        models.Blog.id != blog_id,
    ).first()
    if existing:
        return _render(request, db, "blogs/edit.html", {
            "blog": blog,
            "error": f"Slug '{slug}' already exists",
        })

    blog.name = name.strip()
    blog.slug = slug.strip().lower()
    blog.domain = domain.strip() or None
    blog.ghost_url = ghost_url
    blog.ghost_admin_key = ghost_admin_key.strip()
    blog.status = status
    blog.anthropic_api_key = anthropic_api_key.strip() or None
    blog.anthropic_proxy_url = anthropic_proxy_url.strip() or None
    blog.anthropic_proxy_secret = anthropic_proxy_secret.strip() or None
    blog.serper_api_key = serper_api_key.strip() or None
    blog.jina_api_key = jina_api_key.strip() or None
    blog.yandex_wordstat_api_key = yandex_wordstat_api_key.strip() or None
    blog.yandex_cloud_folder_id = yandex_cloud_folder_id.strip() or None
    blog.rush_analytics_api_key = rush_analytics_api_key.strip() or None
    blog.topvisor_user_id = topvisor_user_id.strip() or None
    blog.topvisor_access_token = topvisor_access_token.strip() or None
    blog.topvisor_project_id = topvisor_project_id or None
    blog.openai_api_key = openai_api_key.strip() or None
    blog.openai_proxy_url = openai_proxy_url.strip() or None
    blog.residential_proxy_url = residential_proxy_url.strip() or None
    db.commit()

    return RedirectResponse(url="/ui/blogs", status_code=303)


# ============ Topics Pages ============

@router.get("/topics", response_class=HTMLResponse)
async def list_topics(request: Request, db: Session = Depends(get_db)):
    """List all topics (sites) for current blog."""
    current_blog = get_current_blog(request, db)
    query = db.query(models.Site).order_by(models.Site.created_at.desc())
    if current_blog:
        query = query.filter(models.Site.blog_id == current_blog.id)
    sites = query.all()

    topics = []
    for site in sites:
        cluster_count = db.query(models.Cluster).filter(models.Cluster.site_id == site.id).count()
        article_count = db.query(models.Draft).filter(models.Draft.site_id == site.id).count()

        topics.append({
            "id": site.id,
            "name": site.name,
            "domain": site.domain,
            "status": site.status,
            "language": site.language,
            "country": site.country,
            "created_at": site.created_at,
            "cluster_count": cluster_count,
            "article_count": article_count,
        })

    return _render(request, db, "topics/list.html", {
        "topics": topics,
    })


@router.get("/topics/new", response_class=HTMLResponse)
async def new_topic_form(request: Request, db: Session = Depends(get_db)):
    """Show form to create a new topic."""
    return _render(request, db, "topics/create.html", {})


@router.post("/topics/new", response_class=HTMLResponse)
async def create_topic(
    request: Request,
    niche: str = Form(...),
    domain: str = Form(""),
    country: str = Form("ru"),
    language: str = Form("ru"),
    db: Session = Depends(get_db),
):
    """Create a new topic by running Discovery Agent."""
    settings = get_settings()
    current_blog = get_current_blog(request, db)
    bs = resolve_blog_settings(current_blog, settings)

    if not bs["serper_api_key"]:
        return _render(request, db, "topics/create.html", {
            "error": "SERPER_API_KEY not configured",
            "niche": niche, "domain": domain, "country": country, "language": language,
        })

    if not bs["anthropic_api_key"]:
        return _render(request, db, "topics/create.html", {
            "error": "ANTHROPIC_API_KEY not configured",
            "niche": niche, "domain": domain, "country": country, "language": language,
        })

    try:
        # Run Discovery Agent
        discovery = DiscoveryAgent(
            serper_api_key=bs["serper_api_key"],
            anthropic_api_key=bs["anthropic_api_key"],
            proxy_url=bs["anthropic_proxy_url"] or None,
            proxy_secret=bs["anthropic_proxy_secret"] or None,
        )

        result = await discovery.discover(
            niche=niche,
            country=country,
            language=language,
        )

        # Create Site
        current_blog = get_current_blog(request, db)
        site = models.Site(
            blog_id=current_blog.id if current_blog else None,
            name=niche,
            domain=domain.strip() if domain.strip() else None,
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
        return _render(request, db, "topics/create.html", {
            "error": str(e),
            "niche": niche, "domain": domain, "country": country, "language": language,
        })


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
async def topic_detail(
    request: Request,
    topic_id: UUID,
    db: Session = Depends(get_db),
):
    """Show topic details with clusters."""
    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    # Get clusters for this topic (top-level only)
    clusters = db.query(models.Cluster).filter(
        models.Cluster.site_id == topic_id,
        models.Cluster.parent_cluster_id.is_(None),
    ).order_by(models.Cluster.created_at.desc()).all()

    # Split clusters into discovered / planned
    discovered_clusters = [c for c in clusters if c.status == "discovered"]
    planned_clusters = [c for c in clusters if c.status != "discovered"]

    # Enrich planned clusters with brief counts
    for cluster in planned_clusters:
        child_ids = [c.id for c in cluster.children] if cluster.children else []
        all_ids = [cluster.id] + child_ids
        cluster.brief_count = db.query(models.Brief).filter(
            models.Brief.cluster_id.in_(all_ids),
        ).count()

    # Count articles for this topic
    article_count = db.query(models.Draft).filter(
        models.Draft.site_id == topic_id,
    ).count()

    # Total traffic across clusters
    total_traffic = sum(c.estimated_traffic or 0 for c in clusters)

    # Knowledge Base folders
    current_blog = get_current_blog(request, db)
    all_folders = get_blog_kb_folders(db, current_blog)
    attached_folder_ids = {str(f.id) for f in topic.knowledge_folders}

    # Load analysis data: competitors + seed keywords count
    competitors = db.query(models.Competitor).filter(
        models.Competitor.site_id == topic_id,
    ).all()
    seed_keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == topic_id,
    ).order_by(models.Keyword.search_volume.desc().nullslast()).all()
    seed_keyword_count = len(seed_keywords)

    return _render(request, db, "topics/detail.html", {
        "topic": topic,
        "clusters": planned_clusters,
        "discovered_clusters": discovered_clusters,
        "article_count": article_count,
        "total_traffic": total_traffic,
        "all_folders": all_folders,
        "attached_folder_ids": attached_folder_ids,
        "competitors": competitors,
        "seed_keywords": seed_keywords,
        "seed_keyword_count": seed_keyword_count,
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
    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404)
    _check_blog_ownership(request, db, site=topic)

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
    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404)
    _check_blog_ownership(request, db, site=topic)

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


@router.post("/topics/{topic_id}/keywords/fetch-volume", response_class=HTMLResponse)
async def fetch_keyword_volume(
    request: Request,
    topic_id: UUID,
    db: Session = Depends(get_db),
):
    """Fetch search volume via best available provider (Wordstat + Rush)."""
    settings = get_settings()

    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    blog = _resolve_blog(request, db, site=topic)
    bs = resolve_blog_settings(blog, settings)

    keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == topic_id,
    ).all()

    if not keywords:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Нет ключевых слов",
            status_code=303,
        )

    try:
        region = topic.country or "ru"
        provider = _make_volume_provider(bs, region)

        if provider.source_name == "none":
            return RedirectResponse(
                url=f"/ui/topics/{topic_id}?error=No volume provider configured (set YANDEX_WORDSTAT_API_KEY or RUSH_ANALYTICS_API_KEY)",
                status_code=303,
            )

        keyword_texts = [kw.keyword for kw in keywords]
        language_code = topic.language or "ru"

        results = await provider.get_volumes(keyword_texts, language_code=language_code)

        # Build lookup by lowercase keyword
        metrics_map = {vr.keyword.lower(): vr for vr in results}

        updated = 0
        for kw in keywords:
            vr = metrics_map.get(kw.keyword.lower())
            if vr:
                kw.search_volume = vr.volume
                kw.difficulty = vr.difficulty
                kw.cpc = vr.cpc
                updated += 1

        db.commit()

        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?success=Обновлено {updated} keywords (источник: {provider.source_name})",
            status_code=303,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/topics/{topic_id}/keywords/expand", response_class=HTMLResponse)
async def expand_keywords(
    request: Request,
    topic_id: UUID,
    db: Session = Depends(get_db),
):
    """Expand seed keywords using Serper + VolumeProvider suggestions."""
    settings = get_settings()

    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    blog = _resolve_blog(request, db, site=topic)
    bs = resolve_blog_settings(blog, settings)

    all_keywords = db.query(models.Keyword).filter(
        models.Keyword.site_id == topic_id,
    ).all()

    if not all_keywords:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Нет ключевых слов для расширения",
            status_code=303,
        )

    if not bs["serper_api_key"]:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=SERPER_API_KEY not configured (needed for keyword discovery)",
            status_code=303,
        )

    try:
        from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider

        language_code = topic.language or "ru"
        region = topic.country or "ru"

        # Build existing keyword set for dedup
        existing_kw_set = {kw.keyword.lower().strip() for kw in all_keywords}

        # Select seeds: up to 20, prefer "new" status
        new_first = sorted(all_keywords, key=lambda k: (0 if k.status == "new" else 1, k.keyword))
        seeds = [s.keyword for s in new_first[:20]]

        # Step 1: Discover keywords via Serper.dev (related searches + PAA + autocomplete)
        import httpx, asyncio as _asyncio
        discovered_keywords = set()
        gl = "ru" if region.lower() in ["ru", "россия", "russia"] else "us"
        hl = "ru" if region.lower() in ["ru", "россия", "russia"] else "en"
        semaphore = _asyncio.Semaphore(5)

        async def search_serper(query):
            async with semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://google.serper.dev/search",
                        headers={"X-API-KEY": bs["serper_api_key"], "Content-Type": "application/json"},
                        json={"q": query, "gl": gl, "hl": hl, "num": 10},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    return resp.json()

        async def autocomplete_serper(query):
            async with semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://google.serper.dev/autocomplete",
                        headers={"X-API-KEY": bs["serper_api_key"], "Content-Type": "application/json"},
                        json={"q": query, "gl": gl, "hl": hl},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    return resp.json()

        tasks = []
        for seed in seeds:
            tasks.append(("search", search_serper(seed)))
            tasks.append(("autocomplete", autocomplete_serper(seed)))

        coros = [t[1] for t in tasks]
        results = await _asyncio.gather(*coros, return_exceptions=True)

        for i, result in enumerate(results):
            task_type = tasks[i][0]
            if isinstance(result, Exception):
                continue
            if task_type == "search":
                for item in result.get("relatedSearches", []):
                    q = item.get("query", "").strip()
                    if q:
                        discovered_keywords.add(q)
                for item in result.get("peopleAlsoAsk", []):
                    q = item.get("question", "").strip()
                    if q:
                        discovered_keywords.add(q)
            elif task_type == "autocomplete":
                for item in result.get("suggestions", []):
                    q = item.get("value", "").strip()
                    if q:
                        discovered_keywords.add(q)

        # Step 1b: Get provider suggestions (Wordstat related queries)
        provider = _make_volume_provider(bs, region)
        if provider.source_name != "none":
            for seed in seeds[:5]:
                try:
                    suggestions = await provider.get_suggestions(seed)
                    discovered_keywords.update(suggestions)
                except Exception:
                    pass

        # Step 2: Fetch volumes via best available provider
        new_keywords = [kw for kw in discovered_keywords if kw.lower().strip() not in existing_kw_set]

        volume_map = {}
        if new_keywords and provider.source_name != "none":
            vol_results = await provider.get_volumes(new_keywords, language_code=language_code)
            for vr in vol_results:
                volume_map[vr.keyword.lower().strip()] = vr

        # Save new keywords to DB
        added = 0
        for kw_text in new_keywords:
            key = kw_text.lower().strip()
            vr = volume_map.get(key)
            keyword = models.Keyword(
                site_id=topic.id,
                keyword=kw_text,
                search_volume=vr.volume if vr else 0,
                difficulty=vr.difficulty if vr else 0,
                cpc=vr.cpc if vr else 0,
                status="new",
            )
            db.add(keyword)
            added += 1

        db.commit()

        msg = f"Добавлено {added} новых keywords (из {len(seeds)} seed, источник: {provider.source_name})"

        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?success={msg}",
            status_code=303,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)}",
            status_code=303,
        )


@router.post("/topics/{topic_id}/generate-articles", response_class=HTMLResponse)
async def generate_articles_for_topic(
    request: Request,
    topic_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Generate articles via Writing Pipeline for all selected keywords."""
    settings = get_settings()

    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    blog = _resolve_blog(request, db, site=topic)
    bs = resolve_blog_settings(blog, settings)

    if not bs["anthropic_api_key"]:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=ANTHROPIC_API_KEY not configured",
            status_code=303,
        )

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
        count = 0
        region = topic.language or "ru"

        blog_ghost_url = bs["ghost_url"]
        blog_ghost_admin_key = bs["ghost_admin_key"]

        # Load KB docs from attached folders
        kb_docs = []
        for folder in topic.knowledge_folders:
            for doc in folder.documents:
                if doc.content_text:
                    kb_docs.append({
                        "id": str(doc.id),
                        "title": doc.original_filename,
                        "content_text": doc.content_text,
                        "word_count": doc.word_count or 0,
                    })

        for keyword in keywords:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_topic = "".join(c if c.isalnum() else "_" for c in keyword.keyword[:30])
            output_dir = f"/tmp/pipeline_output/{timestamp}_{safe_topic}"

            # Create draft with site_id and keyword_id
            draft = models.Draft(
                title=keyword.keyword,
                topic=keyword.keyword,
                site_id=topic.id,
                keyword_id=keyword.id,
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
                    "linking": "pending",
                    "seo_polish": "pending",
                    "quality_gate": "pending",
                    "meta": "pending",
                    "formatting": "pending",
                },
            )
            db.add(draft)
            db.flush()

            # Update keyword status
            keyword.status = "writing"

            # Start background pipeline
            background_tasks.add_task(
                run_pipeline_sync,
                str(draft.id),
                keyword.keyword,
                region,
                output_dir,
                kb_docs,
                ghost_url=blog_ghost_url,
                ghost_admin_key=blog_ghost_admin_key,
            )
            count += 1

        db.commit()

        return RedirectResponse(
            url=f"/ui/articles?site_id={topic_id}&success=Запущена генерация {count} статей",
            status_code=303,
        )

    except Exception as e:
        db.rollback()
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)}",
            status_code=303,
        )


# ============ Knowledge Base (Фактура) Pages ============

@router.get("/kb", response_class=HTMLResponse)
async def kb_list(request: Request, db: Session = Depends(get_db)):
    """List KB folders scoped by blog."""
    current_blog = get_current_blog(request, db)
    query = db.query(models.KnowledgeFolder)
    if current_blog:
        query = query.filter(models.KnowledgeFolder.blog_id == current_blog.id)
    folders = query.order_by(models.KnowledgeFolder.created_at.desc()).all()

    folder_data = []
    for folder in folders:
        doc_count = len(folder.documents)
        site_count = len(folder.sites)
        folder_data.append({
            "id": folder.id,
            "name": folder.name,
            "description": folder.description,
            "doc_count": doc_count,
            "site_count": site_count,
            "created_at": folder.created_at,
        })

    return _render(request, db, "kb/list.html", {
        "folders": folder_data,
    })


@router.get("/kb/new", response_class=HTMLResponse)
async def kb_new_form(request: Request, db: Session = Depends(get_db)):
    """Show create folder form (reuse detail template with empty state)."""
    return _render(request, db, "kb/create.html", {})


@router.post("/kb/new", response_class=HTMLResponse)
async def kb_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    """Create a new KB folder."""
    current_blog = get_current_blog(request, db)
    folder = models.KnowledgeFolder(
        blog_id=current_blog.id if current_blog else None,
        name=name.strip(),
        description=description.strip() or None,
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return RedirectResponse(url=f"/ui/kb/{folder.id}", status_code=303)


@router.get("/kb/{folder_id}", response_class=HTMLResponse)
async def kb_detail(
    request: Request,
    folder_id: UUID,
    db: Session = Depends(get_db),
):
    """Show folder detail with documents."""
    folder = db.query(models.KnowledgeFolder).filter(models.KnowledgeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    return _render(request, db, "kb/detail.html", {
        "folder": folder,
        "documents": folder.documents,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


ALLOWED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}
MIME_MAP = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


@router.post("/kb/{folder_id}/upload", response_class=HTMLResponse)
async def kb_upload(
    request: Request,
    folder_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a document to a KB folder."""
    import os
    from src.services.text_extractor import extract_text

    folder = db.query(models.KnowledgeFolder).filter(models.KnowledgeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return RedirectResponse(
            url=f"/ui/kb/{folder_id}?error=Недопустимый формат файла: {ext}",
            status_code=303,
        )

    # Read file content
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        return RedirectResponse(
            url=f"/ui/kb/{folder_id}?error=Файл слишком большой (макс. 50 МБ)",
            status_code=303,
        )

    # Save to disk
    settings = get_settings()
    upload_dir = os.path.join(settings.upload_dir, str(folder_id))
    os.makedirs(upload_dir, exist_ok=True)

    safe_filename = f"{uuid_lib.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, safe_filename)

    with open(file_path, "wb") as f:
        f.write(content)

    # Extract text
    mime_type = MIME_MAP.get(ext, "application/octet-stream")
    try:
        text, word_count = extract_text(file_path, mime_type)
    except Exception as e:
        # Clean up file on extraction failure
        os.remove(file_path)
        return RedirectResponse(
            url=f"/ui/kb/{folder_id}?error=Ошибка извлечения текста: {e}",
            status_code=303,
        )

    # Create DB record
    doc = models.KnowledgeDocument(
        folder_id=folder_id,
        filename=safe_filename,
        original_filename=file.filename or safe_filename,
        file_path=file_path,
        file_size=len(content),
        mime_type=mime_type,
        content_text=text,
        word_count=word_count,
    )
    db.add(doc)
    db.commit()

    return RedirectResponse(
        url=f"/ui/kb/{folder_id}?success=Загружен: {file.filename} ({word_count} слов)",
        status_code=303,
    )


@router.post("/kb/{folder_id}/upload-ajax")
async def kb_upload_ajax(
    request: Request,
    folder_id: UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """AJAX single-file upload for drag-and-drop UI."""
    from src.services.text_extractor import extract_text

    folder = db.query(models.KnowledgeFolder).filter(models.KnowledgeFolder.id == folder_id).first()
    if not folder:
        return JSONResponse({"ok": False, "error": "Папка не найдена"}, status_code=404)

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"ok": False, "error": f"Недопустимый формат: {ext}"})

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        return JSONResponse({"ok": False, "error": "Файл слишком большой (макс. 50 МБ)"})

    settings = get_settings()
    upload_dir = os.path.join(settings.upload_dir, str(folder_id))
    os.makedirs(upload_dir, exist_ok=True)

    safe_filename = f"{uuid_lib.uuid4().hex}{ext}"
    file_path = os.path.join(upload_dir, safe_filename)

    with open(file_path, "wb") as f:
        f.write(content)

    mime_type = MIME_MAP.get(ext, "application/octet-stream")
    try:
        text, word_count = extract_text(file_path, mime_type)
    except Exception as e:
        os.remove(file_path)
        return JSONResponse({"ok": False, "error": f"Ошибка извлечения текста: {e}"})

    doc = models.KnowledgeDocument(
        folder_id=folder_id,
        filename=safe_filename,
        original_filename=file.filename or safe_filename,
        file_path=file_path,
        file_size=len(content),
        mime_type=mime_type,
        content_text=text,
        word_count=word_count,
    )
    db.add(doc)
    db.commit()

    return JSONResponse({
        "ok": True,
        "filename": file.filename,
        "word_count": word_count,
        "doc_id": str(doc.id),
        "file_size": len(content),
    })


@router.post("/kb/{folder_id}/documents/{doc_id}/delete", response_class=HTMLResponse)
async def kb_delete_document(
    request: Request,
    folder_id: UUID,
    doc_id: UUID,
    db: Session = Depends(get_db),
):
    """Delete a document from a KB folder."""
    import os

    doc = db.query(models.KnowledgeDocument).filter(
        models.KnowledgeDocument.id == doc_id,
        models.KnowledgeDocument.folder_id == folder_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove file from disk
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    db.delete(doc)
    db.commit()

    return RedirectResponse(
        url=f"/ui/kb/{folder_id}?success=Документ удалён",
        status_code=303,
    )


@router.post("/kb/{folder_id}/delete", response_class=HTMLResponse)
async def kb_delete_folder(
    request: Request,
    folder_id: UUID,
    db: Session = Depends(get_db),
):
    """Delete a KB folder with all documents."""
    import os
    import shutil

    folder = db.query(models.KnowledgeFolder).filter(models.KnowledgeFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Remove files from disk
    settings = get_settings()
    upload_dir = os.path.join(settings.upload_dir, str(folder_id))
    if os.path.exists(upload_dir):
        shutil.rmtree(upload_dir)

    db.delete(folder)
    db.commit()

    return RedirectResponse(url="/ui/kb", status_code=303)


@router.post("/topics/{topic_id}/kb", response_class=HTMLResponse)
async def update_topic_kb(
    request: Request,
    topic_id: UUID,
    folder_ids: List[str] = Form(default=[]),
    db: Session = Depends(get_db),
):
    """Update M2M between topic and KB folders."""
    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    # Clear existing and re-add
    topic.knowledge_folders.clear()
    for fid in folder_ids:
        folder = db.query(models.KnowledgeFolder).filter(models.KnowledgeFolder.id == fid).first()
        if folder:
            topic.knowledge_folders.append(folder)

    db.commit()

    return RedirectResponse(
        url=f"/ui/topics/{topic_id}?success=Фактура обновлена",
        status_code=303,
    )


@router.post("/topics/{topic_id}/discover-clusters", response_class=HTMLResponse)
async def discover_clusters_for_topic(
    request: Request,
    topic_id: UUID,
    db: Session = Depends(get_db),
):
    """Discover potential clusters for a topic via Serper + LLM."""
    settings = get_settings()

    topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    _check_blog_ownership(request, db, site=topic)

    blog = _resolve_blog(request, db, site=topic)
    bs = resolve_blog_settings(blog, settings)

    if not bs["anthropic_api_key"] or not bs["serper_api_key"]:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=ANTHROPIC_API_KEY and SERPER_API_KEY required",
            status_code=303,
        )

    try:
        from src.services.cluster_planner import ClusterPlanner

        client = _make_anthropic_client(bs)
        region = topic.country or "ru"
        volume_provider = _make_volume_provider(bs, region)

        tv_client = _make_topvisor_client(bs)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key=bs["serper_api_key"],
            volume_provider=volume_provider,
            topvisor_client=tv_client,
            use_serp_clustering=bool(tv_client),
        )

        niche_boundaries = topic.niche_boundaries if topic.niche_boundaries else None

        discoveries = await planner.discover_clusters(
            big_topic=topic.name,
            region=region,
            target_clusters=5,
            niche_boundaries=niche_boundaries,
        )

        if not discoveries:
            return RedirectResponse(
                url=f"/ui/topics/{topic_id}?error=Не удалось найти подкластеры",
                status_code=303,
            )

        await planner.save_discovered_clusters(discoveries, str(topic.id), region, db)

        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?success=Найдено {len(discoveries)} подкластеров",
            status_code=303,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)[:200]}",
            status_code=303,
        )


@router.post("/clusters/{cluster_id}/plan", response_class=HTMLResponse)
async def plan_discovered_cluster(
    request: Request,
    cluster_id: UUID,
    db: Session = Depends(get_db),
):
    """Run full planning (keywords + briefs) for a discovered cluster."""
    settings = get_settings()

    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    _check_blog_ownership(request, db, cluster=cluster)

    blog = _resolve_blog(request, db, cluster=cluster)
    bs = resolve_blog_settings(blog, settings)

    topic_id = cluster.site_id
    if cluster.status != "discovered":
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=Кластер уже запланирован",
            status_code=303,
        )

    if not bs["anthropic_api_key"] or not bs["serper_api_key"]:
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error=ANTHROPIC_API_KEY and SERPER_API_KEY required",
            status_code=303,
        )

    try:
        from src.services.cluster_planner import ClusterPlanner, NicheContext

        client = _make_anthropic_client(bs)
        region = cluster.region or "ru"
        volume_provider = _make_volume_provider(bs, region)

        tv_client = _make_topvisor_client(bs)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key=bs["serper_api_key"],
            volume_provider=volume_provider,
            topvisor_client=tv_client,
            use_serp_clustering=bool(tv_client),
        )

        # Load KB docs from topic + build niche context
        kb_docs = []
        niche_ctx = None
        if topic_id:
            topic = db.query(models.Site).filter(models.Site.id == topic_id).first()
            if topic:
                for folder in topic.knowledge_folders:
                    for doc in folder.documents:
                        if doc.content_text:
                            kb_docs.append({
                                "id": str(doc.id),
                                "title": doc.original_filename,
                                "content_text": doc.content_text,
                                "word_count": doc.word_count or 0,
                            })
                # Build niche context
                boundaries = topic.niche_boundaries or {}
                niche_ctx = NicheContext(
                    site_name=topic.name,
                    cluster_description=cluster.description or "",
                    include_topics=boundaries.get("include", []),
                    exclude_topics=boundaries.get("exclude", []),
                    target_audience=boundaries.get("target_audience", ""),
                )

        plan = await planner.plan(
            big_topic=cluster.name,
            region=region,
            target_count=10,
            knowledge_base_docs=kb_docs if kb_docs else None,
            niche_context=niche_ctx,
        )

        if not plan.cluster_articles:
            return RedirectResponse(
                url=f"/ui/topics/{topic_id}?error=Планирование не удалось — 0 статей. Попробуйте расширить тему.",
                status_code=303,
            )

        await planner.save_plan_to_existing_cluster(plan, cluster, db)

        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}",
            status_code=303,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url=f"/ui/topics/{topic_id}?error={str(e)[:200]}",
            status_code=303,
        )


# ============ Briefs Pages ============

@router.get("/briefs", response_class=HTMLResponse)
async def list_briefs(request: Request, db: Session = Depends(get_db)):
    """List all briefs, scoped by blog."""
    current_blog = get_current_blog(request, db)
    blog_site_ids = get_blog_site_ids(db, current_blog.id) if current_blog else []
    query = db.query(models.Brief)
    if current_blog:
        if blog_site_ids:
            query = query.filter(models.Brief.site_id.in_(blog_site_ids))
        else:
            query = query.filter(False)
    briefs = query.order_by(models.Brief.created_at.desc()).all()
    return _render(request, db, "briefs/list.html", {
        "briefs": briefs,
    })


@router.get("/briefs/new", response_class=HTMLResponse)
async def new_brief_form(request: Request, db: Session = Depends(get_db)):
    """Show form to create a new brief."""
    return _render(request, db, "briefs/create.html", {})


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
    blog = get_current_blog(request, db)
    bs = resolve_blog_settings(blog, settings)

    if not bs["serper_api_key"]:
        return _render(request, db, "briefs/create.html", {
            "error": "SERPER_API_KEY not configured",
            "topic": topic, "country": country, "language": language,
        })

    if not bs["anthropic_api_key"]:
        return _render(request, db, "briefs/create.html", {
            "error": "ANTHROPIC_API_KEY not configured",
            "topic": topic, "country": country, "language": language,
        })

    try:
        generator = BriefGenerator(
            serper_api_key=bs["serper_api_key"],
            anthropic_api_key=bs["anthropic_api_key"],
            proxy_url=bs["anthropic_proxy_url"] or None,
            proxy_secret=bs["anthropic_proxy_secret"] or None,
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
        return _render(request, db, "briefs/create.html", {
            "error": str(e),
            "topic": topic, "country": country, "language": language,
        })


@router.get("/briefs/{brief_id}", response_class=HTMLResponse)
async def brief_detail(request: Request, brief_id: UUID, db: Session = Depends(get_db)):
    """Show brief details."""
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")
    _check_blog_ownership(request, db, brief=brief)

    # Get associated draft if exists
    draft = db.query(models.Draft).filter(models.Draft.brief_id == brief_id).first()

    return _render(request, db, "briefs/detail.html", {
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
    _check_blog_ownership(request, db, brief=brief)

    if brief.status == "draft":
        brief.status = "approved"
        brief.approved_at = datetime.utcnow()
        db.commit()

    return RedirectResponse(url=f"/ui/briefs/{brief_id}", status_code=303)


@router.post("/briefs/{brief_id}/generate-draft", response_class=HTMLResponse)
async def generate_draft(request: Request, brief_id: UUID, db: Session = Depends(get_db)):
    """Generate draft from brief."""
    settings = get_settings()
    blog = get_current_blog(request, db)
    bs = resolve_blog_settings(blog, settings)

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")
    _check_blog_ownership(request, db, brief=brief)

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
        api_key=bs["anthropic_api_key"],
        proxy_url=bs["anthropic_proxy_url"] or None,
        proxy_secret=bs["anthropic_proxy_secret"] or None,
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


# ============ Articles Pages ============

@router.get("/articles", response_class=HTMLResponse)
async def list_articles(
    request: Request,
    site_id: UUID = None,
    cluster_id: UUID = None,
    status: str = None,
    db: Session = Depends(get_db),
):
    """List all articles (drafts) with filters, scoped by current blog."""
    current_blog = get_current_blog(request, db)
    blog_site_ids = get_blog_site_ids(db, current_blog.id) if current_blog else []

    query = db.query(models.Draft)

    # Scope by blog
    if current_blog:
        if blog_site_ids:
            query = query.filter(models.Draft.site_id.in_(blog_site_ids))
        else:
            query = query.filter(False)

    if site_id:
        query = query.filter(models.Draft.site_id == site_id)
    if status:
        query = query.filter(models.Draft.status == status)
    if cluster_id:
        # Filter by cluster: find briefs in this cluster + children
        child_ids = [c.id for c in db.query(models.Cluster).filter(
            models.Cluster.parent_cluster_id == cluster_id,
        ).all()]
        all_cluster_ids = [cluster_id] + child_ids
        brief_ids = [b.id for b in db.query(models.Brief).filter(
            models.Brief.cluster_id.in_(all_cluster_ids),
        ).all()]
        query = query.filter(models.Draft.brief_id.in_(brief_ids))

    drafts = query.order_by(models.Draft.created_at.desc()).all()

    # Build brief→cluster mapping for display
    brief_ids = [d.brief_id for d in drafts if d.brief_id]
    brief_cluster_map = {}  # brief_id → cluster
    if brief_ids:
        briefs = db.query(models.Brief).filter(models.Brief.id.in_(brief_ids)).all()
        cluster_ids = list(set(b.cluster_id for b in briefs if b.cluster_id))
        clusters = {str(c.id): c for c in db.query(models.Cluster).filter(
            models.Cluster.id.in_(cluster_ids),
        ).all()} if cluster_ids else {}
        for b in briefs:
            if b.cluster_id:
                brief_cluster_map[str(b.id)] = clusters.get(str(b.cluster_id))

    # Get sites for filter dropdown (scoped by blog)
    sites_query = db.query(models.Site).order_by(models.Site.name)
    if current_blog:
        sites_query = sites_query.filter(models.Site.blog_id == current_blog.id)
    sites = sites_query.all()

    return _render(request, db, "drafts/list.html", {
        "drafts": drafts,
        "sites": sites,
        "brief_cluster_map": brief_cluster_map,
        "current_site_id": site_id,
        "current_cluster_id": cluster_id,
        "current_status": status,
        "success": request.query_params.get("success"),
    })


ALL_STAGE_DEFS = [
    ("intent", "Intent Analysis", "Определение интента и тональности"),
    ("research", "Research", "Сбор фактов и источников"),
    ("structure", "Structure", "Построение структуры статьи"),
    ("drafting", "Drafting", "Написание черновика"),
    ("editing", "Editing", "Редактирование и полировка"),
    ("linking", "Linking", "Внутренняя перелинковка"),
    ("seo_polish", "SEO Polish", "SEO-оптимизация"),
    ("quality_gate", "Quality Gate", "Проверка качества"),
    ("meta", "Meta", "Meta-теги и slug"),
    ("formatting", "Formatting", "Обложка и форматирование"),
]


@router.get("/articles/{draft_id}", response_class=HTMLResponse)
async def article_detail(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Show article (draft) details."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Article not found")
    _check_blog_ownership(request, db, draft=draft)

    # Load brief and cluster for breadcrumbs/context
    brief = None
    cluster = None
    if draft.brief_id:
        brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()
        if brief and brief.cluster_id:
            cluster = db.query(models.Cluster).filter(models.Cluster.id == brief.cluster_id).first()

    # Determine if pipeline is active (for auto-refresh)
    is_paused = draft.pipeline_status and draft.pipeline_status.startswith("paused")
    is_running = (
        draft.pipeline_status == "running" or draft.status in ("generating", "pipeline_running")
    ) and not is_paused

    return _render(request, db, "drafts/detail.html", {
        "draft": draft,
        "brief": brief,
        "cluster": cluster,
        "all_stages": ALL_STAGE_DEFS,
        "is_running": is_running,
        "is_paused": is_paused,
    })


@router.get("/articles/{draft_id}/status-fragment", response_class=HTMLResponse)
async def article_status_fragment(draft_id: UUID, db: Session = Depends(get_db)):
    """
    HTMX fragment: returns pipeline progress HTML.
    When pipeline finishes/pauses, returns HX-Redirect header to force full page reload.
    """
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        return HTMLResponse("")

    is_paused = draft.pipeline_status and draft.pipeline_status.startswith("paused")
    is_running = (
        draft.pipeline_status == "running" or draft.status in ("generating", "pipeline_running")
    ) and not is_paused

    # If no longer running — tell HTMX to do a full page reload
    if not is_running:
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = f"/ui/articles/{draft_id}"
        return response

    # Build stage progress HTML
    stages_html = []
    for key, name, desc in ALL_STAGE_DEFS:
        stage_status = (draft.pipeline_stages or {}).get(key, "pending")
        if stage_status == "completed":
            icon = '<span class="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-full bg-green-100"><svg class="w-3 h-3 text-green-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clip-rule="evenodd"/></svg></span>'
            label = f'<span class="text-sm text-gray-600">{name}</span>'
        elif stage_status == "running":
            icon = '<span class="flex-shrink-0 w-5 h-5 flex items-center justify-center"><svg class="animate-spin w-4 h-4 text-blue-600" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg></span>'
            label = f'<span class="text-sm font-medium text-blue-600">{name} — {desc}</span>'
        elif stage_status == "failed":
            icon = '<span class="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-full bg-red-100"><svg class="w-3 h-3 text-red-600" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"/></svg></span>'
            label = f'<span class="text-sm text-red-600">{name}</span>'
        else:
            icon = '<span class="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded-full bg-gray-100"><span class="w-1.5 h-1.5 rounded-full bg-gray-400"></span></span>'
            label = f'<span class="text-sm text-gray-400">{name}</span>'
        stages_html.append(f'<div class="flex items-center space-x-3">{icon}{label}</div>')

    # Pipeline progress bar
    bar_items = []
    for key, name, desc in ALL_STAGE_DEFS:
        stage_status = (draft.pipeline_stages or {}).get(key, "pending")
        if stage_status == "completed":
            css = "bg-green-500"
        elif stage_status == "running":
            css = "bg-blue-500 animate-pulse"
        elif stage_status == "failed":
            css = "bg-red-500"
        else:
            css = "bg-gray-200"
        bar_items.append(f'<div class="flex-1 group relative"><div class="h-2 rounded-full {css}"></div>'
                        f'<div class="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover:block z-10">'
                        f'<div class="bg-gray-900 text-white text-xs rounded py-1 px-2 whitespace-nowrap">{name}: {stage_status}</div></div></div>')

    progress_bar = f'''<div class="px-6 py-4 border-b border-gray-200 bg-gray-50">
        <h3 class="text-xs font-medium text-gray-500 uppercase mb-3">Pipeline</h3>
        <div class="flex items-center space-x-1">{"".join(bar_items)}</div>
        <div class="flex justify-between mt-1"><span class="text-xs text-gray-400">Intent</span><span class="text-xs text-gray-400">Formatting</span></div>
    </div>'''

    html = f'''<div id="pipeline-status" hx-get="/ui/articles/{draft_id}/status-fragment" hx-trigger="every 5s" hx-swap="outerHTML">
    {progress_bar}
    <div class="px-6 py-8">
        <div class="space-y-2 mb-6">{"".join(stages_html)}</div>
        <div class="text-center text-sm text-gray-500">Обновление каждые 5 сек...</div>
    </div>
</div>'''
    return HTMLResponse(html)


# Legacy redirects
@router.get("/drafts", response_class=HTMLResponse)
async def list_drafts_redirect(request: Request):
    """Redirect /ui/drafts to /ui/articles."""
    return RedirectResponse(url="/ui/articles", status_code=301)


@router.get("/drafts/{draft_id}", response_class=HTMLResponse)
async def draft_detail_redirect(request: Request, draft_id: UUID):
    """Redirect /ui/drafts/{id} to /ui/articles/{id}."""
    return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=301)


@router.post("/articles/{draft_id}/validate", response_class=HTMLResponse)
@router.post("/drafts/{draft_id}/validate", response_class=HTMLResponse)
async def validate_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Run validation pipeline on draft."""
    from src.services.validation_pipeline import ValidationPipeline

    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    _check_blog_ownership(request, db, draft=draft)

    if draft.status not in ("generated", "validated", "validation_failed", "pipeline_completed"):
        return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)

    try:
        pipeline = ValidationPipeline()
        await pipeline.run(draft.id)
    except Exception as e:
        # Error handling is done in pipeline
        pass

    return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)


@router.post("/articles/{draft_id}/approve", response_class=HTMLResponse)
@router.post("/drafts/{draft_id}/approve", response_class=HTMLResponse)
async def approve_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Approve a draft for publishing."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    _check_blog_ownership(request, db, draft=draft)

    # Allow approve from any post-generation status
    if draft.status in ("generated", "validated", "validation_failed", "pipeline_completed"):
        draft.status = "approved"
        db.commit()

    return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)


@router.post("/articles/{draft_id}/publish", response_class=HTMLResponse)
@router.post("/drafts/{draft_id}/publish", response_class=HTMLResponse)
async def publish_draft(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Publish draft to Ghost, register for internal linking, run backward linking."""
    from src.services.publisher import GhostPublisher
    settings = get_settings()

    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft or draft.status != "approved":
        return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)
    _check_blog_ownership(request, db, draft=draft)

    blog = _resolve_blog(request, db, draft=draft)
    bs = resolve_blog_settings(blog, settings)

    # Warn-only meta validation
    from src.services.validators.meta import validate_meta_before_publish
    meta_warnings = validate_meta_before_publish(draft)
    if meta_warnings:
        logger.warning("Pre-publish meta warnings for draft %s: %s", draft_id, "; ".join(meta_warnings))

    # Atomically mark as publishing to prevent double-submit
    draft.status = "publishing"
    db.commit()

    try:
        publisher = GhostPublisher(bs["ghost_url"], bs["ghost_admin_key"])
        result = publisher.publish(
            title=draft.title,
            content=draft.content_md,
            slug=draft.slug,
            meta_title=draft.meta_title,
            meta_description=draft.meta_description,
            status="published",
            feature_image=draft.cover_image_url or None,
            feature_image_alt=draft.cover_image_alt or None,
            og_title=draft.og_title,
            og_description=draft.og_description,
            custom_excerpt=draft.custom_excerpt,
        )

        if result["success"]:
            draft.status = "published"
            draft.cms_post_id = result["post"]["id"]

            # Create Post record
            post = models.Post(
                site_id=draft.site_id,
                draft_id=draft.id,
                title=draft.title,
                slug=draft.slug,
                url=result["post"].get("url", ""),
                cms_post_id=result["post"]["id"],
                status="live",
                published_at=datetime.utcnow(),
            )
            db.add(post)
            db.flush()

            # Link keyword → post for monitoring
            keyword_id = draft.keyword_id
            if not keyword_id and draft.brief_id:
                brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()
                if brief:
                    keyword_id = brief.keyword_id

            if keyword_id:
                keyword = db.query(models.Keyword).filter(models.Keyword.id == keyword_id).first()
                if keyword:
                    keyword.post_id = post.id
                    keyword.status = "targeted"

            db.commit()

            # Register article in internal linker DB + run backward linking
            try:
                from src.services.internal_linker import InternalLinker

                if bs["database_url"]:
                    linker = InternalLinker(bs["database_url"])
                    published_url = result["post"]["url"]

                    # Build keywords list: [(keyword, type)]
                    keywords = []
                    if draft.keywords:
                        for i, kw in enumerate(draft.keywords):
                            kw_type = "primary" if i == 0 else "secondary"
                            keywords.append((kw.lower().strip(), kw_type))
                    elif draft.topic:
                        keywords.append((draft.topic.lower().strip(), "primary"))

                    # Register article
                    linker.register_article(
                        post_url=published_url,
                        title=draft.title,
                        cms_post_id=result["post"]["id"],
                        content_md=draft.content_md,
                        keywords=keywords,
                        site_id=str(draft.site_id) if draft.site_id else None,
                    )

                    # Backward linking (update old articles)
                    if bs["anthropic_api_key"] and keywords:
                        client = _make_anthropic_client(bs)
                        await linker.update_backlinks(
                            new_url=published_url,
                            new_title=draft.title,
                            new_keywords=[kw for kw, _ in keywords],
                            llm_client=client,
                            model="claude-sonnet-4-20250514",
                            ghost_publisher=publisher,
                            site_id=str(draft.site_id) if draft.site_id else None,
                        )
            except Exception:
                pass  # Graceful degradation — publish succeeds even if linking fails

            # Ping IndexNow (Yandex + Bing)
            try:
                from src.services.indexnow import ping_indexnow
                published_url = result["post"].get("url", "")
                if published_url:
                    await ping_indexnow(published_url)
            except Exception:
                pass  # Graceful degradation

    except Exception:
        # Rollback to approved so user can retry
        draft.status = "approved"
        db.commit()

    return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)


# ============ Step-by-Step Pipeline Controls ============

def _resume_pipeline_for_draft(draft_id: str, settings, paused_at: str):
    """Background task: resume pipeline from paused state."""
    import asyncio

    async def _run():
        from src.db.session import SessionLocal
        from src.services.writing_pipeline.core.context import WritingContext

        db = SessionLocal()
        try:
            draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if not draft:
                return
            brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first() if draft.brief_id else None

            # Resolve all settings from Blog
            blog = draft.site.blog if (draft.site and draft.site.blog) else None
            bs = resolve_blog_settings(blog, settings)
            runner = _create_runner_from_bs(bs)

            # Rebuild config from brief
            config = {
                "expand_paa": True,
                "fetch_page_content": True,
                "max_pages_to_fetch": 5,
                "max_paa_queries": 3,
                "use_playwright": True,
            }
            if brief and brief.structure:
                structure = brief.structure if isinstance(brief.structure, dict) else {}
                config["brief"] = {
                    "title_candidate": brief.title,
                    "role": structure.get("role", "cluster"),
                    "primary_intent": structure.get("primary_intent", "informational"),
                    "topic_boundaries": structure.get("topic_boundaries", {}),
                    "must_answer_questions": structure.get("must_answer_questions", []),
                    "target_terms": [brief.target_keyword] + (brief.secondary_keywords or []),
                    "unique_angle": structure.get("unique_angle", {}),
                    "internal_links_plan": structure.get("internal_links_plan", []),
                    "seed_queries": structure.get("seed_queries", []),
                }

            # Load KB docs
            kb_docs = []
            if brief and brief.cluster_id:
                cluster = db.query(models.Cluster).filter(models.Cluster.id == brief.cluster_id).first()
                if cluster:
                    for folder in cluster.knowledge_folders:
                        for doc in folder.documents:
                            if doc.content_text:
                                kb_docs.append({
                                    "id": str(doc.id),
                                    "title": doc.original_filename,
                                    "content_text": doc.content_text,
                                    "word_count": doc.word_count or 0,
                                })
                    if cluster.factual_mode and cluster.factual_mode != "default":
                        config["factual_mode"] = cluster.factual_mode
            if kb_docs:
                config["knowledge_base_docs"] = kb_docs

            # Rebuild context from stage_results
            existing_posts = []
            if bs["ghost_url"] and bs["ghost_admin_key"]:
                try:
                    from src.services.publisher import GhostPublisher
                    publisher = GhostPublisher(ghost_url=bs["ghost_url"], admin_key=bs["ghost_admin_key"])
                    existing_posts = publisher.get_posts()
                except Exception:
                    pass

            context = WritingContext(
                topic=draft.topic or draft.title,
                region=config.get("region", "ru"),
                started_at=draft.pipeline_started_at or datetime.utcnow(),
                config=config,
                existing_posts=existing_posts,
            )

            # Replay completed stages from stage_results to rebuild context
            sr = draft.stage_results or {}
            # We need to re-run stages from paused_at onward, but context needs prior data
            # Run completed stages through runner to rebuild context
            found_paused = False
            stages_to_run = []

            for stage in runner.stages:
                if stage.name == paused_at:
                    found_paused = True
                    continue  # Skip the paused stage (already completed)
                if found_paused:
                    stages_to_run.append(stage)
                else:
                    # Re-run completed stages to rebuild context
                    # (This is needed because context isn't serializable as a whole)
                    # However, re-running is expensive, so instead skip stages
                    # that are already completed and use run_stage for the rest
                    pass

            # Actually, re-running all prior stages is too expensive.
            # Instead, we need to run only the remaining stages.
            # The context fields that downstream stages need:
            # - structure→drafting needs outline
            # - drafting→editing needs draft_md
            # - editing→linking needs edited_md
            # We can reconstruct these from stage_results.

            # Reconstruct what we can from stage_results
            if sr.get("intent"):
                try:
                    from src.services.writing_pipeline.contracts import IntentResult
                    context.intent = IntentResult.from_dict(sr["intent"]) if hasattr(IntentResult, 'from_dict') else None
                except Exception:
                    pass

            if sr.get("research"):
                try:
                    from src.services.writing_pipeline.contracts import ResearchResult
                    context.research = ResearchResult.from_dict(sr["research"]) if hasattr(ResearchResult, 'from_dict') else None
                except Exception:
                    pass

            if sr.get("structure"):
                try:
                    from src.services.writing_pipeline.contracts import OutlineResult
                    context.outline = OutlineResult.from_dict(sr["structure"]) if hasattr(OutlineResult, 'from_dict') else None
                except Exception:
                    pass

            if sr.get("drafting") and sr["drafting"].get("content_md"):
                context.draft_md = sr["drafting"]["content_md"]

            if sr.get("editing") and sr["editing"].get("content_md"):
                context.edited_md = sr["editing"]["content_md"]

            # Determine which stages still need to run
            completed = set()
            pipeline_stages = draft.pipeline_stages or {}
            for s in ALL_PIPELINE_STAGES:
                if pipeline_stages.get(s) == "completed":
                    completed.add(s)

            for stage in runner.stages:
                if stage.name in completed:
                    continue

                # Update DB: stage is running
                d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if d:
                    stages = dict(d.pipeline_stages or {})
                    stages[stage.name] = "running"
                    d.pipeline_stages = stages
                    db.commit()

                # Execute stage
                context = await stage.run(context)

                # Update DB: stage completed
                d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if d:
                    stages = dict(d.pipeline_stages or {})
                    stages[stage.name] = "completed"
                    d.pipeline_stages = stages

                    sr_dict = dict(d.stage_results or {})
                    sr_dict[stage.name] = _serialize_stage_result(context, stage.name)
                    d.stage_results = sr_dict
                    db.commit()

                # Check if we should pause again
                if draft.step_by_step and stage.name in PAUSE_STAGES:
                    d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                    if d:
                        d.pipeline_status = f"paused_at_{stage.name}"
                        db.commit()
                    return

            # All stages done
            d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if d:
                d.title = context.outline.title if context.outline else draft.title
                d.slug = context.meta.slug if context.meta else None
                d.content_md = context.edited_md
                d.word_count = len(context.edited_md.split()) if context.edited_md else 0
                d.meta_title = context.meta.meta_title if context.meta else None
                d.meta_description = context.meta.meta_description if context.meta else None
                d.og_title = context.meta.og_title if context.meta else None
                d.og_description = context.meta.og_description if context.meta else None
                d.custom_excerpt = context.meta.custom_excerpt if context.meta else None
                if context.formatting_result:
                    d.cover_image_url = getattr(context.formatting_result, 'cover_ghost_url', '') or ''
                    d.cover_image_alt = getattr(context.formatting_result, 'cover_image_alt', '') or ''
                d.status = "pipeline_completed"
                d.pipeline_status = "completed"
                d.pipeline_completed_at = datetime.utcnow()

                if brief:
                    brief.status = "completed"
                db.commit()

        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if d:
                    d.status = "pipeline_failed"
                    d.pipeline_status = "failed"
                    d.pipeline_error = str(e)[:1000]
                db.commit()
            except Exception:
                pass
        finally:
            db.close()

    asyncio.run(_run())


@router.post("/articles/{draft_id}/next-step", response_class=HTMLResponse)
async def article_next_step(
    request: Request,
    draft_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Resume pipeline from paused state, running until the next pause point or completion."""
    settings = get_settings()
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Article not found")
    _check_blog_ownership(request, db, draft=draft)

    if not draft.pipeline_status or not draft.pipeline_status.startswith("paused"):
        return RedirectResponse(
            url=f"/ui/articles/{draft_id}?error=Pipeline не на паузе",
            status_code=303,
        )

    # Capture paused stage before changing status
    paused_at_stage = draft.pipeline_status.replace("paused_at_", "")

    # Set running NOW so the redirected page shows HTMX polling
    draft.pipeline_status = "running"
    db.commit()

    background_tasks.add_task(
        _resume_pipeline_for_draft,
        str(draft_id),
        settings,
        paused_at_stage,
    )

    return RedirectResponse(
        url=f"/ui/articles/{draft_id}",
        status_code=303,
    )


@router.post("/articles/{draft_id}/edit-stage", response_class=HTMLResponse)
async def article_edit_stage(
    request: Request,
    draft_id: UUID,
    db: Session = Depends(get_db),
):
    """Save user edits to a pipeline stage result."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Article not found")
    _check_blog_ownership(request, db, draft=draft)

    form = await request.form()
    stage = form.get("stage", "")
    content_md = form.get("content_md", "")

    if not stage or stage not in ALL_PIPELINE_STAGES:
        return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)

    sr = dict(draft.stage_results or {})

    if stage in ("drafting", "editing") and content_md:
        sr[stage] = {"content_md": content_md[:50000]}
    else:
        # For other stages, store the raw form data as JSON
        try:
            import json
            data = json.loads(form.get("data", "{}"))
            sr[stage] = data
        except Exception:
            pass

    draft.stage_results = sr
    db.commit()

    return RedirectResponse(
        url=f"/ui/articles/{draft_id}?success=Этап обновлён",
        status_code=303,
    )


@router.post("/articles/{draft_id}/enrich-from-kb", response_class=HTMLResponse)
async def article_enrich_from_kb(
    request: Request,
    draft_id: UUID,
    db: Session = Depends(get_db),
):
    """Enrich research stage with additional facts from KB."""
    import anthropic as anthropic_module

    settings = get_settings()
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Article not found")
    _check_blog_ownership(request, db, draft=draft)

    # Load KB docs from brief's cluster
    kb_docs = []
    if draft.brief_id:
        brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()
        if brief and brief.cluster_id:
            cluster = db.query(models.Cluster).filter(models.Cluster.id == brief.cluster_id).first()
            if cluster:
                for folder in cluster.knowledge_folders:
                    for doc in folder.documents:
                        if doc.content_text:
                            kb_docs.append({
                                "id": str(doc.id),
                                "title": doc.original_filename,
                                "content_text": doc.content_text[:4000],
                            })
                # Also check site-level KB
                if cluster.site_id:
                    site = db.query(models.Site).filter(models.Site.id == cluster.site_id).first()
                    if site:
                        seen = {d["id"] for d in kb_docs}
                        for folder in site.knowledge_folders:
                            for doc in folder.documents:
                                if doc.content_text and str(doc.id) not in seen:
                                    kb_docs.append({
                                        "id": str(doc.id),
                                        "title": doc.original_filename,
                                        "content_text": doc.content_text[:4000],
                                    })

    if not kb_docs:
        return RedirectResponse(
            url=f"/ui/articles/{draft_id}?error=Нет KB-документов для обогащения",
            status_code=303,
        )

    # Get current research from stage_results
    sr = dict(draft.stage_results or {})
    current_research = sr.get("research", {})

    # Build prompt for LLM to extract additional facts from KB
    kb_text = "\n\n".join([f"## {d['title']}\n{d['content_text']}" for d in kb_docs[:10]])
    current_facts_json = json.dumps(current_research.get("facts", []), ensure_ascii=False)

    prompt = f"""Проанализируй материалы из базы знаний и извлеки дополнительные факты для статьи "{draft.title}".

## Текущие факты (уже есть):
{current_facts_json}

## Материалы из базы знаний:
{kb_text}

Извлеки НОВЫЕ факты, которых нет в текущем списке. Каждый факт должен быть конкретным и полезным для статьи.

Ответь JSON-массивом объектов:
[{{"text": "факт", "source": "название документа", "origin": "kb"}}]

Если новых фактов нет — верни пустой массив []."""

    try:
        blog = _resolve_blog(None, db, draft=draft)
        bs = resolve_blog_settings(blog, settings)
        client = _make_anthropic_client(bs)

        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        new_facts = json.loads(text)

        # Merge new facts into research
        existing_facts = current_research.get("facts", [])
        if isinstance(existing_facts, list):
            existing_facts.extend(new_facts)
        current_research["facts"] = existing_facts

        # Add KB sources
        existing_sources = current_research.get("sources", [])
        for doc in kb_docs:
            existing_sources.append({
                "title": doc["title"],
                "url": f"kb://{doc['id']}",
                "origin": "kb",
            })
        current_research["sources"] = existing_sources

        sr["research"] = current_research
        draft.stage_results = sr
        db.commit()

        return RedirectResponse(
            url=f"/ui/articles/{draft_id}?success=Добавлено {len(new_facts)} фактов из KB",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/ui/articles/{draft_id}?error=Ошибка обогащения: {str(e)[:200]}",
            status_code=303,
        )


# ============ Pipeline Pages ============

def run_pipeline_sync(draft_id: str, topic: str, region: str, output_dir: str, knowledge_base_docs: list = None, factual_mode: str = "default", ghost_url: str = None, ghost_admin_key: str = None, blog_settings: dict = None):
    """
    Run writing pipeline synchronously.
    Called from background task.
    """
    settings = get_settings()
    db = SessionLocal()

    # Use blog_settings if provided, else build from ghost_url/ghost_admin_key args + global settings
    bs = blog_settings or resolve_blog_settings(None, settings)
    if ghost_url and not blog_settings:
        bs["ghost_url"] = ghost_url
    if ghost_admin_key and not blog_settings:
        bs["ghost_admin_key"] = ghost_admin_key

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
            "linking": "pending",
            "seo_polish": "pending",
            "quality_gate": "pending",
            "meta": "pending",
            "formatting": "pending",
        }
        db.commit()

        # Initialize pipeline runner from resolved blog settings
        runner = _create_runner_from_bs(bs)

        # Stage progress callback — updates DB after each stage
        def on_stage_complete(stage_name: str, status: str):
            try:
                d = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
                if d and d.pipeline_stages:
                    stages = dict(d.pipeline_stages)
                    stages[stage_name] = status
                    d.pipeline_stages = stages
                    db.commit()
            except Exception:
                db.rollback()

        # Run pipeline (need to run async in sync context)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def run_with_progress():
            config = {}
            if knowledge_base_docs:
                config["knowledge_base_docs"] = knowledge_base_docs
            if factual_mode and factual_mode != "default":
                config["factual_mode"] = factual_mode
            result = await runner.run(
                topic=topic,
                region=region,
                output_dir=output_dir,
                save_intermediate=True,
                on_stage_complete=on_stage_complete,
                config=config,
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
            "linking": "completed",
            "seo_polish": "completed",
            "quality_gate": "completed",
            "meta": "completed",
            "formatting": "completed",
        }

        # Save SEO metadata from Meta stage
        if result.meta:
            draft.meta_title = result.meta.meta_title
            draft.meta_description = result.meta.meta_description
            draft.slug = result.meta.slug
            draft.og_title = result.meta.og_title
            draft.og_description = result.meta.og_description
            draft.custom_excerpt = result.meta.custom_excerpt

        # Save token usage
        draft.total_input_tokens = result.total_input_tokens
        draft.total_output_tokens = result.total_output_tokens
        draft.stage_tokens = result.stage_tokens

        # Save cover image URL from Formatting stage
        if result.cover_image_url:
            draft.cover_image_url = result.cover_image_url
            draft.cover_image_alt = result.cover_image_alt

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

        # Store linking keywords for post-publish registration
        if result.linking_data and result.linking_data.get("keywords"):
            draft.keywords = [kw for kw, _ in result.linking_data["keywords"]]

        db.commit()

    except Exception as e:
        # Update draft with error including full traceback
        try:
            draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if draft:
                draft.pipeline_status = "failed"
                draft.pipeline_error = traceback.format_exc()
                draft.status = "failed"
                draft.pipeline_completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            pass  # Don't mask the original error
    finally:
        db.close()


@router.get("/pipeline/new", response_class=HTMLResponse)
async def new_pipeline_form(request: Request, db: Session = Depends(get_db)):
    """Show form to start new article via pipeline."""
    current_blog = get_current_blog(request, db)
    folders = get_blog_kb_folders(db, current_blog)
    return _render(request, db, "pipeline/new.html", {
        "folders": folders,
    })


@router.post("/pipeline/new", response_class=HTMLResponse)
async def create_pipeline(
    request: Request,
    background_tasks: BackgroundTasks,
    topic: str = Form(...),
    region: str = Form("ru"),
    depth: str = Form("standard"),
    factual_mode: str = Form("default"),
    db: Session = Depends(get_db),
):
    """Start a new article generation via Writing Pipeline."""
    settings = get_settings()

    # Extract folder_ids from form (checkboxes send multiple values)
    form = await request.form()
    folder_ids = form.getlist("folder_ids")

    current_blog = get_current_blog(request, db)
    bs = resolve_blog_settings(current_blog, settings)
    if not bs["anthropic_api_key"]:
        folders = get_blog_kb_folders(db, current_blog)
        return _render(request, db, "pipeline/new.html", {
            "error": "ANTHROPIC_API_KEY not configured",
            "topic": topic,
            "region": region,
            "depth": depth,
            "folders": folders,
        })

    try:
        # Load KB docs from selected folders
        kb_docs = []
        if folder_ids:
            selected_folders = db.query(models.KnowledgeFolder).filter(
                models.KnowledgeFolder.id.in_(folder_ids),
            ).all()
            for folder in selected_folders:
                for doc in folder.documents:
                    if doc.content_text:
                        kb_docs.append({
                            "id": str(doc.id),
                            "title": doc.original_filename,
                            "content_text": doc.content_text,
                            "word_count": doc.word_count or 0,
                        })

        # Generate output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in topic[:30])
        output_dir = f"/tmp/pipeline_output/{timestamp}_{safe_topic}"

        # Resolve site for draft — use first site of current blog, create default if needed
        site_id_for_draft = None
        if current_blog:
            first_site = db.query(models.Site).filter(
                models.Site.blog_id == current_blog.id
            ).first()
            if not first_site:
                first_site = models.Site(
                    blog_id=current_blog.id,
                    name=current_blog.name,
                    domain=current_blog.domain,
                    status="active",
                )
                db.add(first_site)
                db.commit()
                db.refresh(first_site)
            site_id_for_draft = first_site.id

        # Create draft
        draft = models.Draft(
            title=topic,  # Will be updated after pipeline
            topic=topic,
            site_id=site_id_for_draft,
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
                "linking": "pending",
                "seo_polish": "pending",
                "quality_gate": "pending",
                "meta": "pending",
                "formatting": "pending",
            },
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        pipeline_ghost_url = bs["ghost_url"]
        pipeline_ghost_admin_key = bs["ghost_admin_key"]

        # Start background task
        background_tasks.add_task(
            run_pipeline_sync,
            str(draft.id),
            topic,
            region,
            output_dir,
            knowledge_base_docs=kb_docs if kb_docs else None,
            factual_mode=factual_mode,
            ghost_url=pipeline_ghost_url,
            ghost_admin_key=pipeline_ghost_admin_key,
        )

        return RedirectResponse(
            url=f"/ui/articles/{draft.id}",
            status_code=303,
        )

    except Exception as e:
        folders = get_blog_kb_folders(db, current_blog)
        return _render(request, db, "pipeline/new.html", {
            "error": str(e),
            "topic": topic,
            "region": region,
            "depth": depth,
            "folders": folders,
        })


# ============ Monitoring Pages ============

@router.get("/monitoring", response_class=HTMLResponse)
async def monitoring_dashboard(
    request: Request,
    site_id: UUID = None,
    db: Session = Depends(get_db),
):
    """Position monitoring dashboard."""
    current_blog = get_current_blog(request, db)
    sites_q = db.query(models.Site).filter(models.Site.status == "active")
    if current_blog:
        sites_q = sites_q.filter(models.Site.blog_id == current_blog.id)
    sites = sites_q.order_by(models.Site.created_at.desc()).all()

    site = None
    rankings = []
    summary = {"total_tracked": 0, "in_top_3": 0, "in_top_10": 0, "in_top_20": 0, "not_ranking": 0, "avg_position": None, "alerts": 0}

    if site_id:
        site = db.query(models.Site).filter(models.Site.id == site_id).first()
    elif sites:
        site = sites[0]

    if site:
        # Get tracked keywords with latest ranking info
        keywords = db.query(models.Keyword).filter(
            models.Keyword.site_id == site.id,
            models.Keyword.status.in_(["targeted", "achieved"]),
        ).order_by(models.Keyword.current_position.asc().nullslast()).all()

        for kw in keywords:
            # Get latest ranking
            latest = db.query(models.KeywordRanking).filter(
                models.KeywordRanking.keyword_id == kw.id,
            ).order_by(models.KeywordRanking.date.desc()).first()

            # Get 7-day-ago ranking for change calculation
            from datetime import timedelta, date as date_type
            week_ago = datetime.utcnow() - timedelta(days=7)
            old_ranking = db.query(models.KeywordRanking).filter(
                models.KeywordRanking.keyword_id == kw.id,
                models.KeywordRanking.date <= week_ago,
            ).order_by(models.KeywordRanking.date.desc()).first()

            change = None
            if latest and old_ranking and latest.position is not None and old_ranking.position is not None:
                change = latest.position - old_ranking.position  # positive = dropped

            # Get post title
            post_title = None
            if kw.post_id:
                post = db.query(models.Post).filter(models.Post.id == kw.post_id).first()
                if post:
                    post_title = post.title

            rankings.append({
                "keyword_id": str(kw.id),
                "keyword": kw.keyword,
                "position": kw.current_position,
                "change": change,
                "post_title": post_title,
                "last_checked": latest.checked_at if latest else None,
            })

        # Summary
        total = len(keywords)
        in_top_3 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 3)
        in_top_10 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 10)
        in_top_20 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 20)
        not_ranking = sum(1 for kw in keywords if kw.current_position is None)
        positions = [kw.current_position for kw in keywords if kw.current_position is not None]
        avg_pos = round(sum(positions) / len(positions), 1) if positions else None

        alert_count = db.query(models.IterationTask).filter(
            models.IterationTask.status == "pending",
            models.IterationTask.post_id.in_(
                db.query(models.Post.id).filter(models.Post.site_id == site.id)
            ),
        ).count()

        summary = {
            "total_tracked": total,
            "in_top_3": in_top_3,
            "in_top_10": in_top_10,
            "in_top_20": in_top_20,
            "not_ranking": not_ranking,
            "avg_position": avg_pos,
            "alerts": alert_count,
        }

    return _render(request, db, "monitoring/dashboard.html", {
        "sites": sites,
        "site": site,
        "rankings": rankings,
        "summary": summary,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/monitoring/check", response_class=HTMLResponse)
async def trigger_monitoring_check(
    request: Request,
    site_id: UUID = None,
    db: Session = Depends(get_db),
):
    """Manually trigger position check."""
    settings = get_settings()

    if not site_id:
        return RedirectResponse(url="/ui/monitoring?error=No site selected", status_code=303)

    site = db.query(models.Site).filter(models.Site.id == site_id).first()
    if not site or not site.domain:
        return RedirectResponse(
            url=f"/ui/monitoring?site_id={site_id}&error=Site not found or no domain configured",
            status_code=303,
        )

    blog = _resolve_blog(request, db, site=site)
    bs = resolve_blog_settings(blog, settings)

    if not bs["serper_api_key"]:
        return RedirectResponse(
            url=f"/ui/monitoring?site_id={site_id}&error=SERPER_API_KEY not configured",
            status_code=303,
        )

    try:
        tracker = PositionTracker(
            db_session_factory=SessionLocal,
            serper_api_key=bs["serper_api_key"],
        )

        summary = await tracker.run_daily_check(site_id)
        signals = await tracker.detect_decay(site_id)

        msg = f"Checked {summary.get('checked', 0)} keywords"
        if summary.get('found'):
            msg += f", {summary['found']} found in SERP"
        if summary.get('errors'):
            msg += f", {summary['errors']} errors"
        if signals:
            msg += f", {len(signals)} decay signals detected"

        return RedirectResponse(
            url=f"/ui/monitoring?site_id={site_id}&success={msg}",
            status_code=303,
        )

    except Exception as e:
        return RedirectResponse(
            url=f"/ui/monitoring?site_id={site_id}&error={str(e)}",
            status_code=303,
        )


@router.get("/monitoring/keyword/{keyword_id}", response_class=HTMLResponse)
async def keyword_history_page(
    request: Request,
    keyword_id: UUID,
    db: Session = Depends(get_db),
):
    """Show position history for a keyword."""
    keyword = db.query(models.Keyword).filter(models.Keyword.id == keyword_id).first()
    if not keyword:
        raise HTTPException(status_code=404, detail="Keyword not found")

    rankings = db.query(models.KeywordRanking).filter(
        models.KeywordRanking.keyword_id == keyword_id,
    ).order_by(models.KeywordRanking.date.desc()).limit(90).all()

    # Build history with change calculation
    history = []
    for i, r in enumerate(rankings):
        change = None
        if i + 1 < len(rankings) and r.position is not None and rankings[i + 1].position is not None:
            change = r.position - rankings[i + 1].position

        history.append({
            "date": r.date.strftime("%Y-%m-%d"),
            "position": r.position,
            "change": change,
            "url": r.url,
            "serp_features": r.serp_features or [],
        })

    post_title = None
    if keyword.post_id:
        post = db.query(models.Post).filter(models.Post.id == keyword.post_id).first()
        if post:
            post_title = post.title

    return _render(request, db, "monitoring/history.html", {
        "keyword": keyword.keyword,
        "keyword_id": str(keyword.id),
        "site_id": str(keyword.site_id),
        "current_position": keyword.current_position,
        "post_title": post_title,
        "history": history,
    })


# ============ Iterations Pages ============

@router.get("/iterations", response_class=HTMLResponse)
async def iterations_list(
    request: Request,
    db: Session = Depends(get_db),
):
    """List content iteration tasks, scoped by blog."""
    current_blog = get_current_blog(request, db)
    blog_site_ids = get_blog_site_ids(db, current_blog.id) if current_blog else []

    query = db.query(models.IterationTask)
    if current_blog:
        if blog_site_ids:
            blog_post_ids = [p.id for p in db.query(models.Post.id).filter(
                models.Post.site_id.in_(blog_site_ids)
            ).all()]
            if blog_post_ids:
                query = query.filter(models.IterationTask.post_id.in_(blog_post_ids))
            else:
                query = query.filter(False)
        else:
            query = query.filter(False)
    tasks = query.order_by(
        models.IterationTask.priority.asc(),
        models.IterationTask.created_at.desc(),
    ).all()

    # Eager-load posts
    for task in tasks:
        task.post = db.query(models.Post).filter(models.Post.id == task.post_id).first()

    return _render(request, db, "iterations/list.html", {
        "tasks": tasks,
        "success": request.query_params.get("success"),
    })


@router.post("/iterations/{task_id}/skip", response_class=HTMLResponse)
async def skip_iteration_task(
    request: Request,
    task_id: UUID,
    db: Session = Depends(get_db),
):
    """Skip an iteration task."""
    task = db.query(models.IterationTask).filter(models.IterationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("pending", "in_progress"):
        task.status = "skipped"
        task.completed_at = datetime.utcnow()
        db.commit()

    return RedirectResponse(
        url="/ui/iterations?success=Task skipped",
        status_code=303,
    )


# =============================================================================
# Cluster Planner routes
# =============================================================================

@router.get("/clusters", response_class=HTMLResponse)
async def cluster_list(request: Request, db: Session = Depends(get_db)):
    """List all clusters (top-level only — no parent), scoped by blog."""
    current_blog = get_current_blog(request, db)
    blog_site_ids = get_blog_site_ids(db, current_blog.id) if current_blog else []

    query = db.query(models.Cluster).filter(
        models.Cluster.parent_cluster_id.is_(None),
    )
    if current_blog:
        if blog_site_ids:
            query = query.filter(
                (models.Cluster.site_id.in_(blog_site_ids)) | (models.Cluster.site_id.is_(None))
            )
        else:
            query = query.filter(models.Cluster.site_id.is_(None))
    clusters = query.order_by(models.Cluster.created_at.desc()).all()

    # Enrich with brief count (flat model)
    for cluster in clusters:
        cluster.brief_count = len(cluster.briefs)
        cluster.child_count = 0  # no more child clusters

    return _render(request, db, "clusters/list.html", {
        "clusters": clusters,
    })


@router.get("/clusters/plan", response_class=HTMLResponse)
async def cluster_plan_form(request: Request, db: Session = Depends(get_db)):
    """Show cluster planning form."""
    current_blog = get_current_blog(request, db)
    sites_q = db.query(models.Site).order_by(models.Site.name)
    if current_blog:
        sites_q = sites_q.filter(models.Site.blog_id == current_blog.id)
    sites = sites_q.all()
    folders = get_blog_kb_folders(db, current_blog)
    return _render(request, db, "clusters/plan.html", {
        "sites": sites,
        "folders": folders,
    })


@router.post("/clusters/plan", response_class=HTMLResponse)
async def cluster_plan_submit(
    request: Request,
    big_topic: str = Form(...),
    site_id: str = Form(""),
    region: str = Form("ru"),
    target_count: int = Form(10),
    factual_mode: str = Form("default"),
    db: Session = Depends(get_db),
):
    """Generate a cluster plan and save to DB. site_id is optional."""
    settings = get_settings()

    # Extract folder_ids from form (checkboxes send multiple values)
    form = await request.form()
    folder_ids = form.getlist("folder_ids")

    blog = get_current_blog(request, db)
    bs = resolve_blog_settings(blog, settings)

    if not bs["anthropic_api_key"]:
        return RedirectResponse(
            url="/ui/clusters/plan?error=ANTHROPIC_API_KEY not configured",
            status_code=303,
        )

    # Resolve optional site_id
    resolved_site_id = None
    if site_id and site_id.strip():
        resolved_site_id = site_id.strip()

    try:
        from src.services.cluster_planner import ClusterPlanner, NicheContext

        client = _make_anthropic_client(bs)
        volume_provider = _make_volume_provider(bs, region)

        tv_client = _make_topvisor_client(bs)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key=bs["serper_api_key"],
            volume_provider=volume_provider,
            topvisor_client=tv_client,
            use_serp_clustering=bool(tv_client),
        )

        # Load KB docs: from selected folders first, fallback to site's folders
        kb_docs = []
        selected_folders = []
        niche_ctx = None
        if folder_ids:
            selected_folders = db.query(models.KnowledgeFolder).filter(
                models.KnowledgeFolder.id.in_(folder_ids),
            ).all()
            for folder in selected_folders:
                for doc in folder.documents:
                    if doc.content_text:
                        kb_docs.append({
                            "id": str(doc.id),
                            "title": doc.original_filename,
                            "content_text": doc.content_text,
                            "word_count": doc.word_count or 0,
                        })
        if resolved_site_id:
            site = db.query(models.Site).filter(models.Site.id == resolved_site_id).first()
            if site:
                if not kb_docs:
                    for folder in site.knowledge_folders:
                        for doc in folder.documents:
                            if doc.content_text:
                                kb_docs.append({
                                    "id": str(doc.id),
                                    "title": doc.original_filename,
                                    "content_text": doc.content_text,
                                    "word_count": doc.word_count or 0,
                                })
                # Build niche context from site boundaries
                boundaries = site.niche_boundaries or {}
                niche_ctx = NicheContext(
                    site_name=site.name,
                    cluster_description="",
                    include_topics=boundaries.get("include", []),
                    exclude_topics=boundaries.get("exclude", []),
                    target_audience=boundaries.get("target_audience", ""),
                )

        plan = await planner.plan(
            big_topic=big_topic,
            region=region,
            target_count=target_count,
            knowledge_base_docs=kb_docs if kb_docs else None,
            niche_context=niche_ctx,
        )

        # Save to DB (site_id can be None)
        cluster_id = await planner.save_to_db(plan, resolved_site_id, db, factual_mode=factual_mode, region=region)

        # Attach selected KB folders to cluster
        if selected_folders:
            cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
            if cluster:
                cluster.knowledge_folders = selected_folders
                db.commit()

        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?success=Кластер создан: 1 pillar + {len(plan.cluster_articles)} cluster статей",
            status_code=303,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(
            url=f"/ui/clusters/plan?error={str(e)[:200]}",
            status_code=303,
        )


@router.get("/clusters/{cluster_id}", response_class=HTMLResponse)
async def cluster_detail(
    request: Request,
    cluster_id: UUID,
    db: Session = Depends(get_db),
):
    """Show cluster detail with briefs, draft links, and generation controls."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    _check_blog_ownership(request, db, cluster=cluster)

    # All briefs in this cluster (flat model — no child clusters)
    all_briefs_raw = db.query(models.Brief).filter(
        models.Brief.cluster_id == cluster_id,
    ).all()

    # Parse structure JSON for display
    for brief in all_briefs_raw:
        if brief and isinstance(brief.structure, str):
            import json
            try:
                brief.structure = json.loads(brief.structure)
            except Exception:
                brief.structure = {}

    # Separate pillar from cluster briefs
    pillar_brief = None
    children_briefs = []
    for b in all_briefs_raw:
        if b.structure and isinstance(b.structure, dict) and b.structure.get("role") == "pillar":
            pillar_brief = b
        else:
            children_briefs.append(b)

    # Sort cluster briefs by priority (from structure)
    def brief_priority(b):
        s = b.structure if isinstance(b.structure, dict) else {}
        return s.get("priority", 99)
    children_briefs.sort(key=brief_priority)

    all_briefs = ([pillar_brief] if pillar_brief else []) + children_briefs

    # Build brief_id → draft mapping
    brief_ids = [b.id for b in all_briefs if b]
    drafts = db.query(models.Draft).filter(
        models.Draft.brief_id.in_(brief_ids),
    ).all() if brief_ids else []

    brief_drafts = {}
    for d in drafts:
        brief_drafts[str(d.brief_id)] = d

    # Pillar draft
    pillar_draft = brief_drafts.get(str(pillar_brief.id)) if pillar_brief else None

    # Count approved briefs
    approved_count = sum(1 for b in all_briefs if b and b.status == "approved")

    # Check if any articles are currently generating
    has_running = any(
        d.pipeline_status == "running" or d.status in ("generating", "pipeline_running")
        for d in drafts
    )

    # Cluster keywords
    cluster_keywords = db.query(models.Keyword).filter(
        models.Keyword.cluster_id == cluster_id,
    ).order_by(models.Keyword.search_volume.desc().nullslast()).all() if cluster.site_id else []

    # Knowledge Base folders
    current_blog = get_current_blog(request, db)
    all_folders = get_blog_kb_folders(db, current_blog)
    attached_folder_ids = {str(f.id) for f in cluster.knowledge_folders} if hasattr(cluster, 'knowledge_folders') and cluster.knowledge_folders else set()

    return _render(request, db, "clusters/detail.html", {
        "cluster": cluster,
        "pillar_brief": pillar_brief,
        "pillar_draft": pillar_draft,
        "children_briefs": children_briefs,
        "all_briefs": all_briefs,
        "brief_drafts": brief_drafts,
        "approved_count": approved_count,
        "has_running": has_running,
        "cluster_keywords": cluster_keywords,
        "all_folders": all_folders,
        "attached_folder_ids": attached_folder_ids,
    })


@router.post("/clusters/{cluster_id}/approve-all", response_class=HTMLResponse)
async def approve_all_briefs(
    request: Request,
    cluster_id: UUID,
    db: Session = Depends(get_db),
):
    """Approve all draft briefs in the cluster."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404)
    _check_blog_ownership(request, db, cluster=cluster)

    count = db.query(models.Brief).filter(
        models.Brief.cluster_id == cluster_id,
        models.Brief.status == "draft",
    ).update({"status": "approved", "approved_at": datetime.utcnow()}, synchronize_session="fetch")
    db.commit()

    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Одобрено {count} брифов",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/kb", response_class=HTMLResponse)
async def update_cluster_kb(
    cluster_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    """Update knowledge base folders attached to a cluster."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    _check_blog_ownership(request, db, cluster=cluster)

    form = await request.form()
    folder_ids = form.getlist("folder_ids")
    factual_mode = form.get("factual_mode", "default")
    if factual_mode not in ("default", "kb_priority", "kb_only"):
        factual_mode = "default"

    # Clear and re-attach
    cluster.knowledge_folders = []
    if folder_ids:
        folders = db.query(models.KnowledgeFolder).filter(
            models.KnowledgeFolder.id.in_(folder_ids),
        ).all()
        cluster.knowledge_folders = folders
    cluster.factual_mode = factual_mode
    db.commit()

    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Фактура обновлена",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/briefs/{brief_id}/approve", response_class=HTMLResponse)
async def approve_cluster_brief(
    request: Request,
    cluster_id: UUID,
    brief_id: UUID,
    db: Session = Depends(get_db),
):
    """Approve a brief for generation."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404)
    _check_blog_ownership(request, db, cluster=cluster)

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if brief:
        brief.status = "approved"
        brief.approved_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Brief approved",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/briefs/{brief_id}/delete", response_class=HTMLResponse)
async def delete_cluster_brief(
    request: Request,
    cluster_id: UUID,
    brief_id: UUID,
    db: Session = Depends(get_db),
):
    """Delete a brief."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404)
    _check_blog_ownership(request, db, cluster=cluster)

    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if brief:
        db.delete(brief)
        db.commit()
    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Brief deleted",
        status_code=303,
    )


@router.post("/clusters/{cluster_id}/briefs/{brief_id}/generate", response_class=HTMLResponse)
async def generate_single_brief(
    request: Request,
    cluster_id: UUID,
    brief_id: UUID,
    background_tasks: BackgroundTasks,
    step_by_step: str = Form("false"),
    db: Session = Depends(get_db),
):
    """Generate article for a single brief."""
    settings = get_settings()
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    _check_blog_ownership(request, db, cluster=cluster)

    blog = _resolve_blog(request, db, cluster=cluster)
    bs = resolve_blog_settings(blog, settings)
    brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
    if not brief:
        raise HTTPException(status_code=404, detail="Brief not found")

    # Auto-approve if still draft
    if brief.status == "draft":
        brief.status = "approved"
        brief.approved_at = datetime.utcnow()

    structure = brief.structure if isinstance(brief.structure, dict) else {}
    if not structure:
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=Brief has empty structure",
            status_code=303,
        )

    brief_data = {
        "title_candidate": brief.title,
        "role": structure.get("role", "cluster"),
        "primary_intent": structure.get("primary_intent", "informational"),
        "topic_boundaries": structure.get("topic_boundaries", {}),
        "must_answer_questions": structure.get("must_answer_questions", []),
        "target_terms": [brief.target_keyword] + (brief.secondary_keywords or []),
        "unique_angle": structure.get("unique_angle", {}),
        "internal_links_plan": structure.get("internal_links_plan", []),
        "seed_queries": structure.get("seed_queries", []),
    }

    # Load KB docs
    kb_docs = []
    for folder in cluster.knowledge_folders:
        for doc in folder.documents:
            if doc.content_text:
                kb_docs.append({
                    "id": str(doc.id),
                    "title": doc.original_filename,
                    "content_text": doc.content_text,
                    "word_count": doc.word_count or 0,
                })

    # Validate: kb_only requires attached documents
    if (cluster.factual_mode or "default") == "kb_only" and not kb_docs:
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=Режим «Только фактура» требует прикреплённых документов",
            status_code=303,
        )

    brief.status = "in_writing"
    db.commit()

    background_tasks.add_task(
        _run_pipeline_for_brief,
        str(brief.id),
        str(cluster.site_id) if cluster.site_id else "",
        brief.title,
        cluster.region or "ru",
        brief_data,
        settings,
        kb_docs,
        str(cluster_id),
        step_by_step.lower() in ("true", "1", "yes"),
        cluster.factual_mode or "default",
        blog_settings=bs,
    )

    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Запущена генерация: {brief.title}",
        status_code=303,
    )


@router.post("/articles/{draft_id}/cancel", response_class=HTMLResponse)
async def cancel_article(request: Request, draft_id: UUID, db: Session = Depends(get_db)):
    """Cancel a running pipeline."""
    draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    _check_blog_ownership(request, db, draft=draft)

    if draft.status in ("pipeline_running", "generating"):
        draft.status = "cancelled"
        draft.pipeline_status = "cancelled"
        draft.pipeline_error = "Cancelled by user"
        if draft.brief_id:
            brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()
            if brief and brief.status == "in_writing":
                brief.status = "cancelled"
        db.commit()

    return RedirectResponse(url=f"/ui/articles/{draft_id}", status_code=303)


def _update_cluster_status(db, cluster_id: str):
    """Check if all briefs in cluster are done and update cluster status."""
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        return

    briefs = db.query(models.Brief).filter(
        models.Brief.cluster_id == cluster_id,
        models.Brief.status.in_(["in_writing", "completed", "queued"]),
    ).all()

    if not briefs:
        return

    statuses = [b.status for b in briefs]
    if all(s == "completed" for s in statuses):
        cluster.status = "published"
        db.commit()
    elif any(s in ("in_writing", "queued") for s in statuses):
        pass  # still in_progress
    else:
        has_running = db.query(models.Draft).filter(
            models.Draft.brief_id.in_([b.id for b in briefs]),
            models.Draft.status == "pipeline_running",
        ).first()
        if not has_running:
            cluster.status = "published"
            db.commit()


ALL_PIPELINE_STAGES = [
    "intent", "research", "structure", "drafting", "editing",
    "linking", "seo_polish", "quality_gate", "meta", "formatting",
]

# Stages where step-by-step mode pauses for user review
PAUSE_STAGES = {"research", "structure", "drafting", "editing"}


def _serialize_stage_result(context, stage_name: str) -> dict:
    """Serialize a stage's output from context for storage in Draft.stage_results."""
    try:
        if stage_name == "intent" and context.intent:
            return context.intent.to_dict() if hasattr(context.intent, 'to_dict') else {"raw": str(context.intent)}
        elif stage_name == "research" and context.research:
            return context.research.to_dict() if hasattr(context.research, 'to_dict') else {"raw": str(context.research)}
        elif stage_name == "structure" and context.outline:
            return context.outline.to_dict() if hasattr(context.outline, 'to_dict') else {"raw": str(context.outline)}
        elif stage_name == "drafting" and context.draft_md:
            return {"content_md": context.draft_md[:50000]}
        elif stage_name == "editing" and context.edited_md:
            return {"content_md": context.edited_md[:50000]}
        elif stage_name == "meta" and context.meta:
            return context.meta.to_dict() if hasattr(context.meta, 'to_dict') else {"raw": str(context.meta)}
        elif stage_name == "quality_gate" and context.quality_report:
            return context.quality_report if isinstance(context.quality_report, dict) else {"raw": str(context.quality_report)}
        elif stage_name == "formatting" and context.formatting_result:
            return {"cover_url": getattr(context.formatting_result, 'cover_ghost_url', ''), "cover_alt": getattr(context.formatting_result, 'cover_image_alt', '')}
        elif stage_name in ("linking", "seo_polish"):
            # These modify edited_md in place
            if context.edited_md:
                return {"content_md_length": len(context.edited_md)}
        return {}
    except Exception:
        return {}


def _create_runner(settings, ghost_url=None, ghost_admin_key=None):
    """Create a PipelineRunner from settings. Optional ghost creds override env."""
    bs = resolve_blog_settings(None, settings)
    if ghost_url:
        bs["ghost_url"] = ghost_url
    if ghost_admin_key:
        bs["ghost_admin_key"] = ghost_admin_key
    return _create_runner_from_bs(bs)


def _create_runner_from_bs(bs: dict):
    """Create a PipelineRunner from resolved blog settings dict."""
    from src.services.writing_pipeline import PipelineRunner
    return PipelineRunner(
        anthropic_api_key=bs["anthropic_api_key"],
        serper_api_key=bs["serper_api_key"],
        jina_api_key=bs["jina_api_key"],
        proxy_url=bs["anthropic_proxy_url"],
        proxy_secret=bs["anthropic_proxy_secret"],
        ghost_url=bs["ghost_url"],
        ghost_admin_key=bs["ghost_admin_key"],
        database_url=bs["database_url"],
        openai_api_key=bs["openai_api_key"],
        openai_proxy_url=bs["openai_proxy_url"],
        residential_proxy_url=bs["residential_proxy_url"],
        yandex_wordstat_api_key=bs.get("yandex_wordstat_api_key", ""),
        yandex_cloud_folder_id=bs.get("yandex_cloud_folder_id", ""),
        rush_analytics_api_key=bs.get("rush_analytics_api_key", ""),
        topvisor_user_id=bs.get("topvisor_user_id", ""),
        topvisor_access_token=bs.get("topvisor_access_token", ""),
        topvisor_project_id=bs.get("topvisor_project_id", 0),
    )


def _run_pipeline_for_brief(
    brief_id: str,
    site_id: str,
    topic: str,
    region: str,
    brief_data: dict,
    settings,
    knowledge_base_docs: list = None,
    cluster_id: str = None,
    step_by_step: bool = False,
    factual_mode: str = "default",
    ghost_url: str = None,
    ghost_admin_key: str = None,
    blog_settings: dict = None,
):
    """Background task: run pipeline for a single brief."""
    import asyncio

    async def _run():
        from src.db.session import SessionLocal
        from src.services.writing_pipeline.core.context import WritingContext

        db = SessionLocal()
        try:
            brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
            if not brief:
                return

            draft = models.Draft(
                site_id=site_id if site_id else None,
                brief_id=brief_id,
                title=topic,
                topic=topic,
                status="pipeline_running",
                pipeline_status="running",
                pipeline_started_at=datetime.utcnow(),
                pipeline_stages={s: "pending" for s in ALL_PIPELINE_STAGES},
                step_by_step=step_by_step,
                stage_results={},
            )
            db.add(draft)
            db.commit()
            local_draft_id = str(draft.id)

            bs = blog_settings or resolve_blog_settings(None, settings)
            if ghost_url and not blog_settings:
                bs["ghost_url"] = ghost_url
            if ghost_admin_key and not blog_settings:
                bs["ghost_admin_key"] = ghost_admin_key
            runner = _create_runner_from_bs(bs)

            config = {}
            if knowledge_base_docs:
                config["knowledge_base_docs"] = knowledge_base_docs
            if factual_mode and factual_mode != "default":
                config["factual_mode"] = factual_mode
            if brief_data:
                config["brief"] = brief_data

            # Initialize context
            pipeline_config = {
                "expand_paa": True,
                "fetch_page_content": True,
                "max_pages_to_fetch": 5,
                "max_paa_queries": 3,
                "use_playwright": True,
            }
            pipeline_config.update(config)

            # Fetch existing posts for overlap analysis
            existing_posts = []
            if bs["ghost_url"] and bs["ghost_admin_key"]:
                try:
                    from src.services.publisher import GhostPublisher
                    publisher = GhostPublisher(ghost_url=bs["ghost_url"], admin_key=bs["ghost_admin_key"])
                    existing_posts = publisher.get_posts()
                except Exception:
                    pass

            context = WritingContext(
                topic=topic,
                region=region,
                started_at=datetime.now(),
                config=pipeline_config,
                existing_posts=existing_posts,
            )

            # Run stages one by one
            for stage in runner.stages:
                # Check for cancellation
                d = db.query(models.Draft).filter(models.Draft.id == local_draft_id).first()
                if d and d.status == "cancelled":
                    logger.info(f"[pipeline] Cancelled before {stage.name}")
                    return

                # Update DB: stage is running
                if d:
                    stages = dict(d.pipeline_stages or {})
                    stages[stage.name] = "running"
                    d.pipeline_stages = stages
                    db.commit()

                # Execute stage
                context = await stage.run(context)

                # Update DB: stage completed + save result
                d = db.query(models.Draft).filter(models.Draft.id == local_draft_id).first()
                if d:
                    stages = dict(d.pipeline_stages or {})
                    stages[stage.name] = "completed"
                    d.pipeline_stages = stages

                    sr = dict(d.stage_results or {})
                    sr[stage.name] = _serialize_stage_result(context, stage.name)
                    d.stage_results = sr
                    db.commit()

                # Check if we should pause (step-by-step mode)
                if step_by_step and stage.name in PAUSE_STAGES:
                    d = db.query(models.Draft).filter(models.Draft.id == local_draft_id).first()
                    if d:
                        d.pipeline_status = f"paused_at_{stage.name}"
                        d.status = "pipeline_running"
                        db.commit()
                    return  # Stop here; user will resume via /next-step

            # All stages done
            context.completed_at = datetime.now()
            d = db.query(models.Draft).filter(models.Draft.id == local_draft_id).first()
            if d:
                d.title = context.outline.title if context.outline else topic
                d.slug = context.meta.slug if context.meta else None
                d.content_md = context.edited_md
                d.word_count = len(context.edited_md.split()) if context.edited_md else 0
                d.meta_title = context.meta.meta_title if context.meta else None
                d.meta_description = context.meta.meta_description if context.meta else None
                d.og_title = context.meta.og_title if context.meta else None
                d.og_description = context.meta.og_description if context.meta else None
                d.custom_excerpt = context.meta.custom_excerpt if context.meta else None
                if context.formatting_result:
                    d.cover_image_url = getattr(context.formatting_result, 'cover_ghost_url', '') or ''
                    d.cover_image_alt = getattr(context.formatting_result, 'cover_image_alt', '') or ''
                d.status = "pipeline_completed"
                d.pipeline_status = "completed"
                d.pipeline_completed_at = datetime.utcnow()

                brief.status = "completed"
                db.commit()

        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                brief_obj = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
                if brief_obj:
                    brief_obj.status = "error"
                draft = db.query(models.Draft).filter(
                    models.Draft.brief_id == brief_id,
                    models.Draft.status == "pipeline_running",
                ).first()
                if draft:
                    draft.status = "pipeline_failed"
                    draft.pipeline_status = "failed"
                    draft.pipeline_error = str(e)[:1000]
                db.commit()
            except Exception:
                pass
        finally:
            # Update cluster status after each brief completes
            if cluster_id:
                try:
                    _update_cluster_status(db, cluster_id)
                except Exception:
                    pass
            db.close()

    asyncio.run(_run())


def _run_cluster_pipeline_sequential(
    brief_queue: list,
    site_id: str,
    region: str,
    settings,
    knowledge_base_docs: list,
    cluster_id: str,
    step_by_step: bool,
    factual_mode: str,
    ghost_url: str = None,
    ghost_admin_key: str = None,
    blog_settings: dict = None,
):
    """Background task: run pipeline for briefs SEQUENTIALLY, pillar first."""
    for brief_id, topic, brief_data in brief_queue:
        # Check if brief or cluster was cancelled before starting
        from src.db.session import SessionLocal
        db = SessionLocal()
        try:
            # Check cluster-level cancellation
            cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
            if cluster and cluster.status == "cancelled":
                logger.info(f"[seq-gen] Cluster {cluster_id} cancelled, stopping queue")
                return

            brief = db.query(models.Brief).filter(models.Brief.id == brief_id).first()
            if not brief or brief.status in ("cancelled", "completed", "in_writing"):
                logger.info(f"[seq-gen] Skipping brief {brief_id} (status={brief.status if brief else 'missing'})")
                continue
            brief.status = "in_writing"
            db.commit()
        finally:
            db.close()

        # Run pipeline for this brief (blocking)
        _run_pipeline_for_brief(
            brief_id, site_id, topic, region, brief_data,
            settings, knowledge_base_docs, cluster_id,
            step_by_step, factual_mode,
            blog_settings=blog_settings,
        )

        # If step_by_step, the first brief will pause — stop processing queue
        if step_by_step:
            db = SessionLocal()
            try:
                draft = db.query(models.Draft).filter(
                    models.Draft.brief_id == brief_id,
                ).order_by(models.Draft.created_at.desc()).first()
                if draft and draft.pipeline_status and draft.pipeline_status.startswith("paused"):
                    logger.info(f"[seq-gen] Paused at brief {brief_id}, stopping queue")
                    break
            finally:
                db.close()


@router.post("/clusters/{cluster_id}/generate", response_class=HTMLResponse)
async def generate_cluster_articles(
    request: Request,
    cluster_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    step_by_step: str = Form("false"),
):
    """Generate articles for all approved briefs in the cluster (sequentially, pillar first)."""
    settings = get_settings()
    cluster = db.query(models.Cluster).filter(models.Cluster.id == cluster_id).first()
    if not cluster:
        raise HTTPException(status_code=404, detail="Cluster not found")
    _check_blog_ownership(request, db, cluster=cluster)

    blog = _resolve_blog(request, db, cluster=cluster)
    bs = resolve_blog_settings(blog, settings)
    is_step_by_step = step_by_step.lower() == "true"

    # Guard: don't allow double-generation
    if cluster.status == "in_progress":
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=Генерация уже запущена",
            status_code=303,
        )

    # Get approved briefs from this cluster (flat model)
    approved_briefs = db.query(models.Brief).filter(
        models.Brief.cluster_id == cluster_id,
        models.Brief.status == "approved",
    ).all()

    if not approved_briefs:
        # If none approved, generate all draft briefs
        approved_briefs = db.query(models.Brief).filter(
            models.Brief.cluster_id == cluster_id,
            models.Brief.status == "draft",
        ).all()

    if not approved_briefs:
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=No briefs to generate",
            status_code=303,
        )

    region = cluster.region or "ru"
    factual_mode = cluster.factual_mode or "default"

    # Load KB docs from cluster's attached knowledge folders first, then site's
    kb_docs = []
    seen_doc_ids = set()

    # Cluster-level KB folders
    for folder in cluster.knowledge_folders:
        for doc in folder.documents:
            if doc.content_text and str(doc.id) not in seen_doc_ids:
                kb_docs.append({
                    "id": str(doc.id),
                    "title": doc.original_filename,
                    "content_text": doc.content_text,
                    "word_count": doc.word_count or 0,
                })
                seen_doc_ids.add(str(doc.id))

    # Site-level KB folders (if cluster is tied to a site)
    if cluster.site_id:
        site = db.query(models.Site).filter(models.Site.id == cluster.site_id).first()
        if site:
            for folder in site.knowledge_folders:
                for doc in folder.documents:
                    if doc.content_text and str(doc.id) not in seen_doc_ids:
                        kb_docs.append({
                            "id": str(doc.id),
                            "title": doc.original_filename,
                            "content_text": doc.content_text,
                            "word_count": doc.word_count or 0,
                        })
                        seen_doc_ids.add(str(doc.id))

    # Validate: kb_only requires attached documents
    if factual_mode == "kb_only" and not kb_docs:
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=Режим «Только фактура» требует прикреплённых документов",
            status_code=303,
        )

    # Sort: pillar first, then by priority
    def brief_sort_key(b):
        s = b.structure if isinstance(b.structure, dict) else {}
        is_pillar = 0 if s.get("role") == "pillar" else 1
        return (is_pillar, s.get("priority", 99))
    approved_briefs.sort(key=brief_sort_key)

    # Build queue of brief data for sequential processing
    brief_queue = []
    skipped = 0
    for brief in approved_briefs:
        if brief.status in ("in_writing", "completed"):
            skipped += 1
            continue

        structure = brief.structure if isinstance(brief.structure, dict) else {}
        if not structure:
            logger.warning(f"[cluster-generate] Skipping brief {brief.id}: empty structure")
            skipped += 1
            continue

        brief_data = {
            "title_candidate": brief.title,
            "role": structure.get("role", "cluster"),
            "primary_intent": structure.get("primary_intent", "informational"),
            "topic_boundaries": structure.get("topic_boundaries", {}),
            "must_answer_questions": structure.get("must_answer_questions", []),
            "target_terms": [brief.target_keyword] + (brief.secondary_keywords or []),
            "unique_angle": structure.get("unique_angle", {}),
            "internal_links_plan": structure.get("internal_links_plan", []),
            "seed_queries": structure.get("seed_queries", []),
        }

        brief.status = "queued"
        brief_queue.append((str(brief.id), brief.title, brief_data))

    db.commit()

    if not brief_queue:
        return RedirectResponse(
            url=f"/ui/clusters/{cluster_id}?error=Нет брифов для генерации (пропущено: {skipped})",
            status_code=303,
        )

    cluster.status = "in_progress"
    db.commit()

    # One background task processes ALL briefs sequentially
    background_tasks.add_task(
        _run_cluster_pipeline_sequential,
        brief_queue,
        str(cluster.site_id) if cluster.site_id else "",
        region,
        settings,
        kb_docs,
        str(cluster_id),
        is_step_by_step,
        factual_mode,
        blog_settings=bs,
    )

    return RedirectResponse(
        url=f"/ui/clusters/{cluster_id}?success=Запущена генерация {len(brief_queue)} статей (pillar first)",
        status_code=303,
    )
