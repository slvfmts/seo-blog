#!/usr/bin/env python3
"""
E2E test for SEO services — real API calls to Serper, Wordstat, Rush Analytics.
Anthropic is NOT called (only SEO data sources tested).

Run on server:
    docker compose exec api python tests/e2e_seo_services.py
"""

import asyncio
import json
import os
import sys
import time

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

TOPIC = "продвижение сайта в поисковых системах"
DOMAIN = "editors1.com"
REGION = "ru"

PASS = "✓ PASS"
FAIL = "✗ FAIL"
SKIP = "⊘ SKIP"


async def test_serper_search():
    """Test Serper.dev /search — real API call."""
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        return SKIP, "SERPER_API_KEY not set", {}

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": TOPIC, "gl": "ru", "hl": "ru", "num": 10},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            organic = data.get("organic", [])
            paa = data.get("peopleAlsoAsk", [])
            related = data.get("relatedSearches", [])

            details = {
                "organic_count": len(organic),
                "paa_count": len(paa),
                "related_count": len(related),
                "top_3": [o.get("title", "")[:60] for o in organic[:3]],
            }

            if len(organic) > 0:
                return PASS, f"{len(organic)} organic, {len(paa)} PAA, {len(related)} related", details
            else:
                return FAIL, "No organic results", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_serper_autocomplete():
    """Test Serper.dev /autocomplete."""
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        return SKIP, "SERPER_API_KEY not set", {}

    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://google.serper.dev/autocomplete",
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": TOPIC, "gl": "ru", "hl": "ru"},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            suggestions = data.get("suggestions", [])
            details = {
                "count": len(suggestions),
                "top_5": [s.get("value", "") for s in suggestions[:5]],
            }

            if len(suggestions) > 0:
                return PASS, f"{len(suggestions)} suggestions", details
            else:
                return FAIL, "No suggestions", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_yandex_wordstat():
    """Test Yandex Wordstat via YandexWordstatProvider."""
    key = os.environ.get("YANDEX_WORDSTAT_API_KEY", "")
    if not key:
        return SKIP, "YANDEX_WORDSTAT_API_KEY not set", {}

    try:
        from src.services.writing_pipeline.data_sources.wordstat import YandexWordstatProvider

        folder_id = os.environ.get("YANDEX_CLOUD_FOLDER_ID", "")
        provider = YandexWordstatProvider(api_key=key, folder_id=folder_id)

        keywords = [TOPIC, "seo оптимизация", "контент маркетинг"]
        results = await provider.get_volumes(keywords)

        details = {
            "results": [{"keyword": r.keyword, "volume": r.volume} for r in results],
        }

        found = sum(1 for r in results if r.volume > 0)
        if found > 0:
            return PASS, f"{found}/{len(results)} keywords with volume", details
        else:
            return FAIL, "All volumes are 0", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_yandex_wordstat_suggestions():
    """Test Wordstat get_suggestions (related queries)."""
    key = os.environ.get("YANDEX_WORDSTAT_API_KEY", "")
    if not key:
        return SKIP, "YANDEX_WORDSTAT_API_KEY not set", {}

    try:
        from src.services.writing_pipeline.data_sources.wordstat import YandexWordstatProvider

        folder_id = os.environ.get("YANDEX_CLOUD_FOLDER_ID", "")
        provider = YandexWordstatProvider(api_key=key, folder_id=folder_id)

        suggestions = await provider.get_suggestions("seo продвижение")

        details = {
            "count": len(suggestions),
            "top_5": suggestions[:5],
        }

        if len(suggestions) > 0:
            return PASS, f"{len(suggestions)} suggestions", details
        else:
            return FAIL, "No suggestions returned", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_rush_analytics():
    """Test Rush Analytics via RushAnalyticsProvider."""
    key = os.environ.get("RUSH_ANALYTICS_API_KEY", "")
    if not key:
        return SKIP, "RUSH_ANALYTICS_API_KEY not set", {}

    try:
        from src.services.writing_pipeline.data_sources.rush_provider import RushAnalyticsProvider

        provider = RushAnalyticsProvider(api_key=key)

        # First check balance
        balance = await provider.check_balance()
        balance_ok = "error" not in balance

        keywords = [TOPIC, "seo оптимизация"]
        results = await provider.get_volumes(keywords)

        details = {
            "balance": balance,
            "results": [{"keyword": r.keyword, "volume": r.volume} for r in results],
        }

        found = sum(1 for r in results if r.volume > 0)
        if found > 0:
            return PASS, f"{found}/{len(results)} keywords with volume (balance: {'OK' if balance_ok else 'error'})", details
        else:
            # Rush may return 0 if task timed out — not necessarily a failure
            return FAIL, f"All volumes are 0 (balance: {balance})", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_composite_provider():
    """Test CompositeVolumeProvider — both providers in parallel."""
    yandex_key = os.environ.get("YANDEX_WORDSTAT_API_KEY", "")
    rush_key = os.environ.get("RUSH_ANALYTICS_API_KEY", "")

    if not yandex_key and not rush_key:
        return SKIP, "Neither YANDEX_WORDSTAT_API_KEY nor RUSH_ANALYTICS_API_KEY set", {}

    try:
        from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider
        from src.config.settings import get_settings

        settings = get_settings()
        provider = get_volume_provider("ru", settings)

        keywords = [TOPIC, "seo оптимизация", "контент маркетинг", "продвижение сайта"]

        t0 = time.time()
        results = await provider.get_volumes(keywords)
        elapsed = time.time() - t0

        details = {
            "provider": provider.source_name,
            "elapsed_sec": round(elapsed, 1),
            "results": [],
        }
        for r in results:
            entry = {"keyword": r.keyword, "volume": r.volume, "source": r.source}
            if hasattr(r, "yandex_volume") and r.yandex_volume is not None:
                entry["yandex_volume"] = r.yandex_volume
            if hasattr(r, "google_volume") and r.google_volume is not None:
                entry["google_volume"] = r.google_volume
            details["results"].append(entry)

        found = sum(1 for r in results if r.volume > 0)
        if found > 0:
            return PASS, f"{provider.source_name}: {found}/{len(results)} keywords with volume ({elapsed:.1f}s)", details
        else:
            return FAIL, f"All volumes are 0 via {provider.source_name}", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_composite_suggestions():
    """Test CompositeVolumeProvider.get_suggestions()."""
    yandex_key = os.environ.get("YANDEX_WORDSTAT_API_KEY", "")
    rush_key = os.environ.get("RUSH_ANALYTICS_API_KEY", "")

    if not yandex_key and not rush_key:
        return SKIP, "No volume provider keys set", {}

    try:
        from src.services.writing_pipeline.data_sources.volume_provider import get_volume_provider
        from src.config.settings import get_settings

        settings = get_settings()
        provider = get_volume_provider("ru", settings)

        suggestions = await provider.get_suggestions("seo продвижение")

        details = {
            "provider": provider.source_name,
            "count": len(suggestions),
            "top_5": suggestions[:5],
        }

        if len(suggestions) > 0:
            return PASS, f"{len(suggestions)} suggestions via {provider.source_name}", details
        else:
            return FAIL, "No suggestions", details

    except Exception as e:
        return FAIL, str(e), {}


async def test_serper_serp_position():
    """Test SerperSerpClient — position check for our domain."""
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        return SKIP, "SERPER_API_KEY not set", {}

    try:
        from src.services.monitoring.serper_serp import SerperSerpClient

        client = SerperSerpClient(api_key=key)

        # Check a keyword where we might rank
        keywords = ["editors1 блог", TOPIC]
        results = await client.check_positions_batch(
            keywords=keywords,
            domain=DOMAIN,
            region="ru",
            depth=30,
        )

        details = {
            "results": [
                {
                    "keyword": r.keyword,
                    "position": r.position,
                    "url": r.url,
                    "serp_features": r.serp_features,
                    "success": r.success,
                    "error": r.error,
                }
                for r in results
            ],
        }

        successes = sum(1 for r in results if r.success)
        if successes == len(results):
            found = sum(1 for r in results if r.position is not None)
            return PASS, f"{successes}/{len(results)} checked, {found} found in SERP", details
        else:
            errors = [r.error for r in results if not r.success]
            return FAIL, f"Errors: {errors}", details

    except Exception as e:
        return FAIL, str(e), {}


async def main():
    print(f"=" * 60)
    print(f"E2E SEO Services Test")
    print(f"Topic: {TOPIC}")
    print(f"Domain: {DOMAIN}")
    print(f"Region: {REGION}")
    print(f"=" * 60)
    print()

    tests = [
        ("Serper Search", test_serper_search),
        ("Serper Autocomplete", test_serper_autocomplete),
        ("Yandex Wordstat Volumes", test_yandex_wordstat),
        ("Yandex Wordstat Suggestions", test_yandex_wordstat_suggestions),
        ("Rush Analytics Volumes", test_rush_analytics),
        ("Composite Provider Volumes", test_composite_provider),
        ("Composite Provider Suggestions", test_composite_suggestions),
        ("Serper SERP Position Check", test_serper_serp_position),
    ]

    results_summary = []

    for name, test_fn in tests:
        print(f"--- {name} ---")
        try:
            status, message, details = await test_fn()
        except Exception as e:
            status, message, details = FAIL, f"Unhandled: {e}", {}

        print(f"  {status}  {message}")
        if details:
            for k, v in details.items():
                if isinstance(v, list) and len(v) > 0:
                    print(f"    {k}:")
                    for item in v[:5]:
                        if isinstance(item, dict):
                            print(f"      {json.dumps(item, ensure_ascii=False)}")
                        else:
                            print(f"      {item}")
                    if len(v) > 5:
                        print(f"      ... ({len(v)} total)")
                elif isinstance(v, dict):
                    print(f"    {k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    print(f"    {k}: {v}")
        print()

        results_summary.append((name, status))

    # Summary
    print(f"=" * 60)
    print("SUMMARY:")
    passed = sum(1 for _, s in results_summary if s == PASS)
    failed = sum(1 for _, s in results_summary if s == FAIL)
    skipped = sum(1 for _, s in results_summary if s == SKIP)

    for name, status in results_summary:
        print(f"  {status}  {name}")

    print()
    print(f"  {passed} passed, {failed} failed, {skipped} skipped")
    print(f"=" * 60)

    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
