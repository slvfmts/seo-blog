"""Tests for VolumeProvider routing and providers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.writing_pipeline.data_sources.volume_provider import (
    VolumeResult,
    get_volume_provider,
    NullVolumeProvider,
)


# =============================================================================
# Routing logic
# =============================================================================

class TestVolumeProviderRouting:
    """Test get_volume_provider picks the right provider."""

    def test_ru_with_wordstat_key_returns_wordstat(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "test-key"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = "login"
        settings.dataforseo_password = "pass"

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "wordstat"

    def test_ru_with_rush_key_returns_rush(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.rush_analytics_api_key = "test-key"
        settings.dataforseo_login = "login"
        settings.dataforseo_password = "pass"

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "rush"

    def test_ru_with_only_dataforseo_returns_dataforseo(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = "login"
        settings.dataforseo_password = "pass"

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "dataforseo"

    def test_us_ignores_wordstat_uses_dataforseo(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "test-key"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = "login"
        settings.dataforseo_password = "pass"

        provider = get_volume_provider("us", settings)
        assert provider.source_name == "dataforseo"

    def test_no_creds_returns_null_provider(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "none"

    def test_kz_with_wordstat_returns_wordstat(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "test-key"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("kz", settings)
        assert provider.source_name == "wordstat"

    def test_russia_region_string_matches(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "key"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("russia", settings)
        assert provider.source_name == "wordstat"


# =============================================================================
# NullVolumeProvider
# =============================================================================

class TestNullVolumeProvider:
    @pytest.mark.asyncio
    async def test_returns_zero_volumes(self):
        provider = NullVolumeProvider()
        results = await provider.get_volumes(["test", "keyword"])
        assert len(results) == 2
        assert all(r.volume == 0 for r in results)
        assert all(r.source == "none" for r in results)

    def test_source_name(self):
        assert NullVolumeProvider().source_name == "none"


# =============================================================================
# VolumeResult
# =============================================================================

class TestVolumeResult:
    def test_default_values(self):
        vr = VolumeResult(keyword="test", volume=100, source="wordstat")
        assert vr.difficulty == 0.0
        assert vr.cpc == 0.0
        assert vr.competition == 0.0
        assert vr.competition_level == "LOW"
        assert vr.trend is None

    def test_all_fields(self):
        vr = VolumeResult(
            keyword="seo",
            volume=5000,
            source="dataforseo",
            difficulty=45.0,
            cpc=1.5,
            competition=0.7,
            competition_level="HIGH",
            trend=[100, 200, 300],
        )
        assert vr.keyword == "seo"
        assert vr.volume == 5000
        assert vr.trend == [100, 200, 300]


# =============================================================================
# DataForSEOProvider
# =============================================================================

class TestDataForSEOProvider:
    def test_source_name(self):
        from src.services.writing_pipeline.data_sources.dataforseo import DataForSEOProvider
        provider = DataForSEOProvider(login="test", password="test", region="us")
        assert provider.source_name == "dataforseo"

    @pytest.mark.asyncio
    async def test_get_suggestions_returns_empty(self):
        from src.services.writing_pipeline.data_sources.dataforseo import DataForSEOProvider
        provider = DataForSEOProvider(login="test", password="test")
        result = await provider.get_suggestions("test")
        assert result == []
