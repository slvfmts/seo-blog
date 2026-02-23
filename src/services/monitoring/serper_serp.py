"""
Serper.dev SERP client for position tracking.

Uses /search endpoint with sequential requests (no batch API).
Cost: 1 credit per keyword check (~$0.001).

Replaces dataforseo_serp.py — same interface (RankingResult, check_position, check_positions_batch).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

# Region → Serper params (gl=country, hl=language)
REGION_MAP = {
    "ru": {"gl": "ru", "hl": "ru"},
    "russia": {"gl": "ru", "hl": "ru"},
    "kz": {"gl": "kz", "hl": "ru"},
    "kazakhstan": {"gl": "kz", "hl": "ru"},
    "us": {"gl": "us", "hl": "en"},
    "usa": {"gl": "us", "hl": "en"},
    "uk": {"gl": "uk", "hl": "en"},
    "gb": {"gl": "uk", "hl": "en"},
    "de": {"gl": "de", "hl": "de"},
    "fr": {"gl": "fr", "hl": "fr"},
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


class SerperSerpClient:
    """
    Check keyword positions via Serper.dev /search API.

    Usage:
        client = SerperSerpClient(api_key="...")
        result = await client.check_position("python tutorial", "example.com", "us")
        print(result.position)  # 5 or None
    """

    SEARCH_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout

    async def check_position(
        self,
        keyword: str,
        domain: str,
        region: str = "ru",
        depth: int = 100,
    ) -> RankingResult:
        """Check position of a single keyword."""
        results = await self.check_positions_batch(
            keywords=[keyword],
            domain=domain,
            region=region,
            depth=depth,
        )
        return results[0] if results else RankingResult(
            keyword=keyword, position=None, url=None, success=False, error="Empty response",
        )

    async def check_positions_batch(
        self,
        keywords: List[str],
        domain: str,
        region: str = "ru",
        depth: int = 100,
    ) -> List[RankingResult]:
        """
        Check positions for multiple keywords.

        Serper doesn't support batching, so we send sequential requests
        with a small delay to avoid rate limits.
        For 50-100 keywords daily check this takes 25-50 seconds — acceptable.
        """
        params = REGION_MAP.get(region.lower(), {"gl": "us", "hl": "en"})
        results = []

        async with httpx.AsyncClient() as client:
            for i, keyword in enumerate(keywords):
                if i > 0:
                    await asyncio.sleep(0.5)  # rate limit buffer

                result = await self._check_single(
                    client, keyword, domain, params, depth,
                )
                results.append(result)

        return results

    async def _check_single(
        self,
        client: httpx.AsyncClient,
        keyword: str,
        domain: str,
        params: dict,
        depth: int,
    ) -> RankingResult:
        """Send a single search request and find our domain."""
        try:
            response = await client.post(
                self.SEARCH_URL,
                headers={
                    "X-API-KEY": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "q": keyword,
                    "gl": params["gl"],
                    "hl": params["hl"],
                    "num": min(depth, 100),
                },
                timeout=self.timeout,
            )

            if response.status_code != 200:
                error = f"HTTP {response.status_code}: {response.text[:300]}"
                logger.error(f"Serper SERP error for '{keyword}': {error}")
                return RankingResult(
                    keyword=keyword, position=None, url=None,
                    success=False, error=error,
                )

            data = response.json()
            return self._parse_result(keyword, domain, data)

        except httpx.TimeoutException:
            error = f"Request timeout after {self.timeout}s"
            logger.error(f"Serper SERP timeout for '{keyword}': {error}")
            return RankingResult(
                keyword=keyword, position=None, url=None,
                success=False, error=error,
            )
        except Exception as e:
            logger.error(f"Serper SERP error for '{keyword}': {e}")
            return RankingResult(
                keyword=keyword, position=None, url=None,
                success=False, error=str(e),
            )

    def _parse_result(
        self,
        keyword: str,
        domain: str,
        data: dict,
    ) -> RankingResult:
        """Parse Serper search response and find our domain's position."""
        position = None
        ranking_url = None
        serp_features = []

        # Detect SERP features
        if data.get("knowledgeGraph"):
            serp_features.append("knowledge_graph")
        if data.get("answerBox"):
            serp_features.append("answer_box")
        if data.get("peopleAlsoAsk"):
            serp_features.append("people_also_ask")
        if data.get("topStories"):
            serp_features.append("top_stories")
        if data.get("images"):
            serp_features.append("images")
        if data.get("videos"):
            serp_features.append("videos")

        # Clean target domain for matching
        clean_domain = domain.lower().replace("www.", "")

        # Search organic results
        for item in data.get("organic", []):
            item_link = item.get("link", "")
            item_position = item.get("position")

            # Extract domain from URL
            try:
                from urllib.parse import urlparse
                parsed = urlparse(item_link)
                item_domain = parsed.netloc.lower().replace("www.", "")
            except Exception:
                item_domain = ""

            if clean_domain == item_domain or item_domain.endswith(f".{clean_domain}"):
                position = item_position
                ranking_url = item_link
                break

        return RankingResult(
            keyword=keyword,
            position=position,
            url=ranking_url,
            serp_features=serp_features,
            success=True,
            cost=0.0,  # tracked at account level, not per-request
        )
