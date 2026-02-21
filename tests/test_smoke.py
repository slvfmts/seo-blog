"""
Smoke tests — real API calls against live services.

Run manually: pytest tests/test_smoke.py -v -m smoke
"""

import os
import pytest

# All tests in this file are smoke tests
pytestmark = pytest.mark.smoke


def _skip_unless_env(var: str):
    if not os.environ.get(var):
        pytest.skip(f"{var} not set")


class TestAnthropicSmoke:
    def test_simple_prompt(self):
        _skip_unless_env("ANTHROPIC_API_KEY")
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{"role": "user", "content": "Say 'hello' in one word."}],
        )
        assert response.content[0].text.strip().lower().startswith("hello")


class TestGhostSmoke:
    def test_get_posts(self):
        _skip_unless_env("GHOST_URL")
        _skip_unless_env("GHOST_ADMIN_KEY")
        from src.services.publisher import GhostPublisher

        publisher = GhostPublisher(
            ghost_url=os.environ["GHOST_URL"],
            admin_key=os.environ["GHOST_ADMIN_KEY"],
        )
        posts = publisher.get_posts()
        assert isinstance(posts, list)


class TestDatabaseSmoke:
    def test_select_1(self):
        _skip_unless_env("DATABASE_URL")
        from sqlalchemy import create_engine, text

        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
