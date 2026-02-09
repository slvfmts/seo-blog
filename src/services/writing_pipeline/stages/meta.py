"""
Meta Stage - Generates SEO metadata from finished article.
"""

import json
import os

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import MetaResult


class MetaStage(WritingStage):
    """
    Stage 6: Meta

    Generates optimized SEO metadata from the finished article:
    - meta_title (≤60 chars, keyword near start)
    - meta_description (≤160 chars, keyword + CTA)
    - slug (lowercase, hyphens, 3-5 words)
    """

    @property
    def name(self) -> str:
        return "meta"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute meta generation stage."""
        log = context.start_stage(self.name)

        try:
            if context.edited_md is None:
                raise ValueError("Editing stage must be completed before meta generation")
            if context.intent is None:
                raise ValueError("Intent stage must be completed before meta generation")
            if context.outline is None:
                raise ValueError("Structure stage must be completed before meta generation")

            # Load and fill prompt template
            prompt_template = self._load_prompt("meta_v1")

            prompt = prompt_template.replace("{{topic}}", context.intent.topic)
            prompt = prompt.replace("{{primary_intent}}", context.intent.primary_intent)
            prompt = prompt.replace("{{audience_role}}", context.intent.audience.role)
            prompt = prompt.replace("{{article_title}}", context.outline.title)
            prompt = prompt.replace("{{article_md}}", context.edited_md)

            # Call LLM with low token limit (JSON output is small)
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=1024,
                temperature=0.4,
            )

            # Parse JSON response
            data = self._parse_json_response(response_text)
            meta = MetaResult.from_dict(data)

            # Validate and truncate if needed
            if len(meta.meta_title) > 60:
                meta.meta_title = meta.meta_title[:57] + "..."
            if len(meta.meta_description) > 160:
                meta.meta_description = meta.meta_description[:157] + "..."

            # Store in context
            context.meta = meta

            # Save intermediate result
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "08_meta.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "meta_title_len": len(meta.meta_title),
                    "meta_description_len": len(meta.meta_description),
                    "slug": meta.slug,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
