"""
Сервис автогенерации Brief (ТЗ) на основе темы.

Использует Serper.dev для получения SERP данных и Claude для анализа.
"""

import json
import httpx
import anthropic
from typing import Optional


BRIEF_GENERATION_PROMPT = """Ты SEO-эксперт. На основе результатов поиска по теме "{topic}" составь ТЗ (техническое задание) для написания статьи.

Результаты поиска Google:
{search_results}

Проанализируй:
1. Какие темы освещают топовые статьи
2. Какие вопросы задают пользователи (People Also Ask)
3. Какая средняя длина статей у конкурентов
4. Какую структуру используют топовые статьи

Сформируй JSON с ТЗ для статьи. JSON должен быть валидным и содержать:

```json
{{
  "title": "Заголовок статьи (до 60 символов, включает основной ключ)",
  "target_keyword": "основной ключевой запрос",
  "secondary_keywords": ["ключ1", "ключ2", "ключ3", "ключ4", "ключ5"],
  "word_count_min": 1500,
  "word_count_max": 2500,
  "structure": {{
    "sections": [
      {{
        "heading": "H2 заголовок первого раздела",
        "key_points": ["что раскрыть", "какие аспекты осветить"]
      }},
      {{
        "heading": "H2 заголовок второго раздела",
        "key_points": ["ключевые моменты"]
      }}
    ]
  }},
  "serp_analysis": {{
    "paa_questions": ["вопрос из PAA 1", "вопрос из PAA 2", "вопрос из PAA 3"],
    "featured_snippet_target": true,
    "avg_competitor_word_count": 2000
  }},
  "competitor_urls": ["url1", "url2", "url3"]
}}
```

Важно:
- target_keyword должен быть основным поисковым запросом
- secondary_keywords — связанные запросы (LSI), которые помогут ранжированию
- structure должна отражать логичную структуру статьи на основе анализа конкурентов
- word_count_min/max — оценка на основе длины статей конкурентов
- paa_questions — реальные вопросы из блока People Also Ask (если есть)
- competitor_urls — топ-3 URL из выдачи для анализа

Верни ТОЛЬКО JSON без дополнительного текста.
"""


class BriefGenerator:
    """Генератор Brief (ТЗ) на основе SERP анализа."""

    def __init__(
        self,
        serper_api_key: str,
        anthropic_api_key: str,
        proxy_url: Optional[str] = None,
        proxy_secret: Optional[str] = None,
    ):
        self.serper_api_key = serper_api_key

        # Настройка Anthropic клиента
        if proxy_url and proxy_secret:
            self.anthropic = anthropic.Anthropic(
                api_key=anthropic_api_key,
                base_url=proxy_url,
                default_headers={"x-proxy-token": proxy_secret},
            )
        else:
            self.anthropic = anthropic.Anthropic(api_key=anthropic_api_key)

    async def fetch_serp_data(self, query: str, country: str = "ru", language: str = "ru") -> dict:
        """
        Получает данные SERP через Serper.dev API.

        Returns:
            dict с ключами:
            - organic: список органических результатов
            - peopleAlsoAsk: вопросы PAA
            - relatedSearches: связанные запросы
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": self.serper_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "q": query,
                    "gl": country,
                    "hl": language,
                    "num": 10,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    def _format_serp_for_prompt(self, serp_data: dict) -> str:
        """Форматирует SERP данные для промпта."""
        parts = []

        # Органические результаты
        organic = serp_data.get("organic", [])
        if organic:
            parts.append("=== Топ-10 органических результатов ===")
            for i, result in enumerate(organic[:10], 1):
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")
                parts.append(f"{i}. {title}")
                parts.append(f"   URL: {link}")
                parts.append(f"   Описание: {snippet}")
                parts.append("")

        # People Also Ask
        paa = serp_data.get("peopleAlsoAsk", [])
        if paa:
            parts.append("=== People Also Ask (вопросы пользователей) ===")
            for item in paa:
                question = item.get("question", "")
                parts.append(f"- {question}")
            parts.append("")

        # Related Searches
        related = serp_data.get("relatedSearches", [])
        if related:
            parts.append("=== Связанные запросы ===")
            for item in related:
                query = item.get("query", "")
                parts.append(f"- {query}")
            parts.append("")

        return "\n".join(parts)

    def _parse_brief_response(self, response_text: str) -> dict:
        """Парсит ответ Claude и извлекает JSON."""
        text = response_text.strip()

        # Пробуем найти JSON в markdown блоке
        if "```json" in text:
            try:
                json_start = text.index("```json") + 7
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        # Пробуем найти JSON в обычном блоке кода
        if "```" in text:
            try:
                json_start = text.index("```") + 3
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        # Пробуем распарсить весь текст как JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Пробуем найти JSON объект в тексте
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            json_str = text[start:end]
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            raise ValueError(f"Failed to parse Brief JSON from response: {text[:500]}")

    async def generate(self, topic: str, country: str = "ru", language: str = "ru") -> dict:
        """
        Генерирует Brief на основе темы.

        Args:
            topic: Тема/ключевое слово для статьи
            country: Код страны для SERP (ru, us, etc.)
            language: Код языка (ru, en, etc.)

        Returns:
            dict с полями Brief:
            - title
            - target_keyword
            - secondary_keywords
            - word_count_min
            - word_count_max
            - structure
            - serp_analysis
            - competitor_urls
        """
        # 1. Получаем SERP данные
        serp_data = await self.fetch_serp_data(topic, country, language)

        # 2. Форматируем для промпта
        search_results = self._format_serp_for_prompt(serp_data)

        # 3. Генерируем Brief через Claude
        prompt = BRIEF_GENERATION_PROMPT.format(
            topic=topic,
            search_results=search_results,
        )

        response = self.anthropic.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text

        # 4. Парсим ответ
        brief_data = self._parse_brief_response(response_text)

        # 5. Добавляем метаданные о генерации
        brief_data["_meta"] = {
            "topic": topic,
            "country": country,
            "language": language,
            "tokens_used": {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
            "serp_results_count": len(serp_data.get("organic", [])),
            "paa_count": len(serp_data.get("peopleAlsoAsk", [])),
        }

        return brief_data
