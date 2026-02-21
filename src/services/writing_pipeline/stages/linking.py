"""
Linking Stage - Inserts internal links using keyword-based article index.
"""

import json
import os
import re
import logging

from ..core.stage import WritingStage
from ..core.context import WritingContext

logger = logging.getLogger(__name__)


class LinkingStage(WritingStage):
    """
    Stage 6: Linking (between Editing and Meta)

    Uses InternalLinker to find related articles by keyword overlap,
    then asks LLM to insert 2-5 internal links into the edited article.
    """

    def __init__(self, client, model, linker=None):
        super().__init__(client, model)
        self.linker = linker

    @property
    def name(self) -> str:
        return "linking"

    async def run(self, context: WritingContext) -> WritingContext:
        """Execute linking stage."""
        log = context.start_stage(self.name)

        try:
            if not context.edited_md:
                logger.info("Linking skipped: no edited content")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            if not self.linker:
                # Check if brief has internal_links_plan — use it directly
                brief = context.config.get("brief")
                if brief:
                    brief_data = brief if isinstance(brief, dict) else brief.to_dict()
                    links_plan = brief_data.get("internal_links_plan", [])
                    if links_plan:
                        logger.info(f"Linking: using {len(links_plan)} links from brief (no linker)")
                        context.config["_brief_links_plan"] = links_plan
                logger.info("Linking skipped: no linker configured")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            if context.intent is None or context.research is None:
                logger.info("Linking skipped: missing intent or research data")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            # 1. Extract keywords from pipeline data
            from ...internal_linker import InternalLinker
            keywords = InternalLinker.extract_keywords(context.intent, context.research)

            if not keywords:
                logger.info("Linking skipped: no keywords extracted")
                context.complete_stage(tokens_used=0, metadata={"skipped": True})
                return context

            # 2. Find related articles
            keyword_strings = [kw for kw, _ in keywords]
            related = self.linker.find_related(keyword_strings, exclude_url=None, limit=10)

            if not related:
                logger.info("Linking skipped: no related articles found")
                # Store keywords for post-publication registration
                context.config["_article_keywords"] = keywords
                context.config["_related_articles"] = []
                context.complete_stage(tokens_used=0, metadata={"related_count": 0})
                return context

            logger.info(f"Found {len(related)} related articles for linking")

            # 3. LLM call to insert links
            prompt_template = self._load_prompt("linking_v1")

            related_json = json.dumps(
                [{"url": r["post_url"], "title": r["title"]} for r in related],
                ensure_ascii=False,
                indent=2,
            )

            prompt = prompt_template.replace("{{article_md}}", context.edited_md)
            prompt = prompt.replace("{{related_articles_json}}", related_json)

            response_text, tokens = self._call_llm(
                prompt, max_tokens=16000, temperature=0.3
            )

            # 4. Validate — keep only links to known URLs
            valid_urls = {r["post_url"] for r in related}
            linked_md = self._validate_links(response_text.strip(), valid_urls)

            context.edited_md = linked_md

            # 5. Store keywords + related in context for post-publication
            context.config["_article_keywords"] = keywords
            context.config["_related_articles"] = related

            # Count inserted links
            link_count = self._count_internal_links(linked_md, valid_urls)

            # Save intermediate
            if context.save_intermediate and context.output_dir:
                output_path = os.path.join(context.output_dir, "07b_linking.json")
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "related_articles": related,
                        "keywords_used": keyword_strings[:20],
                        "links_inserted": link_count,
                    }, f, ensure_ascii=False, indent=2)

            context.complete_stage(
                tokens_used=tokens,
                metadata={
                    "related_count": len(related),
                    "links_inserted": link_count,
                },
            )

        except Exception as e:
            context.fail_stage(str(e))
            raise

        return context

    def _validate_links(self, text: str, valid_urls: set[str]) -> str:
        """Remove any links whose URL is not in valid_urls set."""
        removed = 0

        def check_link(match):
            nonlocal removed
            anchor = match.group(1)
            url = match.group(2)

            normalized = url.rstrip("/")
            # Check if URL is in valid set (with/without trailing slash)
            if url in valid_urls or normalized in valid_urls or (normalized + "/") in valid_urls:
                return match.group(0)  # keep

            # Check if it looks external (different from our valid URLs)
            # If it starts with http and is not in valid_urls, could be external
            from urllib.parse import urlparse
            valid_domains = set()
            for v in valid_urls:
                try:
                    parsed = urlparse(v)
                    if parsed.netloc:
                        valid_domains.add(parsed.netloc)
                except Exception:
                    pass

            try:
                parsed = urlparse(url)
                if parsed.netloc and parsed.netloc not in valid_domains:
                    return match.group(0)  # external link, keep
            except Exception:
                pass

            # Hallucinated internal link — strip to anchor text
            removed += 1
            return anchor

        result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', check_link, text)
        if removed:
            logger.warning(f"Link validation: removed {removed} hallucinated links")
        return result

    def _count_internal_links(self, text: str, valid_urls: set[str]) -> int:
        """Count how many links point to valid internal URLs."""
        count = 0
        for match in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', text):
            url = match.group(2)
            normalized = url.rstrip("/")
            if url in valid_urls or normalized in valid_urls or (normalized + "/") in valid_urls:
                count += 1
        return count
