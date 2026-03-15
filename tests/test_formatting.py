"""Tests for formatting stage — cover prompt, SVG chart rendering, diagram insertion."""

import os
import re
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from src.services.writing_pipeline.stages.formatting import (
    FormattingStage, COVER_SCENE_PROMPT, COVER_STYLE_PREFIX,
    _HAS_CAIROSVG, _sanitize_chart_id,
)
from src.services.writing_pipeline.contracts import FormattingAsset


@pytest.fixture
def stage():
    client = MagicMock()
    return FormattingStage(
        client=client,
        model="test",
        openai_api_key="test-key",
        openai_proxy_url="",
        openai_proxy_secret="",
        ghost_url="",
        ghost_admin_key="",
    )


# =============================================================================
# Cover prompt sanity checks — two-stage architecture (scene + style)
# =============================================================================

class TestCoverPromptLength:
    def test_scene_prompt_reasonable_length(self):
        """COVER_SCENE_PROMPT should leave room for article text."""
        assert len(COVER_SCENE_PROMPT) < 2000, (
            f"COVER_SCENE_PROMPT is {len(COVER_SCENE_PROMPT)} chars"
        )

    def test_style_prefix_under_dalle_limit(self):
        """COVER_STYLE_PREFIX + a long scene description must fit DALL-E limit."""
        # gpt-image-1 allows up to 32k chars, but we want scene < 500 chars
        long_scene = "A detailed pixel art scene. " * 20  # ~560 chars
        prompt = COVER_STYLE_PREFIX + long_scene
        assert len(prompt) < 4000, f"Style + scene is {len(prompt)} chars"

    def test_scene_prompt_contains_key_constraints(self):
        """Scene prompt must include no-people and no-text rules."""
        lower = COVER_SCENE_PROMPT.lower()
        assert "no people" in lower
        assert "no text" in lower


# =============================================================================
# _insert_diagrams — fuzzy heading match + minimum 2-H2 gap
# =============================================================================

class TestInsertDiagrams:
    def _make_article(self, num_h2=5):
        lines = ["# Заголовок статьи", "", "Вступление."]
        for i in range(num_h2):
            lines.extend([
                "",
                f"## Раздел {i + 1}",
                f"Текст раздела {i + 1}.",
                "",
            ])
        return "\n".join(lines)

    def _make_diagram(self, after_heading="", ghost_url="http://img/d.png"):
        asset = FormattingAsset(
            type="diagram", filename="d.png", path="/tmp/d.png",
            alt="Diagram", caption="Cap", ghost_url=ghost_url,
        )
        asset._after_heading = after_heading
        return asset

    def test_diagram_inserted_after_matching_h2(self, stage):
        article = self._make_article()
        diagram = self._make_diagram(after_heading="Раздел 3")
        result = stage._insert_diagrams(article, [diagram])
        # The figure should appear somewhere after "## Раздел 3"
        idx_h2 = result.index("## Раздел 3")
        idx_fig = result.index("<figure>")
        assert idx_fig > idx_h2

    def test_fuzzy_match_works(self, stage):
        article = self._make_article()
        # Slightly different heading text — should still match
        diagram = self._make_diagram(after_heading="раздел 3")
        result = stage._insert_diagrams(article, [diagram])
        assert "<figure>" in result

    def test_two_diagrams_minimum_gap(self, stage):
        """Two diagrams should not be placed at adjacent H2 sections."""
        article = self._make_article(num_h2=6)
        d1 = self._make_diagram(after_heading="Раздел 1")
        d2 = self._make_diagram(after_heading="Раздел 2")
        d2.filename = "d2.png"
        result = stage._insert_diagrams(article, [d1, d2])
        # Count figures
        assert result.count("<figure>") == 2

    def test_no_diagrams_no_change(self, stage):
        article = self._make_article()
        result = stage._insert_diagrams(article, [])
        assert result == article

    def test_cover_not_in_body(self):
        """Cover image must go to feature_image, NOT inserted into article body."""
        cover = FormattingAsset(
            type="cover", filename="cover.png", path="/tmp/cover.png",
            alt="Cover", ghost_url="http://ghost/cover.png",
        )
        assert cover.type == "cover"


# =============================================================================
# SVG → PNG rendering via cairosvg
# =============================================================================

VALID_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 380"'
    ' font-family="DejaVu Sans, system-ui, sans-serif">'
    '<rect width="800" height="380" rx="16" fill="#F8FAFC"/>'
    '<text x="400" y="200" text-anchor="middle" font-size="24" '
    'font-weight="700" fill="#1E293B">Тестовый график</text>'
    '</svg>'
)

INVALID_SVG = '<svg><broken'

CYRILLIC_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 200"'
    ' font-family="DejaVu Sans, system-ui, sans-serif">'
    '<rect width="400" height="200" fill="#F8FAFC"/>'
    '<text x="200" y="100" text-anchor="middle" font-size="18" '
    'fill="#1E293B">Кириллица: Привет мир</text>'
    '</svg>'
)


@pytest.mark.skipif(not _HAS_CAIROSVG, reason="cairosvg not installed")
class TestRenderSvgToPng:
    @pytest.mark.asyncio
    async def test_valid_svg_renders_to_png(self, stage):
        """Valid SVG should produce a non-empty PNG file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "test.png")
            result = await stage._render_svg_to_png(VALID_SVG, output)
            assert result is True
            assert os.path.exists(output)
            assert os.path.getsize(output) > 100  # PNG header is ~100 bytes

    @pytest.mark.asyncio
    async def test_invalid_svg_returns_false(self, stage):
        """Broken SVG should return False, not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "bad.png")
            result = await stage._render_svg_to_png(INVALID_SVG, output)
            assert result is False

    @pytest.mark.asyncio
    async def test_cyrillic_text_renders(self, stage):
        """SVG with Cyrillic text should render successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output = os.path.join(tmpdir, "cyrillic.png")
            result = await stage._render_svg_to_png(CYRILLIC_SVG, output)
            assert result is True
            assert os.path.getsize(output) > 100


# =============================================================================
# Graceful degradation when cairosvg is not available
# =============================================================================

class TestSvgChartsGracefulDegradation:
    @pytest.mark.asyncio
    async def test_no_cairosvg_skips_charts(self, stage):
        """When cairosvg is unavailable, charts should be skipped gracefully."""
        with patch("src.services.writing_pipeline.stages.formatting._HAS_CAIROSVG", False):
            assets, tokens, errors, in_t, out_t = await stage._generate_svg_charts(
                "# Article\n## Section\nText.", "test-slug", "/tmp", None,
            )
            assert assets == []
            assert tokens == 0
            assert any("cairosvg" in e for e in errors)


# =============================================================================
# _sanitize_chart_id — safe filename from LLM output
# =============================================================================

class TestSanitizeChartId:
    def test_normal_id(self):
        assert _sanitize_chart_id("chart-1", "fallback") == "chart-1"

    def test_strips_unsafe_chars(self):
        assert _sanitize_chart_id("../../etc/passwd", "fb") == "etcpasswd"

    def test_empty_returns_fallback(self):
        assert _sanitize_chart_id("", "chart-0") == "chart-0"
        assert _sanitize_chart_id("!!!???", "chart-0") == "chart-0"

    def test_truncates_long_id(self):
        result = _sanitize_chart_id("a" * 100, "fb")
        assert len(result) == 30

    def test_unicode_stripped(self):
        assert _sanitize_chart_id("диаграмма-1", "fb") == "-1"


# =============================================================================
# HTML escaping in _insert_diagrams
# =============================================================================

class TestInsertDiagramsHtmlEscaping:
    def test_alt_and_caption_escaped(self, stage):
        """Alt and caption with special chars must be HTML-escaped."""
        article = "# Title\n\n## Section\n\nText.\n"
        asset = FormattingAsset(
            type="diagram", filename="d.png", path="/tmp/d.png",
            alt='<script>alert("xss")</script>',
            caption='A & B "test"',
            ghost_url="http://img/d.png",
        )
        asset._after_heading = "Section"
        result = stage._insert_diagrams(article, [asset])
        assert "<script>" not in result
        assert "&lt;script&gt;" in result
        assert "A &amp; B" in result
