"""
Editing Stage - Final editing and markdown formatting.
"""

from ..core.stage import WritingStage
from ..core.context import WritingContext


class EditingStage(WritingStage):
    """
    Stage 5: Editing

    Final pass on the article:
    - Improves clarity and readability
    - Removes filler words and repetition
    - Ensures consistent markdown formatting
    - Does NOT add new facts or change meaning
    """

    @property
    def name(self) -> str:
        return "editing"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute editing stage."""
        log = context.start_stage(self.name)

        try:
            if context.draft_md is None:
                raise ValueError("Drafting stage must be completed before editing")

            # Load and fill prompt template
            prompt_template = self._load_prompt("editing_v1")
            prompt = prompt_template.replace("{{draft_md}}", context.draft_md)

            # Call LLM
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=16000,
                temperature=0.3,  # Lower temperature for more consistent editing
            )

            # Edited result is markdown text
            context.edited_md = response_text.strip()

            # Calculate word count
            word_count = len(context.edited_md.split())

            # Save final result if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "07_edited.md")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(context.edited_md)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "word_count": word_count,
                    "draft_word_count": len(context.draft_md.split()),
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
