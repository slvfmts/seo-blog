"""
PlaywrightBrowser - Headless Chromium browser for content extraction.

Fallback for pages that block Jina Reader and Trafilatura.
Uses stealth techniques to bypass basic bot protection.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

# Realistic Chrome User-Agent strings
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# Common cookie consent selectors to dismiss
_COOKIE_SELECTORS = [
    "button[class*='cookie'] >> text=/accept|agree|принять|согласен/i",
    ".cookie-accept",
    "#cookie-accept",
    "[data-testid='cookie-accept']",
    "button >> text=/accept all|принять все/i",
    ".consent-button",
    "#consent-button",
    ".cc-accept",
    ".gdpr-accept",
]


@dataclass
class PageContent:
    """Result of a page content extraction."""
    url: str
    title: str
    content: str
    word_count: int
    success: bool
    error: Optional[str] = None


class PlaywrightBrowser:
    """
    Headless Chromium browser for extracting page content.

    Uses Playwright with stealth settings to fetch pages that block
    traditional HTTP requests (403, 451, bot detection).

    Features:
    - Randomized User-Agent rotation
    - Cookie banner dismissal
    - Semaphore-based concurrency limiting
    - Trafilatura for HTML-to-text extraction
    """

    def __init__(self, max_concurrent: int = 2, timeout_ms: int = 30000, proxy_url: Optional[str] = None):
        self._max_concurrent = max_concurrent
        self._timeout_ms = timeout_ms
        self._proxy_url = proxy_url
        self._browser = None
        self._playwright = None
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _ensure_browser(self):
        """Lazy-launch Chromium browser."""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            logger.info("Playwright Chromium browser launched")

    async def fetch_content(self, url: str) -> PageContent:
        """
        Fetch and extract content from a single URL.

        Steps:
        1. Create new page with random User-Agent
        2. Set Accept-Language for Russian sites
        3. Navigate and wait for DOM
        4. Dismiss cookie banners
        5. Extract HTML and convert to markdown via trafilatura
        """
        await self._ensure_browser()

        async with self._semaphore:
            page = None
            try:
                context_kwargs = {
                    "user_agent": random.choice(_USER_AGENTS),
                    "viewport": {"width": 1920, "height": 1080},
                    "locale": "ru-RU",
                    "extra_http_headers": {
                        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                }
                if self._proxy_url:
                    context_kwargs["proxy"] = {"server": self._proxy_url}
                context = await self._browser.new_context(**context_kwargs)
                page = await context.new_page()

                # Navigate
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._timeout_ms,
                )

                if response and response.status >= 400:
                    return PageContent(
                        url=url,
                        title="",
                        content="",
                        word_count=0,
                        success=False,
                        error=f"HTTP {response.status}",
                    )

                # Wait for content to settle
                await page.wait_for_timeout(2000)

                # Try to dismiss cookie banners
                await self._dismiss_cookies(page)

                # Get page title
                title = await page.title()

                # Get HTML content
                html = await page.content()

                # Extract clean text using trafilatura
                content = self._extract_text(html, url)

                if not content:
                    return PageContent(
                        url=url,
                        title=title,
                        content="",
                        word_count=0,
                        success=False,
                        error="No content extracted from HTML",
                    )

                word_count = len(content.split())
                return PageContent(
                    url=url,
                    title=title,
                    content=content,
                    word_count=word_count,
                    success=True,
                )

            except Exception as e:
                logger.warning(f"Playwright fetch failed for {url}: {e}")
                return PageContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error=str(e),
                )
            finally:
                if page:
                    try:
                        await page.context.close()
                    except Exception:
                        pass

    async def fetch_batch(
        self,
        urls: List[str],
        delay: float = 1.0,
    ) -> List[PageContent]:
        """
        Fetch content from multiple URLs with concurrency control.

        Args:
            urls: List of URLs to fetch
            delay: Delay between starting each fetch (seconds)

        Returns:
            List of PageContent results (same order as input URLs)
        """
        results: List[PageContent] = []
        tasks = []

        for i, url in enumerate(urls):
            if i > 0:
                await asyncio.sleep(delay)
            task = asyncio.create_task(self.fetch_content(url))
            tasks.append((url, task))

        for url, task in tasks:
            try:
                result = await task
                results.append(result)
            except Exception as e:
                results.append(PageContent(
                    url=url,
                    title="",
                    content="",
                    word_count=0,
                    success=False,
                    error=str(e),
                ))

        success_count = sum(1 for r in results if r.success)
        logger.info(f"Playwright batch: {success_count}/{len(results)} successful")
        return results

    async def close(self):
        """Close browser and cleanup Playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    async def _dismiss_cookies(self, page):
        """Try to dismiss cookie consent banners."""
        for selector in _COOKIE_SELECTORS:
            try:
                element = page.locator(selector).first
                if await element.is_visible(timeout=500):
                    await element.click(timeout=1000)
                    await page.wait_for_timeout(500)
                    return
            except Exception:
                continue

    @staticmethod
    def _extract_text(html: str, url: str) -> str:
        """Extract clean text from HTML using trafilatura."""
        try:
            import trafilatura
            result = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                output_format="txt",
                favor_precision=True,
            )
            return result or ""
        except Exception as e:
            logger.warning(f"Trafilatura extraction failed: {e}")
            return ""
