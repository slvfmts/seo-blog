"""Tests for Yandex Wordstat provider — mock HTTP responses."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from src.services.writing_pipeline.data_sources.wordstat import YandexWordstatProvider


@pytest.fixture
def provider():
    return YandexWordstatProvider(api_key="test-api-key", folder_id="test-folder", region_id=225)


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
            "totalCount": "12345",
            "results": [
                {"phrase": "seo оптимизация сайта", "count": "4567"},
            ],
            "associations": [],
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
            "totalCount": "10000",
            "results": [
                {"phrase": "seo оптимизация", "count": "5000"},
                {"phrase": "seo продвижение", "count": "3000"},
            ],
            "associations": [
                {"phrase": "раскрутка сайта", "count": "2000"},
            ],
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            suggestions = await provider.get_suggestions("seo")

        assert len(suggestions) == 3
        assert "seo оптимизация" in suggestions
        assert "раскрутка сайта" in suggestions

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
        data = {"totalCount": "999", "results": [], "associations": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 999
        assert result.keyword == "test"
        assert result.source == "wordstat"

    def test_integer_total_count(self, provider):
        data = {"totalCount": 5000, "results": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 5000

    def test_missing_total_count(self, provider):
        data = {"results": []}
        result = provider._parse_top_response("test", data)
        assert result.volume == 0

    def test_null_total_count(self, provider):
        data = {"totalCount": None, "results": []}
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

    def test_folder_id_stored(self):
        p = YandexWordstatProvider(api_key="key", folder_id="folder123")
        assert p.folder_id == "folder123"
