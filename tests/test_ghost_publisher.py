"""Tests for Ghost CMS publisher — script extraction, publish, image upload."""

import json
import pytest
from unittest.mock import patch, MagicMock

from src.services.publisher import GhostPublisher


@pytest.fixture
def publisher():
    return GhostPublisher(
        ghost_url="http://ghost:2368",
        admin_key="aabbccdd:1122334455667788aabbccdd1122334455667788aabbccdd1122334455667788",
    )


# =============================================================================
# _extract_script_tags
# =============================================================================

class TestExtractScriptTags:
    def test_no_scripts(self, publisher):
        md = "# Hello\n\nSome content here."
        clean, scripts = publisher._extract_script_tags(md)
        assert clean.strip() == md.strip()
        assert scripts == ""

    def test_single_script(self, publisher):
        md = '# Hello\n\n<script type="application/ld+json">{"@type":"Article"}</script>\n\nContent.'
        clean, scripts = publisher._extract_script_tags(md)
        assert "<script" not in clean
        assert "Content." in clean
        assert '<script type="application/ld+json">' in scripts

    def test_multiple_scripts(self, publisher):
        md = (
            "# Hello\n"
            '<script>console.log("a")</script>\n'
            "Middle.\n"
            '<script>console.log("b")</script>\n'
            "End."
        )
        clean, scripts = publisher._extract_script_tags(md)
        assert "<script" not in clean
        assert scripts.count("<script>") == 2

    def test_multiline_script(self, publisher):
        md = (
            "# Hello\n"
            "<script>\n"
            "  var x = 1;\n"
            "  console.log(x);\n"
            "</script>\n"
            "Content."
        )
        clean, scripts = publisher._extract_script_tags(md)
        assert "<script>" not in clean
        assert "var x = 1" in scripts


# =============================================================================
# publish — scripts go to codeinjection_foot (regression: Ghost rendering bug)
# =============================================================================

class TestPublish:
    @patch("src.services.publisher.httpx.Client")
    def test_scripts_go_to_codeinjection_foot(self, mock_client_cls, publisher):
        """Scripts must NOT be in mobiledoc; they go to codeinjection_foot."""
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "posts": [{"id": "1", "url": "http://ghost/test/", "slug": "test"}]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = publisher.publish(
            title="Test",
            content='# Test\n<script>alert(1)</script>\nBody.',
            schema_json_ld='<script type="application/ld+json">{"@type":"Article"}</script>',
        )

        assert result["success"] is True
        # Check what was sent to Ghost
        call_kwargs = mock_client.post.call_args
        post_data = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        post = post_data["posts"][0]
        # mobiledoc should NOT contain <script>
        assert "<script>" not in post["mobiledoc"]
        # codeinjection_foot should contain scripts
        assert "codeinjection_foot" in post
        assert "<script>" in post["codeinjection_foot"]

    @patch("src.services.publisher.httpx.Client")
    def test_publish_success(self, mock_client_cls, publisher):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "posts": [{"id": "1", "url": "http://ghost/test/", "slug": "test"}]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = publisher.publish(title="Test", content="# Test\nBody.")
        assert result["success"] is True
        assert result["post"]["slug"] == "test"

    @patch("src.services.publisher.httpx.Client")
    def test_publish_failure(self, mock_client_cls, publisher):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        result = publisher.publish(title="Test", content="# Test")
        assert result["success"] is False


# =============================================================================
# get_posts
# =============================================================================

class TestGetPosts:
    @patch("src.services.publisher.httpx.Client")
    def test_pagination(self, mock_client_cls, publisher):
        """get_posts should follow pagination."""
        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = {
            "posts": [{"title": "Post 1", "url": "/p1/", "slug": "p1",
                        "published_at": "2026-01-01", "custom_excerpt": "Ex1"}],
            "meta": {"pagination": {"pages": 2}},
        }
        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = {
            "posts": [{"title": "Post 2", "url": "/p2/", "slug": "p2",
                        "published_at": "2026-01-02", "custom_excerpt": None}],
            "meta": {"pagination": {"pages": 2}},
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = [page1_resp, page2_resp]
        mock_client_cls.return_value = mock_client

        posts = publisher.get_posts()
        assert len(posts) == 2
        assert posts[0]["title"] == "Post 1"
        assert posts[1]["excerpt"] == ""  # None → ""

    @patch("src.services.publisher.httpx.Client")
    def test_graceful_degradation_on_500(self, mock_client_cls, publisher):
        """HTTP 500 should return empty list, not crash."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        posts = publisher.get_posts()
        assert posts == []


# =============================================================================
# upload_image
# =============================================================================

class TestUploadImage:
    @patch("src.services.publisher.httpx.Client")
    def test_upload_returns_url_on_201(self, mock_client_cls, publisher, tmp_path):
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG fake")

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "images": [{"url": "http://ghost/content/images/test.png"}]
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        url = publisher.upload_image(str(img_file))
        assert url == "http://ghost/content/images/test.png"

    @patch("src.services.publisher.httpx.Client")
    def test_upload_returns_none_on_failure(self, mock_client_cls, publisher, tmp_path):
        img_file = tmp_path / "test.png"
        img_file.write_bytes(b"\x89PNG fake")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        url = publisher.upload_image(str(img_file))
        assert url is None
