"""
Jina Reader data source for extracting clean markdown content from URLs.

Uses Jina Reader API (https://r.jina.ai/) which provides:
- Clean markdown extraction from any URL
- Free tier: 10M tokens/month, 100 RPM
- Handles JavaScript-rendered pages
"""

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class PageContent:
    """Extracted content from a page."""
    url: str
    title: str
    content: str  # Markdown content
    word_count: int
    success: bool
    error: Optional[str] = None


class JinaReader:
    """
    Jina Reader API client for extracting clean markdown from URLs.

    Usage:
        reader = JinaReader()
        content = await reader.fetch_content("https://example.com/article")
        print(content.content)  # Clean markdown

    Free tier limits:
        - 10M tokens/month
        - 100 requests/minute
    """

    BASE_URL = "https://r.jina.ai/"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        """
        Initialize Jina Reader client.

        Args:
            api_key: Optional API key for higher limits
            timeout: Request timeout in seconds
            max_retries: Number of retries on failure
        """
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    async def fetch_content(self, url: str) -> PageContent:
        """
        Fetch clean markdown content from a URL.

        Args:
            url: The URL to fetch content from

        Returns:
            PageContent with extracted markdown
        """
        headers = {
            "Accept": "text/markdown",
            "User-Agent": "SEO-Blog-Pipeline/1.0",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        jina_url = f"{self.BASE_URL}{url}"

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        jina_url,
                        headers=headers,
                        timeout=self.timeout,
                        follow_redirects=True,
                    )

                    if response.status_code == 200:
                        content = response.text
                        # Extract title from first line if it's a markdown header
                        lines = content.strip().split('\n')
                        title = ""
                        if lines and lines[0].startswith('#'):
                            title = lines[0].lstrip('#').strip()

                        word_count = len(content.split())

                        return PageContent(
                            url=url,
                            title=title,
                            content=content,
                            word_count=word_count,
                            success=True,
                        )

                    elif response.status_code == 429:
                        # Rate limited - wait and retry
                        if attempt < self.max_retries:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return PageContent(
                            url=url,
                            title="",
                            content="",
                            word_count=0,
                            success=False,
                            error=f"Rate limited after {self.max_retries} retries",
                        )

                    else:
                        return PageContent(
                            url=url,
                            title="",
                            content="",
                            word_count=0,
                            success=False,
                            error=f"HTTP {response.status_code}: {response.text[:200]}",
                        )

            except httpx.TimeoutException:
                if attempt < self.max_retries:
                    await asyncio.sleep(1)
                    continue
                return PageContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error=f"Timeout after {self.timeout}s",
                )

            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return PageContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error=str(e),
                )

        return PageContent(
            url=url,
            title="",
            content="",
            word_count=0,
            success=False,
            error="Max retries exceeded",
        )

    async def fetch_batch(
        self,
        urls: List[str],
        max_concurrent: int = 3,
        delay_between: float = 0.5,
    ) -> List[PageContent]:
        """
        Fetch content from multiple URLs with rate limiting.

        Args:
            urls: List of URLs to fetch
            max_concurrent: Maximum concurrent requests
            delay_between: Delay between batches in seconds

        Returns:
            List of PageContent results
        """
        results = []
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_with_semaphore(url: str) -> PageContent:
            async with semaphore:
                result = await self.fetch_content(url)
                await asyncio.sleep(delay_between)
                return result

        tasks = [fetch_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)

        return list(results)

    def truncate_content(
        self,
        content: str,
        max_words: int = 3000,
        preserve_structure: bool = True,
    ) -> str:
        """
        Truncate content to fit within token limits.

        Args:
            content: Markdown content to truncate
            max_words: Maximum words to keep
            preserve_structure: Try to preserve markdown structure

        Returns:
            Truncated content
        """
        words = content.split()
        if len(words) <= max_words:
            return content

        if preserve_structure:
            # Try to cut at paragraph boundaries
            truncated = ' '.join(words[:max_words])
            # Find last double newline
            last_para = truncated.rfind('\n\n')
            if last_para > len(truncated) * 0.7:  # At least 70% of content
                truncated = truncated[:last_para]
            return truncated + "\n\n[Content truncated...]"
        else:
            return ' '.join(words[:max_words]) + "..."
