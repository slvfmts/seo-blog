"""
VolumeProvider — abstract interface for keyword volume data sources.

Implementations:
- YandexWordstatProvider: Yandex Cloud Search API (primary RU source)
- RushAnalyticsProvider: Rush Analytics API (alternative RU source)
- CompositeVolumeProvider: Yandex + Rush in parallel (preferred)

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
    volume: int           # monthly search volume (max of available sources)
    source: str           # "wordstat" | "rush" | "wordstat+rush" | "none"
    difficulty: float = 0.0
    cpc: float = 0.0
    competition: float = 0.0
    competition_level: str = "LOW"
    trend: Optional[List[int]] = None  # monthly volumes, last 12 months
    yandex_volume: Optional[int] = None  # Yandex Wordstat broad-match
    google_volume: Optional[int] = None  # Rush Analytics / Google volume


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

    RU/KZ: CompositeVolumeProvider(Wordstat + Rush) if both available,
           single provider if only one, NullProvider if none.

    Non-RU: NullProvider (no volume source configured for non-RU).
    """
    is_ru = region.lower() in ("ru", "russia", "kz", "kazakhstan")

    if is_ru:
        yandex_key = getattr(settings, "yandex_wordstat_api_key", "")
        rush_key = getattr(settings, "rush_analytics_api_key", "")
        folder_id = getattr(settings, "yandex_cloud_folder_id", "")

        wordstat_provider = None
        rush_provider = None

        if yandex_key:
            from .wordstat import YandexWordstatProvider
            wordstat_provider = YandexWordstatProvider(api_key=yandex_key, folder_id=folder_id)

        if rush_key:
            from .rush_provider import RushAnalyticsProvider
            rush_provider = RushAnalyticsProvider(api_key=rush_key, region_id=225)

        # Both available → composite
        if wordstat_provider and rush_provider:
            from .composite_provider import CompositeVolumeProvider
            logger.info("Using CompositeVolumeProvider (wordstat+rush) for RU region")
            return CompositeVolumeProvider(
                wordstat_provider=wordstat_provider,
                rush_provider=rush_provider,
            )

        # Only one available → use it directly
        if wordstat_provider:
            logger.info("Using YandexWordstatProvider for RU region")
            return wordstat_provider
        if rush_provider:
            logger.info("Using RushAnalyticsProvider for RU region")
            return rush_provider

        # Try Topvisor
        tv_token = getattr(settings, "topvisor_access_token", "")
        tv_user = getattr(settings, "topvisor_user_id", "")
        tv_project = getattr(settings, "topvisor_project_id", 0)
        if tv_token and tv_user and tv_project:
            from .topvisor_provider import TopvisorProvider
            logger.info("Using TopvisorProvider for RU region")
            return TopvisorProvider(
                user_id=tv_user,
                access_token=tv_token,
                project_id=tv_project,
            )

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
