"""
Internal Linker — data-driven article interlinking.

Maintains a keyword-article index in PostgreSQL and uses it to:
1. Find related articles by keyword overlap (forward linking)
2. Update existing articles with links to new ones (backward linking)
"""

import json
import logging
import re
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.db.models import ArticleKeyword, Base

logger = logging.getLogger(__name__)


class InternalLinker:
    """Service for keyword-based internal linking between articles."""

    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine)
        # Ensure table exists
        Base.metadata.create_all(self.engine, tables=[ArticleKeyword.__table__])

    def register_article(
        self,
        post_url: str,
        title: str,
        cms_post_id: Optional[str],
        content_md: Optional[str],
        keywords: list[tuple[str, str]],
        site_id: Optional[str] = None,
    ):
        """
        Save article + keywords to DB. Upsert — updates if post_url exists.

        Args:
            post_url: Published URL
            title: Article title
            cms_post_id: Ghost post ID
            content_md: Markdown content for backward linking
            keywords: List of (keyword, type) tuples where type is 'primary' | 'secondary'
            site_id: Optional site identifier
        """
        db: Session = self.SessionLocal()
        try:
            # Delete existing keywords for this URL (upsert)
            db.query(ArticleKeyword).filter(
                ArticleKeyword.post_url == post_url
            ).delete()

            for kw, kw_type in keywords:
                normalized = kw.lower().strip()
                if not normalized:
                    continue
                record = ArticleKeyword(
                    site_id=site_id,
                    post_url=post_url,
                    post_title=title,
                    cms_post_id=cms_post_id,
                    content_md=content_md,
                    keyword=normalized,
                    keyword_type=kw_type,
                )
                db.add(record)

            db.commit()
            logger.info(f"Registered article '{title}' with {len(keywords)} keywords")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to register article: {e}")
            raise
        finally:
            db.close()

    def find_related(
        self,
        keywords: list[str],
        exclude_url: Optional[str] = None,
        limit: int = 10,
        site_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Find articles with keyword overlap.

        Returns list of dicts sorted by relevance_score DESC:
            [{post_url, title, shared_keywords, relevance_score}]
        """
        if not keywords:
            return []

        normalized = [kw.lower().strip() for kw in keywords if kw.strip()]
        if not normalized:
            return []

        db: Session = self.SessionLocal()
        try:
            # Find all articles that share at least one keyword
            query = db.query(ArticleKeyword).filter(
                ArticleKeyword.keyword.in_(normalized)
            )
            if exclude_url:
                query = query.filter(ArticleKeyword.post_url != exclude_url)
            if site_id:
                query = query.filter(ArticleKeyword.site_id == site_id)

            matches = query.all()

            # Group by post_url and count shared keywords
            article_map: dict[str, dict] = {}
            for m in matches:
                if m.post_url not in article_map:
                    article_map[m.post_url] = {
                        "post_url": m.post_url,
                        "title": m.post_title,
                        "shared_keywords": [],
                    }
                article_map[m.post_url]["shared_keywords"].append(m.keyword)

            # Calculate relevance score
            results = []
            for url, data in article_map.items():
                # Get total keywords for this article
                total_kw = db.query(ArticleKeyword).filter(
                    ArticleKeyword.post_url == url
                ).count()
                shared_count = len(data["shared_keywords"])
                score = shared_count / max(total_kw, 1)
                data["relevance_score"] = round(score, 3)
                results.append(data)

            # Sort by relevance score DESC
            results.sort(key=lambda x: x["relevance_score"], reverse=True)
            return results[:limit]

        finally:
            db.close()

    def get_article_content(self, post_url: str) -> Optional[str]:
        """Get stored markdown for backward linking."""
        db: Session = self.SessionLocal()
        try:
            record = db.query(ArticleKeyword).filter(
                ArticleKeyword.post_url == post_url
            ).first()
            return record.content_md if record else None
        finally:
            db.close()

    def update_article_content(self, post_url: str, new_content_md: str):
        """Update stored markdown after backward linking."""
        db: Session = self.SessionLocal()
        try:
            db.query(ArticleKeyword).filter(
                ArticleKeyword.post_url == post_url
            ).update({"content_md": new_content_md})
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to update content for {post_url}: {e}")
        finally:
            db.close()

    @staticmethod
    def extract_keywords(intent, research) -> list[tuple[str, str]]:
        """
        Extract keywords from pipeline context.

        Returns: [(keyword, type)] where type = 'primary' | 'secondary'
        - intent.topic -> primary
        - research.queries_used -> secondary (deduplicated, normalized)
        """
        keywords = []
        seen = set()

        # Primary: topic
        if intent and intent.topic:
            topic_lower = intent.topic.lower().strip()
            if topic_lower:
                keywords.append((topic_lower, "primary"))
                seen.add(topic_lower)

        # Secondary: queries from research
        if research and research.queries_used:
            for q in research.queries_used:
                normalized = q.lower().strip()
                if normalized and normalized not in seen:
                    keywords.append((normalized, "secondary"))
                    seen.add(normalized)

        return keywords

    async def update_backlinks(
        self,
        new_url: str,
        new_title: str,
        new_keywords: list[str],
        llm_client,
        model: str,
        ghost_publisher,
        max_articles: int = 5,
        site_id: Optional[str] = None,
    ):
        """
        Update existing articles to include links to the new article.

        1. Find related articles by keyword overlap
        2. For each: load markdown, LLM call to insert link, update Ghost
        """
        related = self.find_related(new_keywords, exclude_url=new_url, limit=max_articles, site_id=site_id)
        if not related:
            logger.info("No related articles found for backward linking")
            return

        logger.info(f"Found {len(related)} candidates for backward linking")

        # Load backlink prompt
        import os
        prompt_dir = os.path.join(
            os.path.dirname(__file__),
            "writing_pipeline", "prompts"
        )
        prompt_path = os.path.join(prompt_dir, "backlink_v1.txt")
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_template = f.read()

        updated_count = 0
        for article in related:
            try:
                content_md = self.get_article_content(article["post_url"])
                if not content_md:
                    logger.debug(f"No stored content for {article['post_url']}, skipping")
                    continue

                # Get cms_post_id for this article
                db: Session = self.SessionLocal()
                try:
                    record = db.query(ArticleKeyword).filter(
                        ArticleKeyword.post_url == article["post_url"]
                    ).first()
                    cms_post_id = record.cms_post_id if record else None
                finally:
                    db.close()

                if not cms_post_id:
                    logger.debug(f"No cms_post_id for {article['post_url']}, skipping")
                    continue

                # Build prompt
                prompt = prompt_template.replace("{{existing_article_md}}", content_md)
                prompt = prompt.replace("{{new_article_url}}", new_url)
                prompt = prompt.replace("{{new_article_title}}", new_title)
                prompt = prompt.replace(
                    "{{new_article_keywords}}",
                    ", ".join(new_keywords[:10])
                )

                # LLM call
                response = llm_client.messages.create(
                    model=model,
                    max_tokens=16000,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                new_content = response.content[0].text.strip()

                # Validate: new content should contain the new URL
                if new_url not in new_content:
                    logger.debug(f"LLM did not add link to {article['post_url']}, skipping")
                    continue

                # Validate: content should not have shrunk dramatically
                if len(new_content) < len(content_md) * 0.8:
                    logger.warning(f"Content shrunk too much for {article['post_url']}, skipping")
                    continue

                # Get current post from Ghost for updated_at
                ghost_post = ghost_publisher.get_post(cms_post_id)
                if not ghost_post:
                    logger.warning(f"Could not fetch Ghost post {cms_post_id}")
                    continue

                # Update Ghost
                update_result = ghost_publisher.update_post(
                    post_id=cms_post_id,
                    content_md=new_content,
                    updated_at=ghost_post.get("updated_at", ""),
                )

                if update_result.get("success"):
                    self.update_article_content(article["post_url"], new_content)
                    updated_count += 1
                    logger.info(f"Backward link added to {article['title']}")
                else:
                    logger.warning(
                        f"Failed to update Ghost post {cms_post_id}: "
                        f"{update_result.get('error')}"
                    )

            except Exception as e:
                logger.error(f"Error during backward linking for {article['post_url']}: {e}")
                continue

        logger.info(f"Backward linking complete: {updated_count}/{len(related)} articles updated")
