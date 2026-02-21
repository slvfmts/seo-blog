"""Tests for Yandex Wordstat provider — mock HTTP responses."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from src.services.writing_pipeline.data_sources.wordstat import YandexWordstatProvider


@pytest.fixture
def provider():
    return YandexWordstatProvider(api_key="test-api-key", region_id=225)


# =============================================================================
# get_volumes
# =============================================================================

class TestGetVolumes:
    @pytest.mark.asyncio
    async def test_empty_keywords(self, provider):
        result = await provider.get_volumes([])
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_volume_fetch(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "query": "seo оптимизация",
            "impressions": 12345,
            "items": [
                {"query": "seo оптимизация сайта", "impressions": 4567},
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await provider.get_volumes(["seo оптимизация"])

        assert len(results) == 1
        assert results[0].keyword == "seo оптимизация"
        assert results[0].volume == 12345
        assert results[0].source == "wordstat"

    @pytest.mark.asyncio
    async def test_http_error_returns_zero(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            results = await provider.get_volumes(["test"])

        assert len(results) == 1
        assert results[0].volume == 0

    @pytest.mark.asyncio
    async def test_exception_returns_zero(self, provider):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            results = await provider.get_volumes(["test"])

        assert len(results) == 1
        assert results[0].volume == 0


# =============================================================================
# get_suggestions
# =============================================================================

class TestGetSuggestions:
    @pytest.mark.asyncio
    async def test_returns_related_queries(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "query": "seo",
            "impressions": 10000,
            "items": [
                {"query": "seo оптимизация", "impressions": 5000},
                {"query": "seo продвижение", "impressions": 3000},
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            suggestions = await provider.get_suggestions("seo")

        assert len(suggestions) == 2
        assert "seo оптимизация" in suggestions

    @pytest.mark.asyncio
    async def test_error_returns_empty(self, provider):
        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            suggestions = await provider.get_suggestions("seo")

        assert suggestions == []


# =============================================================================
# _parse_top_response
# =============================================================================

class TestParseTopResponse:
    def test_normal_response(self, provider):
        data = {"query": "test", "impressions": 999, "items": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 999
        assert result.keyword == "test"
        assert result.source == "wordstat"

    def test_missing_impressions(self, provider):
        data = {"query": "test", "items": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 0

    def test_null_impressions(self, provider):
        data = {"query": "test", "impressions": None, "items": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 0


# =============================================================================
# Provider properties
# =============================================================================

class TestProviderProperties:
    def test_source_name(self, provider):
        assert provider.source_name == "wordstat"

    def test_default_region(self):
        p = YandexWordstatProvider(api_key="key")
        assert p.region_id == 225
