"""
Сервис публикации в Ghost CMS.
"""

import json
import hmac
import hashlib
import base64
import re
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

    def _extract_script_tags(self, markdown: str) -> tuple[str, str]:
        """Extract <script> tags from markdown, return (clean_md, scripts_html)."""
        import re
        scripts = re.findall(r'<script[^>]*>.*?</script>', markdown, re.DOTALL)
        clean = re.sub(r'\s*<script[^>]*>.*?</script>\s*', '\n', markdown, flags=re.DOTALL).rstrip()
        return clean, '\n'.join(scripts)

    def _resolve_link_placeholders(self, content: str) -> str:
        """Replace [[LINK:slug|text]] placeholders with real markdown links."""
        placeholders = re.findall(r'\[\[LINK:([^|]+)\|([^\]]+)\]\]', content)
        if not placeholders:
            return content

        # Fetch slug→url map from Ghost
        slug_to_url: dict[str, str] = {}
        try:
            token = self._create_jwt_token()
            headers = {"Authorization": f"Ghost {token}"}
            with httpx.Client(timeout=30.0) as client:
                response = client.get(
                    f"{self.ghost_url}/ghost/api/admin/posts/",
                    headers=headers,
                    params={"limit": "all", "fields": "slug,url", "status": "published"},
                )
                if response.status_code == 200:
                    for post in response.json().get("posts", []):
                        slug_to_url[post["slug"]] = post["url"]
        except Exception:
            pass  # Graceful degradation — unresolved placeholders become plain text

        def replace_match(m: re.Match) -> str:
            slug, text = m.group(1).strip(), m.group(2).strip()
            url = slug_to_url.get(slug)
            if url:
                return f"[{text}]({url})"
            return text  # No post found — leave anchor as plain text

        return re.sub(r'\[\[LINK:([^|]+)\|([^\]]+)\]\]', replace_match, content)

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

    def upload_image(self, filepath: str, ref: str = "") -> str | None:
        """
        Upload image to Ghost and return its public URL.

        Args:
            filepath: Local path to image file
            ref: Optional reference name for the image

        Returns:
            Ghost image URL (e.g. http://host/content/images/2026/02/cover.png)
            or None on failure.
        """
        import os

        token = self._create_jwt_token()
        headers = {"Authorization": f"Ghost {token}"}

        filename = os.path.basename(filepath)
        content_type = "image/png"
        if filename.endswith(".jpg") or filename.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif filename.endswith(".webp"):
            content_type = "image/webp"
        elif filename.endswith(".svg"):
            content_type = "image/svg+xml"

        try:
            with open(filepath, "rb") as f:
                file_data = f.read()

            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{self.ghost_url}/ghost/api/admin/images/upload/",
                    headers=headers,
                    files={"file": (filename, file_data, content_type)},
                    data={"ref": ref or filename},
                )

            if response.status_code == 201:
                images = response.json().get("images", [])
                if images:
                    return images[0].get("url")
            return None

        except Exception:
            return None

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

        clean_content, extracted_scripts = self._extract_script_tags(content_md)
        clean_content = self._resolve_link_placeholders(clean_content)
        clean_content = re.sub(r'\A\s*#\s+[^\n]+\n*', '', clean_content)
        post_data = {
            "posts": [{
                "mobiledoc": self._markdown_to_mobiledoc(clean_content),
                "updated_at": updated_at,
            }]
        }
        if extracted_scripts.strip():
            post_data["posts"][0]["codeinjection_foot"] = extracted_scripts

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
        schema_json_ld: str = None,
        status: str = "published",
        feature_image: str = None,
        feature_image_alt: str = None,
        og_title: str = None,
        og_description: str = None,
        custom_excerpt: str = None,
    ) -> dict:
        """Публикует статью в Ghost."""

        token = self._create_jwt_token()

        headers = {
            "Authorization": f"Ghost {token}",
            "Content-Type": "application/json"
        }

        # Extract <script> tags (e.g. JSON-LD) from content body
        clean_content, extracted_scripts = self._extract_script_tags(content)
        clean_content = self._resolve_link_placeholders(clean_content)
        # Strip leading H1 — Ghost renders title from post metadata,
        # so H1 in body causes duplicate heading
        clean_content = re.sub(r'\A\s*#\s+[^\n]+\n*', '', clean_content)

        post_data = {
            "posts": [{
                "title": title,
                "mobiledoc": self._markdown_to_mobiledoc(clean_content),
                "status": status,
            }]
        }

        if slug:
            post_data["posts"][0]["slug"] = slug
        if meta_title:
            post_data["posts"][0]["meta_title"] = meta_title
        if meta_description:
            post_data["posts"][0]["meta_description"] = meta_description
        if feature_image:
            post_data["posts"][0]["feature_image"] = feature_image
        if feature_image_alt:
            post_data["posts"][0]["feature_image_alt"] = feature_image_alt
        # OG fields with fallback to meta (so non-pipeline drafts also get OG).
        # Use `is not None` to allow intentional empty-string clearing.
        effective_og_title = og_title if og_title is not None else meta_title
        effective_og_description = og_description if og_description is not None else meta_description
        effective_excerpt = custom_excerpt if custom_excerpt is not None else meta_description
        if effective_og_title:
            post_data["posts"][0]["og_title"] = effective_og_title
        if effective_og_description:
            post_data["posts"][0]["og_description"] = effective_og_description
        if effective_excerpt:
            post_data["posts"][0]["custom_excerpt"] = effective_excerpt
        # JSON-LD and other scripts go to Ghost's code injection footer
        all_scripts = '\n'.join(filter(None, [schema_json_ld, extracted_scripts]))
        if all_scripts.strip():
            post_data["posts"][0]["codeinjection_foot"] = all_scripts

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
