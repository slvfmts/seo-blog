"""Deep resilience tests for ResearchResult.from_dict parsing."""

import pytest
from src.services.writing_pipeline.contracts import ResearchResult


class TestResearchParsingResilience:
    """Ensures ResearchResult.from_dict survives messy LLM output."""

    def _base_data(self):
        return {
            "topic": "Test",
            "region": "ru",
            "generated_at": "2026-01-01T00:00:00",
        }

    def test_completely_empty_lists(self):
        data = self._base_data()
        result = ResearchResult.from_dict(data)
        assert result.sources == []
        assert result.definitions == []
        assert result.facts == []
        assert result.numbers == []
        assert result.examples == []
        assert result.edge_cases == []
        assert result.pitfalls_and_myths == []
        assert result.contradictions == []
        assert result.coverage_map == []

    def test_full_v2_data(self):
        data = self._base_data()
        data.update({
            "sources": [{
                "id": "s1", "title": "T", "publisher": "P", "url": "http://x",
                "source_type": "official",
            }],
            "claim_bank": {
                "allowed_claims": [{
                    "claim_text": "Claim",
                    "claim_type": "definition",
                    "evidence": {
                        "source_title": "S",
                        "source_url": "http://s",
                        "supporting_quote_or_note": "Quote",
                    },
                    "allowed_numeric": True,
                    "allowed_ranges": "5-10%",
                    "use_rules": "Always cite",
                }],
                "disallowed_claim_patterns": ["гарантированно"],
            },
            "unique_angle": {
                "article_role": "pillar",
                "primary_intent": "learn basics",
                "differentiators": ["unique approach"],
                "must_not_cover": ["advanced topics"],
            },
            "cluster_overlap_map": [{
                "post_slug": "existing-post",
                "overlap_topics": ["basics"],
                "avoid_sections": ["Intro"],
                "suggest_links": ["see also"],
            }],
            "example_snippets": [{
                "scenario": "SaaS case",
                "snippet": "Company X grew 200%",
                "where_to_use": "metrics",
                "source_basis": "https://example.com",
            }],
            "terminology_canon": {
                "terms": {"SEO": "Search Engine Optimization"},
                "do_not_use": ["оптимизация сайтов"],
            },
        })
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is not None
        assert len(result.claim_bank.allowed_claims) == 1
        assert result.claim_bank.allowed_claims[0].allowed_ranges == "5-10%"
        assert result.unique_angle.article_role == "pillar"
        assert len(result.cluster_overlap_map) == 1
        assert len(result.example_snippets) == 1
        assert result.terminology_canon.terms["SEO"] == "Search Engine Optimization"

    def test_keyword_clusters_round_trip(self):
        data = self._base_data()
        data["keyword_clusters"] = {
            "primary_cluster": {
                "cluster_name": "Main",
                "cluster_intent": "informational",
                "primary_keyword": "seo",
                "keywords": ["seo", "seo guide"],
                "total_volume": 1000,
                "suggested_section_topic": "SEO basics",
            },
            "secondary_clusters": [],
            "unclustered": ["random kw"],
        }
        result = ResearchResult.from_dict(data)
        assert result.keyword_clusters is not None
        assert result.keyword_clusters.primary_cluster.cluster_name == "Main"
        # Round-trip
        exported = result.to_dict()
        assert exported["keyword_clusters"]["primary_cluster"]["cluster_name"] == "Main"

    def test_source_missing_optional_fields(self):
        data = self._base_data()
        data["sources"] = [{
            "id": "s1",
            "title": "T",
            "publisher": "P",
            "url": "http://x",
            "source_type": "official",
            # No published_date, no relevance_notes
        }]
        result = ResearchResult.from_dict(data)
        assert result.sources[0].published_date is None
        assert result.sources[0].relevance_notes == ""

    def test_coverage_map_missing_optional_ids(self):
        data = self._base_data()
        data["coverage_map"] = [{
            "must_answer_question": "What?",
            "coverage_confidence": "high",
        }]
        result = ResearchResult.from_dict(data)
        cm = result.coverage_map[0]
        assert cm.supporting_fact_ids == []
        assert cm.supporting_number_ids == []
        assert cm.supporting_example_ids == []

    def test_unique_angle_minimal(self):
        """Empty dict {} is falsy → from_dict treats it as None."""
        data = self._base_data()
        data["unique_angle"] = {}
        result = ResearchResult.from_dict(data)
        # Empty dict is falsy in Python, so unique_angle is None
        assert result.unique_angle is None

    def test_unique_angle_with_defaults(self):
        """Non-empty dict with minimal data uses defaults."""
        data = self._base_data()
        data["unique_angle"] = {"primary_intent": "learn"}
        result = ResearchResult.from_dict(data)
        assert result.unique_angle.article_role == "cluster"
        assert result.unique_angle.primary_intent == "learn"
        assert result.unique_angle.differentiators == []

    def test_claim_bank_minimal(self):
        """Empty dict {} is falsy → from_dict treats it as None."""
        data = self._base_data()
        data["claim_bank"] = {}
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is None

    def test_claim_bank_with_empty_lists(self):
        """Non-empty dict with empty claims list."""
        data = self._base_data()
        data["claim_bank"] = {"allowed_claims": [], "disallowed_claim_patterns": []}
        result = ResearchResult.from_dict(data)
        assert result.claim_bank is not None
        assert result.claim_bank.allowed_claims == []
        assert result.claim_bank.disallowed_claim_patterns == []

    def test_example_snippets_missing_optional(self):
        data = self._base_data()
        data["example_snippets"] = [{"scenario": "X", "snippet": "Y"}]
        result = ResearchResult.from_dict(data)
        assert result.example_snippets[0].where_to_use == "process"
        assert result.example_snippets[0].source_basis == ""
