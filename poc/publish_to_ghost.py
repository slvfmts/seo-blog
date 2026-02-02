#!/usr/bin/env python3
"""
Публикация статьи в Ghost CMS через Admin API.

Использование:
    python poc/publish_to_ghost.py
"""

import json
import hmac
import hashlib
import base64
import requests
from datetime import datetime
from pathlib import Path


# Ghost конфигурация
GHOST_URL = "http://95.163.230.43"
GHOST_ADMIN_KEY = "***REDACTED***"


def base64url_encode(data: bytes) -> str:
    """Base64 URL-safe encoding без padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def create_jwt_token(admin_key: str) -> str:
    """Создаёт JWT токен для Ghost Admin API (без внешних зависимостей)."""
    key_id, secret = admin_key.split(":")

    iat = int(datetime.now().timestamp())

    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    payload = {
        "iat": iat,
        "exp": iat + 5 * 60,  # 5 минут
        "aud": "/admin/"
    }

    # Кодируем header и payload
    header_b64 = base64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = base64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    # Создаём подпись
    message = f"{header_b64}.{payload_b64}".encode()
    signature = hmac.new(
        bytes.fromhex(secret),
        message,
        hashlib.sha256
    ).digest()
    signature_b64 = base64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def markdown_to_mobiledoc(markdown: str) -> str:
    """Конвертирует Markdown в Ghost mobiledoc формат."""
    mobiledoc = {
        "version": "0.3.1",
        "markups": [],
        "atoms": [],
        "cards": [["markdown", {"markdown": markdown}]],
        "sections": [[10, 0]]
    }
    return json.dumps(mobiledoc)


def publish_article(
    title: str,
    markdown_content: str,
    slug: str = None,
    meta_title: str = None,
    meta_description: str = None,
    status: str = "draft"
) -> dict:
    """Публикует статью в Ghost."""

    token = create_jwt_token(GHOST_ADMIN_KEY)

    headers = {
        "Authorization": f"Ghost {token}",
        "Content-Type": "application/json"
    }

    # Убираем JSON-блок из начала контента если есть
    content = markdown_content
    if content.startswith("```json"):
        end_marker = content.find("```", 7)
        if end_marker != -1:
            content = content[end_marker + 3:].strip()

    post_data = {
        "posts": [{
            "title": title,
            "mobiledoc": markdown_to_mobiledoc(content),
            "status": status,
        }]
    }

    if slug:
        post_data["posts"][0]["slug"] = slug
    if meta_title:
        post_data["posts"][0]["meta_title"] = meta_title
    if meta_description:
        post_data["posts"][0]["meta_description"] = meta_description

    response = requests.post(
        f"{GHOST_URL}/ghost/api/admin/posts/",
        headers=headers,
        json=post_data
    )

    if response.status_code == 201:
        return {"success": True, "post": response.json()["posts"][0]}
    else:
        return {"success": False, "error": response.text, "status": response.status_code}


def main():
    print("=" * 60)
    print("Публикация статьи в Ghost")
    print("=" * 60)
    print()

    # Находим последнюю сгенерированную статью
    output_dir = Path(__file__).parent / "output"
    md_files = sorted(output_dir.glob("article_*.md"), reverse=True)

    if not md_files:
        print("Ошибка: нет сгенерированных статей в poc/output/")
        return

    latest_article = md_files[0]
    print(f"Публикуем: {latest_article.name}")
    print()

    # Читаем контент
    content = latest_article.read_text(encoding="utf-8")

    # Извлекаем метаданные из JSON-блока
    metadata = {}
    if "```json" in content:
        try:
            json_start = content.index("```json") + 7
            json_end = content.index("```", json_start)
            json_str = content[json_start:json_end].strip()
            metadata = json.loads(json_str)
        except (ValueError, json.JSONDecodeError):
            pass

    title = metadata.get("title", "Контент-стратегия для B2B")
    slug = metadata.get("slug", "content-strategy-b2b")
    meta_title = metadata.get("title")
    meta_description = metadata.get("meta_description")

    print(f"Заголовок: {title}")
    print(f"Slug: {slug}")
    print()

    # Публикуем как черновик
    result = publish_article(
        title=title,
        markdown_content=content,
        slug=slug,
        meta_title=meta_title,
        meta_description=meta_description,
        status="draft"
    )

    if result["success"]:
        post = result["post"]
        print("✓ Статья опубликована как черновик!")
        print()
        print(f"  ID: {post['id']}")
        print(f"  URL: {GHOST_URL}/{post['slug']}/")
        print(f"  Редактировать: {GHOST_URL}/ghost/#/editor/post/{post['id']}")
        print()
        print("Чтобы опубликовать — открой редактор и нажми Publish")
    else:
        print(f"✗ Ошибка: {result['error']}")
        print(f"  Status: {result['status']}")


if __name__ == "__main__":
    main()
