"""Tests for contract dataclass serialization round-trips and resilience."""

import pytest
from src.services.writing_pipeline.contracts import (
    IntentResult,
    ResearchResult,
    OutlineResult,
    MetaResult,
    FormattingResult,
    FormattingAsset,
    QualityGateResult,
    SeoPolishResult,
    SeoAnalysis,
    SeoCheckResult,
    PipelineResult,
    KeywordClusteringResult,
    KeywordCluster,
)

from conftest import make_intent_result, make_research_result, make_outline_result


# =============================================================================
# IntentResult
# =============================================================================

class TestIntentResult:
    def test_round_trip(self):
        original = make_intent_result()
        data = original.to_dict()
        restored = IntentResult.from_dict(data)
        assert restored.topic == original.topic
        assert restored.primary_intent == original.primary_intent
        assert restored.audience.role == original.audience.role
        assert restored.tone.style == original.tone.style
        assert restored.word_count_range.min == original.word_count_range.min
        assert restored.topic_boundaries.in_scope == original.topic_boundaries.in_scope

    def test_from_dict_missing_optional_fields(self):
        """must_not_include and success_criteria have defaults."""
        data = make_intent_result().to_dict()
        del data["must_not_include"]
        del data["success_criteria"]
        result = IntentResult.from_dict(data)
        assert result.must_not_include == []
        assert result.success_criteria == []

    def test_from_dict_missing_topic_boundaries(self):
        """topic_boundaries should default to empty lists."""
        data = make_intent_result().to_dict()
        del data["topic_boundaries"]
        result = IntentResult.from_dict(data)
        assert result.topic_boundaries.in_scope == []
        assert result.topic_boundaries.out_of_scope == []


# =============================================================================
# ResearchResult
# =============================================================================

class TestResearchResult:
    def test_round_trip(self):
        original = make_research_result()
        data = original.to_dict()
        restored = ResearchResult.from_dict(data)
        assert restored.topic == original.topic
        assert len(restored.sources) == len(original.sources)
        assert len(restored.facts) == len(original.facts)
        assert restored.examples[0].why_it_matters == original.examples[0].why_it_matters

    def test_from_dict_missing_why_it_matters(self):
        """Regression: commit 87e1634 — KeyError on missing why_it_matters."""
        data = make_research_result().to_dict()
        # Remove why_it_matters from examples
        for ex in data["examples"]:
            del ex["why_it_matters"]
        result = ResearchResult.from_dict(data)
        assert result.examples[0].why_it_matters == ""

    def test_from_dict_examples_minimal(self):
        """Regression: commit 8c7daa7 — KeyError on missing id/confidence in examples."""
        data = make_research_result().to_dict()
        # Minimal example — only "example" field (no id, confidence, source_id)
        data["examples"] = [{"example": "Minimal example"}]
        result = ResearchResult.from_dict(data)
        assert result.examples[0].example == "Minimal example"
        assert result.examples[0].id == "ex-0"
        assert result.examples[0].confidence == "medium"

    def test_from_dict_edge_cases_minimal(self):
        """Regression: commit 8c7daa7 — missing id/confidence in edge_cases."""
        data = make_research_result().to_dict()
        data["edge_cases"] = [{"case": "Edge", "impact": "High"}]
        result = ResearchResult.from_dict(data)
        assert result.edge_cases[0].id == "ec-0"
        assert result.edge_cases[0].confidence == "medium"

    def test_from_dict_pitfalls_minimal(self):
        data = make_research_result().to_dict()
        data["pitfalls_and_myths"] = [{"item": "Миф", "why_wrong_or_risky": "Потому что"}]
        result = ResearchResult.from_dict(data)
        assert result.pitfalls_and_myths[0].id == "p-0"

    def test_from_dict_missing_v2_fields(self):
        """v2 fields (claim_bank, unique_angle, etc.) should be None/empty."""
        data = make_research_result().to_dict()
        # Explicitly remove v2 fields
        for key in ["claim_bank", "unique_angle", "cluster_overlap_map",
                     "example_snippets", "terminology_canon"]:
            data.pop(key, None)
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is None
        assert result.unique_angle is None
        assert result.cluster_overlap_map == []
        assert result.example_snippets == []
        assert result.terminology_canon is None

    def test_from_dict_null_claim_bank(self):
        data = make_research_result().to_dict()
        data["claim_bank"] = None
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is None

    def test_from_dict_empty_terminology_canon(self):
        data = make_research_result().to_dict()
        data["terminology_canon"] = {"terms": {}, "do_not_use": []}
        result = ResearchResult.from_dict(data)
        assert result.terminology_canon is not None
        assert result.terminology_canon.terms == {}

    def test_from_dict_claim_bank_with_string_evidence(self):
        """Evidence can be a plain string instead of a dict."""
        data = make_research_result().to_dict()
        data["claim_bank"] = {
            "allowed_claims": [
                {
                    "claim_text": "Test claim",
                    "evidence": "Plain string evidence",
                }
            ],
            "disallowed_claim_patterns": [],
        }
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is not None
        assert result.claim_bank.allowed_claims[0].evidence.supporting_quote_or_note == "Plain string evidence"

    def test_from_dict_empty_lists(self):
        """All list fields default to empty when missing."""
        data = {
            "topic": "Test",
            "region": "ru",
            "generated_at": "2026-01-01T00:00:00",
        }
        result = ResearchResult.from_dict(data)
        assert result.sources == []
        assert result.facts == []
        assert result.examples == []
        assert result.coverage_map == []

    def test_examples_with_text_fallback(self):
        """example.text as fallback for example.example (legacy data)."""
        data = make_research_result().to_dict()
        data["examples"] = [{"text": "Legacy example text"}]
        result = ResearchResult.from_dict(data)
        assert result.examples[0].example == "Legacy example text"


# =============================================================================
# OutlineResult
# =============================================================================

class TestOutlineResult:
    def test_round_trip(self):
        original = make_outline_result()
        data = original.to_dict()
        restored = OutlineResult.from_dict(data)
        assert restored.title == original.title
        assert len(restored.sections) == len(original.sections)
        assert restored.conclusion.takeaways == original.conclusion.takeaways


# =============================================================================
# MetaResult
# =============================================================================

class TestMetaResult:
    def test_round_trip(self):
        original = MetaResult(
            meta_title="SEO Guide",
            meta_description="A complete guide to SEO optimization.",
            slug="seo-guide",
            schema_json_ld='{"@type":"Article"}',
            og_title="OG SEO Guide",
            og_description="OG description for social sharing.",
            custom_excerpt="Custom excerpt for Ghost CMS.",
        )
        data = original.to_dict()
        restored = MetaResult.from_dict(data)
        assert restored.meta_title == original.meta_title
        assert restored.slug == original.slug
        assert restored.schema_json_ld == original.schema_json_ld
        assert restored.og_title == original.og_title
        assert restored.og_description == original.og_description
        assert restored.custom_excerpt == original.custom_excerpt

    def test_from_dict_no_schema(self):
        data = {"meta_title": "T", "meta_description": "D", "slug": "t"}
        result = MetaResult.from_dict(data)
        assert result.schema_json_ld is None

    def test_from_dict_backward_compat(self):
        """Old MetaResult dicts without og/excerpt fields deserialize without errors."""
        data = {"meta_title": "T", "meta_description": "D", "slug": "s", "schema_json_ld": None}
        result = MetaResult.from_dict(data)
        assert result.og_title is None
        assert result.og_description is None
        assert result.custom_excerpt is None


# =============================================================================
# Meta Validation
# =============================================================================

class TestValidateMetaBeforePublish:
    """Tests for pre-publish meta validation."""

    def _make_draft(self, **kwargs):
        """Helper: create a simple namespace with draft-like attrs."""
        from types import SimpleNamespace
        defaults = {"meta_title": "A" * 45, "meta_description": "B" * 120, "slug": "valid-slug"}
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _validate(self, draft):
        from src.services.validators.meta import validate_meta_before_publish
        return validate_meta_before_publish(draft)

    def test_all_valid(self):
        assert self._validate(self._make_draft()) == []

    def test_missing_meta_title(self):
        w = self._validate(self._make_draft(meta_title=None))
        assert len(w) == 1 and "meta_title is missing" in w[0]

    def test_meta_title_too_short(self):
        w = self._validate(self._make_draft(meta_title="Short"))
        assert "outside 30-60" in w[0]

    def test_meta_title_too_long(self):
        w = self._validate(self._make_draft(meta_title="X" * 65))
        assert "outside 30-60" in w[0]

    def test_missing_meta_description(self):
        w = self._validate(self._make_draft(meta_description=None))
        assert "meta_description is missing" in w[0]

    def test_meta_description_too_short(self):
        w = self._validate(self._make_draft(meta_description="Short"))
        assert "outside 80-160" in w[0]

    def test_missing_slug(self):
        w = self._validate(self._make_draft(slug=None))
        assert "slug is missing" in w[0]

    def test_all_missing(self):
        w = self._validate(self._make_draft(meta_title=None, meta_description=None, slug=None))
        assert len(w) == 3


# =============================================================================
# FormattingResult
# =============================================================================

class TestFormattingResult:
    def test_round_trip(self):
        original = FormattingResult(
            assets=[
                FormattingAsset(type="cover", filename="cover.png",
                                path="/tmp/cover.png", alt="Cover",
                                ghost_url="http://ghost/cover.png")
            ],
            cover_generated=True,
            diagrams_count=0,
            errors=[],
            cover_ghost_url="http://ghost/cover.png",
            cover_image_alt="Cover",
        )
        data = original.to_dict()
        restored = FormattingResult.from_dict(data)
        assert restored.cover_generated is True
        assert len(restored.assets) == 1
        assert restored.assets[0].ghost_url == "http://ghost/cover.png"

    def test_from_dict_empty(self):
        result = FormattingResult.from_dict({})
        assert result.cover_generated is False
        assert result.assets == []


# =============================================================================
# QualityGateResult
# =============================================================================

class TestQualityGateResult:
    def test_round_trip(self):
        original = QualityGateResult(
            article_md="# Test", quality_report={"score": 85}
        )
        data = original.to_dict()
        restored = QualityGateResult.from_dict(data)
        assert restored.article_md == "# Test"
        assert restored.quality_report["score"] == 85


# =============================================================================
# SeoPolishResult
# =============================================================================

class TestSeoPolishResult:
    def test_round_trip(self):
        analysis = SeoAnalysis(
            checks=[
                SeoCheckResult(check="keyword_density", status="pass",
                               value=1.5, threshold=3.0, details="OK")
            ],
            needs_fix=False,
            keyword_density=1.5,
            keywords_found={"seo": 10},
        )
        original = SeoPolishResult(
            analysis_before=analysis,
            analysis_after=None,
            llm_called=False,
            changes_made=[],
            tokens_used=0,
        )
        data = original.to_dict()
        restored = SeoPolishResult.from_dict(data)
        assert restored.llm_called is False
        assert restored.analysis_after is None
        assert restored.analysis_before.keyword_density == 1.5


# =============================================================================
# PipelineResult
# =============================================================================

class TestPipelineResult:
    def test_to_dict_with_meta(self):
        result = PipelineResult(
            topic="SEO",
            region="ru",
            article_md="# SEO",
            title="SEO Guide",
            subtitle="Complete",
            word_count=1500,
            meta=MetaResult(meta_title="T", meta_description="D", slug="seo"),
        )
        data = result.to_dict()
        assert data["meta"]["slug"] == "seo"

    def test_to_dict_without_meta(self):
        result = PipelineResult(
            topic="SEO",
            region="ru",
            article_md="# SEO",
            title="SEO Guide",
            subtitle="Complete",
            word_count=1500,
        )
        data = result.to_dict()
        assert data["meta"] is None
        assert data["intent"] is None
