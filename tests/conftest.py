"""Shared fixtures for the test suite."""

import sys
import os
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.services.writing_pipeline.contracts import (
    IntentResult,
    AudienceInfo,
    ToneInfo,
    WordCountRange,
    TopicBoundaries,
    ResearchResult,
    Source,
    Definition,
    Fact,
    Number,
    Example,
    EdgeCase,
    Pitfall,
    Contradiction,
    CoverageItem,
    OutlineResult,
    Introduction,
    Conclusion,
    Section,
    ContentBlock,
    SourceRefs,
    CoverageCheck,
    MetaResult,
    FormattingResult,
    QualityGateResult,
)
from src.services.writing_pipeline.core.context import WritingContext


# ---------------------------------------------------------------------------
# Factory functions — produce valid contract objects with sensible defaults
# ---------------------------------------------------------------------------

def make_intent_result(**overrides) -> IntentResult:
    defaults = dict(
        topic="SEO оптимизация",
        region="ru",
        primary_intent="informational",
        user_goal="Узнать основы SEO",
        article_goal="Объяснить основы SEO для начинающих",
        topic_boundaries=TopicBoundaries(
            in_scope=["on-page SEO", "meta теги"],
            out_of_scope=["PPC", "социальные сети"],
        ),
        content_type="guide",
        audience=AudienceInfo(role="маркетолог", knowledge_level="beginner"),
        tone=ToneInfo(formality="neutral", style="educational"),
        depth="standard",
        word_count_range=WordCountRange(min=1500, max=3000),
        must_answer_questions=["Что такое SEO?", "Как начать?"],
        must_not_include=["чёрное SEO"],
        success_criteria=["Понятно для новичка"],
    )
    defaults.update(overrides)
    return IntentResult(**defaults)


def make_research_result(**overrides) -> ResearchResult:
    defaults = dict(
        topic="SEO оптимизация",
        region="ru",
        generated_at="2026-01-01T00:00:00",
        queries_used=["seo оптимизация"],
        sources=[
            Source(
                id="s1", title="Google Guide", publisher="Google",
                url="https://google.com", published_date="2025-01-01",
                source_type="official", relevance_notes="Primary source",
            )
        ],
        definitions=[
            Definition(id="d1", term="SEO", definition="Search Engine Optimization",
                       source_id="s1", confidence="high")
        ],
        facts=[
            Fact(id="f1", category="definition", claim="SEO важен",
                 evidence="Google says so", source_id="s1", confidence="high")
        ],
        numbers=[
            Number(id="n1", metric="organic traffic", value="53%",
                   context="доля трафика", source_id="s1",
                   published_date="2025-01-01", confidence="high")
        ],
        examples=[
            Example(id="ex1", example="Кейс компании X",
                    why_it_matters="Рост трафика на 200%",
                    source_id="s1", confidence="high")
        ],
        edge_cases=[
            EdgeCase(id="ec1", case="Новый домен",
                     impact="Нужно время на индексацию",
                     source_id="s1", confidence="medium")
        ],
        pitfalls_and_myths=[
            Pitfall(id="p1", item="Keyword stuffing",
                    why_wrong_or_risky="Штраф от Google",
                    source_id="s1", confidence="high")
        ],
        contradictions=[],
        coverage_map=[
            CoverageItem(
                must_answer_question="Что такое SEO?",
                supporting_fact_ids=["f1"],
                supporting_number_ids=["n1"],
                supporting_example_ids=["ex1"],
                coverage_confidence="high",
                missing_notes=None,
            )
        ],
    )
    defaults.update(overrides)
    return ResearchResult(**defaults)


def make_outline_result(**overrides) -> OutlineResult:
    defaults = dict(
        title="SEO Оптимизация: Полное Руководство",
        subtitle="Всё, что нужно знать о продвижении",
        target_total_words=2000,
        introduction=Introduction(
            purpose="Вступление",
            key_points=["SEO важен"],
            word_count_target=200,
        ),
        sections=[
            Section(
                id="s1", h2="Основы SEO", purpose="Введение в тему",
                word_count_target=500,
                must_answer_questions=["Что такое SEO?"],
                content_blocks=[
                    ContentBlock(type="explanation", goal="Объяснить основы",
                                 source_refs=SourceRefs(fact_ids=["f1"]))
                ],
            ),
            Section(
                id="s2", h2="Технический SEO", purpose="Техническая часть",
                word_count_target=500,
                must_answer_questions=[],
                content_blocks=[],
            ),
        ],
        conclusion=Conclusion(
            purpose="Итоги",
            takeaways=["SEO — это процесс"],
            word_count_target=200,
        ),
        coverage_check=CoverageCheck(
            all_must_answer_covered=True,
            uncovered_questions=[],
            missing_notes=None,
        ),
    )
    defaults.update(overrides)
    return OutlineResult(**defaults)


def make_writing_context(**overrides) -> WritingContext:
    defaults = dict(
        topic="SEO оптимизация",
        region="ru",
        started_at=datetime.now(),
        config={},
    )
    defaults.update(overrides)
    return WritingContext(**defaults)


# ---------------------------------------------------------------------------
# Mock LLM helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_anthropic_client():
    """MagicMock simulating anthropic.Anthropic with messages.stream()."""
    client = MagicMock()

    # Simulate the streaming context manager used in WritingStage._call_llm
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=stream_cm)
    stream_cm.__exit__ = MagicMock(return_value=False)
    stream_cm.text_stream = iter(["mock response"])

    final_msg = MagicMock()
    final_msg.usage.input_tokens = 100
    final_msg.usage.output_tokens = 50
    stream_cm.get_final_message.return_value = final_msg

    client.messages.stream.return_value = stream_cm

    return client


def configure_mock_llm_response(client: MagicMock, text: str):
    """Configure mock client to return specific text from _call_llm."""
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=stream_cm)
    stream_cm.__exit__ = MagicMock(return_value=False)
    stream_cm.text_stream = iter([text])

    final_msg = MagicMock()
    final_msg.usage.input_tokens = 100
    final_msg.usage.output_tokens = 50
    stream_cm.get_final_message.return_value = final_msg

    client.messages.stream.return_value = stream_cm
