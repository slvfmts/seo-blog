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
