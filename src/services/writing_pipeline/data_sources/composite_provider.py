"""
CompositeVolumeProvider — queries two volume providers in parallel, merges results.

Primary: Yandex Wordstat (broad-match) → stored as yandex_volume
Secondary: Topvisor or Rush Analytics → stored as google_volume

- volume = max(yandex_volume, secondary_volume) for sorting/filtering
- yandex_volume / google_volume stored separately
- If one provider fails, data from the other is still returned
- get_suggestions() collects from both, deduplicates
"""

import asyncio
import logging
from typing import Optional

from .volume_provider import VolumeProvider, VolumeResult

logger = logging.getLogger(__name__)


class CompositeVolumeProvider(VolumeProvider):
    """
    Merges primary (Wordstat) + secondary (Topvisor or Rush) volume data.

    Primary gives broad-match Yandex volume.
    Secondary gives additional volume data (stored as google_volume for backwards compat).
    Both are useful — we keep both and use max for sorting.
    """

    def __init__(
        self,
        wordstat_provider: Optional[VolumeProvider] = None,
        rush_provider: Optional[VolumeProvider] = None,
    ):
        self.wordstat = wordstat_provider
        self.secondary = rush_provider  # Topvisor or Rush

        if not self.wordstat and not self.secondary:
            raise ValueError("CompositeVolumeProvider needs at least one sub-provider")

    @property
    def source_name(self) -> str:
        parts = []
        if self.wordstat:
            parts.append(self.wordstat.source_name)
        if self.secondary:
            parts.append(self.secondary.source_name)
        return "+".join(parts)

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        if not keywords:
            return []

        # Run both providers in parallel
        tasks = {}
        if self.wordstat:
            tasks["primary"] = self.wordstat.get_volumes(keywords, language_code)
        if self.secondary:
            secondary_name = self.secondary.source_name
            tasks[secondary_name] = self.secondary.get_volumes(keywords, language_code)

        results_map = {}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for name, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.error(f"CompositeVolumeProvider: {name} failed: {result}")
                results_map[name] = None
            else:
                results_map[name] = result

        primary_results = results_map.get("primary")
        secondary_name = self.secondary.source_name if self.secondary else None
        secondary_results = results_map.get(secondary_name) if secondary_name else None

        # Merge into composite VolumeResult
        merged = []
        for i, kw in enumerate(keywords):
            ws_vol = 0
            sec_vol = 0
            difficulty = 0.0
            cpc = 0.0
            competition = 0.0
            competition_level = "LOW"

            if primary_results and i < len(primary_results):
                ws = primary_results[i]
                ws_vol = ws.volume

            if secondary_results and i < len(secondary_results):
                sr = secondary_results[i]
                sec_vol = sr.volume
                difficulty = sr.difficulty
                cpc = sr.cpc
                competition = sr.competition
                competition_level = sr.competition_level

            merged.append(VolumeResult(
                keyword=kw,
                volume=max(ws_vol, sec_vol),  # use max for sorting/filtering
                source=self.source_name,
                difficulty=difficulty,
                cpc=cpc,
                competition=competition,
                competition_level=competition_level,
                yandex_volume=ws_vol,
                google_volume=sec_vol,
            ))

        found = sum(1 for r in merged if r.volume > 0)
        logger.info(
            f"CompositeVolumeProvider: {found}/{len(merged)} keywords with volume "
            f"(primary: {'OK' if primary_results else 'FAIL'}, "
            f"{secondary_name or 'secondary'}: {'OK' if secondary_results else 'FAIL'})"
        )
        return merged

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Collect suggestions from both providers, deduplicate."""
        tasks = []
        if self.wordstat:
            tasks.append(self.wordstat.get_suggestions(keyword))
        if self.secondary:
            tasks.append(self.secondary.get_suggestions(keyword))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        seen = set()
        suggestions = []
        for result in results:
            if isinstance(result, Exception):
                continue
            for s in result:
                key = s.lower().strip()
                if key not in seen:
                    seen.add(key)
                    suggestions.append(s)

        return suggestions
