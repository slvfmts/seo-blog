"""
TopvisorProvider — VolumeProvider implementation using Topvisor API v2.

Workflow for volume check:
1. Import keywords into the project
2. Start a volume check (async on Topvisor side)
3. Poll/wait for completion
4. Get keywords with volume compound fields

Cost: ~5 RUB per 100 keywords (Yandex Wordstat via Topvisor).
"""

import asyncio
import logging
from typing import Optional

from .volume_provider import VolumeProvider, VolumeResult
from .topvisor_client import TopvisorClient, YANDEX, GOOGLE, VOL_BROAD, VOL_EXACT

logger = logging.getLogger(__name__)


class TopvisorProvider(VolumeProvider):
    """
    Keyword volume provider via Topvisor API.

    Imports keywords → triggers volume check → polls → returns results.
    """

    def __init__(
        self,
        user_id: str,
        access_token: str,
        project_id: int,
        region_key: int = 213,      # Moscow
        searcher_key: int = YANDEX,  # Yandex
        volume_type: int = VOL_BROAD,
        timeout: float = 300.0,      # Max wait for volume check
    ):
        self.client = TopvisorClient(
            user_id=user_id,
            access_token=access_token,
            project_id=project_id,
        )
        self.region_key = region_key
        self.searcher_key = searcher_key
        self.volume_type = volume_type
        self.timeout = timeout

    @property
    def source_name(self) -> str:
        return "topvisor"

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        """
        Fetch volumes via Topvisor.

        Steps:
        1. Import keywords into project
        2. Trigger volume check
        3. Wait for completion (poll)
        4. Get keywords with volumes
        5. Map results back
        """
        if not keywords:
            return []

        try:
            # Step 1: Import keywords
            import_result = await self.client.import_keywords(
                keywords=keywords,
                group_name="seo-blog-volume-check",
            )
            added = import_result.get("countAdded", 0) if isinstance(import_result, dict) else 0
            logger.info(f"Topvisor: imported {added} new keywords (total sent: {len(keywords)})")

            # Step 2: Trigger volume check
            await self.client.start_volume_check(
                region_key=self.region_key,
                searcher_key=self.searcher_key,
                volume_type=self.volume_type,
                no_recheck=1,  # Skip already-checked keywords
            )

            # Step 3: Wait for check to complete
            # Topvisor processes volumes async. We poll by trying to read results.
            # Volume checks typically complete in 30-120 seconds.
            await asyncio.sleep(10)  # Initial wait

            elapsed = 10
            poll_interval = 10
            while elapsed < self.timeout:
                # Try to get results — if volumes are 0 for most, still processing
                kw_data = await self.client.get_keywords_with_volumes(
                    region_key=self.region_key,
                    searcher_key=self.searcher_key,
                    volume_type=self.volume_type,
                    limit=len(keywords) + 100,
                )

                # Check if we have any volumes
                with_volume = sum(1 for kw in kw_data if kw.get("volume", 0) > 0)
                if with_volume > 0 or elapsed >= 60:
                    # Got some results or waited long enough
                    break

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # Step 4: Get final results
            if elapsed >= self.timeout:
                logger.warning(f"Topvisor volume check timed out after {self.timeout}s")
                kw_data = await self.client.get_keywords_with_volumes(
                    region_key=self.region_key,
                    searcher_key=self.searcher_key,
                    volume_type=self.volume_type,
                    limit=len(keywords) + 100,
                )

            # Step 5: Map back to original keywords
            volume_map: dict[str, int] = {}
            for kw in kw_data:
                name = (kw.get("name") or "").lower().strip()
                vol = kw.get("volume", 0) or 0
                if name:
                    volume_map[name] = max(volume_map.get(name, 0), vol)

            results = []
            for kw in keywords:
                key = kw.lower().strip()
                volume = volume_map.get(key, 0)
                results.append(VolumeResult(
                    keyword=kw,
                    volume=volume,
                    source="topvisor",
                ))

            found = sum(1 for r in results if r.volume > 0)
            logger.info(f"Topvisor: volumes for {found}/{len(results)} keywords")
            return results

        except Exception as e:
            logger.error(f"Topvisor volume error: {e}")
            return [VolumeResult(keyword=kw, volume=0, source="topvisor") for kw in keywords]

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Get autocomplete suggestions via Topvisor hints API."""
        try:
            await self.client.get_suggestions(
                seed_keywords=[keyword],
                region_key=self.region_key,
                searcher_keys=[100, 101],  # Yandex + Google hints
                hint_depth=1,
            )

            # Wait for suggestions to be imported
            await asyncio.sleep(15)

            # Get all keywords — suggestions will be in new groups
            kw_data = await self.client.get_keywords(
                fields=["id", "name"],
                limit=1000,
            )

            # Return keyword names (dedup)
            seen = set()
            result = []
            for kw in kw_data:
                name = kw.get("name", "").strip()
                if name and name.lower() not in seen:
                    seen.add(name.lower())
                    result.append(name)

            return result

        except Exception as e:
            logger.warning(f"Topvisor suggestions error: {e}")
            return []
