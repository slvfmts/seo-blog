"""
Monitoring scheduler — runs daily position checks at configurable hour.

Uses asyncio task inside FastAPI lifespan. Defaults to 6:00 UTC (9:00 MSK).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from src.services.monitoring.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


class MonitoringScheduler:
    """
    Async scheduler for daily monitoring tasks.

    Usage:
        scheduler = MonitoringScheduler(position_tracker, run_hour=6)
        await scheduler.start()  # starts background loop
        ...
        await scheduler.stop()
    """

    def __init__(self, position_tracker: PositionTracker, db_session_factory, run_hour: int = 6):
        self.tracker = position_tracker
        self.db_session_factory = db_session_factory
        self.run_hour = run_hour
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the scheduler background loop."""
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Monitoring scheduler started (daily at {self.run_hour:02d}:00 UTC)")

    async def stop(self):
        """Stop the scheduler."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Monitoring scheduler stopped")

    async def _run_loop(self):
        """Main scheduler loop — sleep until next run, then execute."""
        while True:
            try:
                seconds_until_run = self._seconds_until_next_run()
                logger.info(
                    f"Next monitoring check in {seconds_until_run // 3600}h "
                    f"{(seconds_until_run % 3600) // 60}m"
                )
                await asyncio.sleep(seconds_until_run)
                await self._run_check()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Scheduler error: {e}", exc_info=True)
                # Wait 1 hour before retrying on error
                await asyncio.sleep(3600)

    def _seconds_until_next_run(self) -> float:
        """Calculate seconds until the next scheduled run."""
        now = datetime.utcnow()
        target = now.replace(hour=self.run_hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _run_check(self):
        """Run position checks for all active sites."""
        from src.db import models

        logger.info("Starting scheduled monitoring check")
        db = self.db_session_factory()
        try:
            sites = db.query(models.Site).filter(
                models.Site.status == "active",
                models.Site.domain.isnot(None),
            ).all()

            for site in sites:
                logger.info(f"Checking positions for site: {site.name} ({site.domain})")
                try:
                    summary = await self.tracker.run_daily_check(site.id)
                    logger.info(f"  Result: {summary}")

                    signals = await self.tracker.detect_decay(site.id)
                    if signals:
                        logger.warning(f"  Decay signals: {len(signals)}")
                        for s in signals:
                            logger.warning(f"    [{s.severity}] {s.details.get('keyword')}: {s.details.get('message')}")
                except Exception as e:
                    logger.error(f"  Error checking site {site.name}: {e}")
        finally:
            db.close()

        logger.info("Scheduled monitoring check completed")
