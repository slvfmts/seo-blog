"""
E2E tests for the cluster planning and generation flow.

All external APIs are mocked:
- Anthropic (LLM) — mocked, no real API calls
- Serper (search, autocomplete, scrape) — mocked
- Yandex Wordstat (volumes) — mocked
- DataForSEO — mocked
- Ghost CMS — mocked

Tests verify that the code paths work correctly without spending money.

Run: pytest tests/test_e2e_clusters.py -v
"""

import json
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock
from uuid import uuid4, UUID
from datetime import datetime

from src.services.writing_pipeline.contracts import ArticleBrief, ClusterPlan
from src.services.writing_pipeline.data_sources.volume_provider import (
    VolumeProvider, VolumeResult, NullVolumeProvider, get_volume_provider,
)


# =============================================================================
# Fixtures
# =============================================================================

class MockVolumeProvider(VolumeProvider):
    """Volume provider that returns predictable fake volumes."""

    def __init__(self):
        self.calls = []

    async def get_volumes(self, keywords: list[str], language_code: str = "ru") -> list[VolumeResult]:
        self.calls.append({"keywords": keywords, "language_code": language_code})
        return [
            VolumeResult(
                keyword=kw,
                volume=1000 + i * 100,
                source="mock_wordstat",
                cpc=0.5 + i * 0.1,
                competition=0.3,
            )
            for i, kw in enumerate(keywords)
        ]

    @property
    def source_name(self) -> str:
        return "mock_wordstat"


def make_mock_anthropic_client(response_text: str):
    """Create a mock Anthropic client that returns given text from messages.create()."""
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text=response_text)]
    client.messages.create.return_value = response
    return client


def make_serper_search_response(query: str) -> dict:
    """Fake Serper /search response."""
    return {
        "organic": [
            {"title": f"Топ статья про {query}", "link": f"https://example.com/{i}", "snippet": f"Описание {i}"}
            for i in range(5)
        ],
        "relatedSearches": [
            {"query": f"{query} для начинающих"},
            {"query": f"лучший {query}"},
            {"query": f"как начать {query}"},
        ],
        "peopleAlsoAsk": [
            {"question": f"Что такое {query}?"},
            {"question": f"Как работает {query}?"},
        ],
    }


def make_serper_autocomplete_response(query: str) -> dict:
    """Fake Serper /autocomplete response."""
    return {
        "suggestions": [
            {"value": f"{query} 2026"},
            {"value": f"{query} примеры"},
        ],
    }


def make_serper_scrape_response(url: str) -> dict:
    """Fake Serper /scrape response."""
    return {
        "text": "# Заголовок статьи\n\nВведение\n\n## Основы темы\nТекст про основы\n\n## Продвинутые техники\nТекст про техники\n\n## Инструменты\nСписок инструментов",
    }


LLM_CLUSTER_RESPONSE = json.dumps({
    "pillar": {
        "title_candidate": "Фриланс: полное руководство для начинающих",
        "role": "pillar",
        "primary_intent": "informational",
        "topic_boundaries": {"in_scope": ["фриланс", "удалённая работа"], "out_of_scope": ["офисная работа"]},
        "must_answer_questions": ["Что такое фриланс?", "Как начать?"],
        "target_terms": ["фриланс", "удалённая работа", "фриланс для начинающих"],
        "unique_angle": {"differentiators": ["практические советы"], "must_not_cover": []},
        "internal_links_plan": [{"target_slug": "freelance-tools", "anchor_hint": "инструменты"}],
        "seed_queries": ["фриланс руководство"],
        "estimated_volume": 5000,
        "priority": 1,
    },
    "cluster_articles": [
        {
            "title_candidate": "Лучшие инструменты для фрилансера",
            "role": "cluster",
            "primary_intent": "commercial",
            "topic_boundaries": {"in_scope": ["инструменты"], "out_of_scope": ["основы"]},
            "must_answer_questions": ["Какие инструменты нужны?"],
            "target_terms": ["инструменты фрилансера", "сервисы для фриланса"],
            "unique_angle": {"differentiators": ["сравнение"], "must_not_cover": []},
            "internal_links_plan": [{"target_slug": "freelance-guide", "anchor_hint": "руководство"}],
            "seed_queries": ["инструменты фриланс"],
            "estimated_volume": 2000,
            "priority": 2,
        },
        {
            "title_candidate": "Как найти первых клиентов на фрилансе",
            "role": "cluster",
            "primary_intent": "informational",
            "topic_boundaries": {"in_scope": ["поиск клиентов"], "out_of_scope": ["ценообразование"]},
            "must_answer_questions": ["Где искать клиентов?"],
            "target_terms": ["клиенты фриланс", "как найти заказы"],
            "unique_angle": {"differentiators": ["конкретные площадки"], "must_not_cover": []},
            "internal_links_plan": [],
            "seed_queries": ["клиенты фриланс"],
            "estimated_volume": 3000,
            "priority": 2,
        },
    ],
})


# =============================================================================
# ClusterPlanner unit tests (updated for new search-first API)
# =============================================================================

class TestClusterPlannerSearchFirst:
    """Test the refactored search-first ClusterPlanner."""

    def _make_planner(self, llm_response: str = LLM_CLUSTER_RESPONSE, volume_provider=None):
        from src.services.cluster_planner import ClusterPlanner
        client = make_mock_anthropic_client(llm_response)
        return ClusterPlanner(
            anthropic_client=client,
            serper_api_key="fake-serper-key",
            volume_provider=volume_provider or MockVolumeProvider(),
        )

    @pytest.mark.asyncio
    async def test_discover_keywords_calls_serper(self):
        """Step 1: _discover_keywords makes search + autocomplete calls."""
        planner = self._make_planner()

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()

            def mock_json():
                return make_serper_search_response("фриланс")
            mock_resp.json = mock_json

            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_resp)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            result = await planner._discover_keywords("фриланс", "ru")

            assert "фриланс" in result["keywords"]
            assert len(result["keywords"]) > 1  # should have related searches + autocomplete
            assert len(result["top_urls"]) > 0
            assert mock_client_instance.post.call_count > 0

    @pytest.mark.asyncio
    async def test_discover_keywords_no_serper_key(self):
        """Without Serper key, returns just the topic."""
        planner = self._make_planner()
        planner.serper_api_key = ""
        result = await planner._discover_keywords("фриланс", "ru")
        assert "фриланс" in result["keywords"]
        assert len(result["paa_questions"]) == 0
        assert len(result["top_urls"]) == 0

    @pytest.mark.asyncio
    async def test_analyze_competitors_scrapes_pages(self):
        """Step 2: _analyze_competitors scrapes top URLs."""
        planner = self._make_planner()
        top_urls = [
            {"url": "https://example.com/1", "title": "Page 1"},
            {"url": "https://example.com/2", "title": "Page 2"},
        ]

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = make_serper_scrape_response("test")

            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(return_value=mock_resp)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client_instance

            result = await planner._analyze_competitors(top_urls, "ru")
            assert len(result) == 2
            assert "headings" in result[0]
            assert any("Основы темы" in h for h in result[0]["headings"])

    @pytest.mark.asyncio
    async def test_analyze_competitors_empty_urls(self):
        """No URLs → empty result."""
        planner = self._make_planner()
        result = await planner._analyze_competitors([], "ru")
        assert result == []

    @pytest.mark.asyncio
    async def test_enrich_volumes_uses_provider(self):
        """Step 3: _enrich_volumes uses the VolumeProvider."""
        provider = MockVolumeProvider()
        planner = self._make_planner(volume_provider=provider)

        result = await planner._enrich_volumes(["фриланс", "удалённая работа"], "ru")
        assert len(result) == 2
        assert result[0]["volume"] == 1000
        assert result[1]["volume"] == 1100
        assert len(provider.calls) == 1  # one batch
        assert provider.calls[0]["keywords"] == ["фриланс", "удалённая работа"]

    @pytest.mark.asyncio
    async def test_enrich_volumes_batches(self):
        """Large keyword list is batched (10 per batch)."""
        provider = MockVolumeProvider()
        planner = self._make_planner(volume_provider=provider)

        keywords = [f"keyword_{i}" for i in range(25)]
        result = await planner._enrich_volumes(keywords, "ru")
        assert len(result) == 25
        assert len(provider.calls) == 3  # 10 + 10 + 5

    @pytest.mark.asyncio
    async def test_enrich_volumes_no_provider(self):
        """No provider → zero volumes."""
        planner = self._make_planner(volume_provider=None)
        planner.volume_provider = None
        result = await planner._enrich_volumes(["kw1"], "ru")
        assert result[0]["volume"] == 0

    @pytest.mark.asyncio
    async def test_cluster_and_brief_parses_llm_response(self):
        """Step 4: _cluster_and_brief parses LLM JSON into ClusterPlan."""
        planner = self._make_planner()
        plan = await planner._cluster_and_brief(
            "фриланс", "ru",
            [{"keyword": "фриланс", "volume": 5000, "cpc": 0.5, "competition": 0.3}],
            [{"url": "https://example.com", "title": "Test", "headings": ["H2 heading"]}],
            ["Что такое фриланс?"],
            target_count=5,
        )
        assert plan.pillar.title_candidate == "Фриланс: полное руководство для начинающих"
        assert plan.pillar.role == "pillar"
        assert len(plan.cluster_articles) == 2
        assert plan.cluster_articles[0].role == "cluster"

    @pytest.mark.asyncio
    async def test_cluster_and_brief_handles_code_block(self):
        """LLM wraps JSON in ```json ... ``` — should still parse."""
        wrapped = f"```json\n{LLM_CLUSTER_RESPONSE}\n```"
        planner = self._make_planner(llm_response=wrapped)
        plan = await planner._cluster_and_brief(
            "фриланс", "ru", [], [], [], target_count=5,
        )
        assert plan.pillar.title_candidate == "Фриланс: полное руководство для начинающих"

    @pytest.mark.asyncio
    async def test_cluster_and_brief_error_returns_fallback(self):
        """Invalid LLM response → fallback plan with topic as pillar."""
        planner = self._make_planner(llm_response="not json {{{")
        plan = await planner._cluster_and_brief(
            "fallback topic", "ru", [], [], [], target_count=5,
        )
        assert plan.pillar.title_candidate == "fallback topic"
        assert len(plan.cluster_articles) == 0


# =============================================================================
# VolumeProvider tests
# =============================================================================

class TestVolumeProviderFactory:
    """Test get_volume_provider() picks the right provider."""

    def test_yandex_wordstat_for_ru(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "fake-yandex-key"
        settings.yandex_cloud_folder_id = "fake-folder"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "yandex_wordstat"

    def test_rush_fallback_for_ru(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = "fake-rush-key"
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "rush_analytics"

    def test_dataforseo_for_en(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = "key"
        settings.yandex_cloud_folder_id = "folder"
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = "login"
        settings.dataforseo_password = "pass"

        provider = get_volume_provider("en", settings)
        assert provider.source_name == "dataforseo"

    def test_null_provider_no_keys(self):
        settings = MagicMock()
        settings.yandex_wordstat_api_key = ""
        settings.yandex_cloud_folder_id = ""
        settings.rush_analytics_api_key = ""
        settings.dataforseo_login = ""
        settings.dataforseo_password = ""

        provider = get_volume_provider("ru", settings)
        assert provider.source_name == "none"

    @pytest.mark.asyncio
    async def test_null_provider_returns_zeros(self):
        provider = NullVolumeProvider()
        results = await provider.get_volumes(["test1", "test2"])
        assert len(results) == 2
        assert results[0].volume == 0
        assert results[0].source == "none"


# =============================================================================
# ClusterPlanner.save_to_db (flat model)
# =============================================================================

class TestClusterPlannerSaveToDB:
    """Test that save_to_db creates ONE cluster with N briefs (flat model)."""

    def _make_plan(self):
        return ClusterPlan(
            big_topic="фриланс",
            region="ru",
            pillar=ArticleBrief.from_dict({
                "title_candidate": "Pillar: Фриланс",
                "role": "pillar",
                "target_terms": ["фриланс", "удалённая работа"],
                "estimated_volume": 5000,
            }),
            cluster_articles=[
                ArticleBrief.from_dict({
                    "title_candidate": f"Cluster Article {i}",
                    "role": "cluster",
                    "target_terms": [f"keyword_{i}"],
                    "estimated_volume": 1000 + i * 100,
                })
                for i in range(3)
            ],
            generated_at="2026-01-01T00:00:00",
        )

    @pytest.mark.asyncio
    async def test_creates_one_cluster(self):
        """save_to_db creates exactly 1 cluster."""
        from src.services.cluster_planner import ClusterPlanner
        planner = ClusterPlanner(
            anthropic_client=MagicMock(),
            volume_provider=None,
        )
        plan = self._make_plan()
        db = MagicMock()
        added = []
        db.add = lambda obj: added.append(obj)
        db.commit = MagicMock()

        await planner.save_to_db(plan, site_id=str(uuid4()), db_session=db)

        from src.db.models import Cluster, Brief, Keyword
        clusters = [o for o in added if isinstance(o, Cluster)]
        briefs = [o for o in added if isinstance(o, Brief)]
        keywords = [o for o in added if isinstance(o, Keyword)]

        assert len(clusters) == 1, f"Expected 1 cluster, got {len(clusters)}"
        assert len(briefs) == 4, f"Expected 4 briefs (1 pillar + 3 cluster), got {len(briefs)}"

        # All briefs belong to the same cluster
        cluster_id = clusters[0].id
        for brief in briefs:
            assert brief.cluster_id == cluster_id

        # Pillar brief has role=pillar
        pillar_briefs = [b for b in briefs if (b.structure or {}).get("role") == "pillar"]
        assert len(pillar_briefs) == 1

        # Keywords created
        assert len(keywords) > 0
        for kw in keywords:
            assert kw.cluster_id == cluster_id

    @pytest.mark.asyncio
    async def test_no_site_id_skips_keywords(self):
        """save_to_db with site_id=None skips keyword creation."""
        from src.services.cluster_planner import ClusterPlanner
        planner = ClusterPlanner(anthropic_client=MagicMock(), volume_provider=None)
        plan = self._make_plan()
        db = MagicMock()
        added = []
        db.add = lambda obj: added.append(obj)
        db.commit = MagicMock()

        await planner.save_to_db(plan, site_id=None, db_session=db)

        from src.db.models import Keyword
        keywords = [o for o in added if isinstance(o, Keyword)]
        assert len(keywords) == 0


# =============================================================================
# Heading extraction
# =============================================================================

class TestHeadingExtraction:
    """Test _extract_headings_from_text helper."""

    def test_extracts_markdown_headings(self):
        from src.services.cluster_planner import _extract_headings_from_text
        text = "# Title\n\nIntro\n\n## First Section\nContent\n\n## Second Section\nMore content\n\n### Subsection\nDetails"
        headings = _extract_headings_from_text(text)
        assert "Title" in headings
        assert "First Section" in headings
        assert "Second Section" in headings

    def test_extracts_colon_headings(self):
        from src.services.cluster_planner import _extract_headings_from_text
        text = "Introduction:\nSome text\n\nMain Topic:\nMore text"
        headings = _extract_headings_from_text(text)
        assert "Introduction" in headings
        assert "Main Topic" in headings

    def test_empty_text(self):
        from src.services.cluster_planner import _extract_headings_from_text
        assert _extract_headings_from_text("") == []
        assert _extract_headings_from_text(None) == []

    def test_deduplicates(self):
        from src.services.cluster_planner import _extract_headings_from_text
        text = "## Heading\nText\n## Heading\nMore text"
        headings = _extract_headings_from_text(text)
        assert headings.count("Heading") == 1


# =============================================================================
# Sequential generation guard
# =============================================================================

class TestSequentialGenerationGuard:
    """Test that _run_cluster_pipeline_sequential checks cluster cancellation."""

    def test_skips_cancelled_brief(self):
        """Cancelled briefs are skipped in the queue."""
        from src.api.routes.ui import _run_cluster_pipeline_sequential
        from unittest.mock import patch as mock_patch

        mock_brief = MagicMock()
        mock_brief.status = "cancelled"

        mock_cluster = MagicMock()
        mock_cluster.status = "in_progress"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            mock_cluster,  # cluster query
            mock_brief,    # brief query
        ]

        with mock_patch("src.api.routes.ui.SessionLocal", return_value=mock_db):
            with mock_patch("src.api.routes.ui._run_pipeline_for_brief") as mock_run:
                _run_cluster_pipeline_sequential(
                    brief_queue=[("brief-1", "Test Topic", {})],
                    site_id="site-1",
                    region="ru",
                    settings=MagicMock(),
                    knowledge_base_docs=[],
                    cluster_id="cluster-1",
                    step_by_step=False,
                    factual_mode="default",
                )
                mock_run.assert_not_called()

    def test_stops_on_cancelled_cluster(self):
        """If cluster is cancelled, entire queue stops."""
        from src.api.routes.ui import _run_cluster_pipeline_sequential
        from unittest.mock import patch as mock_patch

        mock_cluster = MagicMock()
        mock_cluster.status = "cancelled"

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

        with mock_patch("src.api.routes.ui.SessionLocal", return_value=mock_db):
            with mock_patch("src.api.routes.ui._run_pipeline_for_brief") as mock_run:
                _run_cluster_pipeline_sequential(
                    brief_queue=[
                        ("brief-1", "Topic 1", {}),
                        ("brief-2", "Topic 2", {}),
                    ],
                    site_id="site-1",
                    region="ru",
                    settings=MagicMock(),
                    knowledge_base_docs=[],
                    cluster_id="cluster-1",
                    step_by_step=False,
                    factual_mode="default",
                )
                mock_run.assert_not_called()


# =============================================================================
# Full plan() flow (all steps mocked)
# =============================================================================

class TestClusterPlannerFullFlow:
    """Test the full plan() method with all external calls mocked."""

    @pytest.mark.asyncio
    async def test_plan_full_flow(self):
        """Full plan() flow: Serper search → scrape → volumes → LLM clustering."""
        from src.services.cluster_planner import ClusterPlanner

        provider = MockVolumeProvider()
        client = make_mock_anthropic_client(LLM_CLUSTER_RESPONSE)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key="fake-key",
            volume_provider=provider,
        )

        call_count = {"search": 0, "autocomplete": 0, "scrape": 0}

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()

            if "google.serper.dev/search" in url:
                call_count["search"] += 1
                q = kwargs.get("json", {}).get("q", "test")
                resp.json.return_value = make_serper_search_response(q)
            elif "google.serper.dev/autocomplete" in url:
                call_count["autocomplete"] += 1
                q = kwargs.get("json", {}).get("q", "test")
                resp.json.return_value = make_serper_autocomplete_response(q)
            elif "scrape.serper.dev" in url:
                call_count["scrape"] += 1
                resp.json.return_value = make_serper_scrape_response("test")
            else:
                resp.json.return_value = {}
            return resp

        with patch("httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post = mock_post
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            plan = await planner.plan("фриланс", region="ru", target_count=5)

        # Verify all steps ran
        assert call_count["search"] > 0, "Serper search should be called"
        assert call_count["autocomplete"] > 0, "Serper autocomplete should be called"
        assert call_count["scrape"] > 0, "Serper scrape should be called"
        assert len(provider.calls) > 0, "Volume provider should be called"

        # Verify plan structure
        assert plan.big_topic == "фриланс"
        assert plan.pillar.role == "pillar"
        assert len(plan.cluster_articles) == 2
        assert plan.generated_at  # non-empty

        # Verify LLM was called (for clustering step)
        assert client.messages.create.called

    @pytest.mark.asyncio
    async def test_plan_graceful_degradation_no_serper(self):
        """Without Serper key, plan() still works (just fewer keywords)."""
        from src.services.cluster_planner import ClusterPlanner

        client = make_mock_anthropic_client(LLM_CLUSTER_RESPONSE)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key="",  # No Serper
            volume_provider=MockVolumeProvider(),
        )

        plan = await planner.plan("фриланс", region="ru", target_count=5)
        assert plan.pillar.title_candidate == "Фриланс: полное руководство для начинающих"

    @pytest.mark.asyncio
    async def test_plan_graceful_degradation_no_volumes(self):
        """Without volume provider, plan() still works (zero volumes)."""
        from src.services.cluster_planner import ClusterPlanner

        client = make_mock_anthropic_client(LLM_CLUSTER_RESPONSE)
        planner = ClusterPlanner(
            anthropic_client=client,
            serper_api_key="",
            volume_provider=None,  # No volume provider
        )

        plan = await planner.plan("фриланс", region="ru", target_count=5)
        assert plan.pillar is not None
