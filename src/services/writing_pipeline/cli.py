#!/usr/bin/env python3
"""
CLI for running the Writing Pipeline.

Usage:
    python -m src.services.writing_pipeline.cli "Topic to write about" --output-dir ./output

Environment variables:
    ANTHROPIC_API_KEY: Required
    SERPER_API_KEY: Optional (for SERP data)
    JINA_API_KEY: Optional (for higher Jina Reader limits)
    DATAFORSEO_LOGIN: Optional (for keyword metrics)
    DATAFORSEO_PASSWORD: Optional (for keyword metrics)
"""

import asyncio
import argparse
import os
import sys
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.services.writing_pipeline.core.runner import PipelineRunner


async def main():
    parser = argparse.ArgumentParser(
        description="Generate an article using the multi-stage writing pipeline"
    )
    parser.add_argument(
        "topic",
        help="Article topic"
    )
    parser.add_argument(
        "--region",
        default="ru",
        help="Target region (default: ru)"
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        help="Output directory for intermediate results"
    )
    parser.add_argument(
        "--no-intermediate",
        action="store_true",
        help="Don't save intermediate results"
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Claude model to use"
    )
    parser.add_argument(
        "--no-paa",
        action="store_true",
        help="Disable PAA query expansion"
    )
    parser.add_argument(
        "--no-content-fetch",
        action="store_true",
        help="Disable full page content fetching"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5,
        help="Max pages to fetch content from (default: 5)"
    )
    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="Disable Playwright headless browser fallback"
    )

    args = parser.parse_args()

    # Get API keys from environment
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY environment variable is required")
        sys.exit(1)

    serper_key = os.environ.get("SERPER_API_KEY")
    jina_key = os.environ.get("JINA_API_KEY")
    dataforseo_login = os.environ.get("DATAFORSEO_LOGIN")
    dataforseo_password = os.environ.get("DATAFORSEO_PASSWORD")
    proxy_url = os.environ.get("ANTHROPIC_PROXY_URL")
    proxy_secret = os.environ.get("ANTHROPIC_PROXY_SECRET")
    ghost_url = os.environ.get("GHOST_URL")
    ghost_admin_key = os.environ.get("GHOST_ADMIN_KEY")
    database_url = os.environ.get("DATABASE_URL")

    # Generate output directory if not specified
    output_dir = args.output_dir
    if not output_dir and not args.no_intermediate:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = "".join(c if c.isalnum() else "_" for c in args.topic[:30])
        output_dir = f"./pipeline_output/{timestamp}_{safe_topic}"

    print(f"Topic: {args.topic}")
    print(f"Region: {args.region}")
    print(f"Model: {args.model}")
    print(f"Output: {output_dir or '(none)'}")
    print(f"Serper API: {'yes' if serper_key else 'no'}")
    print(f"Jina Reader: {'yes (with key)' if jina_key else 'yes (free tier)'}")
    print(f"DataForSEO: {'yes' if dataforseo_login else 'no'}")
    print(f"PAA expansion: {'yes' if not args.no_paa else 'no'}")
    print(f"Content fetch: {'yes' if not args.no_content_fetch else 'no'}")
    print(f"Playwright: {'yes' if not args.no_playwright else 'no'}")
    print(f"Max pages: {args.max_pages}")
    print(f"Internal linking DB: {'yes' if database_url else 'no'}")
    print(f"Ghost CMS: {'yes' if ghost_url else 'no'}")
    print("-" * 50)

    # Initialize and run pipeline
    runner = PipelineRunner(
        anthropic_api_key=anthropic_key,
        serper_api_key=serper_key,
        jina_api_key=jina_key,
        dataforseo_login=dataforseo_login,
        dataforseo_password=dataforseo_password,
        model=args.model,
        proxy_url=proxy_url,
        proxy_secret=proxy_secret,
        ghost_url=ghost_url,
        ghost_admin_key=ghost_admin_key,
        database_url=database_url,
    )

    # Pipeline configuration
    config = {
        "expand_paa": not args.no_paa,
        "fetch_page_content": not args.no_content_fetch,
        "max_pages_to_fetch": args.max_pages,
        "max_paa_queries": 3,
        "use_playwright": not args.no_playwright,
    }

    print("Starting pipeline...")
    result = await runner.run(
        topic=args.topic,
        region=args.region,
        output_dir=output_dir,
        save_intermediate=not args.no_intermediate,
        config=config,
    )

    print("-" * 50)
    print(f"Title: {result.title}")
    print(f"Subtitle: {result.subtitle}")
    print(f"Word count: {result.word_count}")
    if result.meta:
        print(f"Meta title: {result.meta.meta_title}")
        print(f"Meta desc: {result.meta.meta_description}")
        print(f"Slug: {result.meta.slug}")
    if result.linking_data:
        kw_count = len(result.linking_data.get("keywords", []))
        print(f"Linking keywords: {kw_count}")
    print(f"Stages: {', '.join(result.stages_completed)}")
    print(f"Duration: {result.started_at} - {result.completed_at}")

    if output_dir:
        print(f"\nOutput saved to: {output_dir}")
        print(f"  - 00_summary.json")
        print(f"  - 01_intent.json")
        print(f"  - 02_queries.json")
        print(f"  - 03_search_results.json")
        print(f"  - 03a_keyword_metrics.json (if DataForSEO configured)")
        print(f"  - 04_research_pack.json")
        print(f"  - 05_outline.json")
        print(f"  - 06_draft.md")
        print(f"  - 07_edited.md")
        print(f"  - 07b_linking.json (if internal linking DB configured)")
        print(f"  - 08_meta.json")

    print("\n=== FINAL ARTICLE ===\n")
    print(result.article_md)


if __name__ == "__main__":
    asyncio.run(main())
