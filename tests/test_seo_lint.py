"""Tests for SEO lint validator — pure logic, no mocks needed."""

import pytest
from src.services.validators.seo_lint import SEOLintValidator, Severity


@pytest.fixture
def validator():
    return SEOLintValidator()


def _make_good_article(keyword="seo оптимизация"):
    """Article that should pass all SEO checks."""
    content = f"""# {keyword.title()}: Полное Руководство

{keyword} — это процесс улучшения видимости сайта. Правильная {keyword}
помогает привлечь целевой трафик. Давайте разберём основные аспекты {keyword}.

## Что такое {keyword}

{keyword} включает комплекс мер. Для успешной {keyword} нужно учитывать
множество факторов. [Подробнее о SEO](https://example.com/seo).

## Технический аудит

Технический аудит — важная часть {keyword}. Без него невозможно понять
текущее состояние сайта. [Инструменты аудита](https://example.com/audit).

## Контент-стратегия

Качественный контент — основа {keyword}. Создавайте полезные материалы.

## Линкбилдинг

Внешние ссылки усиливают {keyword}. Работайте над естественным профилем.
"""
    # Pad to ~2000 words
    padding = f"\n\n{keyword} требует системного подхода к работе с контентом и техническими аспектами продвижения. " * 150
    return content + padding


class TestSEOLintPerfectArticle:
    def test_perfect_article_high_score(self, validator):
        content = _make_good_article()
        report = validator.validate(
            content_md=content,
            title="SEO Оптимизация: Полное Руководство для Новичков",
            meta_description="Узнайте всё о SEO оптимизации: от технического аудита до контент-стратегии. Пошаговое руководство для начинающих маркетологов.",
            target_keyword="seo оптимизация",
        )
        assert report.score >= 80
        assert report.status in ("passed", "warning")

    def test_all_checks_present(self, validator):
        content = _make_good_article()
        report = validator.validate(
            content_md=content,
            title="SEO Оптимизация: Полное Руководство",
            meta_description="A" * 140,
            target_keyword="seo оптимизация",
        )
        check_names = {i.check for i in report.issues}
        expected = {
            "title_length", "title_keyword", "meta_description",
            "h1_count", "h1_keyword", "keyword_density",
            "word_count", "links", "h2_structure",
        }
        assert check_names == expected


class TestSEOLintFailures:
    def test_no_h1(self, validator):
        content = "## Only H2\n\nNo H1 here.\n\n## Another H2\n\nMore text."
        report = validator.validate(
            content_md=content,
            title="Test Title That Is Long Enough",
            meta_description="A" * 140,
            target_keyword="test",
        )
        h1_issue = next(i for i in report.issues if i.check == "h1_count")
        assert h1_issue.severity == Severity.WARNING

    def test_multiple_h1s(self, validator):
        content = "# First H1\n\nText.\n\n# Second H1\n\nMore text.\n\n## H2\n\nEnd."
        report = validator.validate(
            content_md=content,
            title="Test Title That Is Long Enough",
            meta_description="A" * 140,
            target_keyword="test",
        )
        h1_issue = next(i for i in report.issues if i.check == "h1_count")
        assert h1_issue.severity == Severity.FAIL

    def test_title_too_long(self, validator):
        report = validator.validate(
            content_md="# Test\n\nContent.\n\n## H2\n\nMore.",
            title="A" * 80,
            meta_description="A" * 140,
            target_keyword="test",
        )
        title_issue = next(i for i in report.issues if i.check == "title_length")
        assert title_issue.severity == Severity.FAIL

    def test_title_too_short(self, validator):
        report = validator.validate(
            content_md="# T\n\nC.\n\n## H2\n\nM.",
            title="Hi",
            meta_description="A" * 140,
            target_keyword="test",
        )
        title_issue = next(i for i in report.issues if i.check == "title_length")
        assert title_issue.severity == Severity.FAIL

    def test_missing_keyword_in_title(self, validator):
        report = validator.validate(
            content_md="# Something\n\nContent.\n\n## H2\n\nMore.",
            title="Completely Irrelevant Long Title Here",
            meta_description="A" * 140,
            target_keyword="seo оптимизация",
        )
        kw_issue = next(i for i in report.issues if i.check == "title_keyword")
        assert kw_issue.severity == Severity.FAIL

    def test_no_h2_structure(self, validator):
        content = "# Title\n\nJust a single paragraph with no structure at all."
        report = validator.validate(
            content_md=content,
            title="Test Title That Is Long Enough",
            meta_description="A" * 140,
            target_keyword="test",
        )
        h2_issue = next(i for i in report.issues if i.check == "h2_structure")
        assert h2_issue.severity == Severity.FAIL

    def test_word_count_too_low(self, validator):
        report = validator.validate(
            content_md="# Test\n\nShort.\n\n## H2\n\nVery short article.",
            title="Test Title That Is Long Enough",
            meta_description="A" * 140,
            target_keyword="test",
            word_count_min=1500,
        )
        wc_issue = next(i for i in report.issues if i.check == "word_count")
        assert wc_issue.severity == Severity.FAIL


class TestSEOLintScore:
    def test_score_range(self, validator):
        """Score must be between 0 and 100."""
        report = validator.validate(
            content_md="# T\n\n## H\n\nC.",
            title="T",
            meta_description=None,
            target_keyword="x",
        )
        assert 0 <= report.score <= 100

    def test_report_to_dict(self, validator):
        report = validator.validate(
            content_md="# Test\n\nContent.\n\n## H2\n\nMore.",
            title="Test",
            meta_description="Desc",
            target_keyword="test",
        )
        d = report.to_dict()
        assert "score" in d
        assert "issues" in d
        assert isinstance(d["issues"], list)
