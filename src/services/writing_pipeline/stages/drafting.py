"""
Drafting Stage - Writes article text following the outline.
"""

import json

from ..core.stage import WritingStage
from ..core.context import WritingContext


class DraftingStage(WritingStage):
    """
    Stage 4: Drafting

    Writes the article text strictly following:
    - Outline structure (H2/H3, content blocks)
    - Word count targets
    - Research pack facts (no new facts added)
    - Tone from intent spec
    """

    @property
    def name(self) -> str:
        return "drafting"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute drafting stage."""
        log = context.start_stage(self.name)

        try:
            if context.intent is None:
                raise ValueError("Intent stage must be completed before drafting")
            if context.research is None:
                raise ValueError("Research stage must be completed before drafting")
            if context.outline is None:
                raise ValueError("Structure stage must be completed before drafting")

            # Load and fill prompt template
            prompt_template = self._load_prompt("drafting_v1")

            intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
            outline_json = json.dumps(context.outline.to_dict(), ensure_ascii=False, indent=2)
            research_json = json.dumps(context.research.to_dict(), ensure_ascii=False, indent=2)

            prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
            prompt = prompt.replace("{{outline_json}}", outline_json)
            prompt = prompt.replace("{{research_pack_json}}", research_json)

            # Call LLM with high token limit for full article
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.7,
            )

            # Draft is markdown text, not JSON
            context.draft_md = response_text.strip()

            # Calculate word count
            word_count = len(context.draft_md.split())

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "06_draft.md")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(context.draft_md)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "word_count": word_count,
                    "target_words": context.outline.target_total_words,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
