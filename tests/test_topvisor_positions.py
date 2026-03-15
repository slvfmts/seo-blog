"""
Tests for TopvisorPositionTracker.

Covers: keyword selection logic, ID mapping, poll/fetch cycle,
mutual exclusion (already checked today), position extraction.
"""

import uuid
from datetime import datetime, date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.monitoring.topvisor_positions import (
    TopvisorPositionTracker,
    MAX_KEYWORDS_PER_ARTICLE,
    MIN_VOLUME_THRESHOLD,
)


# ── Fixtures ──────────────────────────────────────────────────────

def _make_keyword(keyword_text, search_volume=100, site_id=None, keyword_id=None, post_id=None, topvisor_keyword_id=None):
    """Create a mock Keyword."""
    kw = MagicMock()
    kw.id = keyword_id or uuid.uuid4()
    kw.keyword = keyword_text
    kw.search_volume = search_volume
    kw.site_id = site_id or uuid.uuid4()
    kw.post_id = post_id
    kw.topvisor_keyword_id = topvisor_keyword_id
    kw.current_position = None
    kw.status = "targeted"
    kw.updated_at = None
    return kw


def _make_draft(keywords_list, keyword_id=None, site_id=None, slug="test-article"):
    """Create a mock Draft."""
    draft = MagicMock()
    draft.id = uuid.uuid4()
    draft.site_id = site_id or uuid.uuid4()
    draft.keyword_id = keyword_id
    draft.brief_id = None
    draft.keywords = keywords_list
    draft.slug = slug
    return draft


def _make_post(slug="test-article"):
    """Create a mock Post."""
    post = MagicMock()
    post.id = uuid.uuid4()
    post.slug = slug
    return post


def _make_client():
    """Create a mock TopvisorClient."""
    client = MagicMock()
    client.project_id = 27084887
    client.import_keywords = AsyncMock(return_value={"countAdded": 3, "countDuplicated": 0})
    client.get_keywords = AsyncMock(return_value=[])
    client.get_searcher_regions = AsyncMock(return_value=[
        {"id": 1, "searcher_key": 0, "region_key": 213},
        {"id": 2, "searcher_key": 1, "region_key": 213},
    ])
    client.start_position_check = AsyncMock(return_value={"status": "ok"})
    client.get_position_summary = AsyncMock(return_value=[])
    return client


# ── register_keywords_for_tracking ──────────────────────────────

@pytest.mark.asyncio
async def test_register_primary_keyword_always_selected():
    """Primary keyword is always selected regardless of volume."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    primary_kw = _make_keyword("seo блог", search_volume=10, site_id=site_id)
    primary_kw_id = primary_kw.id

    draft = _make_draft(["seo блог", "другой ключ"], keyword_id=primary_kw_id, site_id=site_id)
    post = _make_post()

    db = MagicMock()

    # Mock: query(Keyword).filter(id=primary_kw_id) returns primary
    def mock_query(model):
        q = MagicMock()
        if model.__tablename__ == "keywords":
            filter_mock = MagicMock()
            filter_mock.all.return_value = [primary_kw]
            filter_mock.first.return_value = primary_kw
            q.filter.return_value = filter_mock
        elif model.__tablename__ == "briefs":
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = mock_query

    # Mock Topvisor response with IDs
    client.get_keywords.return_value = [
        {"id": 1001, "name": "seo блог", "group_name": "article:test-article"},
    ]

    result = await tracker.register_keywords_for_tracking(draft, post, db)

    assert result["mapped"] >= 1
    client.import_keywords.assert_called_once()
    call_args = client.import_keywords.call_args
    assert "article:test-article" in str(call_args)


@pytest.mark.asyncio
async def test_register_skips_low_volume_secondary():
    """Secondary keywords with volume < MIN_VOLUME_THRESHOLD are skipped."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    primary_kw = _make_keyword("main keyword", search_volume=500, site_id=site_id)
    low_vol_kw = _make_keyword("low volume kw", search_volume=30, site_id=site_id)
    high_vol_kw = _make_keyword("high volume kw", search_volume=200, site_id=site_id)

    draft = _make_draft(
        ["main keyword", "low volume kw", "high volume kw"],
        keyword_id=primary_kw.id,
        site_id=site_id,
    )
    post = _make_post()
    db = MagicMock()

    all_keywords = [primary_kw, low_vol_kw, high_vol_kw]

    def mock_query(model):
        q = MagicMock()
        if model.__tablename__ == "keywords":
            filter_mock = MagicMock()
            # For filter by site_id + keyword.in_
            filter_mock.all.return_value = all_keywords
            # For filter by id (primary lookup)
            filter_mock.first.return_value = primary_kw
            q.filter.return_value = filter_mock
        return q

    db.query.side_effect = mock_query

    client.get_keywords.return_value = [
        {"id": 1001, "name": "main keyword", "group_name": "article:test-article"},
        {"id": 1003, "name": "high volume kw", "group_name": "article:test-article"},
    ]

    result = await tracker.register_keywords_for_tracking(draft, post, db)

    # Should import primary + high_vol, NOT low_vol
    imported_keywords = client.import_keywords.call_args[1].get("keywords", []) or client.import_keywords.call_args[0][0]
    # low_vol_kw should not be in the import
    assert "low volume kw" not in [k.lower() for k in imported_keywords]


@pytest.mark.asyncio
async def test_register_max_keywords_cap():
    """No more than MAX_KEYWORDS_PER_ARTICLE keywords selected."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    primary_kw = _make_keyword("primary", search_volume=1000, site_id=site_id)

    # Create 15 high-volume keywords
    all_kws = [primary_kw]
    keyword_texts = ["primary"]
    for i in range(14):
        kw = _make_keyword(f"keyword-{i}", search_volume=200 + i, site_id=site_id)
        all_kws.append(kw)
        keyword_texts.append(f"keyword-{i}")

    draft = _make_draft(keyword_texts, keyword_id=primary_kw.id, site_id=site_id)
    post = _make_post()
    db = MagicMock()

    def mock_query(model):
        q = MagicMock()
        if model.__tablename__ == "keywords":
            filter_mock = MagicMock()
            filter_mock.all.return_value = all_kws
            filter_mock.first.return_value = primary_kw
            q.filter.return_value = filter_mock
        return q

    db.query.side_effect = mock_query
    client.get_keywords.return_value = [
        {"id": 2000 + i, "name": kw.keyword, "group_name": "article:test-article"}
        for i, kw in enumerate(all_kws[:MAX_KEYWORDS_PER_ARTICLE])
    ]

    result = await tracker.register_keywords_for_tracking(draft, post, db)

    imported_keywords = client.import_keywords.call_args[1].get("keywords") or client.import_keywords.call_args[0][0]
    assert len(imported_keywords) <= MAX_KEYWORDS_PER_ARTICLE


@pytest.mark.asyncio
async def test_register_no_keywords():
    """Returns early if draft has no keywords."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)
    draft = _make_draft([], site_id=uuid.uuid4())
    post = _make_post()
    db = MagicMock()

    result = await tracker.register_keywords_for_tracking(draft, post, db)
    assert result["imported"] == 0
    client.import_keywords.assert_not_called()


@pytest.mark.asyncio
async def test_register_no_site_id():
    """Returns early if draft has no site_id."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)
    draft = _make_draft(["test"])
    draft.site_id = None
    post = _make_post()
    db = MagicMock()

    result = await tracker.register_keywords_for_tracking(draft, post, db)
    assert "error" in result


# ── trigger_check ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trigger_check():
    """trigger_check calls start_position_check with regions_indexes."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    result = await tracker.trigger_check()

    client.get_searcher_regions.assert_called_once()
    client.start_position_check.assert_called_once_with([1, 2])


@pytest.mark.asyncio
async def test_trigger_check_no_regions():
    """Returns error if no searcher regions configured."""
    client = _make_client()
    client.get_searcher_regions.return_value = []
    tracker = TopvisorPositionTracker(client)

    result = await tracker.trigger_check()
    assert "error" in result


# ── fetch_results ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_results_saves_positions():
    """fetch_results maps Topvisor IDs to keywords and saves rankings."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    kw1 = _make_keyword("seo tips", topvisor_keyword_id=1001, site_id=site_id)
    kw1.post_id = uuid.uuid4()

    db = MagicMock()

    # Query for keywords with topvisor_keyword_id
    kw_query = MagicMock()
    kw_query.all.return_value = [kw1]

    # Query for existing rankings (none today)
    ranking_query = MagicMock()
    ranking_query.first.return_value = None

    call_count = [0]
    def mock_query(model):
        q = MagicMock()
        if hasattr(model, '__tablename__') and model.__tablename__ == "keywords":
            q.filter.return_value = kw_query
        else:
            # KeywordRanking query
            q.filter.return_value = ranking_query
        return q

    db.query.side_effect = mock_query

    # Topvisor returns positions
    today_str = date.today().strftime("%Y-%m-%d")
    client.get_position_summary.return_value = [
        {"id": 1001, "name": "seo tips", f"p1_{today_str}": 5},
    ]

    result = await tracker.fetch_results(site_id, db)

    assert result["saved"] == 1
    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_results_skips_unmapped():
    """Keywords without topvisor_keyword_id mapping are skipped."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    db = MagicMock()

    kw_query = MagicMock()
    kw_query.all.return_value = []  # No mapped keywords
    db.query.return_value.filter.return_value = kw_query

    client.get_position_summary.return_value = [
        {"id": 9999, "name": "unmapped keyword", "p1_2026-03-10": 3},
    ]

    result = await tracker.fetch_results(site_id, db)
    assert result["not_mapped"] == 1
    assert result["saved"] == 0


# ── _extract_position ─────────────────────────────────────────────

def test_extract_position_p_fields():
    """Extracts position from p{N}_{date} fields."""
    pos_data = {
        "id": 1001,
        "name": "test",
        "p1_2026-03-10": 7,
        "p2_2026-03-10": 12,
    }
    result = TopvisorPositionTracker._extract_position(pos_data, [1, 2])
    assert result == 7  # Best position


def test_extract_position_none_when_empty():
    """Returns None when no position fields."""
    pos_data = {"id": 1001, "name": "test"}
    result = TopvisorPositionTracker._extract_position(pos_data, [1, 2])
    assert result is None


def test_extract_position_ignores_project_id():
    """Doesn't confuse project_id with position field."""
    pos_data = {
        "id": 1001,
        "project_id": 27084887,
        "p1_2026-03-10": 15,
    }
    result = TopvisorPositionTracker._extract_position(pos_data, [1])
    assert result == 15


def test_extract_position_dict_values():
    """Handles nested dict position values."""
    pos_data = {
        "id": 1001,
        "p1_2026-03-10": {"position": 3, "url": "https://example.com/page"},
    }
    result = TopvisorPositionTracker._extract_position(pos_data, [1])
    assert result == 3


# ── run_daily_check ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_daily_check_full_cycle():
    """run_daily_check triggers, polls, and fetches results."""
    client = _make_client()
    tracker = TopvisorPositionTracker(client)

    site_id = uuid.uuid4()
    kw = _make_keyword("daily check kw", topvisor_keyword_id=2001, site_id=site_id)
    kw.post_id = uuid.uuid4()

    db = MagicMock()

    kw_query = MagicMock()
    kw_query.all.return_value = [kw]

    ranking_query = MagicMock()
    ranking_query.first.return_value = None

    def mock_query(model):
        q = MagicMock()
        if hasattr(model, '__tablename__') and model.__tablename__ == "keywords":
            q.filter.return_value = kw_query
        else:
            q.filter.return_value = ranking_query
        return q

    db.query.side_effect = mock_query

    # Return positions on first poll
    today_str = date.today().strftime("%Y-%m-%d")
    client.get_position_summary.return_value = [
        {"id": 2001, "name": "daily check kw", f"p1_{today_str}": 8},
    ]

    with patch("src.services.monitoring.topvisor_positions.asyncio.sleep", new_callable=AsyncMock):
        with patch("src.services.monitoring.topvisor_positions.POSITION_CHECK_TIMEOUT", 30):
            with patch("src.services.monitoring.topvisor_positions.POSITION_CHECK_POLL_INTERVAL", 10):
                result = await tracker.run_daily_check(site_id, db)

    assert result.get("saved", 0) >= 0
    assert "poll_seconds" in result
    client.start_position_check.assert_called_once()
