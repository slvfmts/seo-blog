"""
CompositeVolumeProvider — queries Yandex Wordstat + Rush Analytics in parallel, merges results.

- volume = max(yandex_volume, google_volume) for sorting/filtering
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
    Merges Yandex Wordstat (yandex_volume) + Rush Analytics (google_volume).

    Rush returns Yandex Wordstat data too, but with exact-match frequency.
    Yandex gives broad-match. Both are useful — we keep both and use max for sorting.
    """

    def __init__(
        self,
        wordstat_provider: Optional[VolumeProvider] = None,
        rush_provider: Optional[VolumeProvider] = None,
    ):
        self.wordstat = wordstat_provider
        self.rush = rush_provider

        if not self.wordstat and not self.rush:
            raise ValueError("CompositeVolumeProvider needs at least one sub-provider")

    @property
    def source_name(self) -> str:
        parts = []
        if self.wordstat:
            parts.append("wordstat")
        if self.rush:
            parts.append("rush")
        return "+".join(parts)

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        if not keywords:
            return []

        # Run both providers in parallel
        tasks = {}
        if self.wordstat:
            tasks["wordstat"] = self.wordstat.get_volumes(keywords, language_code)
        if self.rush:
            tasks["rush"] = self.rush.get_volumes(keywords, language_code)

        results_map = {}
        gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for name, result in zip(tasks.keys(), gathered):
            if isinstance(result, Exception):
                logger.error(f"CompositeVolumeProvider: {name} failed: {result}")
                results_map[name] = None
            else:
                results_map[name] = result

        wordstat_results = results_map.get("wordstat")
        rush_results = results_map.get("rush")

        # Merge into composite VolumeResult
        merged = []
        for i, kw in enumerate(keywords):
            ws_vol = 0
            rush_vol = 0
            difficulty = 0.0
            cpc = 0.0
            competition = 0.0
            competition_level = "LOW"

            if wordstat_results and i < len(wordstat_results):
                ws = wordstat_results[i]
                ws_vol = ws.volume

            if rush_results and i < len(rush_results):
                rs = rush_results[i]
                rush_vol = rs.volume
                # Rush may provide difficulty/cpc from its data
                difficulty = rs.difficulty
                cpc = rs.cpc
                competition = rs.competition
                competition_level = rs.competition_level

            merged.append(VolumeResult(
                keyword=kw,
                volume=max(ws_vol, rush_vol),  # use max for sorting/filtering
                source=self.source_name,
                difficulty=difficulty,
                cpc=cpc,
                competition=competition,
                competition_level=competition_level,
                yandex_volume=ws_vol,
                google_volume=rush_vol,
            ))

        found = sum(1 for r in merged if r.volume > 0)
        logger.info(
            f"CompositeVolumeProvider: {found}/{len(merged)} keywords with volume "
            f"(wordstat: {'OK' if wordstat_results else 'FAIL'}, "
            f"rush: {'OK' if rush_results else 'FAIL'})"
        )
        return merged

    async def get_suggestions(self, keyword: str) -> list[str]:
        """Collect suggestions from both providers, deduplicate."""
        tasks = []
        if self.wordstat:
            tasks.append(self.wordstat.get_suggestions(keyword))
        if self.rush:
            tasks.append(self.rush.get_suggestions(keyword))

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
