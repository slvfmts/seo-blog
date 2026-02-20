"""
Formatting Stage - Generates cover image and Mermaid diagrams.
"""

import json
import os
import logging
import subprocess
import tempfile
import base64
import re
from typing import Optional

import anthropic

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import FormattingResult, FormattingAsset

logger = logging.getLogger(__name__)


COVER_PROMPT = """Ты создаёшь ОБЛОЖКУ для статьи. На входе будет текст статьи ниже. Твоя задача — САМОСТОЯТЕЛЬНО выделить 3–5 ключевых понятий и превратить их в "метафору второго уровня" (система/механизм/экосистема/архитектура), без банальных корпоративных клише.

СТИЛЬ СЕРИИ (не менять между статьями):
- Формат: квадрат 1:1, одно изображение.
- Визуальный стиль: современный минималистичный 3D (не фотореализм), чистая геометрия, матовые материалы + стеклянные/полупрозрачные элементы, резкий контраст.
- Палитра: тёмный фон (глубокий индиго/графит) + 2–3 неоновых акцента (циан, маджента, лайм). Никаких пастельных "корпоративных" цветов.
- Свет: выразительный rim light, аккуратные мягкие тени, ощущение "техно-объекта".
- Композиция: центрированный "тотем" (главный объект) + 2–6 вторичных элементов вокруг/на орбите, много негативного пространства.
- Никакого текста, букв, цифр, логотипов, водяных знаков, скриншотов интерфейса.

СМЫСЛ (сам выбираешь объекты):
1) Прочитай статью и выпиши для себя 3–5 ключевых понятий.
2) Придумай метафору второго уровня: не "люди/карандаши/рукопожатия", а "архитектура/схема/сеть/панель/модуль/узлы/порталы/слои/сигналы/контуры".
3) Собери один главный объект-символ (тотем), который объединяет смысл статьи.
4) Добавь вторичные элементы, которые намекают на процесс/взаимодействие/метрики.
5) Избегай буквальной иллюстрации "что написано" — делай абстрактную, но читаемую метафору.

ЗАПРЕТЫ (строго):
- никаких людей/персонажей/рук/карандашей/человечков/маскотов
- никаких офисных сцен, рукопожатий, графиков с цифрами, "успешного успеха"
- никаких клипартных иконок, смайлов, стоковой пошлости
- никаких кривых линий: связи только прямые/дуги с идеальной геометрией
- не перегружать: максимум 8 объектов в кадре

[ТЕКСТ СТАТЬИ]
"""

DIAGRAM_PROMPT = """Ты — генератор смысловых визуализаций для статьи. На входе текст статьи. Сгенерируй 2–3 диаграммы, которые помогают понять материал: взаимодействие сущностей, последовательность этапов, причинно-следственные связи метрик. Это НЕ креативные иллюстрации и НЕ инфографика со статистикой — только ясные схемы.

Ограничения:
- Диаграммы должны быть пригодны для рендера в Mermaid и читаться в SVG.
- Без людей/персонажей/эмодзи/клипарта.
- Без длинных абзацев в узлах: 2–5 слов на узел.
- Не больше 12 узлов на диаграмму.
- Никаких чисел/процентов на диаграммах, если это не строго необходимо для смысла.
- Выбери разные типы: (1) процесс/этапы, (2) взаимодействие сущностей, (3) опционально причинность метрик.

Верни СТРОГО JSON (без markdown-блока, просто JSON):
{
  "diagrams": [
    {
      "id": "diagram-1",
      "type": "process|interaction|metrics",
      "title": "Короткий заголовок",
      "caption": "1 предложение пояснения",
      "alt": "alt-текст",
      "mermaid": "mermaid-код одной диаграммы"
    }
  ]
}

[ТЕКСТ СТАТЬИ]
"""


class FormattingStage(WritingStage):
    """
    Stage 10: Formatting (after Meta, last stage)

    Generates:
    - Cover image via DALL-E 3
    - 2-3 Mermaid diagrams rendered to SVG
    - Inserts both into article markdown
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-20250514",
        openai_api_key: str = "",
        openai_proxy_url: str = "",
        openai_proxy_secret: str = "",
    ):
        super().__init__(client=client, model=model)
        self.openai_api_key = openai_api_key
        self.openai_proxy_url = openai_proxy_url
        self.openai_proxy_secret = openai_proxy_secret

    @property
    def name(self) -> str:
        return "formatting"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute formatting stage."""
        log = context.start_stage(self.name)

        try:
            if not context.edited_md:
                logger.info("Formatting skipped: no article content")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            slug = context.meta.slug if context.meta else "article"
            assets_dir = os.path.join(context.output_dir, "assets") if context.output_dir else "/tmp/formatting_assets"
            os.makedirs(assets_dir, exist_ok=True)

            result = FormattingResult()
            tokens_total = 0
            article_md = context.edited_md

            # A) Generate cover
            cover_asset, cover_error = await self._generate_cover(
                article_md, slug, assets_dir
            )
            if cover_asset:
                result.assets.append(cover_asset)
                result.cover_generated = True
                # Insert cover at the beginning of article
                cover_line = f"![{cover_asset.alt}](/assets/{cover_asset.filename})\n\n"
                article_md = cover_line + article_md
            elif cover_error:
                result.errors.append(f"Cover: {cover_error}")

            # B) Generate diagrams
            diagrams, diagram_tokens, diagram_errors = await self._generate_diagrams(
                article_md, slug, assets_dir
            )
            tokens_total += diagram_tokens
            result.diagrams_count = len(diagrams)
            result.errors.extend(diagram_errors)

            for asset in diagrams:
                result.assets.append(asset)

            # Insert diagrams after relevant H2 headings
            if diagrams:
                article_md = self._insert_diagrams(article_md, diagrams)

            context.edited_md = article_md
            context.formatting_result = result

            # Save intermediate files
            if context.save_intermediate and context.output_dir:
                report_path = os.path.join(context.output_dir, "09_formatting_report.json")
                with open(report_path, "w", encoding="utf-8") as f:
                    json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens_total,
                metadata={
                    "cover_generated": result.cover_generated,
                    "diagrams_count": result.diagrams_count,
                    "errors": len(result.errors),
                },
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    async def _generate_cover(
        self, article_md: str, slug: str, assets_dir: str
    ) -> tuple[Optional[FormattingAsset], Optional[str]]:
        """Generate cover image via DALL-E 3."""
        if not self.openai_api_key:
            return None, "No OpenAI API key configured"

        try:
            import openai

            client_kwargs = {"api_key": self.openai_api_key}
            if self.openai_proxy_url:
                client_kwargs["base_url"] = self.openai_proxy_url
                if self.openai_proxy_secret:
                    client_kwargs["default_headers"] = {"x-proxy-token": self.openai_proxy_secret}
            client = openai.OpenAI(**client_kwargs)

            # Prepare article summary for cover prompt (first ~3000 chars + headings)
            headings = re.findall(r'^#{1,3}\s+(.+)$', article_md, re.MULTILINE)
            article_summary = article_md[:3000]
            if headings:
                article_summary += "\n\nЗаголовки: " + " | ".join(headings)

            prompt = COVER_PROMPT + article_summary

            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )

            image_url = response.data[0].url

            # Download the image
            import httpx
            async with httpx.AsyncClient() as http:
                img_response = await http.get(image_url)
                img_response.raise_for_status()

            filename = f"{slug}__cover.png"
            filepath = os.path.join(assets_dir, filename)
            with open(filepath, "wb") as f:
                f.write(img_response.content)

            logger.info(f"Cover generated: {filepath}")
            return FormattingAsset(
                type="cover",
                filename=filename,
                path=filepath,
                alt=f"Обложка статьи",
                caption="",
            ), None

        except Exception as e:
            logger.error(f"Cover generation failed: {e}")
            return None, str(e)

    async def _generate_diagrams(
        self, article_md: str, slug: str, assets_dir: str
    ) -> tuple[list[FormattingAsset], int, list[str]]:
        """Generate Mermaid diagrams and render to SVG."""
        assets = []
        errors = []
        tokens_total = 0

        # Check if mmdc is available
        mmdc_available = self._check_mmdc()
        if not mmdc_available:
            return assets, 0, ["mmdc (mermaid-cli) not available, skipping diagrams"]

        # Get diagram specs from LLM
        prompt = DIAGRAM_PROMPT + article_md[:8000]
        try:
            response_text, tokens = self._call_llm(prompt, max_tokens=4096, temperature=0.4)
            tokens_total += tokens
        except Exception as e:
            return assets, 0, [f"Diagram LLM call failed: {e}"]

        # Parse diagram specs
        try:
            data = self._parse_json_response(response_text)
            diagrams = data.get("diagrams", [])
        except Exception as e:
            return assets, tokens_total, [f"Failed to parse diagram specs: {e}"]

        # Render each diagram
        for i, diagram in enumerate(diagrams[:3]):
            diagram_id = diagram.get("id", f"diagram-{i+1}")
            mermaid_code = diagram.get("mermaid", "")
            title = diagram.get("title", "")
            caption = diagram.get("caption", "")
            alt = diagram.get("alt", title)

            if not mermaid_code:
                errors.append(f"{diagram_id}: empty mermaid code")
                continue

            filename = f"{slug}__{diagram_id}.svg"
            filepath = os.path.join(assets_dir, filename)

            # Attempt 1: render Mermaid
            success = self._render_mermaid(mermaid_code, filepath)

            if not success:
                # Retry: ask LLM to fix the mermaid code
                logger.info(f"Retrying {diagram_id} with LLM fix")
                try:
                    fix_prompt = (
                        f"The following Mermaid code failed to render. Fix the syntax errors "
                        f"and return ONLY the corrected Mermaid code, nothing else:\n\n{mermaid_code}"
                    )
                    fixed_code, fix_tokens = self._call_llm(fix_prompt, max_tokens=2048, temperature=0.1)
                    tokens_total += fix_tokens
                    fixed_code = fixed_code.strip()
                    if fixed_code.startswith("```"):
                        fixed_code = re.sub(r'^```\w*\n?', '', fixed_code)
                        fixed_code = re.sub(r'```$', '', fixed_code).strip()
                    success = self._render_mermaid(fixed_code, filepath)
                except Exception as e:
                    logger.warning(f"Retry failed for {diagram_id}: {e}")

            if success:
                assets.append(FormattingAsset(
                    type="diagram",
                    filename=filename,
                    path=filepath,
                    alt=alt,
                    caption=caption,
                ))
                logger.info(f"Diagram rendered: {filepath}")
            else:
                errors.append(f"{diagram_id}: render failed after retry")

        return assets, tokens_total, errors

    def _check_mmdc(self) -> bool:
        """Check if mmdc (mermaid-cli) is available."""
        try:
            result = subprocess.run(
                ["mmdc", "--version"],
                capture_output=True, timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _render_mermaid(self, mermaid_code: str, output_path: str) -> bool:
        """Render Mermaid code to SVG using mmdc."""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".mmd", delete=False, encoding="utf-8"
            ) as f:
                f.write(mermaid_code)
                input_path = f.name

            result = subprocess.run(
                ["mmdc", "-i", input_path, "-o", output_path, "-b", "transparent"],
                capture_output=True,
                timeout=30,
                text=True,
            )

            os.unlink(input_path)

            if result.returncode != 0:
                logger.warning(f"mmdc failed: {result.stderr[:500]}")
                return False

            return os.path.exists(output_path)

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.warning(f"mmdc execution error: {e}")
            return False

    def _insert_diagrams(
        self, article_md: str, diagrams: list[FormattingAsset]
    ) -> str:
        """Insert diagram figures after relevant H2 headings."""
        lines = article_md.split("\n")
        h2_positions = []

        for i, line in enumerate(lines):
            if line.startswith("## "):
                h2_positions.append(i)

        if not h2_positions or not diagrams:
            return article_md

        # Distribute diagrams evenly across H2 sections
        # Skip first H2 (usually intro-adjacent), distribute across remaining
        target_positions = h2_positions[1:] if len(h2_positions) > 1 else h2_positions
        step = max(1, len(target_positions) // len(diagrams))

        insertions = {}  # line_number -> diagram HTML
        for idx, diagram in enumerate(diagrams):
            pos_idx = min(idx * step, len(target_positions) - 1)
            h2_line = target_positions[pos_idx]

            # Find end of the first paragraph after H2
            insert_at = h2_line + 1
            for j in range(h2_line + 1, min(h2_line + 20, len(lines))):
                if lines[j].strip() == "" and j > h2_line + 1:
                    insert_at = j + 1
                    break

            figure_html = (
                f'\n<figure>\n'
                f'  <img src="/assets/{diagram.filename}" alt="{diagram.alt}">\n'
                f'  <figcaption>{diagram.caption}</figcaption>\n'
                f'</figure>\n'
            )
            insertions[insert_at] = figure_html

        # Insert in reverse order to preserve line numbers
        for pos in sorted(insertions.keys(), reverse=True):
            lines.insert(pos, insertions[pos])

        return "\n".join(lines)
