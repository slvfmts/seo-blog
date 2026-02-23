"""
Position Tracker — orchestrates SERP checks, saves rankings, runs decay detection.

Connects SerperSerpClient + DB + DecayDetector into a single workflow.
"""

import logging
from datetime import datetime, date
from typing import List, Optional
from sqlalchemy.orm import Session

from src.db import models
from src.services.monitoring.serper_serp import SerperSerpClient, REGION_MAP
from src.services.monitoring.decay_detector import DecayDetector, DecaySignal

logger = logging.getLogger(__name__)


class PositionTracker:
    """
    Orchestrates daily position checks and decay detection.

    Usage:
        tracker = PositionTracker(db_session_factory, "serper-api-key")
        summary = await tracker.run_daily_check(site_id)
        signals = await tracker.detect_decay(site_id)
    """

    def __init__(
        self,
        db_session_factory,
        serper_api_key: str,
    ):
        self.db_session_factory = db_session_factory
        self.serp_client = SerperSerpClient(api_key=serper_api_key)
        self.decay_detector = DecayDetector()

    async def run_daily_check(self, site_id) -> dict:
        """
        Run position check for all tracked keywords of a site.

        Returns:
            Summary dict: {checked, found, not_found, errors, cost, skipped}
        """
        db: Session = self.db_session_factory()
        try:
            # 1. Get site domain
            site = db.query(models.Site).filter(models.Site.id == site_id).first()
            if not site or not site.domain:
                return {"error": "Site not found or no domain configured"}

            domain = site.domain
            region = (site.country or "ru").lower()

            # 2. Get keywords to check (targeted or achieved)
            keywords = db.query(models.Keyword).filter(
                models.Keyword.site_id == site_id,
                models.Keyword.status.in_(["targeted", "achieved"]),
            ).all()

            if not keywords:
                return {"checked": 0, "message": "No tracked keywords"}

            # 3. Skip already checked today
            today = date.today()
            today_start = datetime(today.year, today.month, today.day)
            already_checked = set()
            existing = db.query(models.KeywordRanking.keyword_id).filter(
                models.KeywordRanking.date >= today_start,
            ).all()
            already_checked = {str(r[0]) for r in existing}

            to_check = [kw for kw in keywords if str(kw.id) not in already_checked]
            if not to_check:
                return {"checked": 0, "skipped": len(keywords), "message": "All already checked today"}

            # 4. Batch check positions
            keyword_texts = [kw.keyword for kw in to_check]
            results = await self.serp_client.check_positions_batch(
                keywords=keyword_texts,
                domain=domain,
                region=region,
                depth=30,
            )

            # 5. Save results
            checked = 0
            found = 0
            not_found = 0
            errors = 0
            total_cost = 0.0

            for kw, result in zip(to_check, results):
                total_cost += result.cost

                if not result.success:
                    errors += 1
                    logger.warning(f"SERP check failed for '{kw.keyword}': {result.error}")
                    continue

                checked += 1

                # Create ranking record
                ranking = models.KeywordRanking(
                    keyword_id=kw.id,
                    post_id=kw.post_id,
                    date=today_start,
                    position=result.position,
                    url=result.url,
                    serp_features=result.serp_features,
                    source="serper",
                )
                db.add(ranking)

                # Update keyword.current_position
                kw.current_position = result.position
                kw.updated_at = datetime.utcnow()

                # Update keyword status based on position
                if result.position is not None and result.position <= 10:
                    if kw.status != "achieved":
                        kw.status = "achieved"
                    found += 1
                elif result.position is not None:
                    found += 1
                else:
                    not_found += 1

            db.commit()

            return {
                "checked": checked,
                "found": found,
                "not_found": not_found,
                "errors": errors,
                "skipped": len(already_checked),
                "cost": round(total_cost, 4),
            }

        except Exception as e:
            logger.error(f"Position check failed for site {site_id}: {e}")
            db.rollback()
            return {"error": str(e)}
        finally:
            db.close()

    async def detect_decay(self, site_id) -> List[DecaySignal]:
        """
        Run decay detection for all tracked keywords and create iteration tasks.

        Returns:
            List of all detected signals
        """
        db: Session = self.db_session_factory()
        try:
            # Get keywords with their rankings
            keywords = db.query(models.Keyword).filter(
                models.Keyword.site_id == site_id,
                models.Keyword.status.in_(["targeted", "achieved"]),
            ).all()

            all_signals = []

            for kw in keywords:
                # Get ranking history (last 60 days)
                rankings = db.query(models.KeywordRanking).filter(
                    models.KeywordRanking.keyword_id == kw.id,
                ).order_by(models.KeywordRanking.date.desc()).limit(60).all()

                if len(rankings) < 2:
                    continue

                # Convert to dicts for DecayDetector
                ranking_dicts = [
                    {
                        "keyword_id": str(kw.id),
                        "post_id": str(kw.post_id) if kw.post_id else None,
                        "keyword": kw.keyword,
                        "date": r.date,
                        "position": r.position,
                    }
                    for r in rankings
                ]

                signals = self.decay_detector.analyze(ranking_dicts)
                all_signals.extend(signals)

                # Create iteration tasks for high/critical severity
                for signal in signals:
                    if signal.severity in ("high", "critical") and signal.post_id:
                        # Check if task already exists for this post
                        existing_task = db.query(models.IterationTask).filter(
                            models.IterationTask.post_id == signal.post_id,
                            models.IterationTask.status.in_(["pending", "in_progress"]),
                        ).first()

                        if not existing_task:
                            priority = 1 if signal.severity == "critical" else 3
                            task = models.IterationTask(
                                post_id=signal.post_id,
                                trigger_type=signal.signal_type,
                                trigger_data=signal.details,
                                priority=priority,
                                status="pending",
                            )
                            db.add(task)

            db.commit()
            return all_signals

        except Exception as e:
            logger.error(f"Decay detection failed for site {site_id}: {e}")
            db.rollback()
            return []
        finally:
            db.close()

    async def get_rankings_summary(self, site_id) -> dict:
        """Get summary statistics for site's keyword rankings."""
        db: Session = self.db_session_factory()
        try:
            keywords = db.query(models.Keyword).filter(
                models.Keyword.site_id == site_id,
                models.Keyword.status.in_(["targeted", "achieved"]),
            ).all()

            total = len(keywords)
            in_top_3 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 3)
            in_top_10 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 10)
            in_top_20 = sum(1 for kw in keywords if kw.current_position and kw.current_position <= 20)
            not_ranking = sum(1 for kw in keywords if kw.current_position is None)

            positions = [kw.current_position for kw in keywords if kw.current_position is not None]
            avg_position = round(sum(positions) / len(positions), 1) if positions else None

            # Count pending alerts
            alert_count = db.query(models.IterationTask).filter(
                models.IterationTask.status == "pending",
                models.IterationTask.post_id.in_(
                    db.query(models.Post.id).filter(models.Post.site_id == site_id)
                ),
            ).count()

            return {
                "total_tracked": total,
                "in_top_3": in_top_3,
                "in_top_10": in_top_10,
                "in_top_20": in_top_20,
                "not_ranking": not_ranking,
                "avg_position": avg_position,
                "alerts": alert_count,
            }
        finally:
            db.close()
