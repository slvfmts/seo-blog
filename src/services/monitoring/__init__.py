"""
Monitoring package: position tracking, decay detection, content iteration.
"""

from src.services.monitoring.serper_serp import SerperSerpClient, RankingResult
from src.services.monitoring.decay_detector import DecayDetector, DecaySignal
from src.services.monitoring.position_tracker import PositionTracker
from src.services.monitoring.scheduler import MonitoringScheduler
from src.services.monitoring.topvisor_positions import TopvisorPositionTracker

__all__ = [
    "SerperSerpClient",
    "RankingResult",
    "DecayDetector",
    "DecaySignal",
    "PositionTracker",
    "MonitoringScheduler",
    "TopvisorPositionTracker",
]


def make_topvisor_client(blog_settings: dict = None):
    """
    Create a TopvisorClient from blog settings (with global fallback), or None.

    Shared helper used by scheduler, monitoring routes, and publish hook.
    """
    if blog_settings:
        token = blog_settings.get("topvisor_access_token", "")
        user_id = blog_settings.get("topvisor_user_id", "")
        project_id = blog_settings.get("topvisor_project_id", 0)
    else:
        from src.config.settings import get_settings
        settings = get_settings()
        token = settings.topvisor_access_token
        user_id = settings.topvisor_user_id
        project_id = settings.topvisor_project_id
    if token and user_id and project_id:
        from src.services.writing_pipeline.data_sources.topvisor_client import TopvisorClient
        return TopvisorClient(
            user_id=user_id,
            access_token=token,
            project_id=project_id,
        )
    return None
