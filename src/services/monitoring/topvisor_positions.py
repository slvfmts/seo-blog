"""
Topvisor-based position tracker.

Registers keywords for tracking at publish time,
triggers daily position checks, and saves results to keyword_rankings.

Uses Topvisor API v2 positions endpoints via TopvisorClient.
"""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy.orm import Session

from src.db import models
from src.services.writing_pipeline.data_sources.topvisor_client import TopvisorClient

logger = logging.getLogger(__name__)

# Constants
MAX_KEYWORDS_PER_ARTICLE = 10
MIN_VOLUME_THRESHOLD = 50
POSITION_CHECK_POLL_INTERVAL = 30  # seconds
POSITION_CHECK_TIMEOUT = 900       # 15 minutes


class TopvisorPositionTracker:
    """
    Position tracking via Topvisor API.

    Responsibilities:
    1. Register keywords in Topvisor at publish time (grouped by article slug)
    2. Trigger async position checks
    3. Poll for results and save to keyword_rankings
    """

    def __init__(self, client: TopvisorClient):
        self.client = client
        self._regions_indexes: Optional[list[int]] = None

    async def _get_regions_indexes(self) -> list[int]:
        """Get configured searcher-region indexes (cached)."""
        if self._regions_indexes is None:
            regions = await self.client.get_searcher_regions()
            self._regions_indexes = [r["id"] for r in regions if r.get("id")]
            if not self._regions_indexes:
                logger.warning("Topvisor: no searcher regions configured for project %s", self.client.project_id)
        return self._regions_indexes

    async def register_keywords_for_tracking(
        self,
        draft: models.Draft,
        post: models.Post,
        db: Session,
    ) -> dict:
        """
        Register keywords for position tracking when article is published.

        Selects: primary keyword (always) + secondary with search_volume > MIN_VOLUME_THRESHOLD
        Max MAX_KEYWORDS_PER_ARTICLE per article.

        Returns: {imported: int, mapped: int}
        """
        site_id = draft.site_id
        if not site_id:
            return {"imported": 0, "mapped": 0, "error": "No site_id on draft"}

        # Collect keyword texts from draft
        keyword_texts = []
        if draft.keywords:
            keyword_texts = [kw.lower().strip() for kw in draft.keywords if kw and kw.strip()]

        if not keyword_texts:
            return {"imported": 0, "mapped": 0, "message": "No keywords on draft"}

        # Find primary keyword
        primary_keyword_id = draft.keyword_id
        if not primary_keyword_id and draft.brief_id:
            brief = db.query(models.Brief).filter(models.Brief.id == draft.brief_id).first()
            if brief:
                primary_keyword_id = brief.keyword_id

        # Load Keyword records from DB
        db_keywords = db.query(models.Keyword).filter(
            models.Keyword.site_id == site_id,
            models.Keyword.keyword.in_(keyword_texts),
        ).all()

        # Build lookup
        kw_by_text = {kw.keyword.lower().strip(): kw for kw in db_keywords}

        # Select keywords to track
        selected = []
        # Primary first
        if primary_keyword_id:
            primary_kw = db.query(models.Keyword).filter(models.Keyword.id == primary_keyword_id).first()
            if primary_kw:
                selected.append(primary_kw)

        # Secondary with volume > threshold
        primary_ids = {kw.id for kw in selected}
        for text in keyword_texts:
            if len(selected) >= MAX_KEYWORDS_PER_ARTICLE:
                break
            kw = kw_by_text.get(text.lower().strip())
            if kw and kw.id not in primary_ids:
                if kw.search_volume and kw.search_volume > MIN_VOLUME_THRESHOLD:
                    selected.append(kw)
                    primary_ids.add(kw.id)

        if not selected:
            return {"imported": 0, "mapped": 0, "message": "No matching Keyword records"}

        # Import into Topvisor with article-slug group
        group_name = f"article:{post.slug or draft.slug or 'unknown'}"
        texts_to_import = [kw.keyword for kw in selected]

        import_result = await self.client.import_keywords(
            keywords=texts_to_import,
            group_name=group_name,
        )
        imported = import_result.get("countAdded", 0) if isinstance(import_result, dict) else 0
        logger.info(
            "Topvisor position tracking: imported %d keywords for %s (group=%s)",
            imported, post.slug, group_name,
        )

        # Fetch Topvisor keyword IDs to map back
        tv_keywords = await self.client.get_keywords(
            fields=["id", "name", "group_name"],
            filters=[{
                "name": "group_name",
                "operator": "EQUALS",
                "values": [group_name],
            }],
        )

        # Map Topvisor IDs → internal keywords
        tv_by_name = {}
        for tvk in tv_keywords:
            name = (tvk.get("name") or "").lower().strip()
            if name:
                tv_by_name[name] = tvk.get("id")

        mapped = 0
        for kw in selected:
            tv_id = tv_by_name.get(kw.keyword.lower().strip())
            if tv_id:
                kw.topvisor_keyword_id = int(tv_id)
                mapped += 1

        db.commit()

        return {"imported": imported, "mapped": mapped, "total_selected": len(selected)}

    async def trigger_check(self) -> dict:
        """Trigger an async position check in Topvisor."""
        regions_indexes = await self._get_regions_indexes()
        if not regions_indexes:
            return {"error": "No searcher regions configured"}
        return await self.client.start_position_check(regions_indexes)

    async def fetch_results(self, site_id, db: Session) -> dict:
        """
        Fetch position results from Topvisor and save to keyword_rankings.

        Maps Topvisor keyword IDs → internal keyword IDs via keywords.topvisor_keyword_id.

        Returns: {saved: int, not_mapped: int}
        """
        regions_indexes = await self._get_regions_indexes()
        if not regions_indexes:
            return {"error": "No searcher regions configured"}

        today = date.today()
        today_str = today.strftime("%Y-%m-%d")
        today_dt = datetime(today.year, today.month, today.day)

        # Get positions from Topvisor
        positions = await self.client.get_position_summary(
            date_from=today_str,
            date_to=today_str,
            regions_indexes=regions_indexes,
            show_tops=100,
        )

        if not positions:
            return {"saved": 0, "message": "No position data from Topvisor"}

        # Build Topvisor ID → internal Keyword mapping
        tv_keywords = db.query(models.Keyword).filter(
            models.Keyword.site_id == site_id,
            models.Keyword.topvisor_keyword_id.isnot(None),
        ).all()

        tv_id_to_kw = {kw.topvisor_keyword_id: kw for kw in tv_keywords}

        saved = 0
        not_mapped = 0

        for pos_data in positions:
            tv_id = pos_data.get("id")
            if not tv_id:
                continue

            kw = tv_id_to_kw.get(int(tv_id))
            if not kw:
                not_mapped += 1
                continue

            # Check if already saved today (any source)
            existing = db.query(models.KeywordRanking).filter(
                models.KeywordRanking.keyword_id == kw.id,
                models.KeywordRanking.date >= today_dt,
                models.KeywordRanking.source == "topvisor",
            ).first()
            if existing:
                continue

            # Extract position — Topvisor returns positions in region-specific fields
            position = self._extract_position(pos_data, regions_indexes)

            ranking = models.KeywordRanking(
                keyword_id=kw.id,
                post_id=kw.post_id,
                date=today_dt,
                position=position,
                source="topvisor",
            )
            db.add(ranking)

            # Update keyword.current_position
            kw.current_position = position
            kw.updated_at = datetime.utcnow()

            if position is not None and position <= 10:
                if kw.status != "achieved":
                    kw.status = "achieved"

            saved += 1

        db.commit()
        return {"saved": saved, "not_mapped": not_mapped}

    async def run_daily_check(self, site_id, db: Session) -> dict:
        """
        Full daily check cycle: trigger → poll → fetch → save.

        Returns combined result dict.
        """
        # Trigger
        trigger_result = await self.trigger_check()
        if isinstance(trigger_result, dict) and trigger_result.get("error"):
            return trigger_result

        # Poll until ready (or timeout)
        elapsed = 0
        while elapsed < POSITION_CHECK_TIMEOUT:
            await asyncio.sleep(POSITION_CHECK_POLL_INTERVAL)
            elapsed += POSITION_CHECK_POLL_INTERVAL

            # Try to fetch — if positions are available, they'll show up
            result = await self.fetch_results(site_id, db)
            if result.get("saved", 0) > 0 or elapsed >= POSITION_CHECK_TIMEOUT:
                result["poll_seconds"] = elapsed
                return result

        # Final attempt after timeout
        result = await self.fetch_results(site_id, db)
        result["poll_seconds"] = elapsed
        result["timed_out"] = True
        return result

    @staticmethod
    def _extract_position(pos_data: dict, regions_indexes: list[int]) -> Optional[int]:
        """
        Extract best position from Topvisor position data.

        Topvisor returns positions in keys like "p{regions_index}_{date}" or "position_{index}".
        The exact format depends on the API response structure.
        """
        position = None

        # Try common Topvisor position field patterns
        for key, value in pos_data.items():
            if not key.startswith("p") or key in ("project_id",):
                continue
            # Position fields: p0_2026-03-10, p1_2026-03-10, etc.
            if isinstance(value, (int, float)) and value > 0:
                val = int(value)
                if position is None or val < position:
                    position = val
            elif isinstance(value, dict):
                # Nested: {"position": 5, ...}
                pos_val = value.get("position")
                if pos_val and isinstance(pos_val, (int, float)):
                    val = int(pos_val)
                    if position is None or val < position:
                        position = val

        # Also check "positionN" fields
        for idx in regions_indexes:
            key = f"position_{idx}"
            val = pos_data.get(key)
            if val and isinstance(val, (int, float)) and val > 0:
                val = int(val)
                if position is None or val < position:
                    position = val

        return position
