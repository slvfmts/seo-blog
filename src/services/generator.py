"""
Сервис генерации статей через Claude API.
"""

import json
import anthropic
from uuid import UUID

from src.db.session import SessionLocal
from src.db import models


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


class ArticleGenerator:
    """Генератор статей через Claude."""

    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def generate(self, topic: str, keywords: list[str]) -> dict:
        """Генерирует статью по теме."""

        user_prompt = f"""Напиши SEO-оптимизированную статью на тему: {topic}

Целевые ключевые слова: {', '.join(keywords)}

Требования:
- Объём: 1500-2000 слов
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

        response = self.client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        content = response.content[0].text

        result = {
            "raw_content": content,
            "tokens_used": {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
        }

        # Парсим метаданные
        if "```json" in content:
            try:
                json_start = content.index("```json") + 7
                json_end = content.index("```", json_start)
                json_str = content[json_start:json_end].strip()
                result["metadata"] = json.loads(json_str)

                # Убираем JSON-блок из контента
                result["content_md"] = content[json_end + 3:].strip()
            except (ValueError, json.JSONDecodeError):
                result["content_md"] = content
        else:
            result["content_md"] = content

        return result

    def generate_and_save(self, draft_id: UUID, topic: str, keywords: list[str]):
        """Генерирует статью и сохраняет в БД."""
        db = SessionLocal()
        try:
            draft = db.query(models.Draft).filter(models.Draft.id == draft_id).first()
            if not draft:
                return

            try:
                result = self.generate(topic, keywords)

                draft.content_md = result.get("content_md", result["raw_content"])
                draft.word_count = len(draft.content_md.split())
                draft.topic = topic
                draft.keywords = keywords

                if "metadata" in result:
                    draft.title = result["metadata"].get("title", topic)
                    draft.slug = result["metadata"].get("slug")
                    draft.meta_description = result["metadata"].get("meta_description")

                draft.status = "generated"

            except Exception as e:
                draft.status = "error"
                draft.validation_report = {"error": str(e)}

            db.commit()

        finally:
            db.close()
