"""
Formatting Stage - Generates cover image and SVG charts.

Cover → DALL-E 3 → upload to Ghost → feature_image (not in body).
Charts → LLM SVG → cairosvg PNG → upload to Ghost → inline <figure> blocks.
"""

import asyncio
import html
import json
import os
import logging
import re
from typing import Optional
from difflib import SequenceMatcher

import anthropic

_MAX_SVG_BYTES = 200_000  # 200KB safety cap for LLM-generated SVG

try:
    import cairosvg
    _HAS_CAIROSVG = True
except ImportError:
    _HAS_CAIROSVG = False

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import FormattingResult, FormattingAsset

logger = logging.getLogger(__name__)


COVER_SCENE_PROMPT = """You are an art director for a pixel-art blog. Read the article and describe ONE specific, unique scene for the cover in 3-5 sentences in English.

Rules:
- The scene is a visual METAPHOR for the article's core idea — not a literal depiction of the topic
- Be CREATIVE with settings. Choose from a WIDE range: a rooftop garden, a busy marketplace, a cat napping on a stack of books, a lighthouse at dawn, a workshop with tools, a train station, a forest path, a kitchen table with scattered notes, a cityscape from above, a boat on a calm lake — pick what resonates with THIS specific article
- VARY the time of day and lighting: bright morning sun, overcast afternoon, neon-lit night, foggy dawn, golden sunset, blue hour — not always evening
- VARY the color mood: cool blues, lush greens, warm oranges, muted pastels, vibrant neons — not always warm tones
- Include 3-5 specific objects that tell a story about the scene
- AVOID: generic desk-with-laptop-and-window scenes. Every article deserves its own world
- NO people, NO hands, NO faces, NO text, NO letters, NO numbers, NO logos
- Write ONLY the scene description, nothing else

[ARTICLE TEXT]
"""

COVER_STYLE_PREFIX = (
    "Wide 16:9 high-quality pixel art illustration. "
    "Detailed retro pixel art style inspired by Owlboy and Eastward. "
    "Fine pixel detail on objects and textures, slight dithering for smooth gradients. "
    "Rich, atmospheric, NOT flat or cartoony. "
    "No text, no letters, no numbers, no logos, no people, no hands. "
    "Scene: "
)

SVG_CHARTS_PROMPT_FILE = "svg_charts_v1"


def _sanitize_chart_id(raw_id: str, fallback: str) -> str:
    """Sanitize LLM-provided chart ID to safe filename component."""
    cleaned = re.sub(r'[^a-z0-9-]', '', raw_id.lower().strip())
    return cleaned[:30] if cleaned else fallback


class FormattingStage(WritingStage):
    """
    Stage 10: Formatting (after Meta, last stage)

    Generates:
    - Cover image via DALL-E 3 → uploaded to Ghost as feature_image
    - 2-3 SVG charts rendered via cairosvg → PNG → uploaded to Ghost → inline <figure>
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
            total_in = 0
            total_out = 0
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
            cover_asset, cover_error, cover_in, cover_out = await self._generate_cover(
                article_md, slug, assets_dir, publisher, article_title
            )
            total_in += cover_in
            total_out += cover_out
            if cover_asset:
                result.assets.append(cover_asset)
                result.cover_generated = True
                result.cover_ghost_url = cover_asset.ghost_url
                result.cover_image_alt = cover_asset.alt
            elif cover_error:
                result.errors.append(f"Cover: {cover_error}")

            # B) Generate SVG charts
            diagrams, diagram_tokens, diagram_errors, diag_in, diag_out = await self._generate_svg_charts(
                article_md, slug, assets_dir, publisher
            )
            tokens_total += diagram_tokens
            total_in += diag_in
            total_out += diag_out
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

            # Collect chart diagnostics
            chart_types = []
            chart_placements = []
            for asset in diagrams:
                chart_types.append(getattr(asset, '_chart_type', 'unknown'))
                chart_placements.append(getattr(asset, '_after_heading', ''))

            context.complete_stage(
                input_tokens=total_in,
                output_tokens=total_out,
                metadata={
                    "cover_generated": result.cover_generated,
                    "cover_ghost_url": result.cover_ghost_url,
                    "diagrams_count": result.diagrams_count,
                    "chart_types": chart_types,
                    "chart_placements": chart_placements,
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
    ) -> tuple[Optional[FormattingAsset], Optional[str], int, int]:
        """Generate cover image via two-stage approach: Claude describes scene, gpt-image-1.5 renders."""
        if not self.openai_api_key:
            return None, "No OpenAI API key configured", 0, 0

        try:
            import openai
            import base64 as b64

            # Stage 1: Claude generates scene description
            article_truncated = article_md[:6000]
            scene_prompt = COVER_SCENE_PROMPT + article_truncated
            scene_description, _scene_in, _scene_out = self._call_llm(
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
                quality="medium",
                n=1,
            )

            # gpt-image-1 returns base64 PNG → convert to WebP for smaller size
            image_base64 = response.data[0].b64_json
            raw_bytes = b64.b64decode(image_base64)
            logger.info(f"Cover raw PNG: {len(raw_bytes)} bytes")

            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(raw_bytes))
            webp_buf = BytesIO()
            img.save(webp_buf, format="WEBP", quality=85, method=6)
            image_bytes = webp_buf.getvalue()

            filename = f"{slug}__cover.webp"
            filepath = os.path.join(assets_dir, filename)
            with open(filepath, "wb") as f:
                f.write(image_bytes)

            logger.info(f"Cover generated: {filepath} ({len(image_bytes)} bytes, "
                        f"{100 * len(image_bytes) / len(raw_bytes):.0f}% of original)")

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
            ), None, _scene_in, _scene_out

        except Exception as e:
            logger.error(f"Cover generation failed: {e}")
            return None, str(e), 0, 0

    async def _generate_svg_charts(
        self, article_md: str, slug: str, assets_dir: str, publisher,
    ) -> tuple[list[FormattingAsset], int, list[str], int, int]:
        """Generate SVG charts via LLM, render to PNG via cairosvg, upload to Ghost."""
        assets = []
        errors = []
        tokens_total = 0
        total_in = 0
        total_out = 0

        if not _HAS_CAIROSVG:
            logger.warning("cairosvg not available — skipping chart generation")
            return assets, 0, ["cairosvg not installed, charts skipped"], 0, 0

        # Load prompt and inject article text
        try:
            prompt_template = self._load_prompt(SVG_CHARTS_PROMPT_FILE)
        except Exception as e:
            return assets, 0, [f"Failed to load chart prompt: {e}"], 0, 0
        prompt = prompt_template.replace("{{article_md}}", article_md[:8000])

        try:
            response_text, in_t, out_t = self._call_llm(prompt, max_tokens=16384, temperature=0.4)
            tokens_total += in_t + out_t
            total_in += in_t
            total_out += out_t
        except Exception as e:
            return assets, 0, [f"Chart LLM call failed: {e}"], 0, 0

        # Parse chart specs
        try:
            data = self._parse_json_response(response_text)
            charts = data.get("charts", [])
        except Exception as e:
            return assets, tokens_total, [f"Failed to parse chart specs: {e}"], total_in, total_out

        # Render each chart: SVG → PNG
        for i, chart in enumerate(charts[:3]):
            try:
                chart_id = _sanitize_chart_id(chart.get("id", ""), f"chart-{i+1}")
                chart_type = chart.get("type", "unknown")
                svg_code = chart.get("svg", "")
                title = chart.get("title", "")
                caption = chart.get("caption", "")
                alt = chart.get("alt", title)
                after_heading = chart.get("after_heading", "")

                if not svg_code:
                    errors.append(f"{chart_id}: empty SVG code")
                    continue

                if len(svg_code.encode("utf-8")) > _MAX_SVG_BYTES:
                    errors.append(f"{chart_id}: SVG exceeds {_MAX_SVG_BYTES} bytes, skipped")
                    continue

                # Save raw SVG for debugging
                svg_filename = f"{slug}__{chart_id}.svg"
                svg_filepath = os.path.join(assets_dir, svg_filename)
                with open(svg_filepath, "w", encoding="utf-8") as f:
                    f.write(svg_code)

                # Render SVG → PNG
                png_filename = f"{slug}__{chart_id}.png"
                png_filepath = os.path.join(assets_dir, png_filename)

                success = await self._render_svg_to_png(svg_code, png_filepath)

                if not success:
                    # Retry: ask LLM to fix the SVG
                    logger.info(f"Retrying {chart_id} with LLM fix")
                    try:
                        fix_prompt = (
                            f"The following SVG failed to render via cairosvg. "
                            f"Common issues: emoji characters, ₽ symbol, → character, "
                            f"marker-end without defined marker, external images, CSS @import. "
                            f"Fix the SVG and return ONLY the corrected SVG code, nothing else.\n\n{svg_code}"
                        )
                        fixed_svg, fix_in, fix_out = self._call_llm(fix_prompt, max_tokens=8192, temperature=0.1)
                        tokens_total += fix_in + fix_out
                        total_in += fix_in
                        total_out += fix_out
                        fixed_svg = fixed_svg.strip()
                        if fixed_svg.startswith("```"):
                            fixed_svg = re.sub(r'^```\w*\n?', '', fixed_svg)
                            fixed_svg = re.sub(r'```$', '', fixed_svg).strip()
                        # Save fixed SVG (with size cap)
                        if len(fixed_svg.encode("utf-8")) <= _MAX_SVG_BYTES:
                            with open(svg_filepath, "w", encoding="utf-8") as f:
                                f.write(fixed_svg)
                            success = await self._render_svg_to_png(fixed_svg, png_filepath)
                        else:
                            logger.warning(f"Fixed SVG for {chart_id} exceeds size cap")
                    except Exception as e:
                        logger.warning(f"Retry failed for {chart_id}: {e}")

                if success:
                    # Upload PNG to Ghost (never raw SVG)
                    ghost_url = ""
                    if publisher:
                        uploaded_url = publisher.upload_image(png_filepath, ref=f"{slug}-{chart_id}")
                        if uploaded_url:
                            ghost_url = uploaded_url
                            logger.info(f"Chart uploaded to Ghost: {ghost_url}")
                        else:
                            logger.warning(f"Chart {chart_id} upload to Ghost failed")

                    asset = FormattingAsset(
                        type="diagram",  # backward compat with DB
                        filename=png_filename,
                        path=png_filepath,
                        alt=alt,
                        caption=caption,
                        ghost_url=ghost_url,
                    )
                    asset._after_heading = after_heading
                    asset._chart_type = chart_type
                    assets.append(asset)
                    logger.info(f"Chart rendered: {png_filepath} (type={chart_type})")
                else:
                    errors.append(f"{chart_id}: SVG render failed after retry")

            except Exception as e:
                cid = f"chart-{i+1}"
                errors.append(f"{cid}: unexpected error: {e}")
                logger.warning(f"Chart {cid} failed: {e}")

        return assets, tokens_total, errors, total_in, total_out

    async def _render_svg_to_png(self, svg_code: str, output_path: str) -> bool:
        """Render SVG string to PNG via cairosvg (CPU-bound, run in thread)."""
        if not _HAS_CAIROSVG:
            return False

        def _do_render():
            try:
                svg_bytes = svg_code.encode("utf-8")
                # unsafe=False (default) blocks external resource fetches
                cairosvg.svg2png(
                    bytestring=svg_bytes,
                    write_to=output_path,
                    output_width=1600,
                    unsafe=False,
                )
                return os.path.exists(output_path) and os.path.getsize(output_path) > 0
            except Exception as e:
                logger.warning(f"cairosvg render failed: {e}")
                return False

        return await asyncio.to_thread(_do_render)

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
            raw_src = diagram.ghost_url if diagram.ghost_url else f"/assets/{diagram.filename}"
            safe_src = html.escape(raw_src, quote=True)
            safe_alt = html.escape(diagram.alt, quote=True)
            safe_caption = html.escape(diagram.caption)
            figure_html = (
                f'\n<figure>\n'
                f'  <img src="{safe_src}" alt="{safe_alt}">\n'
                f'  <figcaption>{safe_caption}</figcaption>\n'
                f'</figure>\n'
            )
            insertions[insert_at] = figure_html

        # Insert in reverse order to preserve line numbers
        for pos in sorted(insertions.keys(), reverse=True):
            lines.insert(pos, insertions[pos])

        return "\n".join(lines)
