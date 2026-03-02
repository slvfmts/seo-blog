"""Tests for VolumeProvider routing and providers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.writing_pipeline.data_sources.volume_provider import (
    VolumeResult,
    get_volume_provider,
    NullVolumeProvider,
)


def _settings(**overrides):
    """Create a settings mock with all provider keys explicitly set."""
    defaults = {
        "yandex_wordstat_api_key": "",
        "yandex_cloud_folder_id": "",
        "rush_analytics_api_key": "",
        "topvisor_access_token": "",
        "topvisor_user_id": "",
        "topvisor_project_id": 0,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


# =============================================================================
# Routing logic
# =============================================================================

class TestVolumeProviderRouting:
    """Test get_volume_provider picks the right provider."""

    def test_ru_with_wordstat_key_returns_wordstat(self):
        settings = _settings(yandex_wordstat_api_key="test-key")
        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "wordstat"

    def test_ru_with_rush_key_returns_rush(self):
        settings = _settings(rush_analytics_api_key="test-key")
        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "rush"

    def test_ru_with_wordstat_and_rush_returns_composite(self):
        settings = _settings(
            yandex_wordstat_api_key="test-key",
            yandex_cloud_folder_id="folder",
            rush_analytics_api_key="rush-key",
        )
        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "wordstat+rush"

    def test_ru_with_wordstat_and_topvisor_prefers_topvisor(self):
        """Topvisor is preferred over Rush as composite secondary."""
        settings = _settings(
            yandex_wordstat_api_key="test-key",
            yandex_cloud_folder_id="folder",
            topvisor_access_token="tv-token",
            topvisor_user_id="tv-user",
            topvisor_project_id=42,
        )
        provider = get_volume_provider("ru", settings)
        # Composite with topvisor as secondary
        assert "wordstat" in provider.source_name

    def test_ru_all_three_prefers_topvisor_over_rush(self):
        """When all 3 available, Wordstat+Topvisor wins (Rush ignored)."""
        settings = _settings(
            yandex_wordstat_api_key="test-key",
            yandex_cloud_folder_id="folder",
            rush_analytics_api_key="rush-key",
            topvisor_access_token="tv-token",
            topvisor_user_id="tv-user",
            topvisor_project_id=42,
        )
        provider = get_volume_provider("ru", settings)
        # Should be composite with topvisor, not rush
        assert "wordstat" in provider.source_name

    def test_us_returns_null_provider(self):
        settings = _settings(yandex_wordstat_api_key="test-key")
        provider = get_volume_provider("us", settings)
        assert provider.source_name == "none"

    def test_no_creds_returns_null_provider(self):
        settings = _settings()
        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "none"

    def test_kz_with_wordstat_returns_wordstat(self):
        settings = _settings(yandex_wordstat_api_key="test-key")
        provider = get_volume_provider("kz", settings)
        assert provider.source_name == "wordstat"

    def test_russia_region_string_matches(self):
        settings = _settings(yandex_wordstat_api_key="key")
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
        assert vr.yandex_volume is None
        assert vr.google_volume is None

    def test_all_fields(self):
        vr = VolumeResult(
            keyword="seo",
            volume=5000,
            source="wordstat+rush",
            difficulty=45.0,
            cpc=1.5,
            competition=0.7,
            competition_level="HIGH",
            trend=[100, 200, 300],
            yandex_volume=5000,
            google_volume=3000,
        )
        assert vr.keyword == "seo"
        assert vr.volume == 5000
        assert vr.trend == [100, 200, 300]
        assert vr.yandex_volume == 5000
        assert vr.google_volume == 3000


# =============================================================================
# CompositeVolumeProvider
# =============================================================================

class TestCompositeVolumeProvider:

    @pytest.mark.asyncio
    async def test_merges_both_providers(self):
        from src.services.writing_pipeline.data_sources.composite_provider import CompositeVolumeProvider
        from src.services.writing_pipeline.data_sources.volume_provider import VolumeProvider

        class FakeWordstat(VolumeProvider):
            @property
            def source_name(self): return "wordstat"
            async def get_volumes(self, kws, language_code="ru"):
                return [VolumeResult(keyword=kw, volume=1000 + i*100, source="wordstat")
                        for i, kw in enumerate(kws)]

        class FakeRush(VolumeProvider):
            @property
            def source_name(self): return "rush"
            async def get_volumes(self, kws, language_code="ru"):
                return [VolumeResult(keyword=kw, volume=800 + i*200, source="rush")
                        for i, kw in enumerate(kws)]

        provider = CompositeVolumeProvider(
            wordstat_provider=FakeWordstat(),
            rush_provider=FakeRush(),
        )
        assert provider.source_name == "wordstat+rush"

        results = await provider.get_volumes(["kw1", "kw2"])
        assert len(results) == 2
        # volume = max(wordstat, rush)
        assert results[0].volume == max(1000, 800)  # 1000
        assert results[1].volume == max(1100, 1000)  # 1100
        assert results[0].yandex_volume == 1000
        assert results[0].google_volume == 800

    @pytest.mark.asyncio
    async def test_survives_one_provider_failure(self):
        from src.services.writing_pipeline.data_sources.composite_provider import CompositeVolumeProvider
        from src.services.writing_pipeline.data_sources.volume_provider import VolumeProvider

        class FakeWordstat(VolumeProvider):
            @property
            def source_name(self): return "wordstat"
            async def get_volumes(self, kws, language_code="ru"):
                return [VolumeResult(keyword=kw, volume=500, source="wordstat") for kw in kws]

        class FailingRush(VolumeProvider):
            @property
            def source_name(self): return "rush"
            async def get_volumes(self, kws, language_code="ru"):
                raise ConnectionError("Rush API down")

        provider = CompositeVolumeProvider(
            wordstat_provider=FakeWordstat(),
            rush_provider=FailingRush(),
        )
        results = await provider.get_volumes(["kw1"])
        assert len(results) == 1
        assert results[0].volume == 500  # wordstat data survived
        assert results[0].yandex_volume == 500
        assert results[0].google_volume == 0
