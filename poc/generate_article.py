#!/usr/bin/env python3
"""
PoC: Проверка качества генерации SEO-статей на русском языке.

Цель: убедиться, что Claude может писать качественные статьи
с реальными источниками без галлюцинаций.

Запуск:
    export ANTHROPIC_API_KEY=your_key
    python poc/generate_article.py
"""

import anthropic
import json
from datetime import datetime
from pathlib import Path


SYSTEM_PROMPT = """Ты — опытный копирайтер и SEO-специалист, пишущий экспертные статьи на русском языке.

КРИТИЧЕСКИ ВАЖНО:
1. Каждый факт, статистика или утверждение ДОЛЖНЫ иметь источник
2. НЕ выдумывай источники — используй только реальные, которые ты точно знаешь
3. Если не уверен в факте — НЕ включай его в статью
4. Лучше меньше фактов с реальными источниками, чем много с выдуманными

Формат источников:
- В тексте: [1], [2], etc.
- В конце: список источников с URL (если известен) или названием публикации

Стиль:
- Экспертный, но доступный
- Без воды и общих фраз
- Конкретика и практические советы
- Структурированный текст с H2/H3 заголовками
"""


def generate_article(
    topic: str,
    keywords: list[str],
    word_count: tuple[int, int] = (1500, 2000),
) -> dict:
    """Генерирует SEO-статью по заданной теме."""

    client = anthropic.Anthropic()

    user_prompt = f"""Напиши SEO-оптимизированную статью на тему: {topic}

Целевые ключевые слова: {', '.join(keywords)}

Требования:
- Объём: {word_count[0]}-{word_count[1]} слов
- Структура: введение, 3-5 разделов с H2, заключение
- Каждый раздел: H2 заголовок + 2-3 абзаца + практические советы
- Обязательно: список источников в конце

Формат вывода:
1. Сначала JSON-блок с метаданными:
```json
{{
  "title": "SEO-заголовок (до 60 символов)",
  "meta_description": "Мета-описание (до 160 символов)",
  "slug": "url-slug-na-translit"
}}
```

2. Затем статья в Markdown формате

3. В конце — раздел "## Источники" со списком использованных источников
"""

    response = client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    content = response.content[0].text

    # Парсим метаданные и контент
    result = {
        "raw_response": content,
        "tokens_used": {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
        "model": response.model,
        "generated_at": datetime.now().isoformat(),
    }

    # Пытаемся извлечь JSON метаданные
    if "```json" in content:
        try:
            json_start = content.index("```json") + 7
            json_end = content.index("```", json_start)
            json_str = content[json_start:json_end].strip()
            result["metadata"] = json.loads(json_str)
        except (ValueError, json.JSONDecodeError) as e:
            result["metadata_error"] = str(e)

    return result


def save_result(result: dict, output_dir: Path) -> Path:
    """Сохраняет результат в файлы."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON с полными данными
    json_path = output_dir / f"article_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Markdown только с контентом
    md_path = output_dir / f"article_{timestamp}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(result["raw_response"])

    return json_path, md_path


def main():
    print("=" * 60)
    print("PoC: Генерация SEO-статьи на русском языке")
    print("=" * 60)
    print()

    # Тестовая тема: контент-маркетинг
    topic = "Как построить контент-стратегию для B2B-компании с нуля"
    keywords = [
        "контент-стратегия B2B",
        "контент-маркетинг для бизнеса",
        "план контент-маркетинга",
        "B2B контент",
    ]

    print(f"Тема: {topic}")
    print(f"Ключевые слова: {', '.join(keywords)}")
    print()
    print("Генерация статьи...")
    print()

    result = generate_article(topic, keywords)

    # Сохраняем
    output_dir = Path(__file__).parent / "output"
    json_path, md_path = save_result(result, output_dir)

    print("=" * 60)
    print("Результат")
    print("=" * 60)
    print()

    if "metadata" in result:
        print("Метаданные:")
        print(f"  Title: {result['metadata'].get('title', 'N/A')}")
        print(f"  Description: {result['metadata'].get('meta_description', 'N/A')}")
        print(f"  Slug: {result['metadata'].get('slug', 'N/A')}")
        print()

    print(f"Токены: {result['tokens_used']['input']} input, {result['tokens_used']['output']} output")
    print(f"Модель: {result['model']}")
    print()
    print(f"Сохранено:")
    print(f"  JSON: {json_path}")
    print(f"  Markdown: {md_path}")
    print()
    print("=" * 60)
    print("Что проверить вручную:")
    print("=" * 60)
    print("1. Качество текста — читается ли естественно?")
    print("2. Источники — реальные ли они? Можно ли их найти?")
    print("3. Ключевые слова — естественно ли вписаны?")
    print("4. Структура — логична ли?")
    print("5. Факты — нет ли явных ошибок?")
    print()
    print("Откройте файл для review:")
    print(f"  cat {md_path}")


if __name__ == "__main__":
    main()
