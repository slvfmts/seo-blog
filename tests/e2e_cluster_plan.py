#!/usr/bin/env python3
"""
E2E test: full ClusterPlanner.plan() — real Serper + Wordstat + Rush + Claude.

Run on server:
    docker compose exec api python tests/e2e_cluster_plan.py
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

TOPIC = "контент-маркетинг для бизнеса"
REGION = "ru"
TARGET_ARTICLES = 10


async def main():
    from src.config.settings import get_settings
    from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider

    settings = get_settings()

    # Init Anthropic client
    import anthropic
    client_kwargs = {}
    if settings.anthropic_proxy_url:
        client_kwargs["base_url"] = settings.anthropic_proxy_url
        if settings.anthropic_proxy_secret:
            client_kwargs["default_headers"] = {
                "X-Proxy-Secret": settings.anthropic_proxy_secret,
            }
    anthropic_client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        **client_kwargs,
    )

    # Init VolumeProvider
    volume_provider = get_volume_provider(REGION, settings)
    print(f"VolumeProvider: {volume_provider.source_name}")

    # Init ClusterPlanner
    from src.services.cluster_planner import ClusterPlanner
    planner = ClusterPlanner(
        anthropic_client=anthropic_client,
        model="claude-sonnet-4-20250514",
        serper_api_key=settings.serper_api_key,
        volume_provider=volume_provider,
    )

    print(f"=" * 70)
    print(f"ClusterPlanner E2E Test")
    print(f"Topic: {TOPIC}")
    print(f"Region: {REGION}")
    print(f"Target articles: {TARGET_ARTICLES}")
    print(f"=" * 70)
    print()

    t0 = time.time()
    plan = await planner.plan(
        big_topic=TOPIC,
        region=REGION,
        target_count=TARGET_ARTICLES,
    )
    elapsed = time.time() - t0

    # Print results
    print(f"\n{'=' * 70}")
    print(f"RESULTS (elapsed: {elapsed:.0f}s)")
    print(f"{'=' * 70}\n")

    # Discovered keywords
    all_kw = plan.discovered_keywords or []
    with_volume = [kw for kw in all_kw if kw.get("volume", 0) > 0]
    print(f"Discovered keywords: {len(all_kw)} total, {len(with_volume)} with volume > 0")

    # Top 20 by volume
    sorted_kw = sorted(all_kw, key=lambda x: x.get("volume", 0), reverse=True)
    print(f"\nTop 20 keywords by volume:")
    for i, kw in enumerate(sorted_kw[:20], 1):
        print(f"  {i:2d}. [{kw['volume']:>6d}] {kw['keyword']}")

    # Pillar
    p = plan.pillar
    print(f"\n{'─' * 70}")
    print(f"PILLAR: {p.title_candidate}")
    print(f"  Intent: {p.primary_intent}")
    print(f"  Target terms: {len(p.target_terms)}")
    print(f"  Est. volume: {p.estimated_volume}")
    print(f"  Questions: {len(p.must_answer_questions)}")
    for q in p.must_answer_questions[:5]:
        print(f"    - {q}")
    print(f"  Top target terms:")
    for t in p.target_terms[:10]:
        print(f"    - {t}")

    # Cluster articles
    print(f"\n{'─' * 70}")
    print(f"CLUSTER ARTICLES: {len(plan.cluster_articles)}")
    print(f"{'─' * 70}")
    for i, a in enumerate(plan.cluster_articles, 1):
        print(f"\n  {i}. {a.title_candidate}")
        print(f"     Intent: {a.primary_intent} | Terms: {len(a.target_terms)} | Volume: {a.estimated_volume}")
        print(f"     Questions: {', '.join(a.must_answer_questions[:3])}...")
        print(f"     Top terms: {', '.join(a.target_terms[:5])}")

    # Summary
    total_terms = len(p.target_terms) + sum(len(a.target_terms) for a in plan.cluster_articles)
    total_volume = (p.estimated_volume or 0) + sum(a.estimated_volume or 0 for a in plan.cluster_articles)
    print(f"\n{'=' * 70}")
    print(f"SUMMARY")
    print(f"  Discovered keywords:    {len(all_kw)}")
    print(f"  With volume > 0:        {len(with_volume)}")
    print(f"  Pillar:                 1")
    print(f"  Cluster articles:       {len(plan.cluster_articles)}")
    print(f"  Total target terms:     {total_terms}")
    print(f"  Total est. volume:      {total_volume:,}")
    print(f"  Time:                   {elapsed:.0f}s")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
