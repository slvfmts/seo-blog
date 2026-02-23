"""Tests for FastAPI health endpoints and auth middleware.

These tests require the full web stack (fastapi, jinja2, markdown, sqlalchemy, etc.).
They will be skipped if dependencies are missing (e.g., running locally without
the full Docker environment). They run in Docker via:
    docker compose exec api python -m pytest tests/test_api_health.py -v
"""

import sys
import pytest
from unittest.mock import patch, MagicMock

# Check if we can import the full app stack
_skip_reason = None
try:
    import markdown  # noqa: F401
    import jinja2  # noqa: F401
    from src.config import Settings  # noqa: F401
except ImportError as e:
    _skip_reason = f"Missing dependency: {e}"

pytestmark = pytest.mark.skipif(
    _skip_reason is not None,
    reason=_skip_reason or "",
)


@pytest.fixture
def client():
    """Create FastAPI TestClient with mocked settings and DB."""
    from fastapi.testclient import TestClient
    from src.config import get_settings

    # Clear the LRU cache so our mock takes effect
    get_settings.cache_clear()

    # Create real Settings object with test values (avoids DB connection)
    with patch.dict("os.environ", {
        "DATABASE_URL": "sqlite://",
        "REDIS_URL": "redis://localhost:6379/0",
        "SECRET_KEY": "test-secret-key-for-session",
        "GHOST_URL": "http://ghost:2368",
        "GHOST_ADMIN_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "SERPER_API_KEY": "",
    }):
        # Mock the DB session module to prevent real DB connections
        mock_session_mod = MagicMock()

        def mock_get_db():
            mock_db = MagicMock()
            yield mock_db

        mock_session_mod.get_db = mock_get_db
        mock_session_mod.SessionLocal = MagicMock()
        mock_session_mod.engine = MagicMock()

        # Patch the db.session module in sys.modules before app import
        original_session = sys.modules.get("src.db.session")
        sys.modules["src.db.session"] = mock_session_mod

        # Clear cached app modules to force re-import with mocks
        mods_to_clear = [k for k in sys.modules if k.startswith("src.api.")]
        for mod in mods_to_clear:
            del sys.modules[mod]

        try:
            get_settings.cache_clear()
            from src.api.main import create_app
            app = create_app()
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            # Restore original module
            if original_session is not None:
                sys.modules["src.db.session"] = original_session
            elif "src.db.session" in sys.modules:
                del sys.modules["src.db.session"]
            get_settings.cache_clear()


class TestHealthEndpoints:
    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_db_endpoint_exists(self, client):
        """/health/db should respond (may error with mock DB, but shouldn't crash)."""
        response = client.get("/health/db")
        assert response.status_code in (200, 500)


class TestAuthMiddleware:
    def test_unauthenticated_ui_redirects_to_login(self, client):
        """Unauthenticated access to /ui/topics should redirect to login."""
        response = client.get("/ui/topics", follow_redirects=False)
        assert response.status_code == 302
        assert "/ui/login" in response.headers.get("location", "")

    def test_health_is_public(self, client):
        """Health endpoint should not require auth."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_root_redirects_unauthenticated(self, client):
        """Root / should redirect to login for unauthenticated users."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers.get("location", "")
        assert "/ui/login" in location or "/ui/" in location
