"""
Decay detection logic for keyword rankings.

Pure logic module with no DB dependencies — takes ranking data as dicts,
returns decay signals. Easy to test in isolation.

Rules:
1. Position dropped >5 in 7 days → severity=medium (alert)
2. Position dropped >10 in 30 days → severity=high (create iteration task)
3. Was in top-10, now not in top-100 → severity=critical (priority=1 task)
4. Position 11-20 → severity=low, type=opportunity (optimization suggestion)
"""

from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta


@dataclass
class DecaySignal:
    """A detected decay or opportunity signal."""
    keyword_id: str
    post_id: Optional[str]
    signal_type: str  # "decay" | "opportunity" | "lost"
    severity: str  # "low" | "medium" | "high" | "critical"
    details: dict
    suggested_action: str


class DecayDetector:
    """
    Analyzes ranking history to detect position drops and opportunities.

    Usage:
        detector = DecayDetector()
        signals = detector.analyze(rankings)

    Rankings format: list of dicts with keys:
        keyword_id, post_id, keyword, date, position (int or None)
    Sorted by date descending (newest first).
    """

    def __init__(
        self,
        short_window_days: int = 7,
        long_window_days: int = 30,
        short_drop_threshold: int = 5,
        long_drop_threshold: int = 10,
    ):
        self.short_window_days = short_window_days
        self.long_window_days = long_window_days
        self.short_drop_threshold = short_drop_threshold
        self.long_drop_threshold = long_drop_threshold

    def analyze(self, rankings: List[dict]) -> List[DecaySignal]:
        """
        Analyze rankings for a single keyword and return decay signals.

        Args:
            rankings: List of ranking dicts sorted by date descending.
                      Each dict: {keyword_id, post_id, keyword, date, position}

        Returns:
            List of DecaySignal objects (may be empty if no issues detected)
        """
        if not rankings or len(rankings) < 2:
            return []

        signals = []
        latest = rankings[0]
        keyword_id = str(latest["keyword_id"])
        post_id = str(latest["post_id"]) if latest.get("post_id") else None
        keyword = latest.get("keyword", "")
        current_pos = latest.get("position")
        now = latest["date"] if isinstance(latest["date"], datetime) else datetime.fromisoformat(str(latest["date"]))

        # Find comparison points
        short_ago = now - timedelta(days=self.short_window_days)
        long_ago = now - timedelta(days=self.long_window_days)

        pos_short_ago = self._find_position_near_date(rankings, short_ago)
        pos_long_ago = self._find_position_near_date(rankings, long_ago)

        # Rule 3: Was in top-10, now not in top-100 (critical)
        best_historical = self._best_position(rankings)
        if best_historical is not None and best_historical <= 10 and current_pos is None:
            signals.append(DecaySignal(
                keyword_id=keyword_id,
                post_id=post_id,
                signal_type="lost",
                severity="critical",
                details={
                    "keyword": keyword,
                    "best_position": best_historical,
                    "current_position": None,
                    "message": f"Was #{best_historical}, now out of top-100",
                },
                suggested_action="Urgent content refresh + on-page optimization",
            ))
            return signals  # Critical = no need to check further

        # Rule 1: Short-term drop >5 positions in 7 days (medium)
        if current_pos is not None and pos_short_ago is not None:
            short_drop = current_pos - pos_short_ago  # positive = dropped
            if short_drop > self.short_drop_threshold:
                signals.append(DecaySignal(
                    keyword_id=keyword_id,
                    post_id=post_id,
                    signal_type="decay",
                    severity="medium",
                    details={
                        "keyword": keyword,
                        "position_before": pos_short_ago,
                        "position_now": current_pos,
                        "drop": short_drop,
                        "period_days": self.short_window_days,
                        "message": f"Dropped {short_drop} positions in {self.short_window_days} days",
                    },
                    suggested_action="Check for SERP changes or algorithm update",
                ))

        # Rule 2: Long-term drop >10 positions in 30 days (high)
        if current_pos is not None and pos_long_ago is not None:
            long_drop = current_pos - pos_long_ago
            if long_drop > self.long_drop_threshold:
                signals.append(DecaySignal(
                    keyword_id=keyword_id,
                    post_id=post_id,
                    signal_type="decay",
                    severity="high",
                    details={
                        "keyword": keyword,
                        "position_before": pos_long_ago,
                        "position_now": current_pos,
                        "drop": long_drop,
                        "period_days": self.long_window_days,
                        "message": f"Dropped {long_drop} positions in {self.long_window_days} days",
                    },
                    suggested_action="Content refresh with updated information and optimization",
                ))

        # Rule 4: Position 11-20 = opportunity (low)
        if current_pos is not None and 11 <= current_pos <= 20:
            # Only flag if stable or improving (not already decaying)
            is_decaying = any(s.signal_type == "decay" for s in signals)
            if not is_decaying:
                signals.append(DecaySignal(
                    keyword_id=keyword_id,
                    post_id=post_id,
                    signal_type="opportunity",
                    severity="low",
                    details={
                        "keyword": keyword,
                        "current_position": current_pos,
                        "message": f"Position #{current_pos} — close to page 1",
                    },
                    suggested_action="On-page optimization + internal links to push into top-10",
                ))

        return signals

    def _find_position_near_date(
        self,
        rankings: List[dict],
        target_date: datetime,
        tolerance_days: int = 3,
    ) -> Optional[int]:
        """Find the position closest to target_date within tolerance."""
        best_match = None
        best_delta = timedelta(days=tolerance_days + 1)

        for r in rankings:
            r_date = r["date"] if isinstance(r["date"], datetime) else datetime.fromisoformat(str(r["date"]))
            delta = abs(r_date - target_date)
            if delta < best_delta and r.get("position") is not None:
                best_delta = delta
                best_match = r["position"]

        return best_match

    def _best_position(self, rankings: List[dict]) -> Optional[int]:
        """Find the best (lowest) position ever recorded."""
        positions = [r["position"] for r in rankings if r.get("position") is not None]
        return min(positions) if positions else None
