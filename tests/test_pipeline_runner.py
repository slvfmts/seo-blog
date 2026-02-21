"""Tests for pipeline runner — full pipeline with all LLM calls mocked."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.services.writing_pipeline.core.runner import PipelineRunner
from src.services.writing_pipeline.core.context import WritingContext


def _mock_stage(name: str, side_effect=None):
    """Create a mock stage with given name and optional side_effect on run()."""
    stage = MagicMock()
    stage.name = name

    async def default_run(ctx):
        return ctx

    if side_effect:
        stage.run = AsyncMock(side_effect=side_effect)
    else:
        stage.run = AsyncMock(side_effect=default_run)
    return stage


class TestPipelineRunnerInit:
    @patch("src.services.writing_pipeline.core.runner.anthropic")
    def test_creates_all_10_stages(self, mock_anthropic):
        mock_anthropic.Anthropic.return_value = MagicMock()
        runner = PipelineRunner(anthropic_api_key="test-key")
        assert len(runner.stages) == 10
        names = runner.get_stage_names()
        assert names == [
            "intent", "research", "structure", "drafting", "editing",
            "linking", "seo_polish", "quality_gate", "meta", "formatting",
        ]

    @patch("src.services.writing_pipeline.core.runner.anthropic")
    def test_get_stage_names(self, mock_anthropic):
        mock_anthropic.Anthropic.return_value = MagicMock()
        runner = PipelineRunner(anthropic_api_key="test-key")
        names = runner.get_stage_names()
        assert len(names) == 10
        assert "intent" in names
        assert "formatting" in names


class TestPipelineRunnerExecution:
    @pytest.mark.asyncio
    @patch("src.services.writing_pipeline.core.runner.anthropic")
    async def test_happy_path_all_stages_complete(self, mock_anthropic):
        """All 10 stages run and on_stage_complete callback fires."""
        mock_anthropic.Anthropic.return_value = MagicMock()
        runner = PipelineRunner(anthropic_api_key="test-key")

        # Replace real stages with mocks that set required context
        from conftest import make_intent_result, make_research_result, make_outline_result
        from src.services.writing_pipeline.contracts import MetaResult, FormattingResult

        completed_stages = []

        async def intent_run(ctx):
            ctx.start_stage("intent")
            ctx.intent = make_intent_result()
            ctx.complete_stage(tokens_used=100)
            return ctx

        async def research_run(ctx):
            ctx.start_stage("research")
            ctx.research = make_research_result()
            ctx.complete_stage(tokens_used=200)
            return ctx

        async def structure_run(ctx):
            ctx.start_stage("structure")
            ctx.outline = make_outline_result()
            ctx.complete_stage(tokens_used=150)
            return ctx

        async def drafting_run(ctx):
            ctx.start_stage("drafting")
            ctx.draft_md = "# Draft\n\nDraft content."
            ctx.complete_stage(tokens_used=500)
            return ctx

        async def editing_run(ctx):
            ctx.start_stage("editing")
            ctx.edited_md = "# Edited\n\n## Section 1\n\nEdited content."
            ctx.complete_stage(tokens_used=300)
            return ctx

        async def generic_run(name):
            async def run(ctx):
                ctx.start_stage(name)
                ctx.complete_stage(tokens_used=50)
                return ctx
            return run

        async def meta_run(ctx):
            ctx.start_stage("meta")
            ctx.meta = MetaResult(
                meta_title="Test Title",
                meta_description="Test description",
                slug="test-slug",
            )
            ctx.complete_stage(tokens_used=100)
            return ctx

        async def formatting_run(ctx):
            ctx.start_stage("formatting")
            ctx.formatting_result = FormattingResult()
            ctx.complete_stage(tokens_used=0)
            return ctx

        runner.stages[0].run = AsyncMock(side_effect=intent_run)
        runner.stages[1].run = AsyncMock(side_effect=research_run)
        runner.stages[2].run = AsyncMock(side_effect=structure_run)
        runner.stages[3].run = AsyncMock(side_effect=drafting_run)
        runner.stages[4].run = AsyncMock(side_effect=editing_run)
        runner.stages[5].run = AsyncMock(side_effect=await generic_run("linking"))
        runner.stages[6].run = AsyncMock(side_effect=await generic_run("seo_polish"))
        runner.stages[7].run = AsyncMock(side_effect=await generic_run("quality_gate"))
        runner.stages[8].run = AsyncMock(side_effect=meta_run)
        runner.stages[9].run = AsyncMock(side_effect=formatting_run)

        def on_stage(name, status):
            completed_stages.append((name, status))

        result = await runner.run(
            topic="Test",
            region="ru",
            save_intermediate=False,
            on_stage_complete=on_stage,
        )

        assert result.topic == "Test"
        assert result.title == "SEO Оптимизация: Полное Руководство"
        assert result.meta is not None
        assert result.meta.slug == "test-slug"
        assert len(result.stages_completed) == 10

        # Check callback received all stages
        running_stages = [s for s, st in completed_stages if st == "running"]
        completed = [s for s, st in completed_stages if st == "completed"]
        assert len(running_stages) == 10
        assert len(completed) == 10

    @pytest.mark.asyncio
    @patch("src.services.writing_pipeline.core.runner.anthropic")
    async def test_run_stage_unknown_raises(self, mock_anthropic):
        mock_anthropic.Anthropic.return_value = MagicMock()
        runner = PipelineRunner(anthropic_api_key="test-key")
        ctx = WritingContext(topic="Test", region="ru")
        with pytest.raises(ValueError, match="Unknown stage"):
            await runner.run_stage("nonexistent_stage", ctx)
