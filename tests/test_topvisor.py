"""Tests for TopvisorClient and TopvisorProvider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.writing_pipeline.data_sources.volume_provider import (
    VolumeResult,
    get_volume_provider,
    NullVolumeProvider,
)
from src.services.writing_pipeline.data_sources.topvisor_provider import TopvisorProvider
from src.services.writing_pipeline.data_sources.topvisor_client import TopvisorClient


# =============================================================================
# TopvisorClient
# =============================================================================

class TestTopvisorClient:
    """Test TopvisorClient initialization and header generation."""

    def test_init(self):
        client = TopvisorClient(
            user_id="12345",
            access_token="test-token",
            project_id=99,
        )
        assert client.user_id == "12345"
        assert client.access_token == "test-token"
        assert client.project_id == 99

    def test_headers(self):
        client = TopvisorClient(
            user_id="12345",
            access_token="test-token",
            project_id=99,
        )
        headers = client._headers()
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["User-Id"] == "12345"
        assert headers["Content-Type"] == "application/json; charset=utf-8"

    @pytest.mark.asyncio
    async def test_import_keywords(self):
        client = TopvisorClient(user_id="1", access_token="t", project_id=1)
        mock_response = {"result": {"countAdded": 3}}
        with patch.object(client, "_post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.import_keywords(
                keywords=["kw1", "kw2", "kw3"],
                group_name="test-group",
            )
            assert result == {"countAdded": 3}
            client._post.assert_called_once()
            call_args = client._post.call_args
            assert call_args[0][0] == "add/keywords_2/keywords/import"

    @pytest.mark.asyncio
    async def test_get_keywords(self):
        client = TopvisorClient(user_id="1", access_token="t", project_id=1)
        mock_response = {
            "result": [
                {"name": "seo оптимизация", "id": 1},
                {"name": "контент маркетинг", "id": 2},
            ]
        }
        with patch.object(client, "_post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_keywords(fields=["name", "id"], limit=100)
            assert len(result) == 2
            assert result[0]["name"] == "seo оптимизация"


# =============================================================================
# TopvisorProvider
# =============================================================================

class TestTopvisorProvider:
    """Test TopvisorProvider volume fetching."""

    def test_source_name(self):
        provider = TopvisorProvider(
            user_id="1", access_token="t", project_id=1
        )
        assert provider.source_name == "topvisor"

    @pytest.mark.asyncio
    async def test_empty_keywords_returns_empty(self):
        provider = TopvisorProvider(
            user_id="1", access_token="t", project_id=1
        )
        results = await provider.get_volumes([])
        assert results == []

    @pytest.mark.asyncio
    async def test_get_volumes_success(self):
        provider = TopvisorProvider(
            user_id="1", access_token="t", project_id=1
        )
        # Mock all client methods
        provider.client.import_keywords = AsyncMock(return_value={"countAdded": 2})
        provider.client.start_volume_check = AsyncMock(return_value={})
        provider.client.get_keywords_with_volumes = AsyncMock(return_value=[
            {"name": "seo аудит", "volume": 1200},
            {"name": "контент план", "volume": 800},
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await provider.get_volumes(["seo аудит", "контент план"])

        assert len(results) == 2
        assert results[0].keyword == "seo аудит"
        assert results[0].volume == 1200
        assert results[0].source == "topvisor"
        assert results[1].volume == 800

    @pytest.mark.asyncio
    async def test_get_volumes_error_returns_zeros(self):
        provider = TopvisorProvider(
            user_id="1", access_token="t", project_id=1
        )
        provider.client.import_keywords = AsyncMock(side_effect=ConnectionError("API down"))

        results = await provider.get_volumes(["kw1", "kw2"])
        assert len(results) == 2
        assert all(r.volume == 0 for r in results)
        assert all(r.source == "topvisor" for r in results)

    @pytest.mark.asyncio
    async def test_get_volumes_case_insensitive_matching(self):
        provider = TopvisorProvider(
            user_id="1", access_token="t", project_id=1
        )
        provider.client.import_keywords = AsyncMock(return_value={"countAdded": 1})
        provider.client.start_volume_check = AsyncMock(return_value={})
        provider.client.get_keywords_with_volumes = AsyncMock(return_value=[
            {"name": "SEO Аудит", "volume": 500},
        ])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            results = await provider.get_volumes(["seo аудит"])

        assert results[0].volume == 500


# =============================================================================
# Provider chain routing — Topvisor fallback
# =============================================================================

class TestTopvisorRouting:
    """Test that Topvisor is used as fallback in provider chain."""

    def test_topvisor_used_when_no_wordstat_no_rush(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = ""
        settings.topvisor_access_token = "tv-token"
        settings.topvisor_user_id = "tv-user"
        settings.topvisor_project_id = 42

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "topvisor"

    def test_wordstat_plus_topvisor_returns_composite(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "ws-key"
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = ""
        settings.topvisor_access_token = "tv-token"
        settings.topvisor_user_id = "tv-user"
        settings.topvisor_project_id = 42

        provider = get_volume_provider("ru", settings)
        # Wordstat + Topvisor both available → composite
        assert provider.source_name == "wordstat+topvisor"

    def test_topvisor_not_used_without_creds(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = ""
        settings.topvisor_access_token = ""
        settings.topvisor_user_id = ""
        settings.topvisor_project_id = 0

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "none"

    def test_topvisor_not_used_for_non_ru(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = ""
        settings.topvisor_access_token = "tv-token"
        settings.topvisor_user_id = "tv-user"
        settings.topvisor_project_id = 42

        provider = get_volume_provider("us", settings)
        assert provider.source_name == "none"
