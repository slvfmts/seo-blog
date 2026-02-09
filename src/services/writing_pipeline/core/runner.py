"""
PipelineRunner - Orchestrates the writing pipeline stages.
"""

import os
import json
from datetime import datetime
from typing import Optional, List

import anthropic

from .context import WritingContext
from ..contracts import PipelineResult
from ..stages import (
    IntentStage,
    ResearchStage,
    StructureStage,
    DraftingStage,
    EditingStage,
    MetaStage,
)


class PipelineRunner:
    """
    Orchestrates the multi-stage article writing pipeline.

    Flow:
    topic → Intent → Research → Structure → Drafting → Editing → article.md

    Features:
    - Executes stages sequentially with shared context
    - Saves intermediate results for debugging
    - Logs token usage and timing
    - Supports resuming from a specific stage (future)
    """

    def __init__(
        self,
        anthropic_api_key: str,
        serper_api_key: Optional[str] = None,
        jina_api_key: Optional[str] = None,
        dataforseo_login: Optional[str] = None,
        dataforseo_password: Optional[str] = None,
        model: str = "claude-sonnet-4-20250514",
        proxy_url: Optional[str] = None,
        proxy_secret: Optional[str] = None,
        ghost_url: Optional[str] = None,
        ghost_admin_key: Optional[str] = None,
    ):
        """
        Initialize the pipeline runner.

        Args:
            anthropic_api_key: Anthropic API key
            serper_api_key: Optional Serper.dev API key for search
            jina_api_key: Optional Jina Reader API key for higher limits
            dataforseo_login: Optional DataForSEO API login for keyword metrics
            dataforseo_password: Optional DataForSEO API password
            model: Claude model to use
            proxy_url: Optional proxy URL for API calls
            proxy_secret: Optional proxy secret
            ghost_url: Optional Ghost CMS URL for internal linking
            ghost_admin_key: Optional Ghost Admin API key
        """
        # Initialize Anthropic client
        if proxy_url and proxy_secret:
            self.client = anthropic.Anthropic(
                api_key=anthropic_api_key,
                base_url=proxy_url,
                default_headers={"x-proxy-token": proxy_secret},
            )
        else:
            self.client = anthropic.Anthropic(api_key=anthropic_api_key)

        self.model = model
        self.serper_api_key = serper_api_key
        self.jina_api_key = jina_api_key
        self.dataforseo_login = dataforseo_login
        self.dataforseo_password = dataforseo_password
        self.ghost_url = ghost_url
        self.ghost_admin_key = ghost_admin_key

        # Initialize stages
        self.stages = [
            IntentStage(client=self.client, model=self.model),
            ResearchStage(
                client=self.client,
                model=self.model,
                serper_api_key=self.serper_api_key,
                jina_api_key=self.jina_api_key,
                dataforseo_login=self.dataforseo_login,
                dataforseo_password=self.dataforseo_password,
            ),
            StructureStage(client=self.client, model=self.model),
            DraftingStage(client=self.client, model=self.model),
            EditingStage(client=self.client, model=self.model),
            MetaStage(client=self.client, model=self.model),
        ]

    async def run(
        self,
        topic: str,
        region: str = "ru",
        output_dir: Optional[str] = None,
        save_intermediate: bool = True,
        config: Optional[dict] = None,
    ) -> PipelineResult:
        """
        Run the full writing pipeline.

        Args:
            topic: Article topic
            region: Target region (affects language, search locale)
            output_dir: Directory for intermediate results
            save_intermediate: Whether to save intermediate files
            config: Optional pipeline configuration:
                - expand_paa: bool (default True) - expand queries with PAA
                - fetch_page_content: bool (default True) - fetch full page content
                - max_pages_to_fetch: int (default 5) - max pages for content fetch
                - max_paa_queries: int (default 3) - max PAA queries to expand

        Returns:
            PipelineResult with final article and metadata
        """
        # Create output directory if specified
        if output_dir and save_intermediate:
            os.makedirs(output_dir, exist_ok=True)

        # Default config
        pipeline_config = {
            "expand_paa": True,
            "fetch_page_content": True,
            "max_pages_to_fetch": 5,
            "max_paa_queries": 3,
        }
        if config:
            pipeline_config.update(config)

        # Fetch existing posts from Ghost for internal linking
        existing_posts = []
        if self.ghost_url and self.ghost_admin_key:
            try:
                from ...publisher import GhostPublisher
                publisher = GhostPublisher(self.ghost_url, self.ghost_admin_key)
                existing_posts = publisher.get_posts()
            except Exception:
                pass  # Graceful degradation

        # Initialize context
        context = WritingContext(
            topic=topic,
            region=region,
            output_dir=output_dir,
            save_intermediate=save_intermediate,
            started_at=datetime.now(),
            config=pipeline_config,
            existing_posts=existing_posts,
        )

        # Run all stages
        for stage in self.stages:
            context = await stage.run(context)

        context.completed_at = datetime.now()

        # Build final result
        result = PipelineResult(
            topic=topic,
            region=region,
            article_md=context.edited_md,
            title=context.outline.title,
            subtitle=context.outline.subtitle,
            word_count=len(context.edited_md.split()),
            meta=context.meta,
            intent=context.intent,
            research=context.research,
            outline=context.outline,
            draft_md=context.draft_md,
            started_at=context.started_at.isoformat(),
            completed_at=context.completed_at.isoformat(),
            stages_completed=context.get_completed_stages(),
        )

        # Save final result summary
        if output_dir and save_intermediate:
            summary_path = os.path.join(output_dir, "00_summary.json")
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump({
                    "topic": topic,
                    "region": region,
                    "title": result.title,
                    "subtitle": result.subtitle,
                    "word_count": result.word_count,
                    "total_tokens": context.get_total_tokens(),
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                    "stages": [
                        {
                            "name": log.stage_name,
                            "status": log.status,
                            "tokens": log.tokens_used,
                            "duration_ms": (
                                (log.completed_at - log.started_at).total_seconds() * 1000
                                if log.completed_at else None
                            ),
                        }
                        for log in context.stage_logs
                    ],
                }, f, ensure_ascii=False, indent=2)

        return result

    async def run_stage(
        self,
        stage_name: str,
        context: WritingContext,
    ) -> WritingContext:
        """
        Run a specific stage.

        Useful for resuming from a specific point or re-running a stage.

        Args:
            stage_name: Name of the stage to run
            context: Existing context with previous stage outputs

        Returns:
            Updated context
        """
        for stage in self.stages:
            if stage.name == stage_name:
                return await stage.run(context)

        raise ValueError(f"Unknown stage: {stage_name}")

    def get_stage_names(self) -> List[str]:
        """Get list of all stage names in order."""
        return [stage.name for stage in self.stages]
