"""
Сервис публикации в Ghost CMS.
"""

import json
import hmac
import hashlib
import base64
import httpx
from datetime import datetime


class GhostPublisher:
    """Публикация статей в Ghost."""

    def __init__(self, ghost_url: str, admin_key: str):
        self.ghost_url = ghost_url.rstrip("/")
        self.admin_key = admin_key

    def _base64url_encode(self, data: bytes) -> str:
        """Base64 URL-safe encoding без padding."""
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    def _create_jwt_token(self) -> str:
        """Создаёт JWT токен для Ghost Admin API."""
        key_id, secret = self.admin_key.split(":")

        iat = int(datetime.now().timestamp())

        header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
        payload = {
            "iat": iat,
            "exp": iat + 5 * 60,
            "aud": "/admin/"
        }

        header_b64 = self._base64url_encode(
            json.dumps(header, separators=(",", ":")).encode()
        )
        payload_b64 = self._base64url_encode(
            json.dumps(payload, separators=(",", ":")).encode()
        )

        message = f"{header_b64}.{payload_b64}".encode()
        signature = hmac.new(
            bytes.fromhex(secret),
            message,
            hashlib.sha256
        ).digest()
        signature_b64 = self._base64url_encode(signature)

        return f"{header_b64}.{payload_b64}.{signature_b64}"

    def _markdown_to_mobiledoc(self, markdown: str) -> str:
        """Конвертирует Markdown в Ghost mobiledoc формат."""
        mobiledoc = {
            "version": "0.3.1",
            "markups": [],
            "atoms": [],
            "cards": [["markdown", {"markdown": markdown}]],
            "sections": [[10, 0]]
        }
        return json.dumps(mobiledoc)

    def get_posts(self) -> list[dict]:
        """
        Fetch all published posts from Ghost.

        Returns list of {title, url, slug, published_at, excerpt} dicts.
        Handles pagination (Ghost returns 15 per page by default).
        """
        token = self._create_jwt_token()
        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json",
        }

        all_posts = []
        page = 1

        try:
            with httpx.Client(timeout=30.0) as client:
                while True:
                    response = client.get(
                        f"{self.ghost_url}/ghost/api/admin/posts/",
                        headers=headers,
                        params={
                            "status": "published",
                            "fields": "title,url,slug,published_at,custom_excerpt",
                            "limit": 100,
                            "page": page,
                        },
                    )

                    if response.status_code != 200:
                        break

                    data = response.json()
                    posts = data.get("posts", [])
                    if not posts:
                        break

                    for post in posts:
                        all_posts.append({
                            "title": post.get("title", ""),
                            "url": post.get("url", ""),
                            "slug": post.get("slug", ""),
                            "published_at": post.get("published_at", ""),
                            "excerpt": post.get("custom_excerpt") or "",
                        })

                    # Check if there are more pages
                    meta = data.get("meta", {}).get("pagination", {})
                    if page >= meta.get("pages", 1):
                        break
                    page += 1

        except Exception:
            pass  # Graceful degradation — return what we have

        return all_posts

    def get_post(self, post_id: str) -> dict | None:
        """
        Fetch single post by Ghost ID.

        Returns: {id, title, url, slug, mobiledoc, updated_at} or None.
        """
        token = self._create_jwt_token()
        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self.ghost_url}/ghost/api/admin/posts/{post_id}/",
                    headers=headers,
                    params={
                        "fields": "id,title,url,slug,updated_at",
                        "formats": "mobiledoc",
                    },
                )

                if response.status_code == 200:
                    post = response.json()["posts"][0]
                    return {
                        "id": post["id"],
                        "title": post.get("title", ""),
                        "url": post.get("url", ""),
                        "slug": post.get("slug", ""),
                        "mobiledoc": post.get("mobiledoc", ""),
                        "updated_at": post.get("updated_at", ""),
                    }

        except Exception:
            pass

        return None

    def update_post(self, post_id: str, content_md: str, updated_at: str) -> dict:
        """
        Update Ghost post content.

        Ghost requires updated_at for conflict detection.
        """
        token = self._create_jwt_token()
        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json",
        }

        post_data = {
            "posts": [{
                "mobiledoc": self._markdown_to_mobiledoc(content_md),
                "updated_at": updated_at,
            }]
        }

        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.put(
                    f"{self.ghost_url}/ghost/api/admin/posts/{post_id}/",
                    headers=headers,
                    json=post_data,
                )

            if response.status_code == 200:
                return {"success": True}
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code,
                }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def publish(
        self,
        title: str,
        content: str,
        slug: str = None,
        meta_title: str = None,
        meta_description: str = None,
        status: str = "draft",
    ) -> dict:
        """Публикует статью в Ghost."""

        token = self._create_jwt_token()

        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json"
        }

        post_data = {
            "posts": [{
                "title": title,
                "mobiledoc": self._markdown_to_mobiledoc(content),
                "status": status,
            }]
        }

        if slug:
            post_data["posts"][0]["slug"] = slug
        if meta_title:
            post_data["posts"][0]["meta_title"] = meta_title
        if meta_description:
            post_data["posts"][0]["meta_description"] = meta_description

        try:
            with httpx.Client() as client:
                response = client.post(
                    f"{self.ghost_url}/ghost/api/admin/posts/",
                    headers=headers,
                    json=post_data,
                    timeout=30.0,
                )

            if response.status_code == 201:
                post = response.json()["posts"][0]
                return {
                    "success": True,
                    "post": {
                        "id": post["id"],
                        "url": post.get("url", f"{self.ghost_url}/{post['slug']}/"),
                        "slug": post["slug"],
                    }
                }
            else:
                return {
                    "success": False,
                    "error": response.text,
                    "status_code": response.status_code
                }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
