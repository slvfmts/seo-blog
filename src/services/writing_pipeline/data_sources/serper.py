"""
Serper.dev data source for SERP data.

Provides rich search results including:
- Organic results with positions and snippets
- People Also Ask questions
- Related searches
"""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import httpx


@dataclass
class OrganicResult:
    """A single organic search result."""
    position: int
    title: str
    link: str
    snippet: str
    domain: Optional[str] = None


@dataclass
class PAAQuestion:
    """A People Also Ask question."""
    question: str
    answer: Optional[str] = None
    source: Optional[str] = None


@dataclass
class SerperResult:
    """Results from a Serper.dev search."""
    query: str
    organic: List[OrganicResult]
    people_also_ask: List[PAAQuestion]
    related_searches: List[str]
    raw_data: Dict[str, Any]

    def to_fact_packer_format(self) -> Dict[str, Any]:
        """
        Convert to format expected by Fact Packer prompt.

        Returns dict with:
        - query: search query
        - organic: list of organic results
        - peopleAlsoAsk: list of PAA questions
        - relatedSearches: list of related queries
        """
        return {
            "query": self.query,
            "organic": [
                {
                    "position": r.position,
                    "title": r.title,
                    "link": r.link,
                    "snippet": r.snippet,
                }
                for r in self.organic
            ],
            "peopleAlsoAsk": [
                {
                    "question": q.question,
                    "answer": q.answer,
                    "source": q.source,
                }
                for q in self.people_also_ask
            ],
            "relatedSearches": [
                {"query": s} for s in self.related_searches
            ],
        }


class SerperDataSource:
    """
    Serper.dev API client for SERP data.

    Usage:
        source = SerperDataSource(api_key="...")
        results = await source.search("how to learn python", region="ru")
        for r in results.organic:
            print(r.title, r.link)
    """

    API_URL = "https://google.serper.dev/search"

    def __init__(self, api_key: str):
        """
        Initialize Serper data source.

        Args:
            api_key: Serper.dev API key
        """
        self.api_key = api_key

    async def search(
        self,
        query: str,
        region: str = "ru",
        num_results: int = 10,
    ) -> SerperResult:
        """
        Execute a search query.

        Args:
            query: Search query
            region: Target region (ru, us, etc.)
            num_results: Number of results to fetch

        Returns:
            SerperResult with organic results, PAA, and related searches
        """
        # Map region to Google parameters
        gl = "ru" if region.lower() in ["ru", "россия", "russia"] else "us"
        hl = "ru" if region.lower() in ["ru", "россия", "russia"] else "en"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.API_URL,
                headers={
                    "X-API-KEY": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "q": query,
                    "gl": gl,
                    "hl": hl,
                    "num": num_results,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

        return self._parse_response(query, data)

    async def search_batch(
        self,
        queries: List[str],
        region: str = "ru",
        num_results: int = 10,
    ) -> List[SerperResult]:
        """
        Execute multiple search queries.

        Args:
            queries: List of search queries
            region: Target region
            num_results: Number of results per query

        Returns:
            List of SerperResult
        """
        results = []
        for query in queries:
            try:
                result = await self.search(query, region, num_results)
                results.append(result)
            except Exception as e:
                # Create empty result on error
                results.append(SerperResult(
                    query=query,
                    organic=[],
                    people_also_ask=[],
                    related_searches=[],
                    raw_data={"error": str(e)},
                ))
        return results

    def _parse_response(self, query: str, data: Dict[str, Any]) -> SerperResult:
        """Parse Serper.dev API response."""
        # Parse organic results
        organic = []
        for i, item in enumerate(data.get("organic", []), start=1):
            organic.append(OrganicResult(
                position=i,
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
                domain=item.get("domain"),
            ))

        # Parse People Also Ask
        paa = []
        for item in data.get("peopleAlsoAsk", []):
            paa.append(PAAQuestion(
                question=item.get("question", ""),
                answer=item.get("snippet"),
                source=item.get("link"),
            ))

        # Parse related searches
        related = [
            item.get("query", "")
            for item in data.get("relatedSearches", [])
        ]

        return SerperResult(
            query=query,
            organic=organic,
            people_also_ask=paa,
            related_searches=related,
            raw_data=data,
        )

    def format_for_prompt(self, results: List[SerperResult]) -> str:
        """
        Format search results for inclusion in a prompt.

        Args:
            results: List of search results

        Returns:
            Formatted string for prompt
        """
        parts = []

        for result in results:
            parts.append(f"=== Запрос: {result.query} ===\n")

            if result.organic:
                parts.append("Результаты поиска:")
                for r in result.organic[:5]:  # Top 5 results
                    parts.append(f"  {r.position}. {r.title}")
                    parts.append(f"     URL: {r.link}")
                    parts.append(f"     {r.snippet}\n")

            if result.people_also_ask:
                parts.append("\nВопросы пользователей:")
                for q in result.people_also_ask:
                    parts.append(f"  - {q.question}")
                    if q.answer:
                        parts.append(f"    Ответ: {q.answer[:200]}...")

            if result.related_searches:
                parts.append("\nПохожие запросы:")
                for s in result.related_searches[:5]:
                    parts.append(f"  - {s}")

            parts.append("\n")

        return "\n".join(parts)
