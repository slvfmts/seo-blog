"""Tests for ClusterPlanner and cluster contracts."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.services.writing_pipeline.contracts import ArticleBrief, ClusterPlan


# =============================================================================
# ArticleBrief contract
# =============================================================================

class TestArticleBrief:
    def test_from_dict_minimal(self):
        data = {"title_candidate": "Test Article"}
        brief = ArticleBrief.from_dict(data)
        assert brief.title_candidate == "Test Article"
        assert brief.role == "cluster"
        assert brief.primary_intent == "informational"
        assert brief.estimated_volume == 0
        assert brief.priority == 1

    def test_from_dict_full(self):
        data = {
            "title_candidate": "SEO Guide",
            "role": "pillar",
            "primary_intent": "informational",
            "topic_boundaries": {"in_scope": ["seo"], "out_of_scope": ["ppc"]},
            "must_answer_questions": ["What is SEO?", "How does it work?"],
            "target_terms": ["seo", "search optimization"],
            "unique_angle": {"differentiators": ["practical"], "must_not_cover": ["theory"]},
            "internal_links_plan": [{"target_slug": "seo-basics", "anchor_hint": "basics"}],
            "seed_queries": ["seo guide"],
            "estimated_volume": 5000,
            "priority": 1,
        }
        brief = ArticleBrief.from_dict(data)
        assert brief.role == "pillar"
        assert brief.estimated_volume == 5000
        assert len(brief.must_answer_questions) == 2
        assert brief.target_terms[0] == "seo"

    def test_to_dict_roundtrip(self):
        data = {
            "title_candidate": "Test",
            "role": "cluster",
            "primary_intent": "commercial",
            "topic_boundaries": {"in_scope": ["a"], "out_of_scope": ["b"]},
            "must_answer_questions": ["q1"],
            "target_terms": ["t1"],
            "unique_angle": {"differentiators": ["d1"], "must_not_cover": ["n1"]},
            "internal_links_plan": [],
            "seed_queries": ["s1"],
            "estimated_volume": 100,
            "priority": 3,
        }
        brief = ArticleBrief.from_dict(data)
        result = brief.to_dict()
        assert result["title_candidate"] == "Test"
        assert result["priority"] == 3

        # Roundtrip
        brief2 = ArticleBrief.from_dict(result)
        assert brief2.title_candidate == brief.title_candidate
        assert brief2.estimated_volume == brief.estimated_volume


# =============================================================================
# ClusterPlan contract
# =============================================================================

class TestClusterPlan:
    def _make_plan_dict(self):
        return {
            "big_topic": "контент-маркетинг",
            "region": "ru",
            "pillar": {
                "title_candidate": "Полный гайд по контент-маркетингу",
                "role": "pillar",
                "primary_intent": "informational",
                "topic_boundaries": {"in_scope": ["всё"], "out_of_scope": []},
                "must_answer_questions": ["Что такое контент-маркетинг?"],
                "target_terms": ["контент-маркетинг"],
                "unique_angle": {"differentiators": [], "must_not_cover": []},
                "internal_links_plan": [],
                "seed_queries": ["контент-маркетинг"],
                "estimated_volume": 10000,
                "priority": 1,
            },
            "cluster_articles": [
                {
                    "title_candidate": "Стратегия контент-маркетинга",
                    "role": "cluster",
                    "estimated_volume": 3000,
                    "priority": 2,
                },
                {
                    "title_candidate": "Инструменты контент-маркетинга",
                    "role": "cluster",
                    "estimated_volume": 2000,
                    "priority": 3,
                },
            ],
            "generated_at": "2025-01-01T00:00:00",
        }

    def test_from_dict(self):
        plan = ClusterPlan.from_dict(self._make_plan_dict())
        assert plan.big_topic == "контент-маркетинг"
        assert plan.pillar.role == "pillar"
        assert len(plan.cluster_articles) == 2

    def test_all_articles(self):
        plan = ClusterPlan.from_dict(self._make_plan_dict())
        assert len(plan.all_articles) == 3
        assert plan.all_articles[0].role == "pillar"

    def test_to_dict_roundtrip(self):
        plan = ClusterPlan.from_dict(self._make_plan_dict())
        d = plan.to_dict()
        plan2 = ClusterPlan.from_dict(d)
        assert plan2.big_topic == plan.big_topic
        assert len(plan2.cluster_articles) == len(plan.cluster_articles)

    def test_empty_cluster_articles(self):
        data = self._make_plan_dict()
        data["cluster_articles"] = []
        plan = ClusterPlan.from_dict(data)
        assert len(plan.cluster_articles) == 0
        assert len(plan.all_articles) == 1


# =============================================================================
# ClusterPlanner service (search-first API)
# =============================================================================

class TestClusterPlannerService:
    """Test ClusterPlanner methods with mocked LLM."""

    def _make_planner(self, llm_response_text: str):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=llm_response_text)]
        mock_client.messages.create.return_value = mock_response

        from src.services.cluster_planner import ClusterPlanner
        return ClusterPlanner(
            anthropic_client=mock_client,
            volume_provider=None,
            serper_api_key="",
        )

    @pytest.mark.asyncio
    async def test_enrich_volumes_no_provider(self):
        planner = self._make_planner("[]")
        planner.volume_provider = None
        result = await planner._enrich_volumes(["kw1", "kw2"], "ru")
        assert len(result) == 2
        assert result[0]["volume"] == 0

    @pytest.mark.asyncio
    async def test_cluster_and_brief_success(self):
        import json
        plan_response = json.dumps({
            "pillar": {
                "title_candidate": "Pillar Article",
                "role": "pillar",
                "primary_intent": "informational",
                "topic_boundaries": {"in_scope": ["all"], "out_of_scope": []},
                "must_answer_questions": ["Q1?"],
                "target_terms": ["main keyword"],
                "unique_angle": {"differentiators": [], "must_not_cover": []},
                "internal_links_plan": [],
                "seed_queries": ["main query"],
                "estimated_volume": 10000,
                "priority": 1,
            },
            "cluster_articles": [
                {
                    "title_candidate": "Cluster 1",
                    "role": "cluster",
                    "estimated_volume": 5000,
                    "priority": 2,
                }
            ],
        })
        planner = self._make_planner(plan_response)
        plan = await planner._cluster_and_brief(
            "big topic", "ru",
            [{"keyword": "kw1", "volume": 100, "cpc": 0.5, "competition": 0.3}],
            [], [], target_count=5,
        )
        assert plan.pillar.title_candidate == "Pillar Article"
        assert len(plan.cluster_articles) == 1

    @pytest.mark.asyncio
    async def test_cluster_and_brief_error_returns_minimal(self):
        planner = self._make_planner("invalid json {{{")
        plan = await planner._cluster_and_brief(
            "fallback topic", "ru", [], [], [], target_count=5,
        )
        assert plan.pillar.title_candidate == "fallback topic"
        assert len(plan.cluster_articles) == 0
