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
from typing import List, Dict, Any, Optional, Set

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import QueryPlannerResult, ResearchResult, KeywordMetricsResult, KeywordMetricsData

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
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.serper_api_key = serper_api_key
        self.jina_api_key = jina_api_key
        self.dataforseo_login = dataforseo_login
        self.dataforseo_password = dataforseo_password

        # Lazy-loaded clients
        self._jina_reader = None
        self._trafilatura = None
        self._dataforseo = None

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
                search_results = await self._fetch_page_contents(search_results, max_pages)

            context.search_results = search_results

            # Save search results if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "03_search_results.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    # Truncate page_content for saving (can be very large)
                    results_for_save = self._truncate_for_save(search_results)
                    json.dump(results_for_save, f, ensure_ascii=False, indent=2)

            # Step 5: Fetch keyword metrics (if DataForSEO configured)
            if self._get_dataforseo():
                keyword_metrics = await self._fetch_keyword_metrics(context)
                context.keyword_metrics = keyword_metrics

                if context.save_intermediate and context.output_dir:
                    output_path = os.path.join(context.output_dir, "03a_keyword_metrics.json")
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(keyword_metrics.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 6: Pack facts
            research, tokens = await self._pack_facts(context)
            context.research = research
            total_tokens += tokens

            # Save research pack if configured
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "04_research_pack.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(research.to_dict(), f, ensure_ascii=False, indent=2)

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
    ) -> List[Dict[str, Any]]:
        """
        Fetch full page content for top organic results.

        Args:
            search_results: Search results with organic listings
            max_pages: Maximum number of pages to fetch

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

        logger.info(f"Fetching content from {len(urls_to_fetch)} pages: {urls_to_fetch}")

        # Try Jina Reader first
        jina = self._get_jina_reader()
        contents = await jina.fetch_batch(urls_to_fetch, max_concurrent=3)

        # Fallback to Trafilatura for failed fetches
        trafilatura = self._get_trafilatura()
        failed_urls = [
            (url, content)
            for url, content in zip(urls_to_fetch, contents)
            if not content.success
        ]

        if failed_urls and trafilatura:
            logger.info(f"Using Trafilatura fallback for {len(failed_urls)} URLs")
            for url, _ in failed_urls:
                try:
                    extracted = await trafilatura.extract_from_url(url)
                    if extracted.success:
                        # Find and update the content
                        for i, (u, c) in enumerate(zip(urls_to_fetch, contents)):
                            if u == url:
                                # Convert to PageContent format
                                from ..data_sources.jina_reader import PageContent
                                contents[i] = PageContent(
                                    url=url,
                                    title=extracted.title,
                                    content=extracted.content,
                                    word_count=extracted.word_count,
                                    success=True,
                                )
                                break
                except Exception as e:
                    logger.warning(f"Trafilatura fallback failed for {url}: {e}")

        # Log results and add content to search results
        success_count = sum(1 for c in contents if c.success)
        logger.info(f"Content fetch results: {success_count}/{len(contents)} successful")
        for url, content in zip(urls_to_fetch, contents):
            if not content.success:
                logger.warning(f"Failed to fetch {url}: {content.error}")

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

    async def _pack_facts(self, context: WritingContext) -> tuple[ResearchResult, int]:
        """Pack search results into structured research pack."""
        prompt_template = self._load_prompt("research_packer_v1")

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

        response_text, tokens = self._call_llm(
            prompt,
            max_tokens=8192,
            temperature=0.5,
        )

        data = self._parse_json_response(response_text)
        return ResearchResult.from_dict(data), tokens

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
