"""
Intent Stage - Analyzes topic and creates editorial contract.
"""

import logging
from datetime import datetime
from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import IntentResult

logger = logging.getLogger(__name__)


class IntentStage(WritingStage):
    """
    Stage 1: Intent Analysis

    Analyzes the topic and creates an editorial contract that defines:
    - User intent and goals
    - Content type and format
    - Target audience
    - Tone and depth
    - Topic boundaries (scope guard)
    - Required questions to answer
    """

    @property
    def name(self) -> str:
        return "intent"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute intent analysis. If a brief is in config, use it to seed the intent."""
        log = context.start_stage(self.name)

        try:
            brief = context.config.get("brief")

            if brief:
                # Brief-seeded mode: use brief data to create richer intent prompt
                import json
                brief_json = json.dumps(brief if isinstance(brief, dict) else brief.to_dict(), ensure_ascii=False, indent=2)

                prompt_template = self._load_prompt("intent_v1")
                prompt = prompt_template.replace("{{topic}}", context.topic)
                prompt = prompt.replace("{{region}}", context.region)
                prompt = prompt.replace("{{today}}", datetime.now().strftime("%Y-%m-%d"))

                # Append brief context to the prompt
                prompt += f"""

## Дополнительный контекст: Brief из кластерного плана

У этой статьи есть готовый brief. Используй его для уточнения intent:
{brief_json}

Учти:
- topic_boundaries из brief — строго следуй in_scope/out_of_scope
- must_answer_questions из brief должны войти в must_answer_questions результата
- unique_angle.must_not_cover → must_not_include
- role (pillar/cluster) влияет на depth и word_count_range
"""
            else:
                # Standard mode
                prompt_template = self._load_prompt("intent_v1")
                prompt = prompt_template.replace("{{topic}}", context.topic)
                prompt = prompt.replace("{{region}}", context.region)
                prompt = prompt.replace("{{today}}", datetime.now().strftime("%Y-%m-%d"))

            # Call LLM
            response_text, in_t, out_t = self._call_llm(
                prompt,
                max_tokens=2048,
                temperature=0.7,
            )

            # Parse response
            data = self._parse_json_response(response_text)

            # Validate and create result
            context.intent = IntentResult.from_dict(data)

            # Log topic scope guard results
            if context.intent.topic_boundaries:
                logger.info(
                    f"Topic scope guard: in_scope={len(context.intent.topic_boundaries.in_scope)}, "
                    f"out_of_scope={len(context.intent.topic_boundaries.out_of_scope)}"
                )
                if context.intent.topic_boundaries.out_of_scope:
                    logger.debug(f"Out of scope topics: {context.intent.topic_boundaries.out_of_scope}")

            # Warn if topic was changed (scope drift detected)
            if context.intent.topic != context.topic:
                logger.warning(
                    f"Topic drift detected! Input: '{context.topic}' -> Output: '{context.intent.topic}'"
                )

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                import json
                output_path = os.path.join(context.output_dir, "01_intent.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(context.intent.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(input_tokens=in_t, output_tokens=out_t)

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
