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
