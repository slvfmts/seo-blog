"""
WritingStage - Abstract base class for pipeline stages.
"""

from abc import ABC, abstractmethod
from typing import Optional
import time
import logging
import anthropic

logger = logging.getLogger(__name__)

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
    ) -> tuple[str, int, int]:
        """
        Call the LLM with a prompt using streaming to avoid proxy timeouts.

        Returns:
            Tuple of (response_text, input_tokens, output_tokens)
        """
        max_retries = 3
        for attempt in range(max_retries):
            try:
                chunks = []

                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                ) as stream:
                    for text in stream.text_stream:
                        chunks.append(text)

                response = stream.get_final_message()
                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens

                text = "".join(chunks)

                return text, input_tokens, output_tokens

            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                is_retryable = isinstance(e, anthropic.APIConnectionError) or (
                    hasattr(e, 'status_code') and e.status_code in (429, 500, 502, 503, 529)
                ) or 'overloaded' in str(e).lower()

                if is_retryable and attempt < max_retries - 1:
                    wait = 2 ** (attempt + 1)  # 2, 4 seconds
                    logger.warning(f"LLM call failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise

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
            pass

        # Try to repair common JSON issues
        try:
            json_str = self._repair_json(text)
            return json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            pass

        # Last resort: ask LLM to fix the JSON
        try:
            return self._llm_repair_json(text)
        except Exception:
            raise ValueError(f"Failed to parse JSON from response: {text[:500]}...")

    @staticmethod
    def _repair_json(text: str) -> str:
        """Try to repair common JSON issues from LLM output."""
        import re

        # Extract JSON from markdown code blocks
        if "```" in text:
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        # Find outermost { ... }
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found")
        text = text[start:end + 1]

        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)

        # Fix unescaped newlines inside strings (common LLM issue)
        # This is a heuristic: replace literal newlines inside strings
        lines = text.split("\n")
        fixed_lines = []
        in_string = False
        for line in lines:
            # Count unescaped quotes to track string state
            quote_count = 0
            i = 0
            while i < len(line):
                if line[i] == '"' and (i == 0 or line[i-1] != '\\'):
                    quote_count += 1
                i += 1
            if in_string and not line.strip().startswith('"'):
                # Continuation of a broken string - append with space
                if fixed_lines:
                    prev = fixed_lines[-1]
                    if prev.rstrip().endswith('"'):
                        # Previous line ended a string, this is a new line
                        fixed_lines.append(line)
                    else:
                        fixed_lines[-1] = prev.rstrip() + " " + line.strip()
                else:
                    fixed_lines.append(line)
            else:
                fixed_lines.append(line)
            # Track string state
            if quote_count % 2 == 1:
                in_string = not in_string
        text = "\n".join(fixed_lines)

        # Remove trailing commas again after fixes
        text = re.sub(r",\s*([}\]])", r"\1", text)

        return text

    def _llm_repair_json(self, text: str) -> dict:
        """Use LLM to repair broken JSON as last resort."""
        import json
        import logging

        logger = logging.getLogger(__name__)

        # Extract the JSON part
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("No JSON object found")
        broken_json = text[start:end + 1]

        # Truncate if too long (keep first and last parts)
        if len(broken_json) > 30000:
            broken_json = broken_json[:15000] + "\n...[TRUNCATED]...\n" + broken_json[-15000:]

        repair_prompt = (
            "The following JSON has syntax errors. Fix ONLY the JSON syntax "
            "(missing commas, unescaped quotes, trailing commas, etc.) and return "
            "the corrected JSON. Do NOT add, remove, or change any data. "
            "Return ONLY valid JSON, no markdown, no explanation.\n\n"
            f"{broken_json}"
        )

        logger.info("Attempting LLM JSON repair")
        response_text, _, _ = self._call_llm(repair_prompt, max_tokens=16384, temperature=0.0)

        # Parse the repaired JSON
        response_text = response_text.strip()
        if response_text.startswith("```"):
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL)
            if match:
                response_text = match.group(1).strip()

        s = response_text.find("{")
        e = response_text.rfind("}")
        if s != -1 and e != -1:
            response_text = response_text[s:e + 1]

        result = json.loads(response_text)
        logger.info("LLM JSON repair succeeded")
        return result
