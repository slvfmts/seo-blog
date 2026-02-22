"""
Serper.dev /scrape endpoint for extracting page content.

Uses Serper's scraping API to fetch clean text from URLs.
Costs 2 credits per page. ~67% success rate for Russian websites.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ScrapedContent:
    """Result of a Serper scrape."""
    url: str
    title: str
    content: str
    word_count: int
    success: bool
    error: Optional[str] = None
    credits_used: int = 0


class SerperScraper:
    """
    Serper.dev /scrape endpoint client.

    Extracts clean text content from web pages via Serper's cloud scraping.
    Works well for static pages; may fail on JS-heavy sites (vc.ru, tinkoff).

    Usage:
        scraper = SerperScraper(api_key="...")
        result = await scraper.fetch_content("https://habr.com/ru/articles/123/")
        print(result.content)
    """

    BASE_URL = "https://scrape.serper.dev"

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout

    async def fetch_content(self, url: str) -> ScrapedContent:
        """
        Scrape a single URL via Serper /scrape API.

        Args:
            url: URL to scrape

        Returns:
            ScrapedContent with extracted text
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.BASE_URL,
                    headers={
                        "X-API-KEY": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={"url": url},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()

            text = data.get("text", "")
            metadata = data.get("metadata", {})
            credits = data.get("credits", 2)
            title = metadata.get("title", "")

            if not text or len(text.strip()) < 50:
                return ScrapedContent(
                    url=url,
                    title=title,
                    content="",
                    word_count=0,
                    success=False,
                    error="Empty or too short content",
                    credits_used=credits,
                )

            word_count = len(text.split())
            return ScrapedContent(
                url=url,
                title=title,
                content=text,
                word_count=word_count,
                success=True,
                credits_used=credits,
            )

        except httpx.HTTPStatusError as e:
            logger.warning(f"Serper scrape HTTP error for {url}: {e.response.status_code}")
            return ScrapedContent(
                url=url, title="", content="", word_count=0,
                success=False, error=f"HTTP {e.response.status_code}",
            )
        except Exception as e:
            logger.warning(f"Serper scrape failed for {url}: {e}")
            return ScrapedContent(
                url=url, title="", content="", word_count=0,
                success=False, error=str(e),
            )

    async def fetch_batch(
        self,
        urls: List[str],
        max_concurrent: int = 3,
        delay_between: float = 0.3,
    ) -> List[ScrapedContent]:
        """
        Scrape multiple URLs with concurrency control.

        Args:
            urls: URLs to scrape
            max_concurrent: Max concurrent requests
            delay_between: Delay between requests (seconds)

        Returns:
            List of ScrapedContent results (same order as input)
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(url: str) -> ScrapedContent:
            async with semaphore:
                result = await self.fetch_content(url)
                await asyncio.sleep(delay_between)
                return result

        tasks = [fetch_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r.success)
        total_credits = sum(r.credits_used for r in results)
        logger.info(
            f"Serper scrape batch: {success_count}/{len(results)} successful, "
            f"{total_credits} credits used"
        )

        return list(results)
