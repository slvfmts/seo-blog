"""
Intent Stage - Analyzes topic and creates editorial contract.
"""

from ..core.stage import WritingStage
from ..core.context import WritingContext
from ..contracts import IntentResult


class IntentStage(WritingStage):
    """
    Stage 1: Intent Analysis

    Analyzes the topic and creates an editorial contract that defines:
    - User intent and goals
    - Content type and format
    - Target audience
    - Tone and depth
    - Required questions to answer
    """

    @property
    def name(self) -> str:
        return "intent"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute intent analysis."""
        log = context.start_stage(self.name)

        try:
            # Load and fill prompt template
            prompt_template = self._load_prompt("intent_v1")
            prompt = prompt_template.replace("{{topic}}", context.topic)
            prompt = prompt.replace("{{region}}", context.region)

            # Call LLM
            response_text, tokens = self._call_llm(
                prompt,
                max_tokens=2048,
                temperature=0.7,
            )

            # Parse response
            data = self._parse_json_response(response_text)

            # Validate and create result
            context.intent = IntentResult.from_dict(data)

            # Save intermediate result if configured
            if context.save_intermediate and context.output_dir:
                import os
                import json
                output_path = os.path.join(context.output_dir, "01_intent.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(context.intent.to_dict(), f, ensure_ascii=False, indent=2)

            context.complete_stage(tokens_used=tokens)

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context
