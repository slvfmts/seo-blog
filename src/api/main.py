"""
SEO Blog API - главный модуль приложения.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from src.config import get_settings
from src.api.routes import health, sites, articles, briefs, ui


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events."""
    # Startup
    settings = get_settings()
    print(f"Starting SEO Blog API (debug={settings.debug})")
    yield
    # Shutdown
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

    # UI Роуты
    app.include_router(ui.router, prefix="/ui", tags=["ui"])

    # Redirect root to UI
    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/briefs")

    return app


app = create_app()
