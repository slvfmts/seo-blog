"""
WritingStage - Abstract base class for pipeline stages.
"""

from abc import ABC, abstractmethod
from typing import Optional
import anthropic

from .context import WritingContext


class WritingStage(ABC):
    """
    Abstract base class for a pipeline stage.

    Each stage:
    1. Reads input from context
    2. Processes (usually via LLM)
    3. Writes output to context
    """

    def __init__(
        self,
        client: anthropic.Anthropic,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.client = client
        self.model = model

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage name for logging."""
        pass

    @abstractmethod
    async def run(self, context: WritingContext) -> WritingContext:
        """
        Execute the stage.

        Args:
            context: Shared pipeline context

        Returns:
            Updated context with stage output
        """
        pass

    def _load_prompt(self, prompt_name: str) -> str:
        """Load a prompt template from the prompts directory."""
        import os
        prompt_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "prompts"
        )
        prompt_path = os.path.join(prompt_dir, f"{prompt_name}.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()

    def _call_llm(
        self,
        prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> tuple[str, int]:
        """
        Call the LLM with a prompt.

        Returns:
            Tuple of (response_text, tokens_used)
        """
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )

        text = response.content[0].text
        tokens = response.usage.input_tokens + response.usage.output_tokens

        return text, tokens

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from LLM response, handling markdown code blocks."""
        import json

        text = text.strip()

        # Try to find JSON in markdown block
        if "```json" in text:
            try:
                json_start = text.index("```json") + 7
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        # Try to find JSON in regular code block
        if "```" in text:
            try:
                json_start = text.index("```") + 3
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json.loads(json_str)
            except (ValueError, json.JSONDecodeError):
                pass

        # Try to parse the whole text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON object in text
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            json_str = text[start:end]
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            raise ValueError(f"Failed to parse JSON from response: {text[:500]}...")
