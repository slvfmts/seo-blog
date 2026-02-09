"""
Structure Stage - Creates article outline with word count distribution.
"""

import json

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import OutlineResult


class StructureStage(WritingStage):
    """
    Stage 3: Structure (Outline)

    Creates the article architecture:
    - Title and subtitle
    - Introduction with key points
    - H2/H3 sections with content blocks
    - Word count distribution
    - Coverage mapping to must_answer_questions
    """

    @property
    def name(self) -> str:
        return "structure"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute structure stage."""
        log = context.start_stage(self.name)

        try:
            if context.intent is None:
                raise ValueError("Intent stage must be completed before structure")
            if context.research is None:
                raise ValueError("Research stage must be completed before structure")

            # Load and fill prompt template
            prompt_template = self._load_prompt("structure_v1")

            intent_json = json.dumps(context.intent.to_dict(), ensure_ascii=False, indent=2)
            research_json = json.dumps(context.research.to_dict(), ensure_ascii=False, indent=2)

            # Extract competitor analysis from research if available
            if context.research.competitor_analysis:
                competitor_json = json.dumps(
                    context.research.competitor_analysis, ensure_ascii=False, indent=2
                )
            else:
                competitor_json = "null"

            # E-E-A-T signals from research
            if context.research.eeat_signals:
                eeat_json = json.dumps(
                    context.research.eeat_signals, ensure_ascii=False, indent=2
                )
            else:
                eeat_json = "null"

            # Keyword clusters from research
            if context.research.keyword_clusters:
                clusters_json = json.dumps(
                    context.research.keyword_clusters.to_dict(), ensure_ascii=False, indent=2
                )
            else:
                clusters_json = "null"

            prompt = prompt_template.replace("{{intent_spec_json}}", intent_json)
            prompt = prompt.replace("{{research_pack_json}}", research_json)
            prompt = prompt.replace("{{competitor_analysis_json}}", competitor_json)
            prompt = prompt.replace("{{eeat_signals_json}}", eeat_json)
            prompt = prompt.replace("{{keyword_clusters_json}}", clusters_json)

            # Call LLM
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=4096,
                temperature=0.7,
            )

            # Parse response
            data = self._parse_json_response(response_text)

            # Validate and create result
            context.outline = OutlineResult.from_dict(data)

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                output_path = os.path.join(context.output_dir, "05_outline.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(context.outline.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "sections_count": len(context.outline.sections),
                    "target_words": context.outline.target_total_words,
                    "all_covered": context.outline.coverage_check.all_must_answer_covered,
                }
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
