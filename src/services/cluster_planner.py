"""
ClusterPlanner — generates a cluster plan from a broad topic.

Search-first approach:
1. Serper Search — discover real keywords from search (related, PAA, autocomplete)
2. Competitor Analysis — scrape top pages, extract H2/H3 headings
3. Volume Enrichment — Yandex Wordstat (RU) / DataForSEO (non-RU) via VolumeProvider
4. LLM Clustering — organize REAL data into briefs (LLM only groups, doesn't invent)
5. Save to DB

Usage:
    from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider
    provider = get_volume_provider(region="ru", settings=settings)
    planner = ClusterPlanner(
        anthropic_client=client,
        serper_api_key="...",
        volume_provider=provider,
    )
    plan = await planner.plan("контент-маркетинг", region="ru", target_count=15)
"""

import json
import logging
import asyncio
import re
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
    Search-first: real data from Serper + VolumeProvider (Wordstat/Rush/DataForSEO), LLM only organizes.
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
        self.serper_api_key = serper_api_key
        self.volume_provider = volume_provider  # VolumeProvider instance (Wordstat/Rush/DataForSEO)

    async def plan(
        self,
        big_topic: str,
        region: str = "ru",
        target_count: int = 15,
        site_id: Optional[str] = None,
        knowledge_base_docs: Optional[List[dict]] = None,
    ) -> ClusterPlan:
        """
        Generate a full cluster plan using search-first approach.
        """
        logger.info(f"ClusterPlanner: starting for '{big_topic}' (region={region}, target={target_count})")

        # Step 1: Discover real keywords from search
        search_data = await self._discover_keywords(big_topic, region)
        logger.info(
            f"Step 1: discovered {len(search_data['keywords'])} keywords, "
            f"{len(search_data['paa_questions'])} PAA questions, "
            f"{len(search_data['top_urls'])} top URLs"
        )

        # Step 2: Competitor analysis — extract headings from top pages
        competitor_headings = await self._analyze_competitors(search_data["top_urls"], region)
        logger.info(f"Step 2: extracted headings from {len(competitor_headings)} pages")

        # Step 2b: Add competitor headings as keywords (short headings < 80 chars)
        all_keywords = search_data["keywords"]
        for page in competitor_headings:
            for heading in page.get("headings", []):
                if len(heading) < 80:
                    all_keywords.add(heading.lower())
        logger.info(f"Step 2b: {len(all_keywords)} keywords after adding headings")

        # Step 2c: VolumeProvider suggestions (Wordstat/Rush associations)
        if self.volume_provider:
            # Pick 3 seed keywords: the topic + 2 related queries
            suggestion_seeds = [big_topic]
            search_queries_list = list(search_data["keywords"] - {big_topic})[:2]
            suggestion_seeds.extend(search_queries_list)

            for seed in suggestion_seeds[:3]:
                try:
                    suggestions = await self.volume_provider.get_suggestions(seed)
                    for s in suggestions:
                        all_keywords.add(s.lower().strip())
                    logger.info(f"VolumeProvider suggestions for '{seed}': +{len(suggestions)} keywords")
                except Exception as e:
                    logger.warning(f"VolumeProvider suggestions error for '{seed}': {e}")

            logger.info(f"Step 2c: {len(all_keywords)} keywords after suggestions")

        # Step 3: Volume enrichment via VolumeProvider (Wordstat for RU, DataForSEO for non-RU)
        kw_with_volumes = await self._enrich_volumes(
            list(all_keywords), region,
        )
        logger.info(f"Step 3: got volumes for {sum(1 for v in kw_with_volumes if v['volume'] > 0)} keywords")

        # Step 4: LLM clustering on REAL data
        plan = await self._cluster_and_brief(
            big_topic, region, kw_with_volumes, competitor_headings,
            search_data["paa_questions"], target_count, knowledge_base_docs,
        )

        # Attach all discovered keywords with volumes to plan
        plan.discovered_keywords = kw_with_volumes

        logger.info(
            f"Step 4: cluster plan ready — 1 pillar + {len(plan.cluster_articles)} cluster articles, "
            f"{len(kw_with_volumes)} discovered keywords"
        )

        return plan

    async def _discover_keywords(self, big_topic: str, region: str) -> dict:
        """
        Step 1: Discover real keywords from Serper search.
        Runs 3-5 search queries + autocomplete around the topic.
        Returns keywords, PAA questions, top organic URLs.
        """
        gl = "ru" if region.lower() in ["ru", "russia", "kz"] else "us"
        hl = "ru" if region.lower() in ["ru", "russia", "kz"] else "en"

        all_keywords = set()
        all_keywords.add(big_topic)
        paa_questions = []
        top_urls = []

        if not self.serper_api_key:
            return {"keywords": all_keywords, "paa_questions": paa_questions, "top_urls": top_urls}

        # Generate search queries around the topic (8-10 patterns for more coverage)
        if hl == "ru":
            search_queries = [
                big_topic,
                f"{big_topic} для начинающих",
                f"как {big_topic}",
                f"{big_topic} советы",
                f"лучший {big_topic}",
                f"{big_topic} примеры",
                f"{big_topic} что это",
                f"{big_topic} пошаговая инструкция",
            ]
        else:
            search_queries = [
                big_topic,
                f"{big_topic} for beginners",
                f"how to {big_topic}",
                f"{big_topic} tips",
                f"best {big_topic}",
                f"{big_topic} examples",
                f"what is {big_topic}",
                f"{big_topic} step by step",
            ]

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

        async def autocomplete_serper(query: str) -> dict:
            async with semaphore:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://google.serper.dev/autocomplete",
                        headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                        json={"q": query, "gl": gl, "hl": hl},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    return resp.json()

        # Run all searches + autocompletes in parallel
        tasks = []
        for q in search_queries:
            tasks.append(("search", q, search_serper(q)))
            tasks.append(("autocomplete", q, autocomplete_serper(q)))

        coros = [t[2] for t in tasks]
        results = await asyncio.gather(*coros, return_exceptions=True)

        seen_urls = set()
        for i, result in enumerate(results):
            task_type = tasks[i][0]
            if isinstance(result, Exception):
                logger.warning(f"Serper {task_type} error: {result}")
                continue

            if task_type == "search":
                # Organic results → top URLs
                for item in result.get("organic", []):
                    url = item.get("link", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        top_urls.append({"url": url, "title": item.get("title", ""), "snippet": item.get("snippet", "")})
                # Related searches → keywords
                for item in result.get("relatedSearches", []):
                    q = item.get("query", "").strip()
                    if q:
                        all_keywords.add(q)
                # PAA → questions + keywords
                for item in result.get("peopleAlsoAsk", []):
                    q = item.get("question", "").strip()
                    if q:
                        paa_questions.append(q)
                        all_keywords.add(q)
            elif task_type == "autocomplete":
                for item in result.get("suggestions", []):
                    q = item.get("value", "").strip()
                    if q:
                        all_keywords.add(q)

        return {
            "keywords": all_keywords,
            "paa_questions": list(set(paa_questions)),
            "top_urls": top_urls[:10],
        }

    async def _analyze_competitors(self, top_urls: list, region: str) -> list:
        """
        Step 2: Scrape top pages and extract H2/H3 headings.
        Uses Serper scrape for Russian content, trafilatura as fallback.
        """
        if not top_urls or not self.serper_api_key:
            return []

        competitor_headings = []
        semaphore = asyncio.Semaphore(3)

        async def scrape_page(url_data: dict) -> Optional[dict]:
            url = url_data["url"]
            async with semaphore:
                try:
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(
                            "https://scrape.serper.dev",
                            headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                            json={"url": url},
                            timeout=30.0,
                        )
                        if resp.status_code != 200:
                            return None
                        data = resp.json()
                        text = data.get("text", "")
                        # Extract headings from text (lines that look like H2/H3)
                        headings = _extract_headings_from_text(text)
                        return {
                            "url": url,
                            "title": url_data.get("title", ""),
                            "headings": headings,
                        }
                except Exception as e:
                    logger.warning(f"Scrape failed for {url}: {e}")
                    return None

        tasks = [scrape_page(u) for u in top_urls[:5]]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception) or result is None:
                continue
            if result.get("headings"):
                competitor_headings.append(result)

        return competitor_headings

    async def _enrich_volumes(
        self, keywords: list[str], region: str,
    ) -> list[dict]:
        """
        Step 3: Get volumes via VolumeProvider (Wordstat for RU, DataForSEO for non-RU).
        Batches of 10 with 1.5s pause to respect Wordstat rate limits (10 req/sec).
        """
        kw_data = [{"keyword": kw, "volume": 0, "cpc": 0, "competition": 0} for kw in keywords]

        if not self.volume_provider:
            logger.warning("No volume_provider configured — returning zero volumes")
            return kw_data

        language_code = "ru" if region.lower() in ["ru", "russia", "kz"] else "en"

        try:
            # Batch in chunks of 10 with pause to avoid rate limits
            volume_map = {}
            batch_size = 10
            for i in range(0, len(keywords), batch_size):
                chunk = keywords[i:i + batch_size]
                try:
                    results = await self.volume_provider.get_volumes(chunk, language_code=language_code)
                    for vr in results:
                        volume_map[vr.keyword.lower().strip()] = {
                            "volume": vr.volume,
                            "cpc": vr.cpc,
                            "competition": vr.competition,
                        }
                except Exception as e:
                    logger.warning(f"Volume enrichment error for chunk {i}: {e}")

                if i + batch_size < len(keywords):
                    await asyncio.sleep(1.5)

            # Merge volumes into kw_data
            for item in kw_data:
                metrics = volume_map.get(item["keyword"].lower().strip(), {})
                item["volume"] = metrics.get("volume", 0)
                item["cpc"] = metrics.get("cpc", 0)
                item["competition"] = metrics.get("competition", 0)

            source = self.volume_provider.source_name
            logger.info(f"Volume enrichment via {source}: {len(volume_map)} keywords processed")

        except Exception as e:
            logger.error(f"Volume enrichment failed: {e}")

        return kw_data

    async def _cluster_and_brief(
        self,
        big_topic: str,
        region: str,
        kw_with_volumes: list[dict],
        competitor_headings: list[dict],
        paa_questions: list[str],
        target_count: int,
        knowledge_base_docs: Optional[List[dict]] = None,
    ) -> ClusterPlan:
        """Step 4: LLM clusters REAL keywords and generates briefs."""

        # Sort by volume, take top 300 (increased from 200)
        kw_with_volumes.sort(key=lambda x: x["volume"], reverse=True)
        top_keywords = kw_with_volumes[:300]

        # Build keyword lookup for post-processing
        kw_volume_map = {kw["keyword"].lower().strip(): kw for kw in kw_with_volumes}

        # Format competitor headings for prompt
        competitor_section = ""
        if competitor_headings:
            comp_lines = []
            for ch in competitor_headings[:5]:
                headings_str = ", ".join(ch["headings"][:15])
                comp_lines.append(f'- "{ch["title"]}": {headings_str}')
            competitor_section = f"""
## Структура конкурентов (H2/H3 заголовки):
{chr(10).join(comp_lines)}
"""

        # Format PAA
        paa_section = ""
        if paa_questions:
            paa_list = "\n".join(f"- {q}" for q in paa_questions[:20])
            paa_section = f"""
## Вопросы из «Люди также спрашивают» (PAA):
{paa_list}
"""

        # KB section
        kb_section = ""
        if knowledge_base_docs:
            kb_snippets = []
            for doc in knowledge_base_docs[:10]:
                title = doc.get("title", "")
                text = doc.get("content_text", "")[:300]
                kb_snippets.append(f"- {title}: {text}")
            kb_section = f"""
## Фактура (материалы заказчика):
{chr(10).join(kb_snippets)}
"""

        today = datetime.now().strftime("%Y-%m-%d")
        prompt = f"""Ты SEO-стратег. На основе РЕАЛЬНЫХ данных из поиска создай кластерный план для темы "{big_topic}" (регион: {region}).

Сегодня: {today}

## Реальные ключевые слова с объёмами из поиска ({len(top_keywords)} шт.):
{json.dumps(top_keywords[:200], ensure_ascii=False, indent=2)}
{competitor_section}{paa_section}{kb_section}
## Задача:
1. Выбери 1 pillar-статью (самая широкая тема, наибольший объём)
2. Выбери {target_count} cluster-статей (узкие подтемы)
3. Для каждой статьи создай brief
4. Используй ТОЛЬКО ключевые слова из списка выше (не придумывай новые!)
5. Учитывай структуру конкурентов и PAA-вопросы

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
- must_answer_questions: 5-10 конкретных вопросов (используй PAA если есть)
- target_terms для pillar: 30-50 ключевых слов ИЗ СПИСКА ВЫШЕ
- target_terms для cluster: 15-25 ключевых слов ИЗ СПИСКА ВЫШЕ
- ОБЯЗАТЕЛЬНО: минимум 15 target_terms на каждую статью, pillar — минимум 30
- Каждое ключевое слово может попасть в НЕСКОЛЬКО статей если релевантно
- estimated_volume: сумма volumes по target_terms
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

            # Post-processing: pad briefs with too few target_terms
            all_briefs = [pillar] + cluster_articles
            used_terms = set()
            for brief in all_briefs:
                used_terms.update(t.lower().strip() for t in brief.target_terms)

            for brief in all_briefs:
                min_terms = 30 if brief.role == "pillar" else 15
                if len(brief.target_terms) < min_terms:
                    # Find related keywords by matching words from title
                    title_words = set(brief.title_candidate.lower().split())
                    title_words -= {"в", "на", "для", "и", "с", "по", "от", "к", "из", "о",
                                    "the", "a", "an", "in", "on", "for", "and", "with", "to", "of"}
                    candidates = []
                    for kw_data in kw_with_volumes:
                        kw_lower = kw_data["keyword"].lower().strip()
                        if kw_lower in {t.lower().strip() for t in brief.target_terms}:
                            continue
                        kw_words = set(kw_lower.split())
                        overlap = title_words & kw_words
                        if overlap:
                            candidates.append((len(overlap), kw_data["volume"], kw_data["keyword"]))
                    # Sort by overlap then volume
                    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
                    for _, _, kw_text in candidates:
                        if len(brief.target_terms) >= min_terms:
                            break
                        brief.target_terms.append(kw_text)
                    if len(brief.target_terms) < min_terms:
                        logger.warning(
                            f"Brief '{brief.title_candidate}' has only {len(brief.target_terms)} "
                            f"target_terms (min {min_terms})"
                        )

            return ClusterPlan(
                big_topic=big_topic,
                region=region,
                pillar=pillar,
                cluster_articles=cluster_articles,
                generated_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error(f"Clustering failed: {e}")
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

        Creates ONE Cluster + N Brief records (flat model, no child clusters).

        Args:
            plan: The generated cluster plan
            site_id: Site UUID (can be None for standalone clusters)
            db_session: SQLAlchemy session
            factual_mode: default | kb_priority | kb_only
            region: Region code

        Returns:
            Cluster ID
        """
        from ..db.models import Cluster, Brief, Keyword

        # Create single cluster for the whole plan
        total_volume = (plan.pillar.estimated_volume or 0) + sum(
            a.estimated_volume or 0 for a in plan.cluster_articles
        )
        cluster = Cluster(
            id=uuid4(),
            site_id=site_id,
            name=plan.big_topic,
            intent=plan.pillar.primary_intent,
            topic_type="pillar",
            estimated_traffic=total_volume,
            factual_mode=factual_mode,
            region=region,
            status="planned",
        )
        db_session.add(cluster)

        # Create brief for pillar article
        pillar_brief = Brief(
            id=uuid4(),
            site_id=site_id,
            cluster_id=cluster.id,
            title=plan.pillar.title_candidate,
            target_keyword=plan.pillar.target_terms[0] if plan.pillar.target_terms else plan.big_topic,
            secondary_keywords=plan.pillar.target_terms[1:50] if len(plan.pillar.target_terms) > 1 else [],
            factual_mode=factual_mode,
            structure={
                "role": plan.pillar.role,
                "primary_intent": plan.pillar.primary_intent,
                "topic_boundaries": plan.pillar.topic_boundaries,
                "must_answer_questions": plan.pillar.must_answer_questions,
                "unique_angle": plan.pillar.unique_angle,
                "internal_links_plan": plan.pillar.internal_links_plan,
                "seed_queries": plan.pillar.seed_queries,
                "estimated_volume": plan.pillar.estimated_volume,
                "priority": plan.pillar.priority,
            },
            status="draft",
        )
        db_session.add(pillar_brief)

        # Create briefs for cluster articles — ALL in the same cluster
        for i, article in enumerate(plan.cluster_articles):
            brief = Brief(
                id=uuid4(),
                site_id=site_id,
                cluster_id=cluster.id,
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
                    "estimated_volume": article.estimated_volume,
                    "priority": article.priority,
                },
                status="draft",
            )
            db_session.add(brief)

        # Save ALL discovered keywords to the cluster (not just target_terms)
        if site_id:
            seen_keywords = set()

            # Build set of clustered keywords (assigned to briefs)
            clustered_kws = set()
            all_articles = [plan.pillar] + plan.cluster_articles
            for article in all_articles:
                for kw_text in article.target_terms:
                    clustered_kws.add(kw_text.lower().strip())

            # Save all discovered keywords with volumes
            discovered_kw_map = {kw["keyword"].lower().strip(): kw for kw in plan.discovered_keywords}

            # First: save clustered keywords (from briefs)
            for article in all_articles:
                for kw_text in article.target_terms:
                    kw_lower = kw_text.lower().strip()
                    if kw_lower not in seen_keywords:
                        seen_keywords.add(kw_lower)
                        metrics = discovered_kw_map.get(kw_lower, {})
                        kw = Keyword(
                            id=uuid4(),
                            site_id=site_id,
                            keyword=kw_text,
                            cluster_id=cluster.id,
                            search_volume=metrics.get("volume", 0),
                            cpc=metrics.get("cpc", 0),
                            status="clustered",
                        )
                        db_session.add(kw)

            # Then: save remaining discovered keywords (not assigned to any brief)
            for kw_data in plan.discovered_keywords:
                kw_lower = kw_data["keyword"].lower().strip()
                if kw_lower not in seen_keywords:
                    seen_keywords.add(kw_lower)
                    kw = Keyword(
                        id=uuid4(),
                        site_id=site_id,
                        keyword=kw_data["keyword"],
                        cluster_id=cluster.id,
                        search_volume=kw_data.get("volume", 0),
                        cpc=kw_data.get("cpc", 0),
                        status="discovered",
                    )
                    db_session.add(kw)

            logger.info(
                f"Saved keywords: {len(clustered_kws & seen_keywords)} clustered + "
                f"{len(seen_keywords) - len(clustered_kws & seen_keywords)} discovered = {len(seen_keywords)} total"
            )

        db_session.commit()
        logger.info(f"Saved cluster plan: {cluster.id} with 1 pillar + {len(plan.cluster_articles)} cluster briefs")
        return str(cluster.id)


def _extract_headings_from_text(text: str) -> list[str]:
    """Extract likely headings from scraped page text."""
    if not text:
        return []

    headings = []
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        # Skip very short or very long lines
        if len(line) < 5 or len(line) > 200:
            continue
        # Lines that look like headings: short, no punctuation at end, Title Case, etc.
        if (
            line.endswith(":")
            or (len(line) < 80 and not line.endswith(".") and not line.endswith(","))
            or line.startswith("#")
        ):
            # Remove markdown heading markers
            clean = re.sub(r"^#+\s*", "", line).rstrip(":")
            if clean and len(clean) > 3:
                headings.append(clean)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for h in headings:
        h_lower = h.lower()
        if h_lower not in seen:
            seen.add(h_lower)
            unique.append(h)

    return unique[:30]
