"""
Formatting Stage - Generates cover image and Mermaid diagrams.

Cover → DALL-E 3 → upload to Ghost → feature_image (not in body).
Diagrams → LLM → kroki.io PNG → upload to Ghost → inline <figure> blocks.
"""

import json
import os
import logging
import re
from typing import Optional
from difflib import SequenceMatcher

import anthropic
import httpx

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import FormattingResult, FormattingAsset

logger = logging.getLogger(__name__)


COVER_SCENE_PROMPT = """You are an art director for a cozy pixel-art blog. Read the article and describe ONE specific scene for the cover in 3-5 sentences in English.

Rules:
- The scene should be an atmospheric interior or landscape that metaphorically relates to the article topic
- Think cozy spaces: a study with bookshelves, a workshop, a window overlooking a city, a garden, a library, a café, a cabin in the woods — pick what fits the topic
- Include environmental storytelling details: warm lighting (lamps, candles, sunset glow, fireplace), objects on shelves/desks, weather outside windows, plants, cats, mugs
- Time of day matters: evening/night with warm interior light is preferred, but dawn/morning works too
- NO people, NO hands, NO faces, NO text, NO letters, NO numbers, NO logos
- Write ONLY the scene description, nothing else

[ARTICLE TEXT]
"""

COVER_STYLE_PREFIX = (
    "Wide 16:9 high-quality pixel art illustration. Detailed retro pixel art style inspired by Owlboy and Eastward. "
    "Rich warm color palette with atmospheric lighting — golden hour, cozy lamplight, or soft moonlight. "
    "Fine pixel detail on objects and textures, slight dithering for smooth gradients. "
    "Moody and atmospheric, NOT flat or cartoony. No text, no letters, no numbers, no logos, no people, no hands. "
    "Scene: "
)

DIAGRAM_PROMPT = """Ты — генератор смысловых визуализаций для статьи. На входе текст статьи. Сгенерируй 2–3 диаграммы, которые помогают понять материал: взаимодействие сущностей, последовательность этапов, причинно-следственные связи метрик. Это НЕ креативные иллюстрации и НЕ инфографика со статистикой — только ясные схемы.

Ограничения:
- Диаграммы должны быть пригодны для рендера в Mermaid и читаться в PNG.
- Без людей/персонажей/эмодзи/клипарта.
- Без длинных абзацев в узлах: 2–5 слов на узел.
- Не больше 12 узлов на диаграмму.
- Никаких чисел/процентов на диаграммах, если это не строго необходимо для смысла.
- Выбери разные типы: (1) процесс/этапы, (2) взаимодействие сущностей, (3) опционально причинность метрик.
- НЕ используй subgraph — он часто ломает рендер. Используй простые graph TD, flowchart TD или sequenceDiagram.
- В текстах узлов не используй кавычки и спецсимволы (&, <, >, #). Только буквы, цифры, пробелы, тире.

Для каждой диаграммы укажи `after_heading` — текст заголовка H2 из статьи, после которого диаграмма должна быть размещена. Выбирай H2, к которому диаграмма семантически относится.

Верни СТРОГО JSON (без markdown-блока, просто JSON):
{
  "diagrams": [
    {
      "id": "diagram-1",
      "type": "process|interaction|metrics",
      "after_heading": "Точный текст H2 заголовка",
      "title": "Короткий заголовок",
      "caption": "1 предложение пояснения",
      "alt": "alt-текст для доступности",
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
    - Cover image via DALL-E 3 → uploaded to Ghost as feature_image
    - 2-3 Mermaid diagrams rendered via kroki.io → uploaded to Ghost → inline <figure>
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-20250514",
        openai_api_key: str = "",
        openai_proxy_url: str = "",
        openai_proxy_secret: str = "",
        ghost_url: str = "",
        ghost_admin_key: str = "",
    ):
        super().__init__(client=client, model=model)
        self.openai_api_key = openai_api_key
        self.openai_proxy_url = openai_proxy_url
        self.openai_proxy_secret = openai_proxy_secret
        self.ghost_url = ghost_url
        self.ghost_admin_key = ghost_admin_key

    def _get_publisher(self):
        """Get GhostPublisher instance if credentials are available."""
        if self.ghost_url and self.ghost_admin_key:
            from ...publisher import GhostPublisher
            return GhostPublisher(ghost_url=self.ghost_url, admin_key=self.ghost_admin_key)
        return None

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
            publisher = self._get_publisher()

            # Derive article title for alt-text
            article_title = ""
            if context.outline:
                article_title = context.outline.title
            if not article_title:
                # Fallback: extract first H1 from markdown
                h1_match = re.search(r'^#\s+(.+)$', article_md, re.MULTILINE)
                if h1_match:
                    article_title = h1_match.group(1).strip()

            # A) Generate cover (uploaded to Ghost, NOT inserted into body)
            cover_asset, cover_error = await self._generate_cover(
                article_md, slug, assets_dir, publisher, article_title
            )
            if cover_asset:
                result.assets.append(cover_asset)
                result.cover_generated = True
                result.cover_ghost_url = cover_asset.ghost_url
                result.cover_image_alt = cover_asset.alt
            elif cover_error:
                result.errors.append(f"Cover: {cover_error}")

            # B) Generate diagrams
            diagrams, diagram_tokens, diagram_errors = await self._generate_diagrams(
                article_md, slug, assets_dir, publisher
            )
            tokens_total += diagram_tokens
            result.diagrams_count = len(diagrams)
            result.errors.extend(diagram_errors)

            for asset in diagrams:
                result.assets.append(asset)

            # Insert diagrams after semantically relevant H2 headings
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
                    "cover_ghost_url": result.cover_ghost_url,
                    "diagrams_count": result.diagrams_count,
                    "errors": len(result.errors),
                },
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    async def _generate_cover(
        self, article_md: str, slug: str, assets_dir: str,
        publisher, article_title: str,
    ) -> tuple[Optional[FormattingAsset], Optional[str]]:
        """Generate cover image via two-stage approach: Claude describes scene, gpt-image-1.5 renders."""
        if not self.openai_api_key:
            return None, "No OpenAI API key configured"

        try:
            import openai
            import base64 as b64

            # Stage 1: Claude generates scene description
            article_truncated = article_md[:6000]
            scene_prompt = COVER_SCENE_PROMPT + article_truncated
            scene_description, scene_tokens = self._call_llm(
                scene_prompt, max_tokens=300, temperature=0.7,
            )
            scene_description = scene_description.strip()
            logger.info(f"Cover scene description: {scene_description[:200]}")

            # Stage 2: gpt-image-1.5 renders the scene
            client_kwargs = {"api_key": self.openai_api_key}
            if self.openai_proxy_url:
                client_kwargs["base_url"] = self.openai_proxy_url
                if self.openai_proxy_secret:
                    client_kwargs["default_headers"] = {"x-proxy-token": self.openai_proxy_secret}
            client = openai.OpenAI(**client_kwargs)

            image_prompt = COVER_STYLE_PREFIX + scene_description

            response = client.images.generate(
                model="gpt-image-1.5",
                prompt=image_prompt,
                size="1536x1024",
                quality="high",
                n=1,
            )

            # gpt-image-1 returns base64
            image_base64 = response.data[0].b64_json
            image_bytes = b64.b64decode(image_base64)

            filename = f"{slug}__cover.png"
            filepath = os.path.join(assets_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)

            logger.info(f"Cover generated: {filepath} ({len(image_bytes)} bytes)")

            # Dynamic alt-text from article title
            alt_text = f"{article_title} — обложка статьи" if article_title else "Обложка статьи"

            # Upload to Ghost
            ghost_url = ""
            if publisher:
                uploaded_url = publisher.upload_image(filepath, ref=f"{slug}-cover")
                if uploaded_url:
                    ghost_url = uploaded_url
                    logger.info(f"Cover uploaded to Ghost: {ghost_url}")
                else:
                    logger.warning("Cover upload to Ghost failed, cover won't be visible")

            return FormattingAsset(
                type="cover",
                filename=filename,
                path=filepath,
                alt=alt_text,
                caption="",
                ghost_url=ghost_url,
            ), None

        except Exception as e:
            logger.error(f"Cover generation failed: {e}")
            return None, str(e)

    async def _generate_diagrams(
        self, article_md: str, slug: str, assets_dir: str, publisher,
    ) -> tuple[list[FormattingAsset], int, list[str]]:
        """Generate Mermaid diagrams via LLM, render via kroki.io, upload to Ghost."""
        assets = []
        errors = []
        tokens_total = 0

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

        # Render each diagram via kroki.io
        for i, diagram in enumerate(diagrams[:3]):
            diagram_id = diagram.get("id", f"diagram-{i+1}")
            mermaid_code = diagram.get("mermaid", "")
            title = diagram.get("title", "")
            caption = diagram.get("caption", "")
            alt = diagram.get("alt", title)
            after_heading = diagram.get("after_heading", "")

            if not mermaid_code:
                errors.append(f"{diagram_id}: empty mermaid code")
                continue

            filename = f"{slug}__{diagram_id}.png"
            filepath = os.path.join(assets_dir, filename)

            # Attempt 1: render via kroki.io
            success = await self._render_mermaid_kroki(mermaid_code, filepath)

            if not success:
                # Retry: ask LLM to fix the mermaid code
                logger.info(f"Retrying {diagram_id} with LLM fix")
                try:
                    fix_prompt = (
                        f"The following Mermaid code failed to render via kroki.io. Fix the syntax errors "
                        f"and return ONLY the corrected Mermaid code, nothing else. "
                        f"Do NOT use subgraph. Avoid special characters in node text.\n\n{mermaid_code}"
                    )
                    fixed_code, fix_tokens = self._call_llm(fix_prompt, max_tokens=2048, temperature=0.1)
                    tokens_total += fix_tokens
                    fixed_code = fixed_code.strip()
                    if fixed_code.startswith("```"):
                        fixed_code = re.sub(r'^```\w*\n?', '', fixed_code)
                        fixed_code = re.sub(r'```$', '', fixed_code).strip()
                    success = await self._render_mermaid_kroki(fixed_code, filepath)
                except Exception as e:
                    logger.warning(f"Retry failed for {diagram_id}: {e}")

            if success:
                # Upload to Ghost
                ghost_url = ""
                if publisher:
                    uploaded_url = publisher.upload_image(filepath, ref=f"{slug}-{diagram_id}")
                    if uploaded_url:
                        ghost_url = uploaded_url
                        logger.info(f"Diagram uploaded to Ghost: {ghost_url}")
                    else:
                        logger.warning(f"Diagram {diagram_id} upload to Ghost failed")

                asset = FormattingAsset(
                    type="diagram",
                    filename=filename,
                    path=filepath,
                    alt=alt,
                    caption=caption,
                    ghost_url=ghost_url,
                )
                # Store after_heading as metadata for insertion
                asset._after_heading = after_heading
                assets.append(asset)
                logger.info(f"Diagram rendered: {filepath}")
            else:
                errors.append(f"{diagram_id}: render failed after retry")

        return assets, tokens_total, errors

    async def _render_mermaid_kroki(self, mermaid_code: str, output_path: str) -> bool:
        """Render Mermaid code to PNG via kroki.io."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://kroki.io/mermaid/png",
                    content=mermaid_code.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )

            if response.status_code != 200:
                logger.warning(f"kroki.io returned {response.status_code}: {response.text[:300]}")
                return False

            with open(output_path, "wb") as f:
                f.write(response.content)

            return os.path.exists(output_path) and os.path.getsize(output_path) > 0

        except Exception as e:
            logger.warning(f"kroki.io request failed: {e}")
            return False

    def _insert_diagrams(
        self, article_md: str, diagrams: list[FormattingAsset]
    ) -> str:
        """Insert diagram figures after semantically relevant H2 headings."""
        lines = article_md.split("\n")

        # Collect H2 positions and their text
        h2_entries = []  # [(line_index, heading_text)]
        for i, line in enumerate(lines):
            if line.startswith("## "):
                h2_entries.append((i, line[3:].strip()))

        if not h2_entries or not diagrams:
            return article_md

        # Build insertion plan: for each diagram, find best matching H2
        insertions = {}  # line_number -> figure HTML
        used_h2_indices = set()

        for diagram in diagrams:
            after_heading = getattr(diagram, '_after_heading', '')
            target_h2_idx = None

            if after_heading:
                # Fuzzy match: find the H2 closest to after_heading
                best_ratio = 0.0
                best_idx = None
                for idx, (line_pos, h2_text) in enumerate(h2_entries):
                    ratio = SequenceMatcher(None, after_heading.lower(), h2_text.lower()).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_idx = idx
                # Accept if similarity > 0.4
                if best_ratio > 0.4 and best_idx is not None:
                    target_h2_idx = best_idx

            if target_h2_idx is None:
                # Fallback: even distribution across remaining H2s
                available = [i for i in range(len(h2_entries)) if i not in used_h2_indices]
                if not available:
                    available = list(range(len(h2_entries)))
                # Pick the one closest to even spacing
                target_h2_idx = available[len(available) // 2]

            # Enforce minimum gap: skip if another diagram within 2 H2 sections
            too_close = False
            for used_idx in used_h2_indices:
                if abs(target_h2_idx - used_idx) < 2:
                    too_close = True
                    break
            if too_close:
                # Find alternative further away
                candidates = sorted(
                    [i for i in range(len(h2_entries)) if all(abs(i - u) >= 2 for u in used_h2_indices)],
                    key=lambda i: abs(i - target_h2_idx)
                )
                if candidates:
                    target_h2_idx = candidates[0]
                # If no good candidate, use the original anyway

            used_h2_indices.add(target_h2_idx)

            h2_line = h2_entries[target_h2_idx][0]

            # Find end of the first paragraph after H2
            insert_at = h2_line + 1
            for j in range(h2_line + 1, min(h2_line + 20, len(lines))):
                if lines[j].strip() == "" and j > h2_line + 1:
                    insert_at = j + 1
                    break

            # Use Ghost URL if available, otherwise local path
            img_src = diagram.ghost_url if diagram.ghost_url else f"/assets/{diagram.filename}"

            figure_html = (
                f'\n<figure>\n'
                f'  <img src="{img_src}" alt="{diagram.alt}">\n'
                f'  <figcaption>{diagram.caption}</figcaption>\n'
                f'</figure>\n'
            )
            insertions[insert_at] = figure_html

        # Insert in reverse order to preserve line numbers
        for pos in sorted(insertions.keys(), reverse=True):
            lines.insert(pos, insertions[pos])

        return "\n".join(lines)
