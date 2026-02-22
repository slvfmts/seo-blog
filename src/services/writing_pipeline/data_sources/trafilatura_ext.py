"""
Trafilatura-based content extractor as fallback for Jina Reader.

Uses trafilatura library for local HTML content extraction.
Works when Jina Reader is unavailable or rate-limited.
"""

from dataclasses import dataclass
from typing import Optional, List
import httpx
import asyncio
import logging

logger = logging.getLogger(__name__)

# Import trafilatura - optional dependency
try:
    import trafilatura
    from trafilatura.settings import use_config

    # Configure trafilatura for better extraction
    TRAFILATURA_CONFIG = use_config()
    TRAFILATURA_CONFIG.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")
    TRAFILATURA_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "100")

    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False
    TRAFILATURA_CONFIG = None
    logger.warning("trafilatura not installed - fallback extraction disabled")


@dataclass
class ExtractedContent:
    """Extracted content from HTML."""
    url: str
    title: str
    content: str  # Plain text or basic markdown
    word_count: int
    success: bool
    error: Optional[str] = None


class TrafilaturaExtractor:
    """
    Local content extractor using trafilatura library.

    Fallback for when Jina Reader is unavailable.
    Performs direct HTTP fetch + content extraction.

    Usage:
        extractor = TrafilaturaExtractor()
        content = await extractor.extract_from_url("https://example.com")
        print(content.content)
    """

    def __init__(
        self,
        timeout: float = 30.0,
        include_comments: bool = False,
        include_tables: bool = True,
        output_format: str = "markdown",
        proxy_url: Optional[str] = None,
    ):
        """
        Initialize extractor.

        Args:
            timeout: HTTP request timeout
            include_comments: Whether to include page comments
            include_tables: Whether to include tables
            output_format: 'markdown' or 'txt'
            proxy_url: Optional SOCKS5/HTTP proxy URL for requests
        """
        if not TRAFILATURA_AVAILABLE:
            raise RuntimeError(
                "trafilatura is not installed. "
                "Install with: pip install trafilatura"
            )

        self.timeout = timeout
        self.include_comments = include_comments
        self.include_tables = include_tables
        self.output_format = output_format
        self.proxy_url = proxy_url

    async def extract_from_url(self, url: str) -> ExtractedContent:
        """
        Fetch URL and extract content.

        Args:
            url: URL to fetch and extract content from

        Returns:
            ExtractedContent with extracted text
        """
        try:
            # Fetch HTML
            html = await self._fetch_html(url)
            if html is None:
                return ExtractedContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error="Failed to fetch HTML",
                )

            # Extract content
            return self.extract_from_html(html, url)

        except Exception as e:
            logger.error(f"Error extracting from {url}: {e}")
            return ExtractedContent(
                url=url,
                title="",
                content="",
                word_count=0,
                success=False,
                error=str(e),
            )

    async def _fetch_html(self, url: str) -> Optional[str]:
        """Fetch HTML content from URL."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5,ru;q=0.3",
        }

        client_kwargs = {}
        if self.proxy_url:
            client_kwargs["proxy"] = self.proxy_url

        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                response = await client.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.text

        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def extract_from_html(self, html: str, url: str = "") -> ExtractedContent:
        """
        Extract content from HTML string.

        Args:
            html: HTML content
            url: Original URL (for metadata)

        Returns:
            ExtractedContent with extracted text
        """
        try:
            # Extract main content
            extracted = trafilatura.extract(
                html,
                include_comments=self.include_comments,
                include_tables=self.include_tables,
                output_format=self.output_format,
                config=TRAFILATURA_CONFIG,
            )

            if not extracted:
                return ExtractedContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error="No content extracted",
                )

            # Extract metadata
            metadata = trafilatura.extract_metadata(html)
            title = ""
            if metadata:
                title = metadata.title or ""

            word_count = len(extracted.split())

            return ExtractedContent(
                url=url,
                title=title,
                content=extracted,
                word_count=word_count,
                success=True,
            )

        except Exception as e:
            logger.error(f"Extraction error for {url}: {e}")
            return ExtractedContent(
                url=url,
                title="",
                content="",
                word_count=0,
                success=False,
                error=str(e),
            )

    async def extract_batch(
        self,
        urls: List[str],
        max_concurrent: int = 3,
        delay_between: float = 1.0,
    ) -> List[ExtractedContent]:
        """
        Extract content from multiple URLs.

        Args:
            urls: List of URLs to process
            max_concurrent: Max concurrent requests
            delay_between: Delay between requests

        Returns:
            List of ExtractedContent results
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_with_semaphore(url: str) -> ExtractedContent:
            async with semaphore:
                result = await self.extract_from_url(url)
                await asyncio.sleep(delay_between)
                return result

        tasks = [extract_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)

        return list(results)


def is_trafilatura_available() -> bool:
    """Check if trafilatura is available."""
    return TRAFILATURA_AVAILABLE
