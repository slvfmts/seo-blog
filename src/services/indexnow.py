"""
IndexNow — мгновенное уведомление поисковиков о новых/обновлённых страницах.
Поддерживает: Yandex, Bing, Naver, Seznam. Google не поддерживает.
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

INDEXNOW_KEY = os.getenv("INDEXNOW_KEY", "")
INDEXNOW_ENDPOINTS = [
    "https://yandex.com/indexnow",
    "https://www.bing.com/indexnow",
]


async def ping_indexnow(url: str, host: str | None = None) -> dict[str, str]:
    """
    Notify search engines about a new/updated URL.

    Args:
        url: Full URL of the page (e.g. https://notes.editors.one/my-post/)
        host: Site host (optional, extracted from url if not given)

    Returns:
        Dict of {endpoint: status} for each search engine pinged.
    """
    if not host:
        from urllib.parse import urlparse
        host = urlparse(url).netloc

    results = {}
    params = {
        "url": url,
        "key": INDEXNOW_KEY,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        for endpoint in INDEXNOW_ENDPOINTS:
            try:
                resp = await client.get(endpoint, params=params)
                status = f"{resp.status_code}"
                results[endpoint] = status
                logger.info(f"IndexNow {endpoint}: {status} for {url}")
            except Exception as e:
                results[endpoint] = f"error: {e}"
                logger.warning(f"IndexNow {endpoint} failed for {url}: {e}")

    return results


async def ping_indexnow_batch(urls: list[str], host: str) -> dict[str, str]:
    """
    Notify search engines about multiple URLs at once.
    Uses POST with JSON body (batch API).
    """
    if not urls:
        return {}

    payload = {
        "host": host,
        "key": INDEXNOW_KEY,
        "urlList": urls,
    }

    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for endpoint in INDEXNOW_ENDPOINTS:
            try:
                resp = await client.post(
                    endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                results[endpoint] = f"{resp.status_code}"
                logger.info(f"IndexNow batch {endpoint}: {resp.status_code} for {len(urls)} URLs")
            except Exception as e:
                results[endpoint] = f"error: {e}"
                logger.warning(f"IndexNow batch {endpoint} failed: {e}")

    return results
