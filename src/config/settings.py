"""
Конфигурация приложения через переменные окружения.
"""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Настройки приложения."""

    # Database
    database_url: str = "postgresql://seo:seopass@localhost:5432/seoblog"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Ghost CMS
    ghost_url: str = "http://localhost:2368"
    ghost_admin_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_proxy_url: str = ""  # Cloudflare Worker URL для обхода geo-блокировки
    anthropic_proxy_secret: str = ""  # Секрет для авторизации на proxy

    # Serper.dev (SERP API)
    serper_api_key: str = ""

    # Jina Reader (content extraction)
    jina_api_key: str = ""  # Optional - free tier available without key

    # Yandex Wordstat (RU volumes via Yandex Cloud Search API)
    yandex_wordstat_api_key: str = ""
    yandex_cloud_folder_id: str = ""

    # Rush Analytics (alternative RU volumes, Pro+ plan)
    rush_analytics_api_key: str = ""

    # OpenAI (DALL-E covers)
    openai_api_key: str = ""
    openai_proxy_url: str = ""  # Cloudflare Worker URL для обхода geo-блокировки OpenAI

    # Residential SOCKS5 proxy (Mac Mini via SSH tunnel)
    residential_proxy_url: str = ""

    # Auth
    secret_key: str = "change-me-in-production"
    auth_email: str = ""
    auth_password_hash: str = ""

    # Uploads
    upload_dir: str = "/data/uploads"

    # App
    debug: bool = False
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированные настройки."""
    return Settings()
