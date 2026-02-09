"""
DataForSEO SERP API client for position tracking.

Uses /v3/serp/google/organic/live/advanced endpoint.
Cost: ~$0.002 per keyword check.

Separate from writing_pipeline/data_sources/dataforseo.py which handles
keyword metrics (search volume, difficulty). This module handles SERP position checks.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import httpx
import base64
import logging

logger = logging.getLogger(__name__)

# Location codes (same as in data_sources/dataforseo.py)
LOCATIONS = {
    "us": 2840,
    "usa": 2840,
    "ru": 2643,
    "russia": 2643,
    "uk": 2826,
    "gb": 2826,
    "de": 2276,
    "fr": 2250,
}


@dataclass
class RankingResult:
    """Result of a single keyword position check."""
    keyword: str
    position: Optional[int]  # None if not in top-N
    url: Optional[str]  # Which URL ranks
    serp_features: List[str] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    cost: float = 0.0


class DataForSEOSerpClient:
    """
    Check keyword positions via DataForSEO SERP Live Advanced API.

    Usage:
        client = DataForSEOSerpClient(login="...", password="...")
        result = await client.check_position("python tutorial", "example.com", 2840, "en")
        print(result.position)  # 5 or None
    """

    BASE_URL = "https://api.dataforseo.com/v3"

    def __init__(self, login: str, password: str, timeout: float = 120.0):
        self.login = login
        self.password = password
        self.timeout = timeout
        credentials = f"{login}:{password}"
        self._auth_header = f"Basic {base64.b64encode(credentials.encode()).decode()}"

    async def check_position(
        self,
        keyword: str,
        domain: str,
        location_code: int = 2643,
        language_code: str = "ru",
        depth: int = 100,
    ) -> RankingResult:
        """Check position of a single keyword."""
        results = await self.check_positions_batch(
            keywords=[keyword],
            domain=domain,
            location_code=location_code,
            language_code=language_code,
            depth=depth,
        )
        return results[0] if results else RankingResult(
            keyword=keyword, position=None, url=None, success=False, error="Empty response",
        )

    async def check_positions_batch(
        self,
        keywords: List[str],
        domain: str,
        location_code: int = 2643,
        language_code: str = "ru",
        depth: int = 100,
        batch_size: int = 100,
    ) -> List[RankingResult]:
        """
        Check positions for multiple keywords.

        DataForSEO supports up to 100 tasks per request,
        so we split into batches if needed.
        """
        all_results = []

        for i in range(0, len(keywords), batch_size):
            batch = keywords[i:i + batch_size]
            batch_results = await self._check_batch(
                batch, domain, location_code, language_code, depth,
            )
            all_results.extend(batch_results)

        return all_results

    async def _check_batch(
        self,
        keywords: List[str],
        domain: str,
        location_code: int,
        language_code: str,
        depth: int,
    ) -> List[RankingResult]:
        """Send a single batch request to DataForSEO SERP API."""
        # Build payload: one task per keyword
        payload = []
        for kw in keywords:
            payload.append({
                "keyword": kw,
                "location_code": location_code,
                "language_code": language_code,
                "depth": depth,
                "device": "desktop",
            })

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/serp/google/organic/live/advanced",
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    error = f"HTTP {response.status_code}: {response.text[:300]}"
                    logger.error(f"DataForSEO SERP error: {error}")
                    return [
                        RankingResult(keyword=kw, position=None, url=None, success=False, error=error)
                        for kw in keywords
                    ]

                data = response.json()
                return self._parse_response(data, keywords, domain)

        except httpx.TimeoutException:
            error = f"Request timeout after {self.timeout}s"
            logger.error(f"DataForSEO SERP timeout: {error}")
            return [
                RankingResult(keyword=kw, position=None, url=None, success=False, error=error)
                for kw in keywords
            ]
        except Exception as e:
            logger.error(f"DataForSEO SERP error: {e}")
            return [
                RankingResult(keyword=kw, position=None, url=None, success=False, error=str(e))
                for kw in keywords
            ]

    def _parse_response(
        self,
        data: dict,
        keywords: List[str],
        domain: str,
    ) -> List[RankingResult]:
        """Parse DataForSEO SERP response and find our domain's positions."""
        results = []

        if data.get("status_code") != 20000:
            error = data.get("status_message", "Unknown API error")
            return [
                RankingResult(keyword=kw, position=None, url=None, success=False, error=error)
                for kw in keywords
            ]

        total_cost = data.get("cost", 0)
        tasks = data.get("tasks", [])
        per_task_cost = total_cost / max(len(tasks), 1)

        # Map tasks to keywords by index
        for i, task in enumerate(tasks):
            kw = keywords[i] if i < len(keywords) else f"unknown_{i}"

            if task.get("status_code") != 20000:
                results.append(RankingResult(
                    keyword=kw,
                    position=None,
                    url=None,
                    success=False,
                    error=task.get("status_message", "Task failed"),
                    cost=per_task_cost,
                ))
                continue

            # Parse SERP items
            position = None
            ranking_url = None
            serp_features = []

            task_results = task.get("result", [])
            for result in task_results:
                items = result.get("items", [])
                for item in items:
                    item_type = item.get("type", "")

                    # Collect SERP features
                    if item_type not in ("organic", "") and item_type not in serp_features:
                        serp_features.append(item_type)

                    # Find our domain in organic results
                    if item_type == "organic" and position is None:
                        item_domain = item.get("domain", "")
                        item_url = item.get("url", "")
                        rank = item.get("rank_group")

                        # Match domain (handle www prefix)
                        clean_domain = domain.lower().replace("www.", "")
                        clean_item = item_domain.lower().replace("www.", "")

                        if clean_domain == clean_item or clean_item.endswith(f".{clean_domain}"):
                            position = rank
                            ranking_url = item_url

            results.append(RankingResult(
                keyword=kw,
                position=position,
                url=ranking_url,
                serp_features=serp_features,
                success=True,
                cost=per_task_cost,
            ))

        return results
