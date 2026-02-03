"""
Discovery Agent — анализ ниши и поиск конкурентов.

Использует Serper.dev для SERP данных и Claude для анализа.
"""

import json
import httpx
import anthropic
from typing import Optional
from urllib.parse import urlparse


DISCOVERY_PROMPT = """Ты SEO-аналитик. Проанализируй результаты поиска по нише "{niche}" и определи:

1. **Конкуренты** — сайты, которые активно публикуют контент по этой теме
2. **Границы ниши** — что включаем, что исключаем
3. **Seed keywords** — базовые ключевые слова для дальнейшего расширения

Результаты поиска Google:
{search_results}

Сформируй JSON:

```json
{{
  "competitors": [
    {{
      "domain": "example.com",
      "relevance_score": 0.9,
      "description": "Краткое описание чем занимается сайт",
      "top_content_types": ["guides", "tutorials", "reviews"]
    }}
  ],
  "niche_boundaries": {{
    "include": ["тема 1", "тема 2"],
    "exclude": ["что не включаем"],
    "target_audience": "описание целевой аудитории"
  }},
  "seed_keywords": [
    "ключевое слово 1",
    "ключевое слово 2",
    "ключевое слово 3"
  ],
  "content_gaps": [
    "тема которую конкуренты плохо освещают"
  ]
}}
```

Требования:
- Включи 5-10 конкурентов, отсортированных по релевантности
- relevance_score от 0 до 1 (1 = идеальный конкурент в нише)
- seed_keywords — 10-20 ключевых слов для старта
- Исключи из конкурентов: Wikipedia, YouTube, соцсети, маркетплейсы

Верни ТОЛЬКО JSON без дополнительного текста.
"""


class DiscoveryAgent:
    """Агент для анализа ниши и поиска конкурентов."""

    def __init__(
        self,
        serper_api_key: str,
        anthropic_api_key: str,
        proxy_url: Optional[str] = None,
        proxy_secret: Optional[str] = None,
    ):
        self.serper_api_key = serper_api_key

        if proxy_url and proxy_secret:
            self.anthropic = anthropic.Anthropic(
                api_key=anthropic_api_key,
                base_url=proxy_url,
                default_headers={"x-proxy-token": proxy_secret},
            )
        else:
            self.anthropic = anthropic.Anthropic(api_key=anthropic_api_key)

    async def search_serp(self, query: str, country: str = "ru", language: str = "ru", num: int = 20) -> dict:
        """Получает SERP данные через Serper.dev."""
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
                    "num": num,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    def _format_serp_for_prompt(self, serp_data: dict) -> str:
        """Форматирует SERP данные для промпта."""
        parts = []

        organic = serp_data.get("organic", [])
        if organic:
            parts.append("=== Органические результаты ===")
            for i, result in enumerate(organic, 1):
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")
                domain = urlparse(link).netloc
                parts.append(f"{i}. [{domain}] {title}")
                parts.append(f"   URL: {link}")
                parts.append(f"   Описание: {snippet}")
                parts.append("")

        related = serp_data.get("relatedSearches", [])
        if related:
            parts.append("=== Связанные запросы ===")
            for item in related:
                query = item.get("query", "")
                parts.append(f"- {query}")
            parts.append("")

        paa = serp_data.get("peopleAlsoAsk", [])
        if paa:
            parts.append("=== People Also Ask ===")
            for item in paa:
                question = item.get("question", "")
                parts.append(f"- {question}")

        return "\n".join(parts)

    def _parse_response(self, response_text: str) -> dict:
        """Парсит JSON из ответа Claude."""
        text = response_text.strip()

        # Ищем JSON в markdown блоке
        if "```json" in text:
            try:
                json_start = text.index("```json") + 7
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        if "```" in text:
            try:
                json_start = text.index("```") + 3
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            json_str = text[start:end]
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            raise ValueError(f"Failed to parse JSON from response: {text[:500]}")

    async def discover(
        self,
        niche: str,
        country: str = "ru",
        language: str = "ru",
        seed_queries: Optional[list[str]] = None,
    ) -> dict:
        """
        Анализирует нишу и находит конкурентов.

        Args:
            niche: Описание ниши (например: "контент-маркетинг для digital-агентств")
            country: Код страны для SERP
            language: Код языка
            seed_queries: Дополнительные запросы для расширения поиска

        Returns:
            dict с полями:
            - competitors: список конкурентов
            - niche_boundaries: границы ниши
            - seed_keywords: ключевые слова для старта
            - content_gaps: пробелы в контенте конкурентов
        """
        # Собираем SERP данные по нескольким запросам
        queries = [niche]
        if seed_queries:
            queries.extend(seed_queries[:3])  # Ограничиваем количество запросов

        all_results = []
        for query in queries:
            serp_data = await self.search_serp(query, country, language)
            all_results.append(serp_data)

        # Объединяем результаты
        combined_serp = self._combine_serp_results(all_results)
        search_results = self._format_serp_for_prompt(combined_serp)

        # Анализируем через Claude
        prompt = DISCOVERY_PROMPT.format(
            niche=niche,
            search_results=search_results,
        )

        response = self.anthropic.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

        response_text = response.content[0].text
        result = self._parse_response(response_text)

        # Добавляем метаданные
        result["_meta"] = {
            "niche": niche,
            "country": country,
            "language": language,
            "queries_used": queries,
            "tokens_used": {
                "input": response.usage.input_tokens,
                "output": response.usage.output_tokens,
            },
        }

        return result

    def _combine_serp_results(self, results: list[dict]) -> dict:
        """Объединяет результаты нескольких SERP запросов."""
        combined = {
            "organic": [],
            "relatedSearches": [],
            "peopleAlsoAsk": [],
        }

        seen_urls = set()
        seen_queries = set()
        seen_questions = set()

        for serp in results:
            for item in serp.get("organic", []):
                url = item.get("link", "")
                if url not in seen_urls:
                    seen_urls.add(url)
                    combined["organic"].append(item)

            for item in serp.get("relatedSearches", []):
                query = item.get("query", "")
                if query not in seen_queries:
                    seen_queries.add(query)
                    combined["relatedSearches"].append(item)

            for item in serp.get("peopleAlsoAsk", []):
                question = item.get("question", "")
                if question not in seen_questions:
                    seen_questions.add(question)
                    combined["peopleAlsoAsk"].append(item)

        return combined
