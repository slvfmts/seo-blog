"""
Rush Analytics provider — alternative RU volume source.

Uses Rush Analytics API v2 for:
- Keyword research (Yandex Wordstat source)
- Autocomplete suggestions from Yandex

Auth: API key from Rush Analytics dashboard (Pro+ plan required).
Rate limit: 1 req/sec.
Pricing: ~$0.01-0.02 per operation.

API docs: https://rush-analytics.ru/api
"""

import asyncio
import logging
from typing import List

import httpx

from .volume_provider import VolumeProvider, VolumeResult

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rush-analytics.ru/v2"


class RushAnalyticsProvider(VolumeProvider):
    """
    Rush Analytics API v2 — Yandex Wordstat wrapper.

    Use as plan B if direct Yandex Wordstat API is unavailable or rate-limited.
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(1)  # 1 req/sec rate limit

    @property
    def source_name(self) -> str:
        return "rush"

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """
        Fetch volumes via Rush Analytics keyword research.

        Rush supports batch requests — we send all keywords at once,
        then poll for the result.
        """
        if not keywords:
            return []

        try:
            # Step 1: Create a task
            task_id = await self._create_task(keywords)
            if not task_id:
                return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

            # Step 2: Poll for completion (max 2 min)
            result_data = await self._poll_task(task_id, max_wait=120)
            if not result_data:
                return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

            # Step 3: Parse results
            return self._parse_results(keywords, result_data)

        except Exception as e:
            logger.error(f"Rush Analytics error: {e}")
            return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

    async def _create_task(self, keywords: list[str]) -> str | None:
        """Create a keyword research task."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/keyword-research/create",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "keywords": keywords,
                        "source": "yandex_wordstat",
                        "region_id": 225,  # all Russia
                    },
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    logger.error(f"Rush create task HTTP {resp.status_code}: {resp.text[:300]}")
                    return None

                data = resp.json()
                return data.get("task_id") or data.get("id")

    async def _poll_task(self, task_id: str, max_wait: int = 120) -> dict | None:
        """Poll task until completion or timeout."""
        elapsed = 0
        interval = 3

        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval

            async with self._semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{BASE_URL}/keyword-research/{task_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=30.0,
                    )

                    if resp.status_code != 200:
                        continue

                    data = resp.json()
                    status = data.get("status", "")

                    if status == "completed":
                        return data.get("result", data)
                    elif status in ("failed", "error"):
                        logger.error(f"Rush task {task_id} failed: {data.get('error', 'unknown')}")
                        return None

        logger.warning(f"Rush task {task_id} timed out after {max_wait}s")
        return None

    def _parse_results(self, keywords: list[str], data: dict) -> list[VolumeResult]:
        """Parse Rush Analytics result into VolumeResult list."""
        # Build lookup from result items
        volume_map: dict[str, int] = {}
        items = data.get("items", data.get("keywords", []))
        for item in items:
            kw = (item.get("keyword") or item.get("query") or "").lower().strip()
            vol = item.get("volume") or item.get("impressions") or 0
            if kw:
                volume_map[kw] = vol

        results = []
        for kw in keywords:
            vol = volume_map.get(kw.lower().strip(), 0)
            results.append(VolumeResult(
                keyword=kw,
                volume=vol,
                source="rush",
            ))
        return results

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Get autocomplete suggestions from Yandex via Rush."""
        try:
            async with self._semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{BASE_URL}/autocomplete",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "keyword": keyword,
                            "source": "yandex",
                        },
                        timeout=30.0,
                    )

                    if resp.status_code != 200:
                        return []

                    data = resp.json()
                    return [
                        item.get("query", "")
                        for item in data.get("suggestions", [])
                        if item.get("query")
                    ]
        except Exception as e:
            logger.warning(f"Rush autocomplete error: {e}")
            return []
