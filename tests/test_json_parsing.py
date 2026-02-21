"""Tests for LLM JSON response parsing (WritingStage._parse_json_response)."""

import json
import pytest
from unittest.mock import MagicMock

from src.services.writing_pipeline.core.stage import WritingStage


class ConcreteStage(WritingStage):
    """Minimal concrete subclass for testing base class methods."""

    @property
    def name(self) -> str:
        return "test"

    async def run(self, context):
        return context


@pytest.fixture
def stage():
    client = MagicMock()
    return ConcreteStage(client=client, model="test")


class TestParseJsonResponse:
    def test_clean_json(self, stage):
        text = '{"key": "value", "num": 42}'
        result = stage._parse_json_response(text)
        assert result == {"key": "value", "num": 42}

    def test_json_in_markdown_block(self, stage):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```\nDone.'
        result = stage._parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_in_plain_code_block(self, stage):
        text = '```\n{"key": "value"}\n```'
        result = stage._parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_with_preamble(self, stage):
        text = 'I analyzed the content. Here is my output:\n{"key": "value"}'
        result = stage._parse_json_response(text)
        assert result == {"key": "value"}

    def test_json_with_trailing_commas(self, stage):
        """Common LLM issue: trailing commas before } or ]."""
        text = '{"items": ["a", "b",], "ok": true,}'
        result = stage._parse_json_response(text)
        assert result["items"] == ["a", "b"]
        assert result["ok"] is True

    def test_unicode_russian_content(self, stage):
        data = {"заголовок": "SEO оптимизация", "описание": "Полное руководство"}
        text = json.dumps(data, ensure_ascii=False)
        result = stage._parse_json_response(text)
        assert result["заголовок"] == "SEO оптимизация"

    def test_invalid_json_raises(self, stage):
        """No JSON at all should raise ValueError."""
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            stage._parse_json_response("This is just plain text with no JSON.")

    def test_json_surrounded_by_text(self, stage):
        text = 'Result: {"a": 1, "b": 2} end.'
        result = stage._parse_json_response(text)
        assert result == {"a": 1, "b": 2}

    def test_nested_json(self, stage):
        data = {
            "sections": [
                {"h2": "Intro", "blocks": [{"type": "explanation"}]},
            ]
        }
        text = json.dumps(data)
        result = stage._parse_json_response(text)
        assert result["sections"][0]["h2"] == "Intro"


class TestRepairJson:
    def test_trailing_comma_object(self):
        text = '{"a": 1, "b": 2,}'
        result = WritingStage._repair_json(text)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_trailing_comma_array(self):
        text = '{"items": [1, 2, 3,]}'
        result = WritingStage._repair_json(text)
        parsed = json.loads(result)
        assert parsed["items"] == [1, 2, 3]

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value",}\n```'
        result = WritingStage._repair_json(text)
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            WritingStage._repair_json("no json here")
