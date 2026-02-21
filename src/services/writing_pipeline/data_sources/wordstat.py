"""
Yandex Wordstat provider via Yandex Cloud Search API.

API docs: https://yandex.cloud/ru/docs/search-api/operations/wordstat
Auth: Api-Key header
Endpoints:
  - POST /v2/wordstat/top — volumes + related queries
  - POST /v2/wordstat/dynamics — monthly trend data
  - POST /v2/wordstat/regions — regional breakdown

Region 225 = all Russia.
"""

import asyncio
import logging
from typing import List, Optional

import httpx

from .volume_provider import VolumeProvider, VolumeResult

logger = logging.getLogger(__name__)

BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"


class YandexWordstatProvider(VolumeProvider):
    """
    Yandex Wordstat via Yandex Cloud Search API (free Preview tier).

    Primary data source for Russian-language keyword volumes.
    """

    def __init__(
        self,
        api_key: str,
        region_id: int = 225,  # 225 = all Russia
        timeout: float = 60.0,
        max_concurrent: int = 3,
    ):
        self.api_key = api_key
        self.region_id = region_id
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @property
    def source_name(self) -> str:
        return "wordstat"

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """
        Fetch volumes from Wordstat GetTop endpoint.

        Each keyword requires a separate API call (Wordstat limitation).
        We run them concurrently with a semaphore.
        """
        if not keywords:
            return []

        tasks = [self._fetch_single_volume(kw) for kw in keywords]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        volume_results = []
        for kw, result in zip(keywords, results):
            if isinstance(result, Exception):
                logger.warning(f"Wordstat error for '{kw}': {result}")
                volume_results.append(VolumeResult(keyword=kw, volume=0, source="wordstat"))
            else:
                volume_results.append(result)

        return volume_results

    async def _fetch_single_volume(self, keyword: str) -> VolumeResult:
        """Fetch volume for a single keyword via GetTop."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/top",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": keyword,
                        "region_id": self.region_id,
                    },
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Wordstat GetTop HTTP {resp.status_code} for '{keyword}': {resp.text[:300]}")
                    return VolumeResult(keyword=keyword, volume=0, source="wordstat")

                data = resp.json()
                return self._parse_top_response(keyword, data)

    def _parse_top_response(self, keyword: str, data: dict) -> VolumeResult:
        """
        Parse GetTop response.

        Expected structure:
        {
          "query": "seo оптимизация",
          "impressions": 12345,
          "items": [
            {"query": "seo оптимизация сайта", "impressions": 4567},
            ...
          ]
        }
        """
        volume = data.get("impressions", 0) or 0

        return VolumeResult(
            keyword=keyword,
            volume=volume,
            source="wordstat",
        )

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Get related queries from Wordstat GetTop response."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/top",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": keyword,
                        "region_id": self.region_id,
                    },
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    return []

                data = resp.json()
                items = data.get("items", [])
                return [item["query"] for item in items if item.get("query")]

    async def get_dynamics(self, keyword: str) -> list[dict]:
        """
        Fetch monthly trend data via Wordstat dynamics endpoint.

        Returns list of {month: "2025-01", impressions: 1234}.
        """
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/dynamics",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": keyword,
                        "region_id": self.region_id,
                    },
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Wordstat dynamics HTTP {resp.status_code}: {resp.text[:300]}")
                    return []

                data = resp.json()
                return data.get("items", [])

    async def get_regions(self, keyword: str) -> dict[int, int]:
        """
        Fetch regional breakdown via Wordstat regions endpoint.

        Returns dict of region_id -> impressions.
        """
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/regions",
                    headers={
                        "Authorization": f"Api-Key {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": keyword,
                        "region_id": self.region_id,
                    },
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Wordstat regions HTTP {resp.status_code}: {resp.text[:300]}")
                    return {}

                data = resp.json()
                result = {}
                for item in data.get("items", []):
                    rid = item.get("region_id")
                    impressions = item.get("impressions", 0)
                    if rid is not None:
                        result[rid] = impressions
                return result
