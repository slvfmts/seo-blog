"""
Monitoring scheduler — runs daily position checks at configurable hour.

Three-phase check (non-blocking):
  Phase 1: Trigger Topvisor position checks for all Topvisor-enabled sites
  Phase 2: Run Serper checks for sites without Topvisor (parallel with Phase 1 wait)
  Phase 3: Fetch Topvisor results (poll until ready, max 15 min)
  Decay detection runs after all position checks.

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

    Resolves credentials per site's blog for full blog isolation.
    Supports Topvisor (primary) and Serper (fallback) for position checks.

    Usage:
        scheduler = MonitoringScheduler(db_session_factory, run_hour=6)
        await scheduler.start()  # starts background loop
        ...
        await scheduler.stop()
    """

    def __init__(self, db_session_factory, run_hour: int = 6):
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
        """
        Three-phase position check for all active sites.

        Phase 1: Trigger Topvisor checks (async, non-blocking)
        Phase 2: Run Serper checks for non-Topvisor sites (while Topvisor processes)
        Phase 3: Fetch Topvisor results (poll until ready)
        Then: Decay detection for all sites.
        """
        from src.db import models
        from src.config.settings import get_settings
        from src.services.monitoring import make_topvisor_client
        from src.services.monitoring.topvisor_positions import TopvisorPositionTracker

        settings = get_settings()

        logger.info("Starting scheduled monitoring check (three-phase)")
        db = self.db_session_factory()
        try:
            sites = db.query(models.Site).filter(
                models.Site.status == "active",
                models.Site.domain.isnot(None),
            ).all()

            topvisor_sites = []    # (site, tv_tracker, blog)
            serper_sites = []      # (site, serper_key, blog)

            # Classify sites by available position source
            for site in sites:
                blog = site.blog if site.blog_id else None
                bs = self._resolve_blog_settings_minimal(blog, settings)

                tv_client = make_topvisor_client(bs)
                if tv_client:
                    tv_tracker = TopvisorPositionTracker(tv_client)
                    topvisor_sites.append((site, tv_tracker, blog))
                else:
                    serper_key = bs.get("serper_api_key")
                    if serper_key:
                        serper_sites.append((site, serper_key, blog))
                    else:
                        logger.info(f"  Skipping site {site.name}: no position source")

            # Phase 1: Trigger Topvisor checks (fast, just sends trigger)
            for site, tv_tracker, blog in topvisor_sites:
                try:
                    logger.info(f"  Phase 1: Triggering Topvisor check for {site.name}")
                    await tv_tracker.trigger_check()
                except Exception as e:
                    logger.error(f"  Topvisor trigger failed for {site.name}: {e}")

            # Phase 2: Serper checks run while Topvisor processes (parallel)
            for site, serper_key, blog in serper_sites:
                tracker = PositionTracker(
                    db_session_factory=self.db_session_factory,
                    serper_api_key=serper_key,
                )
                logger.info(f"  Phase 2: Serper check for {site.name} ({site.domain})")
                try:
                    summary = await tracker.run_daily_check(site.id)
                    logger.info(f"    Serper result: {summary}")
                except Exception as e:
                    logger.error(f"    Serper error for {site.name}: {e}")

            # Phase 3: Fetch Topvisor results (poll until ready)
            if topvisor_sites:
                logger.info("  Phase 3: Fetching Topvisor results...")
                await asyncio.sleep(30)  # Initial wait for Topvisor to process

                for site, tv_tracker, blog in topvisor_sites:
                    try:
                        # Use a dedicated DB session for each site's fetch
                        fetch_db = self.db_session_factory()
                        try:
                            result = await tv_tracker.fetch_results(site.id, fetch_db)
                            logger.info(f"    Topvisor result for {site.name}: {result}")
                        finally:
                            fetch_db.close()
                    except Exception as e:
                        logger.error(f"    Topvisor fetch failed for {site.name}: {e}")

            # Decay detection — after all position checks
            all_sites = [(s, blog) for s, _, blog in topvisor_sites] + [(s, blog) for s, _, blog in serper_sites]
            for site, blog in all_sites:
                try:
                    serper_key = (
                        (blog.serper_api_key if blog and blog.serper_api_key else None)
                        or settings.serper_api_key
                    )
                    if serper_key:
                        tracker = PositionTracker(
                            db_session_factory=self.db_session_factory,
                            serper_api_key=serper_key,
                        )
                        signals = await tracker.detect_decay(site.id)
                        if signals:
                            logger.warning(f"  Decay signals for {site.name}: {len(signals)}")
                            for s in signals:
                                logger.warning(f"    [{s.severity}] {s.details.get('keyword')}: {s.details.get('message')}")
                except Exception as e:
                    logger.error(f"  Decay detection error for {site.name}: {e}")

        finally:
            db.close()

        logger.info("Scheduled monitoring check completed")

    @staticmethod
    def _resolve_blog_settings_minimal(blog, settings) -> dict:
        """Minimal blog settings dict for credential resolution."""
        return {
            "serper_api_key": (blog.serper_api_key if blog and blog.serper_api_key else None) or settings.serper_api_key,
            "topvisor_user_id": (blog.topvisor_user_id if blog and blog.topvisor_user_id else None) or settings.topvisor_user_id,
            "topvisor_access_token": (blog.topvisor_access_token if blog and blog.topvisor_access_token else None) or settings.topvisor_access_token,
            "topvisor_project_id": (blog.topvisor_project_id if blog and blog.topvisor_project_id else None) or settings.topvisor_project_id,
        }
