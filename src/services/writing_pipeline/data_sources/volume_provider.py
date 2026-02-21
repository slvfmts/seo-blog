"""
VolumeProvider — abstract interface for keyword volume data sources.

Implementations:
- DataForSEOProvider: wraps existing DataForSEO client (non-RU fallback)
- YandexWordstatProvider: Yandex Cloud Search API (primary RU source)
- RushAnalyticsProvider: Rush Analytics API (alternative RU source)

Usage:
    provider = get_volume_provider(region="ru", settings=settings)
    results = await provider.get_volumes(["seo оптимизация", "контент маркетинг"])
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class VolumeResult:
    """Volume data for a single keyword."""
    keyword: str
    volume: int           # monthly search volume
    source: str           # "wordstat" | "rush" | "dataforseo"
    difficulty: float = 0.0
    cpc: float = 0.0
    competition: float = 0.0
    competition_level: str = "LOW"
    trend: Optional[List[int]] = None  # monthly volumes, last 12 months


class VolumeProvider(ABC):
    """Abstract base for keyword volume providers."""

    @abstractmethod
    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """Fetch search volumes for a batch of keywords."""

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Related/similar queries. Optional — returns [] by default."""
        return []

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Provider identifier for logging/tracking."""


def get_volume_provider(region: str, settings) -> VolumeProvider:
    """
    Pick the right volume provider based on region and available credentials.

    Priority for RU/KZ:
      1. Yandex Wordstat (if YANDEX_WORDSTAT_API_KEY set)
      2. Rush Analytics (if RUSH_ANALYTICS_API_KEY set)
      3. DataForSEO (fallback with KZ location)

    For all other regions:
      1. DataForSEO
    """
    is_ru = region.lower() in ("ru", "russia", "kz", "kazakhstan")

    if is_ru:
        # Try Yandex Wordstat first
        yandex_key = getattr(settings, "yandex_wordstat_api_key", "")
        if yandex_key:
            from .wordstat import YandexWordstatProvider
            folder_id = getattr(settings, "yandex_cloud_folder_id", "")
            logger.info("Using YandexWordstatProvider for RU region")
            return YandexWordstatProvider(api_key=yandex_key, folder_id=folder_id)

        # Try Rush Analytics
        rush_key = getattr(settings, "rush_analytics_api_key", "")
        if rush_key:
            from .rush_provider import RushAnalyticsProvider
            logger.info("Using RushAnalyticsProvider for RU region")
            return RushAnalyticsProvider(api_key=rush_key)

    # Default: DataForSEO
    login = getattr(settings, "dataforseo_login", "")
    password = getattr(settings, "dataforseo_password", "")
    if login and password:
        from .dataforseo import DataForSEOProvider
        logger.info(f"Using DataForSEOProvider for region={region}")
        return DataForSEOProvider(login=login, password=password, region=region)

    # No provider available
    logger.warning("No volume provider available — returning NullProvider")
    return NullVolumeProvider()


class NullVolumeProvider(VolumeProvider):
    """Fallback when no credentials are configured."""

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        return [
            VolumeResult(keyword=kw, volume=0, source="none")
            for kw in keywords
        ]

    @property
    def source_name(self) -> str:
        return "none"
