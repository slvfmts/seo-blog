"""
PipelineRunner - Orchestrates the writing pipeline stages.
"""

import os
import json
import logging
import re
from datetime import datetime
from typing import Optional, List, Callable

logger = logging.getLogger(__name__)

import anthropic

from .context import WritingContext
from ..contracts import PipelineResult
from ..stages import (
    IntentStage,
    ResearchStage,
    StructureStage,
    DraftingStage,
    EditingStage,
    LinkingStage,
    SeoPolishStage,
    QualityGateStage,
    MetaStage,
    FormattingStage,
)


class PipelineRunner:
    """
    Orchestrates the multi-stage article writing pipeline.

    Flow:
    topic -> Intent -> Research -> Structure -> Drafting -> Editing -> Linking -> SEO Polish -> Quality Gate -> Meta -> Formatting -> article.md

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
        model: str = "claude-sonnet-4-20250514",
        proxy_url: Optional[str] = None,
        proxy_secret: Optional[str] = None,
        ghost_url: Optional[str] = None,
        ghost_admin_key: Optional[str] = None,
        database_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        openai_proxy_url: Optional[str] = None,
        residential_proxy_url: Optional[str] = None,
        yandex_wordstat_api_key: Optional[str] = None,
        yandex_cloud_folder_id: Optional[str] = None,
        rush_analytics_api_key: Optional[str] = None,
    ):
        """
        Initialize the pipeline runner.

        Args:
            anthropic_api_key: Anthropic API key
            serper_api_key: Optional Serper.dev API key for search
            jina_api_key: Optional Jina Reader API key for higher limits
            model: Claude model to use
            proxy_url: Optional proxy URL for API calls
            proxy_secret: Optional proxy secret
            ghost_url: Optional Ghost CMS URL for internal linking
            ghost_admin_key: Optional Ghost Admin API key
            database_url: Optional database URL for keyword-based internal linking
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
        self.ghost_url = ghost_url
        self.ghost_admin_key = ghost_admin_key
        self.database_url = database_url
        self.openai_api_key = openai_api_key or ""
        self.openai_proxy_url = openai_proxy_url or ""
        self.openai_proxy_secret = proxy_secret or ""  # reuse Anthropic proxy secret
        self.residential_proxy_url = residential_proxy_url or ""
        self.yandex_wordstat_api_key = yandex_wordstat_api_key or ""
        self.yandex_cloud_folder_id = yandex_cloud_folder_id or ""
        self.rush_analytics_api_key = rush_analytics_api_key or ""

        # Initialize internal linker if database_url is provided
        self.linker = None
        if database_url:
            try:
                from ...internal_linker import InternalLinker
                self.linker = InternalLinker(database_url)
            except Exception:
                pass  # Graceful degradation

        # Initialize stages
        self.stages = [
            IntentStage(client=self.client, model=self.model),
            ResearchStage(
                client=self.client,
                model=self.model,
                serper_api_key=self.serper_api_key,
                jina_api_key=self.jina_api_key,
                volume_provider=self._init_volume_provider(),
                residential_proxy_url=self.residential_proxy_url,
            ),
            StructureStage(client=self.client, model=self.model),
            DraftingStage(client=self.client, model=self.model),
            EditingStage(client=self.client, model=self.model),
            LinkingStage(client=self.client, model=self.model, linker=self.linker),
            SeoPolishStage(client=self.client, model=self.model),
            QualityGateStage(client=self.client, model=self.model),
            MetaStage(client=self.client, model=self.model),
            FormattingStage(client=self.client, model=self.model, openai_api_key=self.openai_api_key, openai_proxy_url=self.openai_proxy_url, openai_proxy_secret=self.openai_proxy_secret, ghost_url=self.ghost_url or "", ghost_admin_key=self.ghost_admin_key or ""),
        ]

    def _init_volume_provider(self):
        """Initialize the best available volume provider from instance params."""
        try:
            from ..data_sources.volume_provider import get_volume_provider

            class _ProviderSettings:
                pass

            s = _ProviderSettings()
            s.yandex_wordstat_api_key = self.yandex_wordstat_api_key
            s.yandex_cloud_folder_id = self.yandex_cloud_folder_id
            s.rush_analytics_api_key = self.rush_analytics_api_key

            # Default to "ru" — actual region is applied per-run in research stage
            return get_volume_provider("ru", s)
        except Exception as e:
            logger.warning(f"Could not init volume provider: {e}")
            return None

    async def run(
        self,
        topic: str,
        region: str = "ru",
        output_dir: Optional[str] = None,
        save_intermediate: bool = True,
        config: Optional[dict] = None,
        on_stage_complete: Optional[Callable[[str, str], None]] = None,
        brief: Optional[dict] = None,
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
            on_stage_complete: Optional callback(stage_name, status) called after each stage

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
            "use_playwright": True,
        }
        if config:
            pipeline_config.update(config)

        # Pass brief into config for stages to consume
        if brief:
            pipeline_config["brief"] = brief

        # Fetch existing posts from Ghost for cluster overlap analysis
        existing_posts = []
        if self.ghost_url and self.ghost_admin_key:
            try:
                from ...publisher import GhostPublisher
                publisher = GhostPublisher(ghost_url=self.ghost_url, admin_key=self.ghost_admin_key)
                existing_posts = publisher.get_posts()
                logger.info(f"Fetched {len(existing_posts)} existing posts from Ghost for overlap analysis")
            except Exception as e:
                logger.warning(f"Failed to fetch existing posts from Ghost: {e}")

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
            if on_stage_complete:
                on_stage_complete(stage.name, "running")
            context = await stage.run(context)
            if on_stage_complete:
                on_stage_complete(stage.name, "completed")

        context.completed_at = datetime.now()

        # Build linking data for post-publication registration
        linking_data = None
        if context.config.get("_article_keywords"):
            linking_data = {
                "keywords": context.config["_article_keywords"],
                "content_md": context.edited_md,
            }

        # Extract cover image URL from formatting result
        cover_image_url = ""
        cover_image_alt = ""
        if context.formatting_result and hasattr(context.formatting_result, 'cover_ghost_url'):
            cover_image_url = context.formatting_result.cover_ghost_url or ""
            cover_image_alt = context.formatting_result.cover_image_alt or ""

        # Strip H1 (and optional italic subtitle) — Ghost renders title separately
        final_md = context.edited_md
        final_md = re.sub(r'^#\s+.+\n*', '', final_md, count=1)
        final_md = re.sub(r'^_[^_]+_\s*\n*', '', final_md, count=1)
        final_md = final_md.lstrip('\n')

        # Build per-stage token breakdown
        stage_tokens = {}
        for slog in context.stage_logs:
            stage_tokens[slog.stage_name] = {
                "input": slog.input_tokens,
                "output": slog.output_tokens,
                "total": slog.tokens_used,
            }

        # Build final result
        result = PipelineResult(
            topic=topic,
            region=region,
            article_md=final_md,
            title=context.outline.title,
            subtitle=context.outline.subtitle,
            word_count=len(context.edited_md.split()),
            meta=context.meta,
            cover_image_url=cover_image_url,
            cover_image_alt=cover_image_alt,
            linking_data=linking_data,
            total_input_tokens=context.get_total_input_tokens(),
            total_output_tokens=context.get_total_output_tokens(),
            stage_tokens=stage_tokens,
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
                    "total_input_tokens": context.get_total_input_tokens(),
                    "total_output_tokens": context.get_total_output_tokens(),
                    "stages": [
                        {
                            "name": log.stage_name,
                            "status": log.status,
                            "tokens": log.tokens_used,
                            "input_tokens": log.input_tokens,
                            "output_tokens": log.output_tokens,
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
