"""
ClusterPlanner — generates a cluster plan from a broad topic.

Steps:
1. Seed generation (LLM) — 30-50 subtopic candidates
2. Keyword expansion (Serper + provider suggestions)
3. Volume enrichment (VolumeProvider)
4. Clustering + brief generation (LLM)
5. Save to DB

Usage:
    planner = ClusterPlanner(
        anthropic_client=client,
        volume_provider=provider,
        serper_api_key="...",
    )
    plan = await planner.plan("контент-маркетинг", region="ru", target_count=15)
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, List
from uuid import uuid4

import anthropic
import httpx

from .writing_pipeline.contracts import ArticleBrief, ClusterPlan

logger = logging.getLogger(__name__)


class ClusterPlanner:
    """
    Generates a cluster plan: pillar article + N cluster articles from a broad topic.
    """

    def __init__(
        self,
        anthropic_client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-20250514",
        volume_provider=None,
        serper_api_key: str = "",
        proxy_url: str = "",
        proxy_secret: str = "",
    ):
        self.client = anthropic_client
        self.model = model
        self.volume_provider = volume_provider
        self.serper_api_key = serper_api_key

    async def plan(
        self,
        big_topic: str,
        region: str = "ru",
        target_count: int = 15,
        site_id: Optional[str] = None,
        knowledge_base_docs: Optional[List[dict]] = None,
    ) -> ClusterPlan:
        """
        Generate a full cluster plan.

        Args:
            big_topic: Broad topic like "контент-маркетинг"
            region: Target region (ru, us, etc.)
            target_count: Target number of cluster articles
            site_id: Optional site ID for DB integration
            knowledge_base_docs: Optional list of KB docs [{id, title, content_text, word_count}]

        Returns:
            ClusterPlan with pillar + cluster articles
        """
        logger.info(f"ClusterPlanner: starting for '{big_topic}' (region={region}, target={target_count})")

        # Step 1: Generate seed subtopics via LLM (with KB context if available)
        seeds = await self._generate_seeds(big_topic, region, target_count, knowledge_base_docs)
        logger.info(f"Step 1: generated {len(seeds)} seed subtopics")

        # Step 2: Keyword expansion via Serper + provider suggestions
        expanded_keywords = await self._expand_keywords(seeds, big_topic, region)
        logger.info(f"Step 2: expanded to {len(expanded_keywords)} keywords")

        # Step 3: Volume enrichment
        volume_map = await self._enrich_volumes(expanded_keywords, region)
        logger.info(f"Step 3: got volumes for {sum(1 for v in volume_map.values() if v > 0)} keywords")

        # Step 4: Clustering + brief generation via LLM
        plan = await self._cluster_and_brief(
            big_topic, region, seeds, expanded_keywords, volume_map, target_count,
        )
        logger.info(
            f"Step 4: cluster plan ready — 1 pillar + {len(plan.cluster_articles)} cluster articles"
        )

        return plan

    async def _generate_seeds(
        self, big_topic: str, region: str, target_count: int,
        knowledge_base_docs: Optional[List[dict]] = None,
    ) -> list[str]:
        """Step 1: LLM generates subtopic candidates (with optional KB context)."""
        kb_section = ""
        if knowledge_base_docs:
            kb_snippets = []
            for doc in knowledge_base_docs[:10]:  # Limit to 10 docs
                title = doc.get("title", "")
                text = doc.get("content_text", "")[:500]  # First 500 chars
                kb_snippets.append(f"- {title}: {text}")
            snippets_text = "\n".join(kb_snippets)
            kb_section = f"""

## Фактура (материалы заказчика):
Используй эти материалы как источник тем и подтем. Приоритет — темы, которые подкреплены фактурой.
{snippets_text}
"""

        today = datetime.now().strftime("%Y-%m-%d")
        prompt = f"""Ты SEO-стратег. Для большой темы "{big_topic}" (регион: {region}) сгенерируй {target_count * 3} подтем-кандидатов для статей.

Сегодня: {today}
{kb_section}
Правила:
- Каждая подтема — конкретная, чтобы по ней можно было написать отдельную статью
- Разнообразие интентов: informational (как, что, зачем), commercial (сравнение, лучший, топ), transactional (купить, заказать)
- Включи 1 pillar-тему (самая широкая) и остальные cluster-темы (узкие)
- Не дублируй — каждая подтема должна покрывать уникальный аспект{'''
- Если есть фактура — учти её темы при генерации подтем''' if knowledge_base_docs else ''}

Ответь JSON-массивом строк:
["подтема 1", "подтема 2", ...]"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                temperature=0.8,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text
            # Parse JSON from response
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            seeds = json.loads(text)
            return seeds[:target_count * 3]
        except Exception as e:
            logger.error(f"Seed generation failed: {e}")
            return [big_topic]

    async def _expand_keywords(
        self, seeds: list[str], big_topic: str, region: str,
    ) -> list[str]:
        """Step 2: Discover additional keywords via Serper + provider."""
        all_keywords = set(seeds)
        all_keywords.add(big_topic)

        if self.serper_api_key:
            gl = "ru" if region.lower() in ["ru", "russia", "kz"] else "us"
            hl = "ru" if region.lower() in ["ru", "russia", "kz"] else "en"
            semaphore = asyncio.Semaphore(5)

            async def search_serper(query: str) -> dict:
                async with semaphore:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            "https://google.serper.dev/search",
                            headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                            json={"q": query, "gl": gl, "hl": hl, "num": 10},
                            timeout=30.0,
                        )
                        resp.raise_for_status()
                        return resp.json()

            # Search top 10 seeds
            tasks = [search_serper(s) for s in seeds[:10]]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    continue
                for item in result.get("relatedSearches", []):
                    q = item.get("query", "").strip()
                    if q:
                        all_keywords.add(q)
                for item in result.get("peopleAlsoAsk", []):
                    q = item.get("question", "").strip()
                    if q:
                        all_keywords.add(q)

        # Provider suggestions
        if self.volume_provider:
            for seed in seeds[:5]:
                try:
                    suggestions = await self.volume_provider.get_suggestions(seed)
                    all_keywords.update(suggestions)
                except Exception:
                    pass

        return list(all_keywords)

    async def _enrich_volumes(self, keywords: list[str], region: str) -> dict[str, int]:
        """Step 3: Fetch volumes for all keywords."""
        volume_map: dict[str, int] = {}

        if not self.volume_provider:
            return volume_map

        lang = "ru" if region.lower() in ["ru", "russia", "kz"] else "en"

        # Batch in chunks of 100
        for i in range(0, len(keywords), 100):
            chunk = keywords[i:i + 100]
            try:
                results = await self.volume_provider.get_volumes(chunk, language_code=lang)
                for vr in results:
                    volume_map[vr.keyword.lower().strip()] = vr.volume
            except Exception as e:
                logger.warning(f"Volume enrichment error for chunk {i}: {e}")

        return volume_map

    async def _cluster_and_brief(
        self,
        big_topic: str,
        region: str,
        seeds: list[str],
        all_keywords: list[str],
        volume_map: dict[str, int],
        target_count: int,
    ) -> ClusterPlan:
        """Step 4: LLM clusters keywords and generates briefs."""

        # Build keyword list with volumes for the prompt
        kw_data = []
        for kw in all_keywords:
            vol = volume_map.get(kw.lower().strip(), 0)
            kw_data.append({"keyword": kw, "volume": vol})
        kw_data.sort(key=lambda x: x["volume"], reverse=True)

        today = datetime.now().strftime("%Y-%m-%d")
        prompt = f"""Ты SEO-стратег. На основе ключевых слов ниже создай кластерный план для темы "{big_topic}" (регион: {region}).

Сегодня: {today}

## Ключевые слова с объёмами:
{json.dumps(kw_data[:200], ensure_ascii=False, indent=2)}

## Задача:
1. Выбери 1 pillar-статью (самая широкая тема, наибольший объём)
2. Выбери {target_count} cluster-статей (узкие подтемы)
3. Для каждой статьи создай brief

## Формат ответа (строго JSON):
{{
  "pillar": {{
    "title_candidate": "...",
    "role": "pillar",
    "primary_intent": "informational|transactional|commercial|navigational",
    "topic_boundaries": {{"in_scope": ["..."], "out_of_scope": ["..."]}},
    "must_answer_questions": ["вопрос 1", "вопрос 2", ...],
    "target_terms": ["keyword1", "keyword2", ...],
    "unique_angle": {{"differentiators": ["..."], "must_not_cover": ["..."]}},
    "internal_links_plan": [{{"target_slug": "cluster-article-slug", "anchor_hint": "текст"}}],
    "seed_queries": ["запрос 1", "запрос 2"],
    "estimated_volume": 12345,
    "priority": 1
  }},
  "cluster_articles": [
    {{
      "title_candidate": "...",
      "role": "cluster",
      "primary_intent": "...",
      "topic_boundaries": {{"in_scope": ["..."], "out_of_scope": ["..."]}},
      "must_answer_questions": ["..."],
      "target_terms": ["..."],
      "unique_angle": {{"differentiators": ["..."], "must_not_cover": ["..."]}},
      "internal_links_plan": [{{"target_slug": "pillar-slug", "anchor_hint": "текст"}}],
      "seed_queries": ["..."],
      "estimated_volume": 5000,
      "priority": 2
    }}
  ]
}}

Правила:
- У каждой статьи уникальный topic_boundaries (не пересекаются)
- must_answer_questions: 5-10 конкретных вопросов
- target_terms: 10-30 ключевых слов из списка выше
- internal_links_plan: каждый cluster → pillar, pillar → все clusters
- Приоритет по объёму: больше объём → выше приоритет (меньше число)
- Ответь ТОЛЬКО JSON, без markdown-обёртки"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8192,
                temperature=0.5,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            data = json.loads(text)

            pillar = ArticleBrief.from_dict(data["pillar"])
            cluster_articles = [
                ArticleBrief.from_dict(a) for a in data.get("cluster_articles", [])
            ]

            return ClusterPlan(
                big_topic=big_topic,
                region=region,
                pillar=pillar,
                cluster_articles=cluster_articles,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error(f"Clustering failed: {e}")
            # Return minimal plan with just the big topic
            return ClusterPlan(
                big_topic=big_topic,
                region=region,
                pillar=ArticleBrief(
                    title_candidate=big_topic,
                    role="pillar",
                    primary_intent="informational",
                    topic_boundaries={"in_scope": [big_topic], "out_of_scope": []},
                    must_answer_questions=[],
                    target_terms=[big_topic],
                    unique_angle={"differentiators": [], "must_not_cover": []},
                    internal_links_plan=[],
                    seed_queries=[big_topic],
                ),
                cluster_articles=[],
                generated_at=datetime.now(timezone.utc).isoformat(),
            )

    async def save_to_db(self, plan: ClusterPlan, site_id: Optional[str], db_session, factual_mode: str = "default", region: str = "ru") -> str:
        """
        Save cluster plan to database.

        Creates Cluster records + Brief records.

        Args:
            plan: The generated cluster plan
            site_id: Site UUID (can be None for standalone clusters)
            db_session: SQLAlchemy session
            factual_mode: default | kb_priority | kb_only
            region: Region code

        Returns:
            Parent cluster ID
        """
        from .writing_pipeline.contracts import ArticleBrief as ABrief
        from ..db.models import Cluster, Brief, Keyword

        # Create parent cluster (pillar)
        parent_cluster = Cluster(
            id=uuid4(),
            site_id=site_id,
            name=plan.big_topic,
            intent=plan.pillar.primary_intent,
            topic_type="pillar",
            estimated_traffic=plan.pillar.estimated_volume,
            factual_mode=factual_mode,
            region=region,
            status="planned",
        )
        db_session.add(parent_cluster)

        # Create brief for pillar
        pillar_brief = Brief(
            id=uuid4(),
            site_id=site_id,
            cluster_id=parent_cluster.id,
            title=plan.pillar.title_candidate,
            target_keyword=plan.pillar.target_terms[0] if plan.pillar.target_terms else plan.big_topic,
            secondary_keywords=plan.pillar.target_terms[1:30] if len(plan.pillar.target_terms) > 1 else [],
            factual_mode=factual_mode,
            structure={
                "role": plan.pillar.role,
                "primary_intent": plan.pillar.primary_intent,
                "topic_boundaries": plan.pillar.topic_boundaries,
                "must_answer_questions": plan.pillar.must_answer_questions,
                "unique_angle": plan.pillar.unique_angle,
                "internal_links_plan": plan.pillar.internal_links_plan,
                "seed_queries": plan.pillar.seed_queries,
            },
            status="draft",
        )
        db_session.add(pillar_brief)

        # Create cluster articles
        for i, article in enumerate(plan.cluster_articles):
            child_cluster = Cluster(
                id=uuid4(),
                site_id=site_id,
                name=article.title_candidate,
                intent=article.primary_intent,
                topic_type=article.role,
                parent_cluster_id=parent_cluster.id,
                estimated_traffic=article.estimated_volume,
                priority_score=float(100 - article.priority),
                factual_mode=factual_mode,
                region=region,
                status="planned",
            )
            db_session.add(child_cluster)

            brief = Brief(
                id=uuid4(),
                site_id=site_id,
                cluster_id=child_cluster.id,
                title=article.title_candidate,
                target_keyword=article.target_terms[0] if article.target_terms else article.title_candidate,
                secondary_keywords=article.target_terms[1:30] if len(article.target_terms) > 1 else [],
                factual_mode=factual_mode,
                structure={
                    "role": article.role,
                    "primary_intent": article.primary_intent,
                    "topic_boundaries": article.topic_boundaries,
                    "must_answer_questions": article.must_answer_questions,
                    "unique_angle": article.unique_angle,
                    "internal_links_plan": article.internal_links_plan,
                    "seed_queries": article.seed_queries,
                },
                status="draft",
            )
            db_session.add(brief)

            # Save target keywords only if site_id is provided
            if site_id:
                for kw_text in article.target_terms[:30]:
                    kw = Keyword(
                        id=uuid4(),
                        site_id=site_id,
                        keyword=kw_text,
                        cluster_id=child_cluster.id,
                        status="clustered",
                    )
                    db_session.add(kw)

        db_session.commit()
        logger.info(f"Saved cluster plan: {parent_cluster.id} with {len(plan.cluster_articles)} children")
        return str(parent_cluster.id)
