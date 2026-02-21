"""
WritingContext - Shared context between pipeline stages.
"""

from dataclasses import dataclass, field
from typing import Optional, Any, Dict, List
from datetime import datetime

from ..contracts import IntentResult, ResearchResult, OutlineResult, QueryPlannerResult, KeywordMetricsResult, MetaResult, DraftMeta, QualityGateResult, ArticleBrief


@dataclass
class StageLog:
    """Log entry for a stage execution."""
    stage_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    status: str = "running"  # running | completed | failed
    error: Optional[str] = None
    tokens_used: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WritingContext:
    """
    Shared context passed between pipeline stages.

    Each stage reads from and writes to this context.
    """
    # Input parameters
    topic: str
    region: str = "ru"

    # Stage outputs (populated as pipeline progresses)
    intent: Optional[IntentResult] = None
    queries: Optional[QueryPlannerResult] = None
    search_results: Optional[List[Dict[str, Any]]] = None  # Raw search results
    research: Optional[ResearchResult] = None
    outline: Optional[OutlineResult] = None
    draft_md: Optional[str] = None
    edited_md: Optional[str] = None
    meta: Optional[MetaResult] = None
    keyword_metrics: Optional[KeywordMetricsResult] = None
    draft_meta: Optional[DraftMeta] = None  # v3 drafting metadata
    quality_report: Optional[Dict[str, Any]] = None  # v3 quality gate report
    formatting_result: Optional[Any] = None  # FormattingResult from formatting stage

    # External data (fetched before pipeline starts)
    existing_posts: List[Dict[str, Any]] = field(default_factory=list)

    # Execution metadata
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    stage_logs: List[StageLog] = field(default_factory=list)
    current_stage: Optional[str] = None

    # Configuration
    output_dir: Optional[str] = None  # Directory for saving intermediate results
    save_intermediate: bool = True  # Whether to save intermediate results to files
    config: Dict[str, Any] = field(default_factory=dict)  # Pipeline configuration

    def start_stage(self, stage_name: str) -> StageLog:
        """Mark the start of a stage."""
        self.current_stage = stage_name
        log = StageLog(
            stage_name=stage_name,
            started_at=datetime.now(),
        )
        self.stage_logs.append(log)
        return log

    def complete_stage(self, tokens_used: int = 0, metadata: Optional[Dict[str, Any]] = None):
        """Mark the current stage as completed."""
        if self.stage_logs:
            log = self.stage_logs[-1]
            log.completed_at = datetime.now()
            log.status = "completed"
            log.tokens_used = tokens_used
            if metadata:
                log.metadata.update(metadata)
        self.current_stage = None

    def fail_stage(self, error: str):
        """Mark the current stage as failed."""
        if self.stage_logs:
            log = self.stage_logs[-1]
            log.completed_at = datetime.now()
            log.status = "failed"
            log.error = error
        self.current_stage = None

    def get_completed_stages(self) -> List[str]:
        """Get list of completed stage names."""
        return [
            log.stage_name
            for log in self.stage_logs
            if log.status == "completed"
        ]

    def get_total_tokens(self) -> int:
        """Get total tokens used across all stages."""
        return sum(log.tokens_used for log in self.stage_logs)

    def get_stage_log(self, stage_name: str) -> Optional[StageLog]:
        """Get log for a specific stage."""
        for log in self.stage_logs:
            if log.stage_name == stage_name:
                return log
        return None
