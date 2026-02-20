"""
Research Stage - Generates queries, fetches data, and packs facts.

This is a multi-step stage:
1. Query Planner: Generates search queries based on intent
2. Search Runner: Executes searches (Serper.dev)
3. Content Fetcher: Fetches full page content for top results (Jina Reader)
4. PAA Expansion: Generates additional queries from PAA questions
5. Keyword Metrics: Fetches search volume and difficulty (DataForSEO)
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
    - Keyword metrics from DataForSEO
    - Trafilatura fallback for content extraction
    """

    def __init__(
        self,
        *args,
        serper_api_key: Optional[str] = None,
        jina_api_key: Optional[str] = None,
        dataforseo_login: Optional[str] = None,
        dataforseo_password: Optional[str] = None,
        use_playwright: bool = True,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.serper_api_key = serper_api_key
        self.jina_api_key = jina_api_key
        self.dataforseo_login = dataforseo_login
        self.dataforseo_password = dataforseo_password
        self.use_playwright = use_playwright

        # Lazy-loaded clients
        self._jina_reader = None
        self._trafilatura = None
        self._dataforseo = None
        self._playwright_browser = None

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

    def _get_dataforseo(self):
        """Lazy-load DataForSEO client."""
        if self._dataforseo is None and self.dataforseo_login and self.dataforseo_password:
            from ..data_sources.dataforseo import DataForSEO
            self._dataforseo = DataForSEO(
                login=self.dataforseo_login,
                password=self.dataforseo_password,
            )
        return self._dataforseo

    def _get_playwright_browser(self):
        """Lazy-load Playwright browser."""
        if self._playwright_browser is None and self.use_playwright:
            try:
                from ..data_sources.playwright_browser import PlaywrightBrowser
                self._playwright_browser = PlaywrightBrowser(max_concurrent=2, timeout_ms=30000)
            except Exception as e:
                logger.warning(f"Playwright not available: {e}")
        return self._playwright_browser

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute research stage."""
        log = context.start_stage(self.name)
        total_tokens = 0

        try:
            if context.intent is None:
                raise ValueError("Intent stage must be completed before research")

            # Step 1: Generate search queries
            queries, tokens = await self._generate_queries(context)
            context.queries = queries
            total_tokens += tokens

            # Save queries if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "02_queries.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(queries.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 2: Execute initial searches
            search_results = await self._execute_searches(context)

            # Step 3: PAA Expansion (if enabled)
            expand_paa = context.config.get("expand_paa", True)
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

            # Step 4b: Inject Knowledge Base documents (if attached)
            kb_docs = context.config.get("knowledge_base_docs", [])
            if kb_docs:
                kb_result = {
                    "query": context.topic,
                    "purpose": "knowledge_base",
                    "is_knowledge_base": True,
                    "organic": [{
                        "position": 0,
                        "title": doc["title"],
                        "link": f"kb://{doc['id']}",
                        "snippet": doc["content_text"][:300],
                        "page_content": doc["content_text"][:4000],
                        "page_word_count": doc.get("word_count", 0),
                        "is_knowledge_base": True,
                    } for doc in kb_docs],
                }
                search_results.insert(0, kb_result)
                logger.info(f"Injected {len(kb_docs)} KB documents into search results")

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

            # Step 5: Fetch keyword metrics (if DataForSEO configured)
            if self._get_dataforseo():
                keyword_metrics = await self._fetch_keyword_metrics(context)
                context.keyword_metrics = keyword_metrics

                if context.save_intermediate and context.output_dir:
                    output_path = os.path.join(context.output_dir, "03a_keyword_metrics.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(keyword_metrics.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 6: Pack facts (with competitor analysis)
            research, tokens = await self._pack_facts(context, competitor_pages)
            context.research = research
            total_tokens += tokens

            # Save research pack if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "04_research_pack.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(research.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 7: Keyword clustering
            clusters, cluster_tokens = await self._cluster_keywords(context)
            if clusters:
                context.research.keyword_clusters = clusters
                total_tokens += cluster_tokens

                if context.save_intermediate and context.output_dir:
                    output_path = os.path.join(context.output_dir, "04b_keyword_clusters.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(clusters.to_dict(), f, ensure_ascii=False, indent=2)

            # Calculate metadata
            pages_fetched = sum(
                1 for r in search_results
                for org in r.get("organic", [])
                if org.get("page_content")
            )

            context.complete_stage(
                tokens_used=total_tokens,
                metadata={
                    "queries_count": len(queries.queries),
                    "sources_count": len(research.sources),
                    "facts_count": len(research.facts),
                    "pages_fetched": pages_fetched,
                    "paa_expanded": expand_paa,
                    "has_keyword_metrics": context.keyword_metrics is not None,
                    "has_keyword_clusters": context.research.keyword_clusters is not None,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    async def _generate_queries(self, context: WritingContext) -> tuple[QueryPlannerResult, int]:
        """Generate search queries from intent."""
        prompt_template = self._load_prompt("research_queries_v1")
        intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
        prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)

        response_text, tokens = self._call_llm(
            prompt,
            max_tokens=1024,
            temperature=0.7,
        )

        data = self._parse_json_response(response_text)
        return QueryPlannerResult.from_dict(data), tokens

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
        gl = "ru" if context.region.lower() in ["ru", "россия", "russia"] else "us"
        hl = "ru" if context.region.lower() in ["ru", "россия", "russia"] else "en"

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

                    results.append({
                        "query": query.query,
                        "purpose": query.purpose,
                        "organic": serp_data.get("organic", []),
                        "peopleAlsoAsk": serp_data.get("peopleAlsoAsk", []),
                        "relatedSearches": serp_data.get("relatedSearches", []),
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
        gl = "ru" if context.region.lower() in ["ru", "россия", "russia"] else "us"
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

        For Russian regions, uses Trafilatura (direct requests) first since Jina Reader
        gets blocked with HTTP 451 by Russian websites. For other regions, uses Jina first.

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

        # Determine fetch strategy based on region
        # For Russian sites, use Trafilatura first (direct requests from our Russian server)
        # because Jina Reader gets blocked with HTTP 451
        is_russian = region.lower() in ["ru", "россия", "russia"]

        from ..data_sources.jina_reader import PageContent
        contents: List[PageContent] = []

        if is_russian:
            # Try Trafilatura first for Russian content
            trafilatura = self._get_trafilatura()
            if trafilatura:
                logger.info("Using Trafilatura (direct) for Russian content")
                extracted_list = await trafilatura.extract_batch(
                    urls_to_fetch,
                    max_concurrent=3,
                    delay_between=0.5,
                )
                contents = [
                    PageContent(
                        url=ext.url,
                        title=ext.title,
                        content=ext.content,
                        word_count=ext.word_count,
                        success=ext.success,
                        error=ext.error,
                    )
                    for ext in extracted_list
                ]
            else:
                # Fallback to Jina if Trafilatura not available
                jina = self._get_jina_reader()
                contents = await jina.fetch_batch(urls_to_fetch, max_concurrent=3)

            # For still-failed URLs, try Jina as backup
            jina = self._get_jina_reader()
            failed_urls = [
                (i, url) for i, (url, content) in enumerate(zip(urls_to_fetch, contents))
                if not content.success
            ]
            if failed_urls:
                logger.info(f"Trying Jina Reader for {len(failed_urls)} failed URLs")
                for i, url in failed_urls:
                    try:
                        result = await jina.fetch_content(url)
                        if result.success:
                            contents[i] = result
                    except Exception as e:
                        logger.warning(f"Jina fallback failed for {url}: {e}")
        else:
            # For non-Russian content, use Jina first (better quality)
            jina = self._get_jina_reader()
            contents = await jina.fetch_batch(urls_to_fetch, max_concurrent=3)

            # Fallback to Trafilatura for failed fetches
            trafilatura = self._get_trafilatura()
            failed_urls = [
                (i, url) for i, (url, content) in enumerate(zip(urls_to_fetch, contents))
                if not content.success
            ]

            if failed_urls and trafilatura:
                logger.info(f"Using Trafilatura fallback for {len(failed_urls)} URLs")
                for i, url in failed_urls:
                    try:
                        extracted = await trafilatura.extract_from_url(url)
                        if extracted.success:
                            contents[i] = PageContent(
                                url=url,
                                title=extracted.title,
                                content=extracted.content,
                                word_count=extracted.word_count,
                                success=True,
                            )
                    except Exception as e:
                        logger.warning(f"Trafilatura fallback failed for {url}: {e}")

        # Playwright fallback for still-failed URLs (both Russian and non-Russian)
        use_pw = self.use_playwright
        playwright_browser = self._get_playwright_browser() if use_pw else None
        if playwright_browser:
            still_failed = [
                (i, url) for i, (url, content) in enumerate(zip(urls_to_fetch, contents))
                if not content.success
            ]
            if still_failed:
                logger.info(f"Trying Playwright for {len(still_failed)} still-failed URLs")
                failed_urls_only = [url for _, url in still_failed]
                pw_results = await playwright_browser.fetch_batch(failed_urls_only, delay=1.5)
                for (i, url), pw_result in zip(still_failed, pw_results):
                    if pw_result.success:
                        contents[i] = PageContent(
                            url=pw_result.url,
                            title=pw_result.title,
                            content=pw_result.content,
                            word_count=pw_result.word_count,
                            success=True,
                        )

        # Log results and add content to search results
        success_count = sum(1 for c in contents if c.success)
        logger.info(f"Content fetch results: {success_count}/{len(contents)} successful")
        for url, content in zip(urls_to_fetch, contents):
            if not content.success:
                logger.warning(f"Failed to fetch {url}: {content.error}")

        # Get Jina reader for truncation utility (always available)
        jina = self._get_jina_reader()

        for url, content in zip(urls_to_fetch, contents):
            if content.success and url in url_to_result_indices:
                # Truncate content to avoid token limits
                truncated = jina.truncate_content(content.content, max_words=2000)

                for result_idx, org_idx in url_to_result_indices[url]:
                    search_results[result_idx]["organic"][org_idx]["page_content"] = truncated
                    search_results[result_idx]["organic"][org_idx]["page_word_count"] = content.word_count

        return search_results

    async def _fetch_keyword_metrics(self, context: WritingContext) -> KeywordMetricsResult:
        """
        Fetch keyword metrics from DataForSEO.

        Args:
            context: Pipeline context with queries

        Returns:
            KeywordMetricsResult with metrics for all queries
        """
        dataforseo = self._get_dataforseo()
        if not dataforseo:
            return KeywordMetricsResult(metrics={}, source="none")

        # Collect keywords from queries
        keywords = [q.query for q in context.queries.queries]

        # Add topic as keyword
        keywords.insert(0, context.topic)

        # Determine location code
        location_name = context.region.lower()

        try:
            result = await dataforseo.get_keyword_metrics(
                keywords=keywords,
                location_name=location_name,
                language_code="ru" if location_name in ["ru", "russia"] else "en",
            )

            if result.success:
                # Convert to contract format
                metrics = {}
                for kw in result.keywords:
                    metrics[kw.keyword.lower()] = KeywordMetricsData(
                        keyword=kw.keyword,
                        search_volume=kw.search_volume,
                        difficulty=kw.difficulty,
                        cpc=kw.cpc,
                        competition=kw.competition,
                        competition_level=kw.competition_level,
                    )
                return KeywordMetricsResult(metrics=metrics, source="dataforseo")
            else:
                logger.warning(f"DataForSEO error: {result.error}")
                return KeywordMetricsResult(metrics={}, source="error")

        except Exception as e:
            logger.error(f"Failed to fetch keyword metrics: {e}")
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
    ) -> tuple[ResearchResult, int]:
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

        response_text, tokens = self._call_llm(
            prompt,
            max_tokens=12000,
            temperature=0.5,
        )

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

        return ResearchResult.from_dict(data), tokens

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
    ) -> tuple[Optional[KeywordClusteringResult], int]:
        """
        Cluster keywords by semantic similarity using LLM.

        Collects all keywords from: queries, related searches, PAA questions, topic.
        Skips if fewer than 5 unique keywords.

        Returns:
            (KeywordClusteringResult or None, tokens_used)
        """
        # Collect all keywords
        all_keywords: Set[str] = set()
        all_keywords.add(context.topic)

        if context.queries:
            for q in context.queries.queries:
                all_keywords.add(q.query)

        if context.search_results:
            for result in context.search_results:
                # Related searches
                for rs in result.get("relatedSearches", []):
                    query = rs.get("query", "")
                    if query:
                        all_keywords.add(query)
                # PAA questions
                for paa in result.get("peopleAlsoAsk", []):
                    question = paa.get("question", "")
                    if question:
                        all_keywords.add(question)

        # Skip if too few keywords
        if len(all_keywords) < 5:
            logger.info(f"Skipping keyword clustering: only {len(all_keywords)} keywords")
            return None, 0

        # Build keywords list with volume if available
        keywords_data = []
        for kw in all_keywords:
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
            response_text, tokens = self._call_llm(
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

            return result, tokens

        except Exception as e:
            logger.warning(f"Keyword clustering failed: {e}")
            return None, 0
