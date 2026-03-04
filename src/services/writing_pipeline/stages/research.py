"""
Research Stage - Generates queries, fetches data, and packs facts.

This is a multi-step stage:
1. Query Planner: Generates search queries based on intent
2. Search Runner: Executes searches (Serper.dev)
3. Content Fetcher: Fetches full page content for top results (Jina Reader)
4. PAA Expansion: Generates additional queries from PAA questions
5. Keyword Metrics: Fetches search volume and difficulty (Wordstat / Rush)
6. Fact Packer: Processes results into structured research pack
"""

import json
import os
import logging
import re as re_module
from typing import List, Dict, Any, Optional, Set

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import QueryPlannerResult, ResearchResult, KeywordMetricsResult, KeywordMetricsData, KeywordClusteringResult

logger = logging.getLogger(__name__)


class ResearchStage(WritingStage):
    """
    Stage 2: Research

    Enhanced with:
    - Full page content fetching via Jina Reader
    - PAA (People Also Ask) query expansion
    - Keyword metrics via pluggable VolumeProvider (Wordstat / Rush)
    - Trafilatura fallback for content extraction
    """

    def __init__(
        self,
        *args,
        serper_api_key: Optional[str] = None,
        jina_api_key: Optional[str] = None,
        volume_provider=None,
        use_playwright: bool = True,
        residential_proxy_url: Optional[str] = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.serper_api_key = serper_api_key
        self.jina_api_key = jina_api_key
        self.volume_provider = volume_provider  # VolumeProvider instance (optional)
        self.use_playwright = use_playwright
        self.residential_proxy_url = residential_proxy_url or ""

        # Lazy-loaded clients
        self._jina_reader = None
        self._trafilatura = None
        self._playwright_browser = None
        self._serper_scraper = None
        self._trafilatura_proxied = None

    @property
    def name(self) -> str:
        return "research"

    def _get_jina_reader(self):
        """Lazy-load Jina Reader client."""
        if self._jina_reader is None:
            from ..data_sources.jina_reader import JinaReader
            self._jina_reader = JinaReader(api_key=self.jina_api_key)
        return self._jina_reader

    def _get_trafilatura(self):
        """Lazy-load Trafilatura extractor."""
        if self._trafilatura is None:
            try:
                from ..data_sources.trafilatura_ext import TrafilaturaExtractor, is_trafilatura_available
                if is_trafilatura_available():
                    self._trafilatura = TrafilaturaExtractor()
            except Exception as e:
                logger.warning(f"Trafilatura not available: {e}")
        return self._trafilatura

    def _get_playwright_browser(self, proxy_url: str = None):
        """Lazy-load Playwright browser."""
        if self._playwright_browser is None and self.use_playwright:
            try:
                from ..data_sources.playwright_browser import PlaywrightBrowser
                self._playwright_browser = PlaywrightBrowser(
                    max_concurrent=2, timeout_ms=30000,
                    proxy_url=proxy_url,
                )
            except Exception as e:
                logger.warning(f"Playwright not available: {e}")
        return self._playwright_browser

    def _get_serper_scraper(self):
        """Lazy-load Serper Scraper client."""
        if self._serper_scraper is None and self.serper_api_key:
            from ..data_sources.serper_scrape import SerperScraper
            self._serper_scraper = SerperScraper(api_key=self.serper_api_key)
        return self._serper_scraper

    def _get_trafilatura_proxied(self):
        """Lazy-load Trafilatura with residential proxy."""
        if self._trafilatura_proxied is None and self.residential_proxy_url:
            try:
                from ..data_sources.trafilatura_ext import TrafilaturaExtractor, is_trafilatura_available
                if is_trafilatura_available():
                    self._trafilatura_proxied = TrafilaturaExtractor(proxy_url=self.residential_proxy_url)
            except Exception as e:
                logger.warning(f"Trafilatura (proxied) not available: {e}")
        return self._trafilatura_proxied

    def _filter_kb_docs(
        self,
        kb_docs: List[Dict[str, Any]],
        topic: str,
        keywords: List[str],
        max_docs: int = 7,
    ) -> List[Dict[str, Any]]:
        """Filter KB documents by keyword overlap relevance.

        Args:
            kb_docs: Raw KB documents with title and content_text
            topic: Article topic
            keywords: Target terms, seed queries, must-answer questions
            max_docs: Maximum documents to keep

        Returns:
            Filtered list sorted by relevance score (descending)
        """
        if len(kb_docs) <= max_docs:
            return kb_docs

        # Build keyword set from topic + all keyword sources
        raw_terms = [topic] + keywords
        term_words: set[str] = set()
        for term in raw_terms:
            for w in re_module.findall(r'[a-zA-Zа-яА-ЯёЁ]{3,}', term.lower()):
                term_words.add(w)

        if not term_words:
            return kb_docs[:max_docs]

        scored: list[tuple[float, int, Dict[str, Any]]] = []
        for idx, doc in enumerate(kb_docs):
            title = (doc.get("title") or "").lower()
            content = (doc.get("content_text") or "")[:2000].lower()

            title_words = set(re_module.findall(r'[a-zA-Zа-яА-ЯёЁ]{3,}', title))
            content_words = set(re_module.findall(r'[a-zA-Zа-яА-ЯёЁ]{3,}', content))

            title_overlap = len(term_words & title_words)
            content_overlap = len(term_words & content_words)
            score = title_overlap * 3 + content_overlap

            scored.append((score, idx, doc))

        scored.sort(key=lambda x: (-x[0], x[1]))
        selected = [item[2] for item in scored[:max_docs]]
        scores_str = ", ".join(f"{s[2].get('title','?')}={s[0]}" for s in scored[:max_docs])
        logger.info(f"KB filtering: {len(kb_docs)} docs → {len(selected)} selected (scores: {scores_str})")
        return selected

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute research stage."""
        log = context.start_stage(self.name)
        total_tokens = 0
        total_in = 0
        total_out = 0
        factual_mode = context.config.get("factual_mode", "default")

        try:
            if context.intent is None:
                raise ValueError("Intent stage must be completed before research")

            # Step 1: Generate search queries (skip in kb_only mode)
            from ..contracts import Query
            if factual_mode == "kb_only":
                queries = QueryPlannerResult(
                    topic=context.topic,
                    queries=[Query(query=context.topic, purpose="other")],
                )
                tokens = 0
                logger.info("factual_mode=kb_only: skipping query generation")
            else:
                queries, tokens, q_in, q_out = await self._generate_queries(context)
                total_in += q_in
                total_out += q_out

            # If brief provides seed_queries, add them as additional queries
            brief = context.config.get("brief")
            if brief:
                brief_data = brief if isinstance(brief, dict) else brief.to_dict()
                for sq in brief_data.get("seed_queries", []):
                    if sq not in [q.query for q in queries.queries]:
                        queries.queries.append(Query(query=sq, purpose="other"))

            context.queries = queries
            total_tokens += tokens

            # Save queries if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "02_queries.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(queries.to_dict(), f, ensure_ascii=False, indent=2)

            search_results = []
            expand_paa = context.config.get("expand_paa", True)

            # In kb_only mode: skip web search entirely
            if factual_mode != "kb_only":
                # Step 2: Execute initial searches
                search_results = await self._execute_searches(context)

                # Step 3: PAA Expansion (if enabled)
                if expand_paa and self.serper_api_key:
                    paa_results = await self._expand_paa_queries(context, search_results)
                    search_results.extend(paa_results)

                # Step 4: Fetch full page content for top results
                fetch_content = context.config.get("fetch_page_content", True)
                max_pages = context.config.get("max_pages_to_fetch", 5)
                if fetch_content:
                    search_results = await self._fetch_page_contents(
                        search_results, max_pages, region=context.region
                    )

                # Mark all web results with origin
                for sr in search_results:
                    for org in sr.get("organic", []):
                        if "origin" not in org:
                            org["origin"] = "web"
            else:
                logger.info("factual_mode=kb_only: skipping web search")

            # Inject Knowledge Base documents (if attached)
            kb_docs = context.config.get("knowledge_base_docs", [])
            if kb_docs:
                # Fix 1: Filter by relevance
                filter_keywords: list[str] = []
                if brief:
                    brief_data = brief if isinstance(brief, dict) else brief.to_dict()
                    filter_keywords.extend(brief_data.get("target_terms", []))
                    filter_keywords.extend(brief_data.get("seed_queries", []))
                if context.intent and context.intent.must_answer_questions:
                    filter_keywords.extend(context.intent.must_answer_questions)
                kb_docs = self._filter_kb_docs(kb_docs, context.topic, filter_keywords)

                # Fix 2: Budget model — distribute chars across docs
                KB_TOTAL_BUDGET = 60_000
                chars_per_doc = max(4000, KB_TOTAL_BUDGET // len(kb_docs))

                kb_result = {
                    "query": context.topic,
                    "purpose": "knowledge_base",
                    "is_knowledge_base": True,
                    "organic": [{
                        "position": 0,
                        "title": doc["title"],
                        "link": f"kb://{doc['id']}",
                        "page_content": doc["content_text"][:chars_per_doc],
                        "page_word_count": doc.get("word_count", 0),
                        "is_knowledge_base": True,
                        "origin": "kb",
                    } for doc in kb_docs],
                }
                # kb_priority/kb_only: KB first; default: KB appended
                if factual_mode in ("kb_priority", "kb_only"):
                    search_results.insert(0, kb_result)
                else:
                    search_results.append(kb_result)
                logger.info(f"Injected {len(kb_docs)} KB documents (mode={factual_mode}, budget={chars_per_doc} chars/doc)")
            elif factual_mode == "kb_only":
                logger.warning("factual_mode=kb_only but no KB docs provided — research will be thin")

            context.search_results = search_results

            # Save search results if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "03_search_results.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    # Truncate page_content for saving (can be very large)
                    results_for_save = self._truncate_for_save(search_results)
                    json.dump(results_for_save, f, ensure_ascii=False, indent=2)

            # Step 4.5: Analyze competitor pages from search results
            competitor_pages = self._analyze_competitors(search_results)

            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "03b_competitor_analysis.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(competitor_pages, f, ensure_ascii=False, indent=2)

            # Step 5: Fetch keyword metrics (if VolumeProvider configured)
            if self.volume_provider:
                keyword_metrics = await self._fetch_keyword_metrics(context)
                context.keyword_metrics = keyword_metrics

                if context.save_intermediate and context.output_dir:
                    output_path = os.path.join(context.output_dir, "03a_keyword_metrics.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(keyword_metrics.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 6: Pack facts (with competitor analysis)
            research, tokens, fp_in, fp_out = await self._pack_facts(context, competitor_pages)
            context.research = research
            total_tokens += tokens
            total_in += fp_in
            total_out += fp_out

            # Save research pack if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "04_research_pack.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(research.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 7: Keyword clustering
            clusters, cluster_tokens, ck_in, ck_out = await self._cluster_keywords(context)
            if clusters:
                context.research.keyword_clusters = clusters
                total_tokens += cluster_tokens
                total_in += ck_in
                total_out += ck_out

                if context.save_intermediate and context.output_dir:
                    output_path = os.path.join(context.output_dir, "04b_keyword_clusters.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(clusters.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 8: Select monitoring keywords
            context.monitoring_keywords = self._select_monitoring_keywords(
                context, clusters,
            )

            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "04c_monitoring_keywords.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(context.monitoring_keywords, f, ensure_ascii=False, indent=2)

            # Calculate metadata
            pages_fetched = sum(
                1 for r in search_results
                for org in r.get("organic", [])
                if org.get("page_content")
            )

            context.complete_stage(
                input_tokens=total_in,
                output_tokens=total_out,
                metadata={
                    "queries_count": len(queries.queries),
                    "sources_count": len(research.sources),
                    "facts_count": len(research.facts),
                    "pages_fetched": pages_fetched,
                    "paa_expanded": expand_paa,
                    "has_keyword_metrics": context.keyword_metrics is not None,
                    "has_keyword_clusters": context.research.keyword_clusters is not None,
                    "monitoring_keywords_count": len(context.monitoring_keywords),
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    async def _generate_queries(self, context: WritingContext) -> tuple[QueryPlannerResult, int, int, int]:
        """Generate search queries from intent. Returns (result, total_tokens, input_tokens, output_tokens)."""
        prompt_template = self._load_prompt("research_queries_v1")
        intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
        prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)

        response_text, in_t, out_t = self._call_llm(
            prompt,
            max_tokens=1024,
            temperature=0.7,
        )

        data = self._parse_json_response(response_text)
        return QueryPlannerResult.from_dict(data), in_t + out_t, in_t, out_t

    async def _execute_searches(self, context: WritingContext) -> List[Dict[str, Any]]:
        """Execute searches using available data sources."""
        results = []

        if self.serper_api_key:
            results = await self._search_with_serper(context)
        else:
            results = self._create_minimal_search_results(context)

        return results

    async def _search_with_serper(self, context: WritingContext) -> List[Dict[str, Any]]:
        """Execute searches using Serper.dev API."""
        import httpx

        results = []
        is_russian = context.region.lower() in ["ru", "россия", "russia"]
        # Use gl=us even for Russian queries: gl=ru returns 0 PAA/Related.
        # gl=us + hl=ru returns Russian-language PAA and Related Searches.
        gl = "us"
        hl = "ru" if is_russian else "en"

        async with httpx.AsyncClient() as client:
            for query in context.queries.queries:
                try:
                    response = await client.post(
                        "https://google.serper.dev/search",
                        headers={
                            "X-API-KEY": self.serper_api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "q": query.query,
                            "gl": gl,
                            "hl": hl,
                            "num": 10,
                        },
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    serp_data = response.json()

                    paa = serp_data.get("peopleAlsoAsk", []) or []
                    related = serp_data.get("relatedSearches", []) or []
                    logger.info(f"Serper PAA={len(paa)}, Related={len(related)} for '{query.query}'")

                    results.append({
                        "query": query.query,
                        "purpose": query.purpose,
                        "organic": serp_data.get("organic", []),
                        "peopleAlsoAsk": paa,
                        "relatedSearches": related,
                    })
                except Exception as e:
                    logger.error(f"Search error for '{query.query}': {e}")
                    results.append({
                        "query": query.query,
                        "purpose": query.purpose,
                        "error": str(e),
                        "organic": [],
                    })

        return results

    async def _expand_paa_queries(
        self,
        context: WritingContext,
        initial_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Expand queries using People Also Ask questions from initial results.

        Args:
            context: Pipeline context
            initial_results: Initial search results with PAA data

        Returns:
            Additional search results from PAA queries
        """
        import httpx

        # Collect unique PAA questions
        paa_questions: Set[str] = set()
        for result in initial_results:
            for paa in result.get("peopleAlsoAsk", []):
                question = paa.get("question", "")
                if question:
                    paa_questions.add(question)

        if not paa_questions:
            return []

        # Limit to top 3 PAA questions
        max_paa_queries = context.config.get("max_paa_queries", 3)
        paa_list = list(paa_questions)[:max_paa_queries]

        logger.info(f"Expanding with {len(paa_list)} PAA queries")

        # Execute PAA searches
        results = []
        # gl=us + hl=ru for Russian PAA (gl=ru returns 0 PAA/Related)
        gl = "us"
        hl = "ru" if context.region.lower() in ["ru", "россия", "russia"] else "en"

        async with httpx.AsyncClient() as client:
            for question in paa_list:
                try:
                    response = await client.post(
                        "https://google.serper.dev/search",
                        headers={
                            "X-API-KEY": self.serper_api_key,
                            "Content-Type": "application/json",
                        },
                        json={
                            "q": question,
                            "gl": gl,
                            "hl": hl,
                            "num": 5,  # Fewer results for PAA
                        },
                        timeout=30.0,
                    )
                    response.raise_for_status()
                    serp_data = response.json()

                    results.append({
                        "query": question,
                        "purpose": "paa_expansion",
                        "is_paa_query": True,
                        "organic": serp_data.get("organic", []),
                        "peopleAlsoAsk": serp_data.get("peopleAlsoAsk", []),
                        "relatedSearches": serp_data.get("relatedSearches", []),
                    })
                except Exception as e:
                    logger.error(f"PAA search error for '{question}': {e}")

        return results

    async def _fetch_page_contents(
        self,
        search_results: List[Dict[str, Any]],
        max_pages: int = 5,
        region: str = "ru",
    ) -> List[Dict[str, Any]]:
        """
        Fetch full page content for top organic results.

        Russian chain: Serper Scrape → Trafilatura (proxied) → Playwright (proxied) → skip
        International chain: Jina → Trafilatura (direct) → Playwright

        Args:
            search_results: Search results with organic listings
            max_pages: Maximum number of pages to fetch
            region: Region code to determine fetch strategy

        Returns:
            Search results with page_content added to organic items
        """
        # Collect unique URLs from top positions
        urls_to_fetch: List[str] = []
        url_to_result_indices: Dict[str, List[tuple]] = {}

        for result_idx, result in enumerate(search_results):
            for org_idx, organic in enumerate(result.get("organic", [])[:3]):  # Top 3 per query
                url = organic.get("link", "")
                if url and url not in url_to_result_indices:
                    urls_to_fetch.append(url)
                    url_to_result_indices[url] = []
                if url:
                    url_to_result_indices[url].append((result_idx, org_idx))

        # Limit total pages
        urls_to_fetch = urls_to_fetch[:max_pages]

        if not urls_to_fetch:
            return search_results

        logger.info(f"Fetching content from {len(urls_to_fetch)} pages")

        is_russian = region.lower() in ["ru", "россия", "russia"]

        from ..data_sources.jina_reader import PageContent

        if is_russian:
            contents = await self._fetch_russian_content(urls_to_fetch)
        else:
            contents = await self._fetch_international_content(urls_to_fetch)

        # Log results
        success_count = sum(1 for c in contents if c.success)
        logger.info(f"Content fetch results: {success_count}/{len(contents)} successful")
        for url, content in zip(urls_to_fetch, contents):
            if not content.success:
                logger.warning(f"Failed to fetch {url}: {content.error}")

        # Add content to search results
        jina = self._get_jina_reader()

        for url, content in zip(urls_to_fetch, contents):
            if content.success and url in url_to_result_indices:
                truncated = jina.truncate_content(content.content, max_words=2000)

                for result_idx, org_idx in url_to_result_indices[url]:
                    search_results[result_idx]["organic"][org_idx]["page_content"] = truncated
                    search_results[result_idx]["organic"][org_idx]["page_word_count"] = content.word_count

        return search_results

    async def _fetch_russian_content(self, urls: List[str]):
        """
        Fetch chain for Russian content:
        1. Serper Scrape (cloud, ~67% success, 2 credits/page)
        2. Trafilatura via residential proxy (if configured)
        3. Playwright via residential proxy (if configured)
        4. Skip (use snippets only)
        """
        from ..data_sources.jina_reader import PageContent
        contents = [None] * len(urls)

        # Layer 1: Serper Scrape
        scraper = self._get_serper_scraper()
        if scraper:
            logger.info("Layer 1: Serper Scrape for Russian content")
            scraped = await scraper.fetch_batch(urls, max_concurrent=3)
            for i, sc in enumerate(scraped):
                if sc.success:
                    contents[i] = PageContent(
                        url=sc.url, title=sc.title, content=sc.content,
                        word_count=sc.word_count, success=True,
                    )

        # Layer 2: Trafilatura (proxied if available, otherwise direct)
        still_needed = [(i, url) for i, url in enumerate(urls) if contents[i] is None]
        if still_needed:
            traf = self._get_trafilatura_proxied()
            if not traf:
                traf = self._get_trafilatura()  # fallback to direct
            if traf:
                logger.info(f"Layer 2: Trafilatura for {len(still_needed)} URLs")
                for i, url in still_needed:
                    try:
                        ext = await traf.extract_from_url(url)
                        if ext.success:
                            contents[i] = PageContent(
                                url=ext.url, title=ext.title, content=ext.content,
                                word_count=ext.word_count, success=True,
                            )
                    except Exception as e:
                        logger.warning(f"Trafilatura failed for {url}: {e}")

        # Layer 3: Playwright (proxied if available)
        still_needed = [(i, url) for i, url in enumerate(urls) if contents[i] is None]
        if still_needed:
            pw = self._get_playwright_browser(proxy_url=self.residential_proxy_url or None)
            if pw:
                logger.info(f"Layer 3: Playwright for {len(still_needed)} URLs")
                pw_urls = [url for _, url in still_needed]
                pw_results = await pw.fetch_batch(pw_urls, delay=1.5)
                for (i, url), pr in zip(still_needed, pw_results):
                    if pr.success:
                        contents[i] = PageContent(
                            url=pr.url, title=pr.title, content=pr.content,
                            word_count=pr.word_count, success=True,
                        )

        # Fill remaining with empty PageContent
        for i in range(len(urls)):
            if contents[i] is None:
                contents[i] = PageContent(
                    url=urls[i], title="", content="", word_count=0,
                    success=False, error="All fetch layers failed",
                )

        return contents

    async def _fetch_international_content(self, urls: List[str]):
        """
        Fetch chain for non-Russian content:
        1. Jina Reader
        2. Trafilatura (direct)
        3. Playwright
        """
        from ..data_sources.jina_reader import PageContent

        # Jina first
        jina = self._get_jina_reader()
        contents = await jina.fetch_batch(urls, max_concurrent=3)

        # Trafilatura fallback
        trafilatura = self._get_trafilatura()
        failed = [(i, url) for i, (url, c) in enumerate(zip(urls, contents)) if not c.success]
        if failed and trafilatura:
            logger.info(f"Trafilatura fallback for {len(failed)} URLs")
            for i, url in failed:
                try:
                    ext = await trafilatura.extract_from_url(url)
                    if ext.success:
                        contents[i] = PageContent(
                            url=url, title=ext.title, content=ext.content,
                            word_count=ext.word_count, success=True,
                        )
                except Exception as e:
                    logger.warning(f"Trafilatura failed for {url}: {e}")

        # Playwright fallback
        still_failed = [(i, url) for i, (url, c) in enumerate(zip(urls, contents)) if not c.success]
        pw = self._get_playwright_browser() if self.use_playwright else None
        if still_failed and pw:
            logger.info(f"Playwright fallback for {len(still_failed)} URLs")
            pw_urls = [url for _, url in still_failed]
            pw_results = await pw.fetch_batch(pw_urls, delay=1.5)
            for (i, url), pr in zip(still_failed, pw_results):
                if pr.success:
                    contents[i] = PageContent(
                        url=pr.url, title=pr.title, content=pr.content,
                        word_count=pr.word_count, success=True,
                    )

        return contents

    def _collect_volume_candidates(
        self,
        context: WritingContext,
        search_results: Optional[List[Dict[str, Any]]] = None,
        max_keywords: int = 80,
    ) -> List[str]:
        """
        Collect short keywords suitable for volume checking.

        Shared between _fetch_keyword_metrics and _cluster_keywords to ensure
        volume data matches what clustering consumes (EDI-120).

        Sources: topic, queries (≤5 words), PAA questions (≤5 words),
        Related Searches (≤5 words), brief.target_terms.

        Note: Pillar Topvisor expansion keywords are NOT included here —
        they are added inside _cluster_keywords after this step (known limitation,
        pillar flow not yet in production).
        """
        seen: Set[str] = set()
        candidates: List[str] = []

        def _add(kw: str) -> None:
            key = kw.lower().strip()
            if key and key not in seen and len(key) >= 2:
                seen.add(key)
                candidates.append(kw.strip())

        # 1. Topic (always)
        _add(context.topic)

        # 2. Query planner phrases — only short ones (≤5 words)
        if context.queries:
            for q in context.queries.queries:
                if len(q.query.split()) <= 5:
                    _add(q.query)

        # 3. PAA questions and Related Searches from Serper results
        results = search_results or []
        for result in results:
            # Related Searches — often 2-4 word real queries
            for rs in result.get("relatedSearches", []):
                query = rs.get("query", "")
                if query and len(query.split()) <= 5:
                    _add(query)
            # PAA questions — filter to short ones
            for paa in result.get("peopleAlsoAsk", []):
                question = paa.get("question", "")
                if question and len(question.split()) <= 5:
                    _add(question)

        # 4. Brief target_terms (if available)
        brief = context.config.get("brief")
        if brief:
            brief_data = brief if isinstance(brief, dict) else brief.to_dict()
            for term in brief_data.get("target_terms", []):
                _add(term)

        # Cap at max_keywords
        if len(candidates) > max_keywords:
            candidates = candidates[:max_keywords]

        logger.info(f"Volume candidates: {len(candidates)} keywords collected")
        return candidates

    async def _fetch_keyword_metrics(self, context: WritingContext) -> KeywordMetricsResult:
        """
        Fetch keyword metrics via VolumeProvider (Wordstat / Rush / Composite).

        Uses _collect_volume_candidates() for real short keywords instead of
        synthetic LLM-generated research queries (EDI-120 fix).
        """
        keywords = self._collect_volume_candidates(context, context.search_results)

        location_name = context.region.lower()
        lang = "ru" if location_name in ["ru", "russia", "kz"] else "en"

        if not self.volume_provider:
            return KeywordMetricsResult(metrics={}, source="none")

        try:
            results = await self.volume_provider.get_volumes(keywords, language_code=lang)
            source = self.volume_provider.source_name
            metrics = {}
            for vr in results:
                metrics[vr.keyword.lower()] = KeywordMetricsData(
                    keyword=vr.keyword,
                    search_volume=vr.volume,
                    difficulty=vr.difficulty,
                    cpc=vr.cpc,
                    competition=vr.competition,
                    competition_level=vr.competition_level,
                )
            logger.info(f"Fetched {len(metrics)} keyword metrics via {source}")
            return KeywordMetricsResult(metrics=metrics, source=source)
        except Exception as e:
            logger.error(f"VolumeProvider ({self.volume_provider.source_name}) error: {e}")
            return KeywordMetricsResult(metrics={}, source="error")

    def _create_minimal_search_results(self, context: WritingContext) -> List[Dict[str, Any]]:
        """Create minimal search results when no API is available."""
        return [
            {
                "query": q.query,
                "purpose": q.purpose,
                "organic": [],
                "note": "No search API configured - using LLM knowledge only",
            }
            for q in context.queries.queries
        ]

    def _truncate_for_save(
        self,
        search_results: List[Dict[str, Any]],
        max_content_length: int = 500,
    ) -> List[Dict[str, Any]]:
        """
        Truncate page_content for saving to avoid huge files.

        Args:
            search_results: Full search results
            max_content_length: Maximum characters for page_content

        Returns:
            Truncated copy of search results
        """
        import copy
        results = copy.deepcopy(search_results)

        for result in results:
            for organic in result.get("organic", []):
                if "page_content" in organic:
                    content = organic["page_content"]
                    if len(content) > max_content_length:
                        organic["page_content"] = content[:max_content_length] + "... [truncated for save]"

        return results

    def _analyze_competitors(self, search_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze competitor pages from search results.

        Extracts structural metrics (headings, word counts) from organic results
        that have page_content. Deduplicates by URL, keeping the best position.

        Returns:
            Dict with pages list and aggregate stats.
        """
        # Deduplicate organic results by URL, keep best position
        seen: Dict[str, Dict[str, Any]] = {}
        for result in search_results:
            for i, org in enumerate(result.get("organic", [])):
                url = org.get("link", "")
                if not url:
                    continue
                position = org.get("position", i + 1)
                if url not in seen or position < seen[url].get("position", 999):
                    seen[url] = {
                        "url": url,
                        "title": org.get("title", ""),
                        "position": position,
                        "snippet": org.get("snippet", ""),
                        "page_content": org.get("page_content"),
                        "page_word_count": org.get("page_word_count"),
                    }

        # Sort by position
        pages = sorted(seen.values(), key=lambda x: x.get("position", 999))

        # Extract headings and compute stats for pages with content
        analyzed_pages = []
        word_counts = []

        for page in pages[:10]:  # Top 10 unique URLs
            entry = {
                "url": page["url"],
                "title": page["title"],
                "position": page["position"],
            }

            content = page.get("page_content")
            wc = page.get("page_word_count")

            if content:
                # Extract H2/H3 headings via regex
                headings = re_module.findall(r'^(#{2,3})\s+(.+)$', content, re_module.MULTILINE)
                entry["headings"] = [
                    {"level": "h2" if h[0] == "##" else "h3", "text": h[1].strip()}
                    for h in headings
                ]
                entry["word_count"] = wc or len(content.split())
                word_counts.append(entry["word_count"])
            else:
                entry["headings"] = []
                entry["word_count"] = wc or 0
                if wc and wc > 0:
                    word_counts.append(wc)

            analyzed_pages.append(entry)

        # Compute aggregate stats
        stats: Dict[str, Any] = {}
        if word_counts:
            stats["avg_word_count"] = round(sum(word_counts) / len(word_counts))
            stats["min_word_count"] = min(word_counts)
            stats["max_word_count"] = max(word_counts)
        else:
            stats["avg_word_count"] = 0
            stats["min_word_count"] = 0
            stats["max_word_count"] = 0

        # Find common headings (appear in 2+ pages)
        heading_counts: Dict[str, int] = {}
        for page in analyzed_pages:
            seen_in_page = set()
            for h in page.get("headings", []):
                text_lower = h["text"].lower().strip()
                if text_lower not in seen_in_page:
                    heading_counts[text_lower] = heading_counts.get(text_lower, 0) + 1
                    seen_in_page.add(text_lower)

        common_headings = [
            h for h, count in heading_counts.items() if count >= 2
        ]
        stats["common_headings"] = sorted(common_headings)

        logger.info(
            f"Competitor analysis: {len(analyzed_pages)} pages, "
            f"avg {stats['avg_word_count']} words, "
            f"{len(common_headings)} common headings"
        )

        return {
            "pages": analyzed_pages,
            "stats": stats,
        }

    async def _pack_facts(
        self,
        context: WritingContext,
        competitor_pages: Optional[Dict[str, Any]] = None,
    ) -> tuple[ResearchResult, int, int, int]:
        """Pack search results into structured research pack (v2 with claim_bank, unique_angle, etc.)."""
        prompt_template = self._load_prompt("research_packer_v2")

        intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
        search_json = json.dumps(context.search_results, ensure_ascii=False, indent=2)

        prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
        prompt = prompt.replace("{{search_and_fetch_results_json}}", search_json)

        # Add keyword metrics context if available
        if context.keyword_metrics and context.keyword_metrics.metrics:
            metrics_summary = self._format_keyword_metrics_for_prompt(context.keyword_metrics)
            prompt = prompt.replace(
                "{{keyword_metrics_json}}",
                metrics_summary,
            )
        else:
            prompt = prompt.replace("{{keyword_metrics_json}}", "null")

        # Add competitor pages analysis if available
        if competitor_pages and competitor_pages.get("pages"):
            prompt = prompt.replace(
                "{{competitor_pages_json}}",
                json.dumps(competitor_pages, ensure_ascii=False, indent=2),
            )
        else:
            prompt = prompt.replace("{{competitor_pages_json}}", "null")

        # Add existing posts for cluster overlap analysis
        if context.existing_posts:
            existing_posts_summary = self._format_existing_posts_for_prompt(context.existing_posts)
            prompt = prompt.replace(
                "{{existing_posts_json}}",
                existing_posts_summary,
            )
        else:
            prompt = prompt.replace("{{existing_posts_json}}", "null")

        # Inject factual mode instructions
        factual_mode = context.config.get("factual_mode", "default")
        if factual_mode == "kb_priority":
            prompt += "\n\nВАЖНО: Приоритет своей фактуры (is_knowledge_base=true). Используй факты из KB в первую очередь. Открытые источники — как дополнение. Помечай в sources origin: 'kb' для фактов из KB и 'web' для остальных."
        elif factual_mode == "kb_only":
            prompt += "\n\nВАЖНО: СТРОГО используй только факты из собственной базы знаний (is_knowledge_base=true). Не включай факты из открытых веб-источников. Все sources должны иметь origin: 'kb'."

        response_text, in_t, out_t = self._call_llm(
            prompt,
            max_tokens=12000,
            temperature=0.5,
        )
        tokens = in_t + out_t

        data = self._parse_json_response(response_text)

        # Ensure v2 fields are present (graceful degradation)
        if "claim_bank" not in data or data["claim_bank"] is None:
            data["claim_bank"] = {"allowed_claims": [], "disallowed_claim_patterns": []}
            logger.warning("claim_bank missing from LLM response, using empty default")
        if "unique_angle" not in data or data["unique_angle"] is None:
            data["unique_angle"] = {
                "article_role": "cluster",
                "primary_intent": context.intent.primary_intent if context.intent else "",
                "differentiators": [],
                "must_not_cover": [],
            }
            logger.warning("unique_angle missing from LLM response, using empty default")
        if "example_snippets" not in data:
            data["example_snippets"] = []
        if "terminology_canon" not in data or data["terminology_canon"] is None:
            data["terminology_canon"] = {"terms": {}, "do_not_use": []}
            logger.warning("terminology_canon missing from LLM response, using empty default")
        if "cluster_overlap_map" not in data:
            data["cluster_overlap_map"] = []

        return ResearchResult.from_dict(data), tokens, in_t, out_t

    def _format_existing_posts_for_prompt(self, existing_posts: List[Dict[str, Any]]) -> str:
        """Format existing posts for inclusion in research packer prompt."""
        if not existing_posts:
            return "null"

        items = []
        for post in existing_posts:
            items.append({
                "title": post.get("title", ""),
                "slug": post.get("slug", ""),
                "excerpt": post.get("excerpt", post.get("custom_excerpt", ""))[:300],
                "url": post.get("url", ""),
            })

        return json.dumps(items, ensure_ascii=False, indent=2)

    def _format_keyword_metrics_for_prompt(self, metrics: KeywordMetricsResult) -> str:
        """Format keyword metrics for inclusion in prompt."""
        if not metrics.metrics:
            return "null"

        items = []
        for keyword, data in metrics.metrics.items():
            items.append({
                "keyword": data.keyword,
                "volume": data.search_volume,
                "difficulty": data.difficulty,
                "competition": data.competition_level,
            })

        # Sort by volume descending
        items.sort(key=lambda x: x["volume"], reverse=True)

        return json.dumps(items[:20], ensure_ascii=False, indent=2)

    async def _cluster_keywords(
        self,
        context: WritingContext,
    ) -> tuple[Optional[KeywordClusteringResult], int, int, int]:
        """
        Cluster keywords by semantic similarity using LLM.

        Uses _collect_volume_candidates() as base (short keywords with volume data),
        then adds longer phrases from PAA/Related/queries for clustering context.

        Returns:
            (KeywordClusteringResult or None, tokens_used)
        """
        # Start with volume-checked candidates (short keywords, ≤5 words)
        short_keywords = self._collect_volume_candidates(context, context.search_results)
        all_keywords: Set[str] = set(short_keywords)

        # Add longer phrases (>5 words) from queries, PAA, Related —
        # these won't have volume data but are useful for clustering context
        if context.queries:
            for q in context.queries.queries:
                all_keywords.add(q.query)

        if context.search_results:
            for result in context.search_results:
                for rs in result.get("relatedSearches", []):
                    query = rs.get("query", "")
                    if query:
                        all_keywords.add(query)
                for paa in result.get("peopleAlsoAsk", []):
                    question = paa.get("question", "")
                    if question:
                        all_keywords.add(question)

        # ── Topvisor deep research for pillar articles ──
        is_pillar = context.config.get("brief", {}).get("role") == "pillar"
        topvisor_client = context.config.get("_topvisor_client")
        if is_pillar and topvisor_client:
            try:
                import asyncio as _asyncio
                logger.info("Pillar article: running Topvisor keyword research")
                await topvisor_client.import_keywords(
                    keywords=[context.topic],
                    group_name="pillar-research",
                )
                is_ru = context.region.lower() in ("ru", "russia", "kz")
                await topvisor_client.research_keywords(
                    seed_keywords=[context.topic],
                    region_key=213 if is_ru else 2840,
                    searcher_key=0 if is_ru else 1,
                )
                await _asyncio.sleep(20)
                kw_data = await topvisor_client.get_keywords(
                    fields=["name"],
                    limit=2000,
                )
                for kw in kw_data:
                    name = kw.get("name", "").strip()
                    if name:
                        all_keywords.add(name)
                logger.info(f"Topvisor pillar research: {len(all_keywords)} total keywords")
            except Exception as e:
                logger.warning(f"Topvisor pillar research failed: {e}")

        # Skip if too few keywords
        if len(all_keywords) < 5:
            logger.info(f"Skipping keyword clustering: only {len(all_keywords)} keywords")
            return None, 0, 0, 0

        # ── Keyword filter: rules + fuzzy dedup + optional LLM ──
        from .keyword_filter import KeywordFilter

        volume_map = {}
        if context.keyword_metrics:
            for kw in all_keywords:
                vol = context.keyword_metrics.get_volume(kw)
                if vol > 0:
                    volume_map[kw.lower().strip()] = vol

        lang = "ru" if context.region.lower() in ("ru", "russia", "kz") else "en"
        use_llm_filter = context.config.get("keyword_filter_llm", True)

        kw_filter = KeywordFilter(client=self.client, model=self.model)
        filtered_keywords = kw_filter.filter(
            keywords=all_keywords,
            topic=context.topic,
            language=lang,
            volume_map=volume_map,
            use_llm=use_llm_filter,
        )

        if len(filtered_keywords) < 5:
            logger.info(f"Too few keywords after filter ({len(filtered_keywords)}), using unfiltered")
            filtered_keywords = list(all_keywords)

        # Build keywords list with volume if available
        keywords_data = []
        for kw in filtered_keywords:
            entry = {"keyword": kw}
            if context.keyword_metrics:
                vol = context.keyword_metrics.get_volume(kw)
                if vol > 0:
                    entry["volume"] = vol
            keywords_data.append(entry)

        # Sort by volume descending (keywords with volume first)
        keywords_data.sort(key=lambda x: x.get("volume", 0), reverse=True)

        prompt_template = self._load_prompt("keyword_clustering_v1")
        prompt = prompt_template.replace("{{topic}}", context.topic)
        prompt = prompt.replace("{{primary_intent}}", context.intent.primary_intent)
        prompt = prompt.replace("{{keywords_json}}", json.dumps(keywords_data, ensure_ascii=False, indent=2))

        try:
            response_text, in_t, out_t = self._call_llm(
                prompt,
                max_tokens=2048,
                temperature=0.5,
            )

            data = self._parse_json_response(response_text)
            result = KeywordClusteringResult.from_dict(data)

            logger.info(
                f"Keyword clustering: 1 primary + {len(result.secondary_clusters)} secondary clusters, "
                f"{len(result.unclustered)} unclustered"
            )

            return result, in_t + out_t, in_t, out_t

        except Exception as e:
            logger.warning(f"Keyword clustering failed: {e}")
            return None, 0, 0, 0

    def _select_monitoring_keywords(
        self,
        context: WritingContext,
        clusters: Optional[KeywordClusteringResult],
        max_keywords: int = 10,
    ) -> List[str]:
        """
        Select keywords for SERP position monitoring after clustering.

        Takes primary_keyword from each cluster + top by volume.
        """
        candidates: Dict[str, int] = {}  # keyword -> volume

        def _safe_int(v) -> int:
            try:
                return int(v or 0)
            except (ValueError, TypeError):
                return 0

        # candidates: normalized_key -> (display_form, volume)
        norm_candidates: Dict[str, tuple] = {}

        def _add_candidate(kw: str, vol: int) -> None:
            key = kw.strip().lower()
            if not key:
                return
            existing_vol = norm_candidates.get(key, ("", 0))[1]
            if vol > existing_vol or key not in norm_candidates:
                norm_candidates[key] = (kw.strip(), max(vol, existing_vol))

        # From clusters: primary_keyword of each
        if clusters:
            pk = clusters.primary_cluster.primary_keyword
            _add_candidate(pk, _safe_int(clusters.primary_cluster.total_volume))
            for sc in clusters.secondary_clusters:
                _add_candidate(sc.primary_keyword, _safe_int(sc.total_volume))

        # From keyword_metrics: top by volume
        if context.keyword_metrics:
            for key, data in context.keyword_metrics.metrics.items():
                vol = _safe_int(data.search_volume)
                if vol > 0:
                    _add_candidate(data.keyword, vol)

        # Sort by volume descending, take top N
        sorted_kws = sorted(norm_candidates.values(), key=lambda x: x[1], reverse=True)
        selected = [display for display, _ in sorted_kws[:max_keywords]]

        # Always include topic if not already present
        topic_lower = context.topic.lower().strip()
        if topic_lower not in norm_candidates:
            selected.insert(0, context.topic)
            if len(selected) > max_keywords:
                selected = selected[:max_keywords]

        logger.info(f"Monitoring keywords selected: {len(selected)}")
        return selected
