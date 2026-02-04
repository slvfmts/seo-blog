"""
Research Stage - Generates queries, fetches data, and packs facts.

This is a multi-step stage:
1. Query Planner: Generates search queries based on intent
2. Search Runner: Executes searches (Serper.dev or WebSearch)
3. Fact Packer: Processes results into structured research pack
"""

import json
from typing import List, Dict, Any, Optional

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import QueryPlannerResult, ResearchResult


class ResearchStage(WritingStage):
    """
    Stage 2: Research

    Three sub-steps:
    1. Query Planner - generates search queries from intent
    2. Search Runner - executes searches via data sources
    3. Fact Packer - structures results into research pack
    """

    def __init__(self, *args, serper_api_key: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.serper_api_key = serper_api_key
        self._serper_source = None

    @property
    def name(self) -> str:
        return "research"

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
                import os
                output_path = os.path.join(context.output_dir, "02_queries.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(queries.to_dict(), f, ensure_ascii=False, indent=2)

            # Step 2: Execute searches
            search_results = await self._execute_searches(context)
            context.search_results = search_results

            # Save search results if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "03_search_results.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(search_results, f, ensure_ascii=False, indent=2)

            # Step 3: Pack facts
            research, tokens = await self._pack_facts(context)
            context.research = research
            total_tokens += tokens

            # Save research pack if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "04_research_pack.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(research.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=total_tokens,
                metadata={
                    "queries_count": len(queries.queries),
                    "sources_count": len(research.sources),
                    "facts_count": len(research.facts),
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
            # Use Serper.dev for rich SERP data
            results = await self._search_with_serper(context)
        else:
            # Fallback: Create minimal results for LLM to work with
            results = self._create_minimal_search_results(context)

        return results

    async def _search_with_serper(self, context: WritingContext) -> List[Dict[str, Any]]:
        """Execute searches using Serper.dev API."""
        import httpx

        results = []

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
                            "gl": "ru" if context.region.lower() in ["ru", "россия", "russia"] else "us",
                            "hl": "ru" if context.region.lower() in ["ru", "россия", "russia"] else "en",
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
                    # Log error but continue with other queries
                    results.append({
                        "query": query.query,
                        "purpose": query.purpose,
                        "error": str(e),
                        "organic": [],
                    })

        return results

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

    async def _pack_facts(self, context: WritingContext) -> tuple[ResearchResult, int]:
        """Pack search results into structured research pack."""
        prompt_template = self._load_prompt("research_packer_v1")

        intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
        search_json = json.dumps(context.search_results, ensure_ascii=False, indent=2)

        prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
        prompt = prompt.replace("{{search_and_fetch_results_json}}", search_json)

        response_text, tokens = self._call_llm(
            prompt,
            max_tokens=8192,
            temperature=0.5,  # Lower temperature for more factual output
        )

        data = self._parse_json_response(response_text)
        return ResearchResult.from_dict(data), tokens
