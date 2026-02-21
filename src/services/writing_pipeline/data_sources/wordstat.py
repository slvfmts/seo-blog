"""
Yandex Wordstat provider via Yandex Cloud Search API v2.

Endpoint: POST https://searchapi.api.cloud.yandex.net/v2/wordstat/topRequests
Auth: Api-Key header (service account API key)
Docs: https://yandex.cloud/en/docs/search-api/api-ref/Wordstat/getTop

Required role on service account: search-api.webSearch.user

Region 225 = all Russia.
"""

import asyncio
import logging
from typing import Optional

import httpx

from .volume_provider import VolumeProvider, VolumeResult

logger = logging.getLogger(__name__)

BASE_URL = "https://searchapi.api.cloud.yandex.net/v2/wordstat"


class YandexWordstatProvider(VolumeProvider):
    """
    Yandex Wordstat via Yandex Cloud Search API v2.

    Primary data source for Russian-language keyword volumes.
    Uses /topRequests endpoint — returns totalCount + top related phrases.
    """

    def __init__(
        self,
        api_key: str,
        folder_id: str = "",
        region_id: int = 225,  # 225 = all Russia
        timeout: float = 60.0,
        max_concurrent: int = 3,
    ):
        self.api_key = api_key
        self.folder_id = folder_id
        self.region_id = region_id
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @property
    def source_name(self) -> str:
        return "wordstat"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Api-Key {self.api_key}",
            "Content-Type": "application/json",
        }

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """
        Fetch volumes from Wordstat topRequests endpoint.

        Each keyword requires a separate API call.
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

        found = sum(1 for r in volume_results if r.volume > 0)
        logger.info(f"Wordstat: got volumes for {found}/{len(volume_results)} keywords")
        return volume_results

    async def _fetch_single_volume(self, keyword: str) -> VolumeResult:
        """Fetch volume for a single keyword via topRequests."""
        async with self._semaphore:
            body = {
                "phrase": keyword,
                "regions": [str(self.region_id)],
                "devices": ["DEVICE_ALL"],
            }
            if self.folder_id:
                body["folderId"] = self.folder_id

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/topRequests",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(
                        f"Wordstat topRequests HTTP {resp.status_code} for '{keyword}': "
                        f"{resp.text[:300]}"
                    )
                    return VolumeResult(keyword=keyword, volume=0, source="wordstat")

                data = resp.json()
                return self._parse_top_response(keyword, data)

    def _parse_top_response(self, keyword: str, data: dict) -> VolumeResult:
        """
        Parse topRequests response.

        Response structure:
        {
          "totalCount": "12345",       # total impressions (string!)
          "results": [                 # top matching phrases
            {"phrase": "seo оптимизация сайта", "count": "4567"}
          ],
          "associations": [            # related queries
            {"phrase": "продвижение сайта", "count": "3456"}
          ]
        }
        """
        # totalCount is the main volume metric
        total = data.get("totalCount", 0)
        try:
            volume = int(total)
        except (ValueError, TypeError):
            volume = 0

        return VolumeResult(
            keyword=keyword,
            volume=volume,
            source="wordstat",
        )

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Get related queries from Wordstat topRequests — results + associations."""
        async with self._semaphore:
            body = {
                "phrase": keyword,
                "regions": [str(self.region_id)],
                "devices": ["DEVICE_ALL"],
                "numPhrases": "50",
            }
            if self.folder_id:
                body["folderId"] = self.folder_id

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/topRequests",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    return []

                data = resp.json()
                suggestions = []

                # Collect from results (matching phrases)
                for item in data.get("results", []):
                    phrase = item.get("phrase", "").strip()
                    if phrase and phrase.lower() != keyword.lower():
                        suggestions.append(phrase)

                # Collect from associations (related queries)
                for item in data.get("associations", []):
                    phrase = item.get("phrase", "").strip()
                    if phrase and phrase.lower() != keyword.lower():
                        suggestions.append(phrase)

                return suggestions

    async def get_dynamics(self, keyword: str) -> list[dict]:
        """
        Fetch monthly trend data via Wordstat dynamics endpoint.

        Returns list of {date, count, share}.
        """
        async with self._semaphore:
            body = {
                "phrase": keyword,
                "regions": [str(self.region_id)],
                "devices": ["DEVICE_ALL"],
            }
            if self.folder_id:
                body["folderId"] = self.folder_id

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/dynamics",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Wordstat dynamics HTTP {resp.status_code}: {resp.text[:300]}")
                    return []

                data = resp.json()
                return data.get("dynamics", data.get("items", []))

    async def get_regions(self, keyword: str) -> dict[int, int]:
        """
        Fetch regional breakdown via Wordstat regions endpoint.

        Returns dict of region_id -> impressions.
        """
        async with self._semaphore:
            body = {
                "phrase": keyword,
                "devices": ["DEVICE_ALL"],
            }
            if self.folder_id:
                body["folderId"] = self.folder_id

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/regionsDistribution",
                    headers=self._headers(),
                    json=body,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Wordstat regions HTTP {resp.status_code}: {resp.text[:300]}")
                    return {}

                data = resp.json()
                result = {}
                for item in data.get("regions", []):
                    rid = item.get("regionId")
                    count = item.get("count", 0)
                    if rid is not None:
                        try:
                            result[int(rid)] = int(count)
                        except (ValueError, TypeError):
                            pass
                return result
