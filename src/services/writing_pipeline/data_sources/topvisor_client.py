"""
Topvisor API v2 — low-level HTTP client.

Base URL: https://api.topvisor.ru/v2/json
Auth: Authorization: Bearer <token> + User-Id: <user_id>
All requests: POST with JSON body.

Docs: https://topvisor.com/api/v2/
OpenAPI: https://github.com/topvisor/topvisor-openapi

Searcher keys: 0=Yandex, 1=Google
Volume types: 1=broad, 2=phrase, 3=exact, 5=ordered, 6=exact+ordered
Clustering types: 0=soft, 1=moderate, 2=hard
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.topvisor.ru/v2/json"

# Searcher keys
YANDEX = 0
GOOGLE = 1

# Volume types
VOL_BROAD = 1
VOL_PHRASE = 2
VOL_EXACT = 3

# Clustering types
CLUSTER_SOFT = 0
CLUSTER_MODERATE = 1
CLUSTER_HARD = 2


class TopvisorClient:
    """
    Low-level Topvisor API v2 client.

    Covers: keywords CRUD, volume checks, keyword collection (research),
    suggestions (hints), SERP-based clustering.
    """

    def __init__(
        self,
        user_id: str,
        access_token: str,
        project_id: int,
        timeout: float = 120.0,
    ):
        self.user_id = user_id
        self.access_token = access_token
        self.project_id = project_id
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(3)  # Topvisor allows concurrent calls

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Id": str(self.user_id),
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-cache",
        }

    async def _post(self, path: str, body: dict) -> dict:
        """Make a POST request to Topvisor API."""
        url = f"{BASE_URL}/{path}"
        async with self._semaphore:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=self._headers(), json=body)

                if resp.status_code != 200:
                    logger.error(f"Topvisor {path} HTTP {resp.status_code}: {resp.text[:500]}")
                    resp.raise_for_status()

                data = resp.json()

                # Topvisor wraps errors in {"errors": [...]}
                if isinstance(data, dict) and data.get("errors"):
                    error_msg = str(data["errors"])
                    logger.error(f"Topvisor API error: {error_msg[:300]}")
                    raise TopvisorError(error_msg)

                return data

    # ── Keywords CRUD ────────────────────────────────────────────────

    async def import_keywords(
        self,
        keywords: list[str],
        group_name: str = "seo-blog-import",
        folder_id: Optional[int] = None,
    ) -> dict:
        """
        Import keywords into a project.

        POST /v2/json/add/keywords_2/keywords/import
        CSV format: first row = field names, then one keyword per row.

        Returns: {countSended, countDuplicated, countAdded, countChanged}
        """
        csv_rows = ["name"] + keywords
        csv_data = "\n".join(csv_rows)

        body = {
            "project_id": self.project_id,
            "keywords": csv_data,
            "group_name": group_name,
        }
        if folder_id:
            body["folder_id"] = folder_id

        result = await self._post("add/keywords_2/keywords/import", body)
        logger.info(f"Topvisor import: {result.get('result', result)}")
        return result.get("result", result)

    async def get_keywords(
        self,
        fields: Optional[list[str]] = None,
        limit: int = 5000,
        offset: int = 0,
        filters: Optional[list[dict]] = None,
    ) -> list[dict]:
        """
        Get keywords from project with optional volume compound fields.

        POST /v2/json/get/keywords_2/keywords

        Volume field format: "volume:region_key:searcher_key:type"
        Example: "volume:213:0:1" = Yandex Moscow broad match

        Returns: list of keyword objects
        """
        body: dict = {
            "project_id": self.project_id,
            "fields": fields or ["id", "name", "group_id"],
            "limit": limit,
            "offset": offset,
        }
        if filters:
            body["filters"] = filters

        result = await self._post("get/keywords_2/keywords", body)
        return result.get("result", []) if isinstance(result, dict) else result

    async def delete_keywords(self, keyword_ids: list[int]) -> dict:
        """Delete keywords by IDs."""
        body = {
            "project_id": self.project_id,
            "id": keyword_ids,
        }
        return await self._post("del/keywords_2/keywords", body)

    # ── Volume Checking ──────────────────────────────────────────────

    async def start_volume_check(
        self,
        region_key: int = 213,
        searcher_key: int = YANDEX,
        volume_type: int = VOL_BROAD,
        no_recheck: int = 1,
    ) -> dict:
        """
        Start an async volume check for all keywords in the project.

        POST /v2/json/edit/keywords_2/volumes/go

        Args:
            region_key: region code (213=Moscow, 225=Russia, 2=Saint-Petersburg)
            searcher_key: 0=Yandex, 1=Google
            volume_type: 1=broad, 2=phrase, 3=exact
            no_recheck: 0=recheck all, 1=skip fresh, 2=skip any checked
        """
        qualifier_id = f"vol_{region_key}_{searcher_key}_{volume_type}"
        body = {
            "project_id": self.project_id,
            "qualifiers": [{
                "id": qualifier_id,
                "region_key": region_key,
                "searcher_key": searcher_key,
                "type": volume_type,
            }],
            "no_recheck": no_recheck,
        }
        return await self._post("edit/keywords_2/volumes/go", body)

    async def get_keywords_with_volumes(
        self,
        region_key: int = 213,
        searcher_key: int = YANDEX,
        volume_type: int = VOL_BROAD,
        limit: int = 5000,
    ) -> list[dict]:
        """
        Get keywords with volume data using compound fields.

        Returns: [{id, name, group_id, volume_value}, ...]
        """
        vol_field = f"volume:{region_key}:{searcher_key}:{volume_type}"
        fields = ["id", "name", "group_id", vol_field]

        keywords = await self.get_keywords(fields=fields, limit=limit)

        # Normalize: rename compound field to simple 'volume'
        result = []
        for kw in keywords:
            entry = {
                "id": kw.get("id"),
                "name": kw.get("name", ""),
                "group_id": kw.get("group_id"),
                "volume": kw.get(vol_field, 0) or 0,
            }
            result.append(entry)

        return result

    # ── Keyword Collection (Research) ────────────────────────────────

    async def research_keywords(
        self,
        seed_keywords: list[str],
        region_key: int = 213,
        searcher_key: int = YANDEX,
        also_searched: bool = True,
        depth: int = 1,
        folder_id: Optional[int] = None,
    ) -> list[dict]:
        """
        Run keyword research (collection) from seeds.

        POST /v2/json/edit/keywords_2/collect/go

        Uses Yandex Wordstat or Google Keyword Planner to find related keywords.
        Results are imported directly into the project.

        Returns: list of created groups (or empty if async)
        """
        body: dict = {
            "project_id": self.project_id,
            "keywords": seed_keywords,
            "qualifiers": [{
                "region_key": region_key,
                "searcher_key": searcher_key,
                "also_searched": also_searched,
                "depth": depth,
            }],
            "in_one_group": False,
        }
        if folder_id:
            body["to_id"] = folder_id

        result = await self._post("edit/keywords_2/collect/go", body)
        logger.info(f"Topvisor research started for {len(seed_keywords)} seeds")
        return result.get("result", []) if isinstance(result, dict) else []

    # ── Suggestions (Hints/Autocomplete) ─────────────────────────────

    async def get_suggestions(
        self,
        seed_keywords: list[str],
        region_key: int = 213,
        searcher_keys: Optional[list[int]] = None,
        hint_depth: int = 1,
        folder_id: Optional[int] = None,
    ) -> list[dict]:
        """
        Get autocomplete suggestions from Yandex/Google/Bing.

        Uses searcher_key 100=Yandex hints, 101=Google hints, 105=Bing hints.

        Returns: list of created groups
        """
        if searcher_keys is None:
            searcher_keys = [100, 101]  # Yandex + Google hints

        qualifiers = []
        for sk in searcher_keys:
            qualifiers.append({
                "region_key": region_key,
                "searcher_key": sk,
                "hint_depth": hint_depth,
                "hint_generators": ["letter_ru", "space"] if sk == 100 else ["letter", "space"],
            })

        body: dict = {
            "project_id": self.project_id,
            "keywords": seed_keywords,
            "qualifiers": qualifiers,
            "in_one_group": False,
        }
        if folder_id:
            body["to_id"] = folder_id

        result = await self._post("edit/keywords_2/collect/go", body)
        logger.info(f"Topvisor suggestions started for {len(seed_keywords)} seeds")
        return result.get("result", []) if isinstance(result, dict) else []

    # ── SERP-based Clustering ────────────────────────────────────────

    async def start_clustering(
        self,
        region_key: int = 213,
        searcher_key: int = YANDEX,
        region_lang: str = "ru",
        count: Optional[list[int]] = None,
        cluster_type: int = CLUSTER_MODERATE,
        folder_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Start SERP-based keyword clustering.

        POST /v2/json/add/keywords_2/claster/task

        Args:
            region_key: region code
            searcher_key: 0=Yandex, 1=Google
            region_lang: language code
            count: clustering degrees (min common URLs in top-10). [3] = moderate.
            cluster_type: 0=soft, 1=moderate, 2=hard
            folder_id: optional folder to cluster

        Returns: task ID (int) or None
        """
        body: dict = {
            "project_id": self.project_id,
            "searcher_key": searcher_key,
            "region_key": region_key,
            "region_lang": region_lang,
            "count": count or [3],
            "type": cluster_type,
        }
        if folder_id:
            body["folder_id"] = folder_id

        result = await self._post("add/keywords_2/claster/task", body)
        task_id = result.get("result") if isinstance(result, dict) else result
        logger.info(f"Topvisor clustering started: task_id={task_id}")
        return task_id

    async def get_clustering_progress(self) -> float:
        """
        Get clustering completion percentage (0-100).

        POST /v2/json/get/keywords_2/claster/percent
        """
        body = {"project_id": self.project_id}
        result = await self._post("get/keywords_2/claster/percent", body)
        return float(result.get("result", 0) if isinstance(result, dict) else result or 0)

    async def get_clustering_price(self, count: Optional[list[int]] = None) -> float:
        """Get clustering cost estimate."""
        body = {
            "project_id": self.project_id,
            "count": count or [3],
        }
        result = await self._post("get/keywords_2/claster/price", body)
        return float(result.get("result", 0) if isinstance(result, dict) else result or 0)

    async def wait_for_clustering(self, timeout: int = 600, poll_interval: int = 10) -> bool:
        """Poll clustering progress until 100% or timeout."""
        elapsed = 0
        while elapsed < timeout:
            progress = await self.get_clustering_progress()
            if progress >= 100:
                logger.info(f"Topvisor clustering completed after {elapsed}s")
                return True
            logger.debug(f"Topvisor clustering: {progress}% ({elapsed}s)")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(f"Topvisor clustering timed out after {timeout}s")
        return False

    async def get_clustered_keywords(
        self,
        region_key: int = 213,
        searcher_key: int = YANDEX,
        volume_type: int = VOL_BROAD,
        limit: int = 5000,
    ) -> list[dict]:
        """
        Get keywords with group_id (clusters) and optionally volume.

        After clustering, group_id represents the cluster.
        """
        vol_field = f"volume:{region_key}:{searcher_key}:{volume_type}"
        fields = ["id", "name", "group_id", "group_name", vol_field]

        keywords = await self.get_keywords(fields=fields, limit=limit)

        result = []
        for kw in keywords:
            result.append({
                "id": kw.get("id"),
                "name": kw.get("name", ""),
                "group_id": kw.get("group_id"),
                "group_name": kw.get("group_name", ""),
                "volume": kw.get(vol_field, 0) or 0,
            })

        return result

    # ── Utility ──────────────────────────────────────────────────────

    async def get_volume_check_price(
        self,
        region_key: int = 213,
        searcher_key: int = YANDEX,
        volume_type: int = VOL_BROAD,
    ) -> float:
        """Get cost estimate for a volume check."""
        body = {
            "project_id": self.project_id,
            "qualifiers": [{
                "id": "price_check",
                "region_key": region_key,
                "searcher_key": searcher_key,
                "type": volume_type,
            }],
        }
        result = await self._post("get/keywords_2/volumes/price", body)
        return float(result.get("result", 0) if isinstance(result, dict) else result or 0)

    async def get_collection_price(
        self,
        keywords: list[str],
        region_key: int = 213,
        searcher_key: int = YANDEX,
    ) -> float:
        """Get cost estimate for keyword research."""
        body = {
            "project_id": self.project_id,
            "keywords": keywords,
            "qualifiers": [{
                "region_key": region_key,
                "searcher_key": searcher_key,
            }],
        }
        result = await self._post("get/keywords_2/collect/price", body)
        return float(result.get("result", 0) if isinstance(result, dict) else result or 0)


class TopvisorError(Exception):
    """Topvisor API error."""
    pass
