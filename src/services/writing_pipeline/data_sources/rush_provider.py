"""
Rush Analytics provider — RU volume source via Yandex Wordstat data.

Swagger: https://app.rush-analytics.ru/apiv2/doc/
Base URL: https://app.rush-analytics.ru/apiv2
Auth: apikey in request body (POST) or query param (GET)

Endpoints (from swagger):
  POST /create/wordstat     → {id, type}
  GET  /status/{type}/{id}  → {id, status, resulturl}
  GET  /result/wordstat/{id}→ WordstatResult
  POST /create/suggest      → {id, type}
  GET  /result/suggest/{id} → SuggestResult
  GET  /balance             → balance info

Rate limit: 1 req/sec. Threads depend on plan tier.
"""

import asyncio
import logging

import httpx

from .volume_provider import VolumeProvider, VolumeResult

logger = logging.getLogger(__name__)

BASE_URL = "https://app.rush-analytics.ru/apiv2"

# Project type IDs (for status endpoint)
TYPE_WORDSTAT = 4
TYPE_SUGGEST = 7


class RushAnalyticsProvider(VolumeProvider):
    """
    Rush Analytics API — Yandex Wordstat wrapper.

    Uses /create/wordstat with projecttype=SearchVolume
    to get exact Wordstat frequency data.
    """

    def __init__(
        self,
        api_key: str,
        region_id: int = 213,  # 213 = Moscow; 225 = all Russia
        timeout: float = 180.0,
    ):
        self.api_key = api_key
        self.region_id = region_id
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(1)  # 1 req/sec

    @property
    def source_name(self) -> str:
        return "rush"

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """
        Fetch volumes via Rush Analytics Wordstat endpoint.

        Creates a SearchVolume task, polls until done, parses results.
        """
        if not keywords:
            return []

        try:
            # Step 1: Create task
            project_id = await self._create_wordstat_task(keywords)
            if not project_id:
                return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

            logger.info(f"Rush: created wordstat task {project_id} for {len(keywords)} keywords")

            # Step 2: Poll for completion
            if not await self._poll_task(project_id, task_type=TYPE_WORDSTAT):
                logger.warning(f"Rush: task {project_id} did not complete in time")
                return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

            # Step 3: Get results
            return await self._get_wordstat_results(project_id, keywords)

        except Exception as e:
            logger.error(f"Rush Analytics error: {e}")
            return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

    async def _create_wordstat_task(self, keywords: list[str]) -> int | None:
        """Create a Wordstat SearchVolume task. Returns project id."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/create/wordstat",
                    json={
                        "apikey": self.api_key,
                        "name": f"seo-blog-vol-{len(keywords)}kw",
                        "regionid": self.region_id,
                        "projecttype": "SearchVolume",
                        "normal": True,
                        "parenthesis": False,
                        "exclamation": True,
                        "wordorder": False,
                        "minimumwordstat": 0,
                        "keywords": keywords,
                        "stopwords": ["xxx_placeholder"],  # API requires ≥1 stopword
                    },
                    timeout=self.timeout,
                )

                if resp.status_code not in (200, 201):
                    logger.error(f"Rush create wordstat HTTP {resp.status_code}: {resp.text[:500]}")
                    return None

                data = resp.json()
                project_id = data.get("id")
                if not project_id:
                    logger.error(f"Rush: no id in create response: {data}")
                    return None
                return project_id

    async def _poll_task(self, project_id: int, task_type: int, max_wait: int = 300) -> bool:
        """Poll task status until completed or timeout."""
        elapsed = 0
        interval = 5

        while elapsed < max_wait:
            await asyncio.sleep(interval)
            elapsed += interval

            async with self._semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{BASE_URL}/status/{task_type}/{project_id}",
                        params={"apikey": self.api_key},
                        timeout=30.0,
                    )

                    if resp.status_code != 200:
                        logger.debug(f"Rush poll HTTP {resp.status_code}: {resp.text[:200]}")
                        continue

                    data = resp.json()
                    status = (data.get("status") or "").lower()

                    if status in ("completed", "done", "finished"):
                        logger.info(f"Rush: task {project_id} completed after {elapsed}s")
                        return True
                    elif status in ("failed", "error"):
                        logger.error(f"Rush: task {project_id} failed: {data}")
                        return False

            # Increase interval after 30s
            if elapsed > 30:
                interval = 10

        logger.warning(f"Rush: task {project_id} timed out after {max_wait}s")
        return False

    async def _get_wordstat_results(self, project_id: int, keywords: list[str]) -> list[VolumeResult]:
        """Fetch and parse Wordstat results."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{BASE_URL}/result/wordstat/{project_id}",
                    params={"apikey": self.api_key},
                    timeout=30.0,
                )

                if resp.status_code != 200:
                    logger.error(f"Rush results HTTP {resp.status_code}: {resp.text[:300]}")
                    return [VolumeResult(keyword=kw, volume=0, source="rush") for kw in keywords]

                data = resp.json()

        # Parse wordstatSearchVolumeResult
        volume_map: dict[str, dict] = {}
        for item in data.get("wordstatSearchVolumeResult", []):
            kw = (item.get("keyword") or "").lower().strip()
            if kw:
                volume_map[kw] = {
                    "displays": item.get("displays", 0) or 0,
                    "exact": item.get("displaysexclamation", 0) or 0,
                }

        # Map back to original keywords
        results = []
        for kw in keywords:
            key = kw.lower().strip()
            info = volume_map.get(key, {})
            # Prefer exact match (exclamation), fall back to broad (displays)
            volume = info.get("exact") or info.get("displays", 0)
            results.append(VolumeResult(keyword=kw, volume=volume, source="rush"))

        found = sum(1 for r in results if r.volume > 0)
        logger.info(f"Rush: got volumes for {found}/{len(results)} keywords")
        return results

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Get search suggestions from Yandex via Rush suggest endpoint."""
        try:
            project_id = await self._create_suggest_task(keyword)
            if not project_id:
                return []

            if not await self._poll_task(project_id, task_type=TYPE_SUGGEST):
                return []

            return await self._get_suggest_results(project_id)

        except Exception as e:
            logger.warning(f"Rush suggestions error: {e}")
            return []

    async def _create_suggest_task(self, keyword: str) -> int | None:
        """Create a suggestions task."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{BASE_URL}/create/suggest",
                    json={
                        "apikey": self.api_key,
                        "name": f"seo-blog-suggest",
                        "yandexRegionId": self.region_id,
                        "depth": 1,
                        "keywords": [keyword],
                        "stopwords": ["xxx_placeholder"],
                    },
                    timeout=self.timeout,
                )

                if resp.status_code not in (200, 201):
                    logger.error(f"Rush suggest create HTTP {resp.status_code}: {resp.text[:300]}")
                    return None

                data = resp.json()
                return data.get("id")

    async def _get_suggest_results(self, project_id: int) -> list[str]:
        """Fetch suggest results. Format: {keyword: [suggest1, suggest2, ...]}"""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{BASE_URL}/result/suggest/{project_id}",
                    params={"apikey": self.api_key},
                    timeout=30.0,
                )

                if resp.status_code != 200:
                    return []

                data = resp.json()
                # SuggestResult is {keyword: [suggestions]}
                suggestions = []
                if isinstance(data, dict):
                    for kw, items in data.items():
                        if isinstance(items, list):
                            suggestions.extend(items)
                return suggestions

    async def check_balance(self) -> dict:
        """Check account credit balance."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/balance",
                params={"apikey": self.api_key},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    return data
                return {"balance": data}
            return {"error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
