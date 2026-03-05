"""Tests for FAQ schema.org JSON-LD extraction in MetaStage."""

import pytest

from conftest import make_intent_result, make_outline_result, make_writing_context
from src.services.writing_pipeline.stages.meta import MetaStage


def _make_stage() -> MetaStage:
    """Create a MetaStage instance (no LLM needed for helpers)."""
    stage = MetaStage.__new__(MetaStage)
    return stage


def _make_ctx(
    markdown: str = "",
    must_answer_questions=None,
    search_results=None,
):
    """Build a WritingContext with the fields needed by _extract_faq_pairs."""
    ctx = make_writing_context()
    ctx.edited_md = markdown
    ctx.intent = make_intent_result(
        must_answer_questions=must_answer_questions or [],
    )
    ctx.outline = make_outline_result()
    ctx.search_results = search_results
    return ctx


# ---------- Source 1a: bold **Q?** pattern ----------

BOLD_MD = """\
## Часто задаваемые вопросы

**Что такое SEO?**
SEO — это процесс оптимизации сайта для поисковых систем, который помогает повысить видимость.

**Зачем нужна оптимизация?**
Оптимизация помогает привлечь целевой трафик и увеличить конверсию сайта.
"""


def test_faq_from_bold_pattern():
    stage = _make_stage()
    ctx = _make_ctx(markdown=BOLD_MD)
    pairs = stage._extract_faq_pairs(BOLD_MD, ctx)

    assert len(pairs) == 2
    assert pairs[0][0] == "Что такое SEO?"
    assert "оптимизации сайта" in pairs[0][1]
    assert pairs[1][0] == "Зачем нужна оптимизация?"


# ---------- Source 1b: ### heading pattern ----------

HEADING_MD = """\
## Основы SEO

Введение в тему.

### Что такое мета-теги?

Мета-теги — это HTML-элементы, которые предоставляют информацию о странице поисковым системам.

### Как работает индексация?

Поисковые роботы обходят сайт и добавляют страницы в индекс для последующей выдачи.

## Заключение

Подведём итоги.
"""


def test_faq_from_heading_pattern():
    stage = _make_stage()
    ctx = _make_ctx(markdown=HEADING_MD)
    pairs = stage._extract_faq_pairs(HEADING_MD, ctx)

    assert len(pairs) == 2
    assert pairs[0][0] == "Что такое мета-теги?"
    assert "HTML-элементы" in pairs[0][1]
    assert pairs[1][0] == "Как работает индексация?"


# ---------- Source 2: must_answer_questions ----------

ARTICLE_WITH_HEADINGS = """\
## Что такое SEO оптимизация

SEO оптимизация — это комплекс мер по улучшению позиций сайта в поисковой выдаче. Она включает работу с контентом, техническими факторами и ссылочной массой.

## Как начать продвижение сайта

Начните с аудита текущего состояния сайта. Проверьте техническую доступность, скорость загрузки и качество контента.

## Инструменты для SEO

Google Search Console и Яндекс.Вебмастер — основные бесплатные инструменты.
"""


def test_faq_from_must_answer_questions():
    stage = _make_stage()
    ctx = _make_ctx(
        markdown=ARTICLE_WITH_HEADINGS,
        must_answer_questions=[
            "Что такое SEO оптимизация",
            "Как начать продвижение",
        ],
    )
    pairs = stage._extract_faq_pairs(ARTICLE_WITH_HEADINGS, ctx)

    assert len(pairs) == 2
    assert any("оптимизация" in q.lower() for q, _ in pairs)
    assert any("продвижение" in q.lower() or "начать" in q.lower() for q, _ in pairs)
    # Answers should come from article text
    assert any("комплекс мер" in a for _, a in pairs)


# ---------- Source 3: PAA ----------

def test_faq_from_paa():
    stage = _make_stage()
    search_results = [
        {
            "query": "SEO оптимизация",
            "organic": [],
            "peopleAlsoAsk": [
                {"question": "Какие инструменты нужны для SEO"},
                {"question": "Неизвестный вопрос без ответа в статье"},
            ],
        }
    ]
    ctx = _make_ctx(
        markdown=ARTICLE_WITH_HEADINGS,
        search_results=search_results,
    )
    pairs = stage._extract_faq_pairs(ARTICLE_WITH_HEADINGS, ctx)

    # Only the first PAA should match (heading "Инструменты для SEO")
    assert len(pairs) >= 1
    matched_qs = [q for q, _ in pairs]
    assert any("инструменты" in q.lower() for q in matched_qs)


# ---------- Deduplication ----------

def test_dedup_regex_and_intent():
    """Same question from bold regex and must_answer → counted once."""
    md = """\
## Что такое SEO

**Что такое SEO?**
SEO — это оптимизация сайта для повышения позиций в поисковых системах. Включает техническую и контентную работу.
"""
    stage = _make_stage()
    ctx = _make_ctx(
        markdown=md,
        must_answer_questions=["Что такое SEO?"],
    )
    pairs = stage._extract_faq_pairs(md, ctx)

    questions = [q for q, _ in pairs]
    norm = [q.lower().rstrip("? ") for q in questions]
    assert len(norm) == len(set(norm)), f"Duplicates found: {questions}"


# ---------- Empty / no sources ----------

def test_empty_article_no_pairs():
    stage = _make_stage()
    ctx = _make_ctx(markdown="", must_answer_questions=[], search_results=None)
    pairs = stage._extract_faq_pairs("", ctx)
    assert pairs == []


# ---------- Limit 5 ----------

MANY_HEADINGS_MD = "\n".join(
    f"### Вопрос номер {i}?\n\nОтвет на вопрос номер {i}, достаточно длинный текст для прохождения фильтра.\n"
    for i in range(1, 9)
)


def test_limit_5_pairs():
    stage = _make_stage()
    ctx = _make_ctx(markdown=MANY_HEADINGS_MD)
    pairs = stage._extract_faq_pairs(MANY_HEADINGS_MD, ctx)

    assert len(pairs) == 5
