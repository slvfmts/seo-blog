#!/usr/bin/env python3
"""
Cold start migration: populate article_keywords from existing Ghost posts.

Usage:
    DATABASE_URL=postgresql://... GHOST_URL=http://... GHOST_ADMIN_KEY=... \
    ANTHROPIC_API_KEY=... python scripts/populate_article_keywords.py

This script:
1. Fetches all published posts from Ghost
2. For each post: uses LLM to extract 5-10 keywords from title + excerpt
3. Registers each article in the article_keywords table
"""

import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import anthropic
from src.services.publisher import GhostPublisher
from src.services.internal_linker import InternalLinker


def extract_keywords_with_llm(client, title: str, excerpt: str, model: str) -> list[tuple[str, str]]:
    """Use LLM to extract keywords from title and excerpt."""
    prompt = f"""Извлеки 5-10 ключевых слов/фраз из заголовка и описания статьи.
Верни JSON-массив объектов с полями "keyword" и "type" (primary или secondary).
Первый элемент должен быть primary (основная тема), остальные — secondary.

Заголовок: {title}
Описание: {excerpt or '(нет описания)'}

Верни ТОЛЬКО JSON-массив, без пояснений:
[{{"keyword": "...", "type": "primary"}}, {{"keyword": "...", "type": "secondary"}}]"""

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    text = response.content[0].text.strip()

    # Parse JSON
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
        return [(item["keyword"].lower().strip(), item["type"]) for item in data]
    except (json.JSONDecodeError, KeyError):
        # Fallback: use title words as keywords
        words = title.lower().split()
        return [(title.lower().strip(), "primary")] + [
            (w, "secondary") for w in words if len(w) > 3
        ][:5]


def main():
    # Read environment
    database_url = os.environ.get("DATABASE_URL")
    ghost_url = os.environ.get("GHOST_URL")
    ghost_admin_key = os.environ.get("GHOST_ADMIN_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("MODEL", "claude-sonnet-4-20250514")

    if not all([database_url, ghost_url, ghost_admin_key, anthropic_key]):
        print("Required environment variables:")
        print("  DATABASE_URL, GHOST_URL, GHOST_ADMIN_KEY, ANTHROPIC_API_KEY")
        sys.exit(1)

    # Initialize services
    publisher = GhostPublisher(ghost_url, ghost_admin_key)
    linker = InternalLinker(database_url)
    client = anthropic.Anthropic(api_key=anthropic_key)

    # Fetch all posts
    print("Fetching posts from Ghost...")
    posts = publisher.get_posts()
    print(f"Found {len(posts)} published posts")

    if not posts:
        print("No posts to process")
        return

    registered = 0
    skipped = 0

    for i, post in enumerate(posts, 1):
        title = post.get("title", "")
        url = post.get("url", "")
        excerpt = post.get("excerpt", "")

        if not url or not title:
            skipped += 1
            continue

        print(f"\n[{i}/{len(posts)}] {title}")
        print(f"  URL: {url}")

        try:
            # Extract keywords
            keywords = extract_keywords_with_llm(client, title, excerpt, model)
            print(f"  Keywords: {[kw for kw, _ in keywords]}")

            # Register (no content_md — Ghost doesn't return markdown in list API)
            linker.register_article(
                post_url=url,
                title=title,
                cms_post_id=None,  # We don't have post IDs from get_posts()
                content_md=None,
                keywords=keywords,
            )
            registered += 1

        except Exception as e:
            print(f"  ERROR: {e}")
            skipped += 1

    print(f"\n{'='*50}")
    print(f"Done! Registered: {registered}, Skipped: {skipped}")
    print(f"Total keywords in DB can be checked with:")
    print(f"  SELECT COUNT(*) FROM article_keywords;")


if __name__ == "__main__":
    main()
