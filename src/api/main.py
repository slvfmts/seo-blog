"""
SEO Blog API - главный модуль приложения.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse as StarletteRedirect

from src.config import get_settings
from src.api.routes import health, sites, articles, briefs, ui, discovery, monitoring, iterations


# Paths that don't require authentication
_PUBLIC_PREFIXES = ("/ui/login", "/health", "/docs", "/openapi.json", "/api/", "/redoc")


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated /ui/ requests to login page. Enforce blog selection."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Only protect /ui/* paths (except login itself)
        if path.startswith("/ui/") or path == "/ui":
            if not any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                user = request.session.get("user")
                if not user:
                    return StarletteRedirect(url="/ui/login", status_code=302)
                # Enforce blog selection for all pages except /ui/blogs* and /ui/login
                if (path.startswith("/ui/") and
                    not path.startswith("/ui/blogs") and
                    not path.startswith("/ui/login") and
                    not path.startswith("/ui/logout") and
                    not path.startswith("/ui/kb")):
                    if not request.session.get("blog_id"):
                        return StarletteRedirect(url="/ui/blogs", status_code=302)
        # Root redirect also needs auth check
        if path == "/":
            user = request.session.get("user")
            if not user:
                return StarletteRedirect(url="/ui/login", status_code=302)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events."""
    # Startup
    settings = get_settings()
    print(f"Starting SEO Blog API (debug={settings.debug})")

    # Start monitoring scheduler if Serper API key is configured
    scheduler = None
    if settings.serper_api_key:
        from src.db.session import SessionLocal
        from src.services.monitoring.position_tracker import PositionTracker
        from src.services.monitoring.scheduler import MonitoringScheduler

        tracker = PositionTracker(
            db_session_factory=SessionLocal,
            serper_api_key=settings.serper_api_key,
        )
        scheduler = MonitoringScheduler(
            position_tracker=tracker,
            db_session_factory=SessionLocal,
            run_hour=6,  # 6 UTC = 9 MSK
        )
        await scheduler.start()
        print("Monitoring scheduler started (daily at 06:00 UTC)")
    else:
        print("Monitoring scheduler skipped (SERPER_API_KEY not configured)")

    yield

    # Shutdown
    if scheduler:
        await scheduler.stop()
    print("Shutting down SEO Blog API")


def create_app() -> FastAPI:
    """Создаёт и конфигурирует приложение."""
    settings = get_settings()

    app = FastAPI(
        title="SEO Blog API",
        description="Автоматизированная генерация и публикация SEO-контента",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # Auth middleware (checked first, added last due to ASGI ordering)
    app.add_middleware(AuthMiddleware)

    # Session middleware
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Роуты
    app.include_router(health.router, tags=["health"])
    app.include_router(sites.router, prefix="/api/v1/sites", tags=["sites"])
    app.include_router(articles.router, prefix="/api/v1/articles", tags=["articles"])
    app.include_router(briefs.router, prefix="/api/v1/briefs", tags=["briefs"])
    app.include_router(discovery.router, prefix="/api/v1/discovery", tags=["discovery"])
    app.include_router(monitoring.router, prefix="/api/v1/monitoring", tags=["monitoring"])
    app.include_router(iterations.router, prefix="/api/v1/iterations", tags=["iterations"])

    # UI Роуты
    app.include_router(ui.router, prefix="/ui", tags=["ui"])

    # Redirect root to UI
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/blogs")

    return app


app = create_app()
