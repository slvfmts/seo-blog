"""
DataForSEO integration for keyword metrics.

Provides:
- Search volume data
- Keyword difficulty
- CPC (cost per click)
- Competition level

API Documentation: https://docs.dataforseo.com/
Pricing: $50 minimum deposit, ~$0.05/keyword
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import httpx
import base64
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class KeywordMetrics:
    """Metrics for a single keyword."""
    keyword: str
    search_volume: int  # Monthly search volume
    difficulty: float  # 0-100 scale
    cpc: float  # Cost per click in USD
    competition: float  # 0-1 scale
    competition_level: str  # LOW, MEDIUM, HIGH
    trend: Optional[List[int]] = None  # Monthly search volumes (last 12 months)


@dataclass
class KeywordMetricsResult:
    """Result of keyword metrics fetch."""
    keywords: List[KeywordMetrics]
    success: bool
    error: Optional[str] = None
    cost: Optional[float] = None  # API cost for this request


@dataclass
class KeywordExpansionResult:
    """Result of keyword expansion (suggestions or related)."""
    keywords: List[KeywordMetrics]
    seed_keyword: str
    source: str  # "suggestions" or "related"
    success: bool
    error: Optional[str] = None
    cost: Optional[float] = None


class DataForSEO:
    """
    DataForSEO API client for keyword metrics.

    Usage:
        client = DataForSEO(login="your_login", password="your_password")
        result = await client.get_keyword_metrics(
            ["python tutorial", "learn python"],
            location_code=2840,  # USA
            language_code="en",
        )
        for kw in result.keywords:
            print(f"{kw.keyword}: {kw.search_volume} vol, {kw.difficulty} diff")

    Location codes:
        - 2840: United States
        - 2643: Russia
        - 2826: United Kingdom
        - 2276: Germany
    """

    BASE_URL = "https://api.dataforseo.com/v3"

    # Common location codes
    LOCATIONS = {
        "us": 2840,
        "usa": 2840,
        "ru": 2643,
        "russia": 2643,
        "uk": 2826,
        "gb": 2826,
        "de": 2276,
        "germany": 2276,
        "fr": 2250,
        "france": 2250,
    }

    def __init__(
        self,
        login: str,
        password: str,
        timeout: float = 120.0,
    ):
        """
        Initialize DataForSEO client.

        Args:
            login: DataForSEO API login
            password: DataForSEO API password
            timeout: Request timeout in seconds
        """
        self.login = login
        self.password = password
        self.timeout = timeout
        self._auth_header = self._create_auth_header()

    def _create_auth_header(self) -> str:
        """Create Basic auth header."""
        credentials = f"{self.login}:{self.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def get_location_code(self, region: str) -> int:
        """
        Get location code from region string.

        Args:
            region: Region identifier (us, ru, uk, etc.)

        Returns:
            Location code for DataForSEO API
        """
        return self.LOCATIONS.get(region.lower(), 2840)

    async def get_keyword_metrics(
        self,
        keywords: List[str],
        location_code: Optional[int] = None,
        location_name: Optional[str] = None,
        language_code: str = "en",
    ) -> KeywordMetricsResult:
        """
        Get search volume and keyword metrics.

        Args:
            keywords: List of keywords (max 1000 per request)
            location_code: DataForSEO location code (e.g., 2840 for USA)
            location_name: Alternative: location name (e.g., "us", "ru")
            language_code: Language code (e.g., "en", "ru")

        Returns:
            KeywordMetricsResult with metrics for each keyword
        """
        if not keywords:
            return KeywordMetricsResult(
                keywords=[],
                success=True,
            )

        # Limit to 1000 keywords per request
        keywords = keywords[:1000]

        # Resolve location code
        if location_code is None:
            if location_name:
                location_code = self.get_location_code(location_name)
            else:
                location_code = 2840  # Default to USA

        # Build request payload
        payload = [{
            "keywords": keywords,
            "location_code": location_code,
            "language_code": language_code,
        }]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/keywords_data/google_ads/search_volume/live",
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    return KeywordMetricsResult(
                        keywords=[],
                        success=False,
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                    )

                data = response.json()
                return self._parse_search_volume_response(data, keywords)

        except httpx.TimeoutException:
            return KeywordMetricsResult(
                keywords=[],
                success=False,
                error=f"Request timeout after {self.timeout}s",
            )
        except Exception as e:
            logger.error(f"DataForSEO error: {e}")
            return KeywordMetricsResult(
                keywords=[],
                success=False,
                error=str(e),
            )

    def _parse_search_volume_response(
        self,
        data: Dict[str, Any],
        original_keywords: List[str],
    ) -> KeywordMetricsResult:
        """Parse DataForSEO search volume response."""
        keywords = []
        cost = None

        # Check for errors
        if data.get("status_code") != 20000:
            return KeywordMetricsResult(
                keywords=[],
                success=False,
                error=data.get("status_message", "Unknown error"),
            )

        # Extract cost
        cost = data.get("cost", 0)

        # Parse results
        tasks = data.get("tasks", [])
        if not tasks:
            return KeywordMetricsResult(
                keywords=[],
                success=False,
                error="No tasks in response",
            )

        task = tasks[0]
        if task.get("status_code") != 20000:
            return KeywordMetricsResult(
                keywords=[],
                success=False,
                error=task.get("status_message", "Task failed"),
            )

        results = task.get("result", [])
        keywords_found = set()

        for result in results:
            keyword = result.get("keyword", "")
            keywords_found.add(keyword.lower())

            # Extract metrics
            search_volume = result.get("search_volume", 0) or 0
            cpc = result.get("cpc", 0) or 0
            competition = result.get("competition", 0) or 0
            competition_level = result.get("competition_level", "LOW") or "LOW"

            # Monthly trend data
            monthly_searches = result.get("monthly_searches", [])
            trend = None
            if monthly_searches:
                trend = [
                    m.get("search_volume", 0) or 0
                    for m in monthly_searches[-12:]  # Last 12 months
                ]

            # Calculate difficulty (DataForSEO doesn't provide difficulty directly,
            # so we estimate based on competition and volume)
            difficulty = self._estimate_difficulty(competition, search_volume)

            keywords.append(KeywordMetrics(
                keyword=keyword,
                search_volume=search_volume,
                difficulty=difficulty,
                cpc=cpc,
                competition=competition,
                competition_level=competition_level,
                trend=trend,
            ))

        # Add missing keywords with zero metrics
        for kw in original_keywords:
            if kw.lower() not in keywords_found:
                keywords.append(KeywordMetrics(
                    keyword=kw,
                    search_volume=0,
                    difficulty=0,
                    cpc=0,
                    competition=0,
                    competition_level="LOW",
                    trend=None,
                ))

        return KeywordMetricsResult(
            keywords=keywords,
            success=True,
            cost=cost,
        )

    def _estimate_difficulty(
        self,
        competition: float,
        search_volume: int,
    ) -> float:
        """
        Estimate keyword difficulty based on competition and volume.

        This is a rough estimate - for accurate difficulty,
        use the keyword_difficulty endpoint (separate API call).

        Args:
            competition: Competition level 0-1
            search_volume: Monthly search volume

        Returns:
            Estimated difficulty 0-100
        """
        # Base difficulty from competition (0-1 -> 0-60)
        base = competition * 60

        # Volume adjustment (higher volume = slightly higher difficulty)
        if search_volume > 100000:
            volume_adj = 20
        elif search_volume > 10000:
            volume_adj = 15
        elif search_volume > 1000:
            volume_adj = 10
        elif search_volume > 100:
            volume_adj = 5
        else:
            volume_adj = 0

        return min(100, base + volume_adj)

    async def get_keyword_difficulty(
        self,
        keywords: List[str],
        location_code: int = 2840,
        language_code: str = "en",
    ) -> Dict[str, float]:
        """
        Get accurate keyword difficulty scores.

        Note: This is a separate API endpoint with additional cost.

        Args:
            keywords: List of keywords
            location_code: Location code
            language_code: Language code

        Returns:
            Dict mapping keyword to difficulty score (0-100)
        """
        payload = [{
            "keywords": keywords[:1000],
            "location_code": location_code,
            "language_code": language_code,
        }]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/dataforseo_labs/google/keyword_difficulty/live",
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    logger.error(f"Keyword difficulty API error: {response.status_code}")
                    return {}

                data = response.json()
                return self._parse_difficulty_response(data)

        except Exception as e:
            logger.error(f"Keyword difficulty error: {e}")
            return {}

    def _parse_difficulty_response(self, data: Dict[str, Any]) -> Dict[str, float]:
        """Parse keyword difficulty response."""
        result = {}

        tasks = data.get("tasks", [])
        if not tasks:
            return result

        task = tasks[0]
        items = task.get("result", [])

        for item in items:
            keyword = item.get("keyword", "")
            difficulty = item.get("keyword_difficulty", 0)
            result[keyword] = difficulty

        return result

    async def get_keyword_suggestions(
        self,
        keyword: str,
        location_code: int = 2840,
        language_code: str = "en",
        limit: int = 700,
    ) -> KeywordExpansionResult:
        """
        Get keyword suggestions (seed expansion) from DataForSEO Labs.

        Args:
            keyword: Seed keyword to expand
            location_code: Location code (e.g., 2840 for USA)
            language_code: Language code (e.g., "en", "ru")
            limit: Max number of suggestions (default 700)

        Returns:
            KeywordExpansionResult with discovered keywords
        """
        payload = [{
            "keyword": keyword,
            "location_code": location_code,
            "language_code": language_code,
            "limit": limit,
            "include_seed_keyword": True,
        }]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/dataforseo_labs/google/keyword_suggestions/live",
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    return KeywordExpansionResult(
                        keywords=[],
                        seed_keyword=keyword,
                        source="suggestions",
                        success=False,
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                    )

                data = response.json()
                return self._parse_suggestions_response(data, keyword)

        except httpx.TimeoutException:
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=keyword,
                source="suggestions",
                success=False,
                error=f"Request timeout after {self.timeout}s",
            )
        except Exception as e:
            logger.error(f"DataForSEO keyword_suggestions error for '{keyword}': {e}")
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=keyword,
                source="suggestions",
                success=False,
                error=str(e),
            )

    def _parse_suggestions_response(
        self,
        data: Dict[str, Any],
        seed_keyword: str,
    ) -> KeywordExpansionResult:
        """Parse keyword_suggestions response."""
        if data.get("status_code") != 20000:
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=seed_keyword,
                source="suggestions",
                success=False,
                error=data.get("status_message", "Unknown error"),
            )

        cost = data.get("cost", 0)
        tasks = data.get("tasks", [])
        if not tasks:
            return KeywordExpansionResult(
                keywords=[], seed_keyword=seed_keyword, source="suggestions",
                success=False, error="No tasks in response",
            )

        task = tasks[0]
        if task.get("status_code") != 20000:
            return KeywordExpansionResult(
                keywords=[], seed_keyword=seed_keyword, source="suggestions",
                success=False, error=task.get("status_message", "Task failed"),
            )

        results = task.get("result", [])
        keywords = []

        for result_block in results:
            items = result_block.get("items", [])
            for item in items:
                kw_text = item.get("keyword", "")
                if not kw_text:
                    continue

                kw_info = item.get("keyword_info", {}) or {}
                kw_props = item.get("keyword_properties", {}) or {}

                search_volume = kw_info.get("search_volume", 0) or 0
                competition = kw_info.get("competition", 0) or 0
                cpc = kw_info.get("cpc", 0) or 0
                competition_level = kw_info.get("competition_level", "LOW") or "LOW"

                # Prefer keyword_difficulty from keyword_properties if available
                kw_difficulty = kw_props.get("keyword_difficulty")
                if kw_difficulty is not None:
                    difficulty = float(kw_difficulty)
                else:
                    difficulty = self._estimate_difficulty(competition, search_volume)

                # Monthly trend
                monthly_searches = kw_info.get("monthly_searches", [])
                trend = None
                if monthly_searches:
                    trend = [
                        m.get("search_volume", 0) or 0
                        for m in monthly_searches[-12:]
                    ]

                keywords.append(KeywordMetrics(
                    keyword=kw_text,
                    search_volume=search_volume,
                    difficulty=difficulty,
                    cpc=cpc,
                    competition=competition,
                    competition_level=competition_level,
                    trend=trend,
                ))

        return KeywordExpansionResult(
            keywords=keywords,
            seed_keyword=seed_keyword,
            source="suggestions",
            success=True,
            cost=cost,
        )

    async def get_related_keywords(
        self,
        keyword: str,
        location_code: int = 2840,
        language_code: str = "en",
        depth: int = 2,
        limit: int = 500,
    ) -> KeywordExpansionResult:
        """
        Get related keywords from DataForSEO Labs.

        Args:
            keyword: Seed keyword
            location_code: Location code
            language_code: Language code
            depth: Relation depth (2 = ~72 keywords/seed)
            limit: Max number of related keywords

        Returns:
            KeywordExpansionResult with discovered keywords
        """
        payload = [{
            "keyword": keyword,
            "location_code": location_code,
            "language_code": language_code,
            "depth": depth,
            "limit": limit,
        }]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/dataforseo_labs/google/related_keywords/live",
                    headers={
                        "Authorization": self._auth_header,
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code != 200:
                    return KeywordExpansionResult(
                        keywords=[],
                        seed_keyword=keyword,
                        source="related",
                        success=False,
                        error=f"HTTP {response.status_code}: {response.text[:500]}",
                    )

                data = response.json()
                return self._parse_related_response(data, keyword)

        except httpx.TimeoutException:
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=keyword,
                source="related",
                success=False,
                error=f"Request timeout after {self.timeout}s",
            )
        except Exception as e:
            logger.error(f"DataForSEO related_keywords error for '{keyword}': {e}")
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=keyword,
                source="related",
                success=False,
                error=str(e),
            )

    def _parse_related_response(
        self,
        data: Dict[str, Any],
        seed_keyword: str,
    ) -> KeywordExpansionResult:
        """Parse related_keywords response. Note: items have an extra keyword_data wrapper."""
        if data.get("status_code") != 20000:
            return KeywordExpansionResult(
                keywords=[],
                seed_keyword=seed_keyword,
                source="related",
                success=False,
                error=data.get("status_message", "Unknown error"),
            )

        cost = data.get("cost", 0)
        tasks = data.get("tasks", [])
        if not tasks:
            return KeywordExpansionResult(
                keywords=[], seed_keyword=seed_keyword, source="related",
                success=False, error="No tasks in response",
            )

        task = tasks[0]
        if task.get("status_code") != 20000:
            return KeywordExpansionResult(
                keywords=[], seed_keyword=seed_keyword, source="related",
                success=False, error=task.get("status_message", "Task failed"),
            )

        results = task.get("result", [])
        keywords = []

        for result_block in results:
            items = result_block.get("items", [])
            for item in items:
                # Related keywords have extra keyword_data wrapper
                kw_data = item.get("keyword_data", {}) or {}
                kw_text = kw_data.get("keyword", "")
                if not kw_text:
                    continue

                kw_info = kw_data.get("keyword_info", {}) or {}
                kw_props = kw_data.get("keyword_properties", {}) or {}

                search_volume = kw_info.get("search_volume", 0) or 0
                competition = kw_info.get("competition", 0) or 0
                cpc = kw_info.get("cpc", 0) or 0
                competition_level = kw_info.get("competition_level", "LOW") or "LOW"

                kw_difficulty = kw_props.get("keyword_difficulty")
                if kw_difficulty is not None:
                    difficulty = float(kw_difficulty)
                else:
                    difficulty = self._estimate_difficulty(competition, search_volume)

                monthly_searches = kw_info.get("monthly_searches", [])
                trend = None
                if monthly_searches:
                    trend = [
                        m.get("search_volume", 0) or 0
                        for m in monthly_searches[-12:]
                    ]

                keywords.append(KeywordMetrics(
                    keyword=kw_text,
                    search_volume=search_volume,
                    difficulty=difficulty,
                    cpc=cpc,
                    competition=competition,
                    competition_level=competition_level,
                    trend=trend,
                ))

        return KeywordExpansionResult(
            keywords=keywords,
            seed_keyword=seed_keyword,
            source="related",
            success=True,
            cost=cost,
        )
