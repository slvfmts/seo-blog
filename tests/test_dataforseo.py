"""Tests for DataForSEO client — location fallback, competition parsing."""

import pytest
from src.services.writing_pipeline.data_sources.dataforseo import DataForSEO


@pytest.fixture
def client():
    return DataForSEO(login="test", password="test")


# =============================================================================
# Location fallback
# =============================================================================

class TestLocationFallback:
    def test_russia_falls_back_to_kazakhstan(self, client):
        """Russia (2643) must fall back to Kazakhstan (2398)."""
        code = client.get_safe_location_code("ru")
        assert code == 2398, f"Expected 2398 (Kazakhstan), got {code}"

    def test_russia_direct_code_falls_back(self, client):
        """Direct code 2643 should also fall back."""
        assert client.LOCATION_FALLBACK.get(2643) == 2398

    def test_us_no_fallback(self, client):
        code = client.get_safe_location_code("us")
        assert code == 2840

    def test_uk_no_fallback(self, client):
        code = client.get_safe_location_code("uk")
        assert code == 2826

    def test_unknown_region_defaults_to_us(self, client):
        code = client.get_safe_location_code("xx")
        assert code == 2840

    def test_get_location_code_case_insensitive(self, client):
        assert client.get_location_code("RU") == 2643
        assert client.get_location_code("Us") == 2840


# =============================================================================
# Competition field parsing
# =============================================================================

class TestCompetitionParsing:
    def test_string_competition_uses_competition_index(self, client):
        """When competition is a string like 'MEDIUM', use competition_index."""
        data = {
            "status_code": 20000,
            "cost": 0.05,
            "tasks": [{
                "status_code": 20000,
                "result": [{
                    "keyword": "test keyword",
                    "search_volume": 1000,
                    "cpc": 0.5,
                    "competition": "MEDIUM",
                    "competition_index": 50,
                    "competition_level": "MEDIUM",
                }],
            }],
        }
        result = client._parse_search_volume_response(data, ["test keyword"])
        assert result.success is True
        kw = result.keywords[0]
        assert kw.competition == 0.5  # 50/100
        assert kw.competition_level == "MEDIUM"

    def test_float_competition(self, client):
        """When competition is already a float, use it directly."""
        data = {
            "status_code": 20000,
            "tasks": [{
                "status_code": 20000,
                "result": [{
                    "keyword": "test keyword",
                    "search_volume": 500,
                    "cpc": 1.0,
                    "competition": 0.3,
                    "competition_level": "LOW",
                }],
            }],
        }
        result = client._parse_search_volume_response(data, ["test keyword"])
        assert result.keywords[0].competition == 0.3

    def test_missing_keywords_get_zero_metrics(self, client):
        """Keywords not in the response get zero metrics."""
        data = {
            "status_code": 20000,
            "tasks": [{
                "status_code": 20000,
                "result": [{
                    "keyword": "found keyword",
                    "search_volume": 100,
                    "cpc": 0.1,
                    "competition": 0.1,
                    "competition_level": "LOW",
                }],
            }],
        }
        result = client._parse_search_volume_response(
            data, ["found keyword", "missing keyword"]
        )
        assert len(result.keywords) == 2
        missing = [k for k in result.keywords if k.keyword == "missing keyword"]
        assert len(missing) == 1
        assert missing[0].search_volume == 0

    def test_api_error_response(self, client):
        data = {"status_code": 40000, "status_message": "Unauthorized"}
        result = client._parse_search_volume_response(data, ["kw"])
        assert result.success is False
        assert "Unauthorized" in result.error


# =============================================================================
# Difficulty estimation
# =============================================================================

class TestDifficultyEstimation:
    def test_zero_competition_zero_volume(self, client):
        assert client._estimate_difficulty(0, 0) == 0

    def test_high_competition_high_volume(self, client):
        diff = client._estimate_difficulty(1.0, 200000)
        assert diff <= 100
        assert diff >= 70

    def test_caps_at_100(self, client):
        assert client._estimate_difficulty(1.0, 1000000) <= 100
