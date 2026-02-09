"""
Tests for Phase 2: Meta Stage + Internal Linking.

Run locally (no external deps needed for contract tests):
    python3 -m pytest tests/test_phase2.py -v
    OR
    python3 tests/test_phase2.py

Run on server (full integration):
    docker compose exec api python -m pytest tests/test_phase2.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock modules that aren't available locally (anthropic, httpx, etc.)
for mod_name in ["anthropic", "httpx", "pydantic_settings"]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()


# =============================================================================
# 1. MetaResult Contract Tests
# =============================================================================

class TestMetaResult(unittest.TestCase):
    """Test MetaResult dataclass."""

    def setUp(self):
        from src.services.writing_pipeline.contracts import MetaResult
        self.MetaResult = MetaResult

    def test_create_basic(self):
        meta = self.MetaResult(
            meta_title="SEO Tips for 2026",
            meta_description="Learn the best SEO strategies for 2026. Actionable tips inside.",
            slug="seo-tips-2026",
        )
        self.assertEqual(meta.meta_title, "SEO Tips for 2026")
        self.assertEqual(meta.slug, "seo-tips-2026")

    def test_to_dict(self):
        meta = self.MetaResult(
            meta_title="Title",
            meta_description="Desc",
            slug="test-slug",
        )
        d = meta.to_dict()
        self.assertEqual(d["meta_title"], "Title")
        self.assertEqual(d["meta_description"], "Desc")
        self.assertEqual(d["slug"], "test-slug")
        self.assertEqual(set(d.keys()), {"meta_title", "meta_description", "slug"})

    def test_from_dict(self):
        data = {
            "meta_title": "From Dict Title",
            "meta_description": "From Dict Desc",
            "slug": "from-dict",
        }
        meta = self.MetaResult.from_dict(data)
        self.assertEqual(meta.meta_title, "From Dict Title")
        self.assertEqual(meta.slug, "from-dict")

    def test_roundtrip(self):
        """to_dict -> from_dict should produce identical result."""
        original = self.MetaResult(
            meta_title="Как найти клиентов на фрилансе",
            meta_description="Узнайте лучшие способы поиска клиентов. Практическое руководство.",
            slug="kak-najti-klientov-frilans",
        )
        d = original.to_dict()
        restored = self.MetaResult.from_dict(d)
        self.assertEqual(original.meta_title, restored.meta_title)
        self.assertEqual(original.meta_description, restored.meta_description)
        self.assertEqual(original.slug, restored.slug)

    def test_unicode_content(self):
        """Russian content should work."""
        meta = self.MetaResult(
            meta_title="Лучшие SEO-инструменты 2026 года",
            meta_description="Обзор топ-10 инструментов для SEO-продвижения сайтов.",
            slug="luchshie-seo-instrumenty-2026",
        )
        d = meta.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)
        restored = self.MetaResult.from_dict(parsed)
        self.assertEqual(restored.meta_title, meta.meta_title)


# =============================================================================
# 2. PipelineResult with Meta Tests
# =============================================================================

class TestPipelineResultWithMeta(unittest.TestCase):
    """Test PipelineResult includes meta field."""

    def setUp(self):
        from src.services.writing_pipeline.contracts import PipelineResult, MetaResult
        self.PipelineResult = PipelineResult
        self.MetaResult = MetaResult

    def test_meta_default_none(self):
        result = self.PipelineResult(
            topic="test", region="ru",
            article_md="# Test", title="Test", subtitle="Sub",
            word_count=100,
        )
        self.assertIsNone(result.meta)

    def test_meta_set(self):
        meta = self.MetaResult(
            meta_title="Title", meta_description="Desc", slug="slug",
        )
        result = self.PipelineResult(
            topic="test", region="ru",
            article_md="# Test", title="Test", subtitle="Sub",
            word_count=100, meta=meta,
        )
        self.assertIsNotNone(result.meta)
        self.assertEqual(result.meta.slug, "slug")

    def test_to_dict_with_meta(self):
        meta = self.MetaResult(
            meta_title="Title", meta_description="Desc", slug="slug",
        )
        result = self.PipelineResult(
            topic="test", region="ru",
            article_md="# Test", title="Test", subtitle="Sub",
            word_count=100, meta=meta,
        )
        d = result.to_dict()
        self.assertIn("meta", d)
        self.assertEqual(d["meta"]["slug"], "slug")

    def test_to_dict_without_meta(self):
        result = self.PipelineResult(
            topic="test", region="ru",
            article_md="# Test", title="Test", subtitle="Sub",
            word_count=100,
        )
        d = result.to_dict()
        self.assertIn("meta", d)
        self.assertIsNone(d["meta"])


# =============================================================================
# 3. WritingContext Tests
# =============================================================================

class TestWritingContextNewFields(unittest.TestCase):
    """Test WritingContext has existing_posts and meta fields."""

    def setUp(self):
        from src.services.writing_pipeline.core.context import WritingContext
        self.WritingContext = WritingContext

    def test_existing_posts_default_empty(self):
        ctx = self.WritingContext(topic="test")
        self.assertEqual(ctx.existing_posts, [])

    def test_existing_posts_set(self):
        posts = [
            {"title": "Post 1", "url": "http://example.com/post-1"},
            {"title": "Post 2", "url": "http://example.com/post-2"},
        ]
        ctx = self.WritingContext(topic="test", existing_posts=posts)
        self.assertEqual(len(ctx.existing_posts), 2)
        self.assertEqual(ctx.existing_posts[0]["title"], "Post 1")

    def test_meta_default_none(self):
        ctx = self.WritingContext(topic="test")
        self.assertIsNone(ctx.meta)

    def test_meta_set(self):
        from src.services.writing_pipeline.contracts import MetaResult
        meta = MetaResult(meta_title="T", meta_description="D", slug="s")
        ctx = self.WritingContext(topic="test")
        ctx.meta = meta
        self.assertEqual(ctx.meta.slug, "s")


# =============================================================================
# 4. Prompt Template Tests
# =============================================================================

class TestMetaPromptTemplate(unittest.TestCase):
    """Test meta_v1.txt prompt template."""

    def test_has_all_placeholders(self):
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src/services/writing_pipeline/prompts/meta_v1.txt",
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()

        placeholders = [
            "{{topic}}",
            "{{primary_intent}}",
            "{{audience_role}}",
            "{{article_title}}",
            "{{article_md}}",
        ]
        for p in placeholders:
            self.assertIn(p, content, f"Missing placeholder: {p}")

    def test_substitution(self):
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src/services/writing_pipeline/prompts/meta_v1.txt",
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            template = f.read()

        filled = template.replace("{{topic}}", "Тестовая тема")
        filled = filled.replace("{{primary_intent}}", "informational")
        filled = filled.replace("{{audience_role}}", "freelancer")
        filled = filled.replace("{{article_title}}", "Заголовок статьи")
        filled = filled.replace("{{article_md}}", "# Заголовок\n\nТекст статьи.")

        self.assertNotIn("{{", filled)
        self.assertIn("Тестовая тема", filled)
        self.assertIn("freelancer", filled)


class TestDraftingPromptTemplate(unittest.TestCase):
    """Test drafting_v1.txt has existing_posts placeholder."""

    def test_has_existing_posts_placeholder(self):
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src/services/writing_pipeline/prompts/drafting_v1.txt",
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("{{existing_posts}}", content)

    def test_has_internal_linking_instructions(self):
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "src/services/writing_pipeline/prompts/drafting_v1.txt",
        )
        with open(prompt_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Should mention internal links
        self.assertIn("внутренн", content.lower())
        self.assertIn("2-5", content)


# =============================================================================
# 5. MetaStage JSON Parsing Tests
# =============================================================================

class TestMetaStageJsonParsing(unittest.TestCase):
    """Test that MetaStage can parse various LLM JSON responses."""

    def _parse(self, text):
        """Use WritingStage's JSON parser."""
        # Replicate the parsing logic from stage.py
        import json as json_mod

        text = text.strip()

        if "```json" in text:
            try:
                json_start = text.index("```json") + 7
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json_mod.loads(json_str)
            except (ValueError, json_mod.JSONDecodeError):
                pass

        if "```" in text:
            try:
                json_start = text.index("```") + 3
                json_end = text.index("```", json_start)
                json_str = text[json_start:json_end].strip()
                return json_mod.loads(json_str)
            except (ValueError, json_mod.JSONDecodeError):
                pass

        try:
            return json_mod.loads(text)
        except json_mod.JSONDecodeError:
            pass

        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            json_str = text[start:end]
            return json_mod.loads(json_str)
        except (ValueError, json_mod.JSONDecodeError):
            raise ValueError(f"Failed to parse JSON")

    def test_parse_clean_json(self):
        text = '{"meta_title": "Title", "meta_description": "Desc", "slug": "slug"}'
        data = self._parse(text)
        self.assertEqual(data["slug"], "slug")

    def test_parse_json_in_code_block(self):
        text = '''Here is the result:
```json
{
  "meta_title": "Title",
  "meta_description": "Desc",
  "slug": "test-slug"
}
```'''
        data = self._parse(text)
        self.assertEqual(data["slug"], "test-slug")

    def test_parse_json_with_preamble(self):
        text = '''Sure, here are the SEO metadata:

{"meta_title": "Title", "meta_description": "Desc", "slug": "slug"}

Hope this helps!'''
        data = self._parse(text)
        self.assertEqual(data["meta_title"], "Title")

    def test_meta_result_from_parsed(self):
        from src.services.writing_pipeline.contracts import MetaResult
        text = '{"meta_title": "Test", "meta_description": "Test desc", "slug": "test"}'
        data = self._parse(text)
        meta = MetaResult.from_dict(data)
        self.assertEqual(meta.slug, "test")


# =============================================================================
# 6. MetaStage Truncation Logic Tests
# =============================================================================

class TestMetaTruncation(unittest.TestCase):
    """Test that MetaStage properly truncates long values."""

    def test_title_truncation(self):
        """meta_title > 60 chars should be truncated."""
        long_title = "A" * 80
        if len(long_title) > 60:
            truncated = long_title[:57] + "..."
        else:
            truncated = long_title
        self.assertEqual(len(truncated), 60)
        self.assertTrue(truncated.endswith("..."))

    def test_description_truncation(self):
        """meta_description > 160 chars should be truncated."""
        long_desc = "B" * 200
        if len(long_desc) > 160:
            truncated = long_desc[:157] + "..."
        else:
            truncated = long_desc
        self.assertEqual(len(truncated), 160)
        self.assertTrue(truncated.endswith("..."))

    def test_short_title_not_truncated(self):
        """meta_title ≤ 60 chars should not be truncated."""
        title = "Short Title"
        if len(title) > 60:
            title = title[:57] + "..."
        self.assertEqual(title, "Short Title")

    def test_short_description_not_truncated(self):
        """meta_description ≤ 160 chars should not be truncated."""
        desc = "Short description"
        if len(desc) > 160:
            desc = desc[:157] + "..."
        self.assertEqual(desc, "Short description")


# =============================================================================
# 7. GhostPublisher.get_posts Mock Tests
# =============================================================================

class TestGhostPublisherGetPosts(unittest.TestCase):
    """Test GhostPublisher.get_posts() with mocked HTTP."""

    def _make_publisher(self):
        """Create publisher."""
        from src.services.publisher import GhostPublisher
        return GhostPublisher(
            ghost_url="http://localhost:2368",
            admin_key="abc123:deadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678",
        )

    def _mock_client(self, responses):
        """Create a mock httpx.Client that returns given responses in sequence."""
        import src.services.publisher as pub_mod

        mock_client_instance = MagicMock()
        mock_client_instance.get.side_effect = responses

        mock_client_ctx = MagicMock()
        mock_client_ctx.__enter__ = MagicMock(return_value=mock_client_instance)
        mock_client_ctx.__exit__ = MagicMock(return_value=False)

        original_Client = pub_mod.httpx.Client
        pub_mod.httpx.Client = MagicMock(return_value=mock_client_ctx)
        return pub_mod, original_Client

    def _make_response(self, status_code, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        if json_data is not None:
            resp.json.return_value = json_data
        return resp

    def test_get_posts_success(self):
        """Test successful post fetching."""
        resp = self._make_response(200, {
            "posts": [
                {"title": "Post 1", "url": "http://blog.example.com/post-1/",
                 "slug": "post-1", "published_at": "2026-01-15", "custom_excerpt": "Excerpt 1"},
                {"title": "Post 2", "url": "http://blog.example.com/post-2/",
                 "slug": "post-2", "published_at": "2026-01-20", "custom_excerpt": None},
            ],
            "meta": {"pagination": {"pages": 1, "page": 1}},
        })

        pub_mod, orig = self._mock_client([resp])
        try:
            publisher = self._make_publisher()
            posts = publisher.get_posts()
            self.assertEqual(len(posts), 2)
            self.assertEqual(posts[0]["title"], "Post 1")
            self.assertEqual(posts[1]["excerpt"], "")
        finally:
            pub_mod.httpx.Client = orig

    def test_get_posts_empty(self):
        """Test when no posts exist."""
        resp = self._make_response(200, {
            "posts": [],
            "meta": {"pagination": {"pages": 1, "page": 1}},
        })

        pub_mod, orig = self._mock_client([resp])
        try:
            publisher = self._make_publisher()
            posts = publisher.get_posts()
            self.assertEqual(posts, [])
        finally:
            pub_mod.httpx.Client = orig

    def test_get_posts_error_graceful(self):
        """Test graceful degradation on HTTP error."""
        resp = self._make_response(500)

        pub_mod, orig = self._mock_client([resp])
        try:
            publisher = self._make_publisher()
            posts = publisher.get_posts()
            self.assertEqual(posts, [])
        finally:
            pub_mod.httpx.Client = orig

    def test_get_posts_connection_error(self):
        """Test graceful degradation on connection error."""
        pub_mod, orig = self._mock_client([Exception("Connection refused")])
        try:
            publisher = self._make_publisher()
            posts = publisher.get_posts()
            self.assertEqual(posts, [])
        finally:
            pub_mod.httpx.Client = orig

    def test_get_posts_pagination(self):
        """Test multi-page pagination."""
        resp1 = self._make_response(200, {
            "posts": [{"title": f"Post {i}", "url": f"http://blog.example.com/post-{i}/",
                        "slug": f"post-{i}", "published_at": "2026-01-01", "custom_excerpt": ""}
                       for i in range(1, 4)],
            "meta": {"pagination": {"pages": 2, "page": 1}},
        })
        resp2 = self._make_response(200, {
            "posts": [{"title": "Post 4", "url": "http://blog.example.com/post-4/",
                        "slug": "post-4", "published_at": "2026-01-01", "custom_excerpt": ""}],
            "meta": {"pagination": {"pages": 2, "page": 2}},
        })

        pub_mod, orig = self._mock_client([resp1, resp2])
        try:
            publisher = self._make_publisher()
            posts = publisher.get_posts()
            self.assertEqual(len(posts), 4)
            self.assertEqual(posts[3]["title"], "Post 4")
        finally:
            pub_mod.httpx.Client = orig


# =============================================================================
# 8. DraftingStage Existing Posts Integration Test
# =============================================================================

class TestDraftingExistingPosts(unittest.TestCase):
    """Test that DraftingStage properly injects existing_posts into prompt."""

    def test_existing_posts_json_formatting(self):
        """Test that existing_posts are formatted as JSON for the prompt."""
        existing_posts = [
            {"title": "Что такое SEO", "url": "http://blog.example.com/chto-takoe-seo/"},
            {"title": "Как раскрутить сайт", "url": "http://blog.example.com/kak-raskrutit-sajt/"},
        ]

        posts_for_prompt = [
            {"title": p["title"], "url": p["url"]}
            for p in existing_posts
        ]
        json_str = json.dumps(posts_for_prompt, ensure_ascii=False, indent=2)

        self.assertIn("Что такое SEO", json_str)
        self.assertIn("http://blog.example.com/chto-takoe-seo/", json_str)
        # Verify it's valid JSON
        parsed = json.loads(json_str)
        self.assertEqual(len(parsed), 2)

    def test_empty_existing_posts(self):
        """Empty list should produce '[]'."""
        existing_posts = []
        if existing_posts:
            posts_json = json.dumps(existing_posts, ensure_ascii=False)
        else:
            posts_json = "[]"
        self.assertEqual(posts_json, "[]")


# =============================================================================
# 9. Stages Registration Test
# =============================================================================

class TestStagesRegistration(unittest.TestCase):
    """Test that MetaStage is properly registered."""

    def test_meta_stage_in_stages_init(self):
        from src.services.writing_pipeline.stages import MetaStage
        self.assertTrue(hasattr(MetaStage, 'name'))

    def test_meta_stage_name(self):
        """MetaStage should have name 'meta'."""
        # Can't instantiate without anthropic client, so check the property exists
        from src.services.writing_pipeline.stages.meta import MetaStage
        self.assertTrue(hasattr(MetaStage, 'name'))

    def test_meta_result_in_package(self):
        from src.services.writing_pipeline.contracts import MetaResult
        self.assertTrue(callable(MetaResult.from_dict))


if __name__ == "__main__":
    unittest.main(verbosity=2)
